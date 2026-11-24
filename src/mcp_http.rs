#![cfg(target_os="linux")]
//! Opt-in detached snapshot MCP JSON transport. No stateful or tool channel.
use crate::{mcp_scoped,plugins::{Permission,PluginKind,Plugins},snapshot::Snapshot,store::Store};
use axum::{Router,body::to_bytes,extract::{Request,State},http::{header,HeaderMap,HeaderValue,Method,StatusCode},
    response::{IntoResponse,Response},routing::any};
use serde::{Deserialize,Deserializer};
use serde_json::{json,Value};
use std::{error::Error,fs::File,io::{self,Read},net::Ipv4Addr,path::Path,sync::Arc};
use tokio::sync::Semaphore;

const PROTOCOL:&str="2025-11-25";
const MAX_GRANTS:usize=64*1024;
const MAX_INPUT:usize=24*1024;
const MAX_REPLY:usize=256*1024;

#[derive(Clone)]
struct Lane {authority:HeaderValue,plugins:Plugins,id:String,snapshot:Snapshot,store:Store,slots:Arc<Semaphore>}

fn failure(message:&'static str)->io::Error {io::Error::new(io::ErrorKind::InvalidInput,message)}
fn private_grants(path:&Path)->Result<Plugins,Box<dyn Error>> {
    use rustix::fs::{fstat,open,FileType,Mode,OFlags};
    let file:File=open(path,OFlags::RDONLY|OFlags::NOFOLLOW|OFlags::NONBLOCK|OFlags::CLOEXEC,Mode::empty())
        .map_err(|_|failure("private MCP grant file unavailable"))?.into();
    let meta=fstat(&file).map_err(|_|failure("private MCP grant file unavailable"))?;
    if FileType::from_raw_mode(meta.st_mode)!=FileType::RegularFile || meta.st_mode&0o077!=0
        || meta.st_nlink!=1 || meta.st_uid!=rustix::process::geteuid().as_raw() {
        return Err(failure("MCP grants must be a private current-owner regular file").into());
    }
    let mut input=Vec::new();
    file.take((MAX_GRANTS+1) as u64).read_to_end(&mut input)
        .map_err(|_|failure("private MCP grant file unavailable"))?;
    Ok(Plugins::parse(&input)?)
}
pub async fn serve(path:&Path,grants:&Path,id:String,port:u16)->Result<(),Box<dyn Error>> {
    if port==0 || !(1..=128).contains(&id.len()) ||
        !id.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'.'|b'_'|b'-')) {
        return Err(failure("expected exact local port and scoped MCP grant ID").into());
    }
    let plugins=private_grants(grants)?;
    if !plugins.snapshot_tool_id(&id) {return Err(failure("configured scoped snapshot MCP grant is unavailable").into());}
    let snapshot=Snapshot::open(path)?;
    let store=Store::open(snapshot.path())?;
    let socket=tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST,port)).await?;
    let authority=HeaderValue::from_str(&format!("127.0.0.1:{port}"))?;
    let lane=Lane{authority,plugins,id,snapshot,store,slots:Arc::new(Semaphore::new(4))};
    let router=Router::new().route("/mcp",any(handle)).fallback(||async{reply(StatusCode::NOT_FOUND,None)}).with_state(lane);
    println!("{}",json!({"event":"ready","origin":format!("http://127.0.0.1:{port}"),
        "path":"/mcp","mode":"mcp-http-snapshot-read-only","protocol":PROTOCOL}));
    axum::serve(socket,router).await?;
    Ok(())
}

fn one<'a>(headers:&'a HeaderMap,name:&str)->Option<&'a HeaderValue> {
    let all=headers.get_all(name);
    let mut iter=all.iter();
    let first=iter.next()?;
    iter.next().is_none().then_some(first)
}
fn exact(headers:&HeaderMap,name:&str,value:&str)->bool {
    one(headers,name).and_then(|h|h.to_str().ok())==Some(value)
}
fn accept(headers:&HeaderMap)->bool {
    let Some(raw)=one(headers,"accept").and_then(|h|h.to_str().ok()) else{return false;};
    if raw.len()>256 {return false;}
    let mut json=false;
    let mut sse=false;
    for value in raw.split(',').map(str::trim) {
        match value { "application/json"=>json=true,"text/event-stream"=>sse=true,_=>return false }
    }
    json && sse
}
fn reply(status:StatusCode,data:Option<Vec<u8>>)->Response {
    let mut response=match data {
        Some(bytes)=>(status,[(header::CONTENT_TYPE,"application/json")],bytes).into_response(),
        None=>status.into_response(),
    };
    let h=response.headers_mut();
    h.insert(header::CACHE_CONTROL,HeaderValue::from_static("no-store"));
    h.insert(header::X_CONTENT_TYPE_OPTIONS,HeaderValue::from_static("nosniff"));
    h.insert(header::REFERRER_POLICY,HeaderValue::from_static("no-referrer"));
    if status==StatusCode::UNAUTHORIZED {h.insert(header::WWW_AUTHENTICATE,HeaderValue::from_static("Bearer"));}
    if status==StatusCode::METHOD_NOT_ALLOWED {h.insert(header::ALLOW,HeaderValue::from_static("POST"));}
    response
}
fn rpc(id:Value,result:Result<Value,(i64,&'static str)>)->Response {
    let value=match result {
        Ok(value)=>json!({"jsonrpc":"2.0","id":id,"result":value}),
        Err((code,message))=>json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}}),
    };
    match serde_json::to_vec(&value) {
        Ok(bytes) if bytes.len()<=MAX_REPLY=>reply(StatusCode::OK,Some(bytes)),
        _=>reply(StatusCode::PAYLOAD_TOO_LARGE,None),
    }
}
fn invalid_request(status:StatusCode,code:i64,message:&'static str)->Response {
    let data=json!({"jsonrpc":"2.0","id":null,"error":{"code":code,"message":message}});
    reply(status,serde_json::to_vec(&data).ok())
}
fn present<'de,D:Deserializer<'de>>(input:D)->Result<Option<Value>,D::Error>{Value::deserialize(input).map(Some)}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Call {
    jsonrpc:String,
    method:String,
    #[serde(default,deserialize_with="present")] id:Option<Value>,
    params:Option<Value>,
}
fn valid_id(id:&Value)->bool {
    id.as_str().is_some_and(|s|s.len()<=128)||id.as_i64().is_some()||id.as_u64().is_some()
}
fn init(params:Option<&Value>)->bool {
    let Some(p)=params.and_then(Value::as_object) else{return false;};
    let Some(info)=p.get("clientInfo").and_then(Value::as_object) else{return false;};
    p.get("protocolVersion").and_then(Value::as_str)==Some(PROTOCOL)
        && p.get("capabilities").is_some_and(Value::is_object)
        && ["name","version"].iter().all(|field|
            info.get(*field).and_then(Value::as_str).is_some_and(|s|(1..=128).contains(&s.len())))
}
async fn handle(State(lane):State<Lane>,request:Request)->Response {
    let h=request.headers();
    if one(h,"host")!=Some(&lane.authority) || request.uri().scheme().is_some() || request.uri().authority().is_some()
        || h.contains_key("origin") || h.contains_key("cookie") || h.contains_key("sec-fetch-site")
        || h.contains_key("forwarded") || h.contains_key("x-forwarded-host")
    {return reply(StatusCode::FORBIDDEN,None);}
    if request.uri().query().is_some() || h.contains_key("mcp-session-id")
        || h.contains_key("last-event-id") || h.contains_key("content-encoding")
    {return reply(StatusCode::BAD_REQUEST,None);}
    let Some(plugin)=one(h,"authorization").and_then(|h|h.to_str().ok())
        .and_then(|value|lane.plugins.authenticate(value))
        .filter(|p|p.id==lane.id && p.kind==PluginKind::ScopedTool && !p.permissions.contains(&Permission::McpIncoming)
            && !p.permissions.contains(&Permission::McpConversationNote))
    else {return reply(StatusCode::UNAUTHORIZED,None);};
    let Some(scope)=plugin.account_scope.clone() else{return reply(StatusCode::FORBIDDEN,None);};
    if request.method()!=Method::POST {return reply(StatusCode::METHOD_NOT_ALLOWED,None);}
    if !accept(h) {return reply(StatusCode::NOT_ACCEPTABLE,None);}
    if !exact(h,"content-type","application/json") && !exact(h,"content-type","application/json; charset=utf-8") {
        return reply(StatusCode::UNSUPPORTED_MEDIA_TYPE,None);
    }
    let has_protocol=h.contains_key("mcp-protocol-version");
    let protocol_ok=exact(h,"mcp-protocol-version",PROTOCOL);
    if has_protocol && !protocol_ok {return reply(StatusCode::BAD_REQUEST,None);}
    let Ok(slot)=lane.slots.clone().try_acquire_owned() else{return reply(StatusCode::SERVICE_UNAVAILABLE,None);};
    let body=match tokio::time::timeout(std::time::Duration::from_secs(5),to_bytes(request.into_body(),MAX_INPUT)).await {
        Ok(Ok(body))=>body,
        Ok(Err(_))=>return reply(StatusCode::PAYLOAD_TOO_LARGE,None),
        Err(_)=>return reply(StatusCode::REQUEST_TIMEOUT,None),
    };
    if body.iter().copied().find(|byte|!matches!(byte,b' '|b'\r'|b'\n'|b'\t'))!=Some(b'{') {
        return invalid_request(StatusCode::BAD_REQUEST,-32600,"Expected one JSON-RPC object.");
    }
    let message:Call=match serde_json::from_slice(&body) {
        Ok(call)=>call,
        Err(e) if e.is_syntax()||e.is_eof()=>return invalid_request(StatusCode::BAD_REQUEST,-32700,"Invalid JSON."),
        Err(_)=>return invalid_request(StatusCode::BAD_REQUEST,-32600,"Invalid request."),
    };
    if message.jsonrpc!="2.0" || !(1..=64).contains(&message.method.len())
        || message.id.as_ref().is_some_and(|id|!valid_id(id)) {
        return invalid_request(StatusCode::BAD_REQUEST,-32600,"Invalid request.");
    }
    let params=message.params.as_ref();
    if message.method=="initialize" {
        let Some(id)=message.id else{return invalid_request(StatusCode::BAD_REQUEST,-32600,"Expected request ID.");};
        if !init(params) {return rpc(id,Err((-32602,"Expected protocol 2025-11-25 and local client metadata.")));}
        let mut caps=json!({"resources":{"subscribe":false,"listChanged":false}});
        if plugin.permissions.contains(&Permission::McpPrompts) {caps["prompts"]=json!({"listChanged":false});}
        return rpc(id,Ok(json!({"protocolVersion":PROTOCOL,"capabilities":caps,
              "serverInfo":{"name":"local-message-hub-rs","version":env!("CARGO_PKG_VERSION")}})));
    }
    if !protocol_ok {return reply(StatusCode::BAD_REQUEST,None);}
    let Some(id)=message.id else {
        return if message.method=="notifications/initialized" && params.is_none_or(Value::is_object) {
            reply(StatusCode::ACCEPTED,None)
        } else {reply(StatusCode::BAD_REQUEST,None)};
    };
    if message.method=="ping" && params.is_none_or(Value::is_object) {return rpc(id,Ok(json!({})));}
    if !matches!(message.method.as_str(),"resources/list"|"resources/templates/list"|"resources/read"|"prompts/list"|"prompts/get") {
        return rpc(id,Err((-32601,"Method not supported on this read-only transport.")));
    }
    let result=tokio::task::spawn_blocking(move|| {
        let _slot=slot;
        lane.snapshot.verify().map_err(|_|(-32000,"Snapshot unavailable."))?;
        let result=mcp_scoped::dispatch(&message.method,message.params.as_ref(),&lane.snapshot,&lane.store,&plugin,&scope);
        lane.snapshot.verify().map_err(|_|(-32000,"Snapshot unavailable."))?;
        result
    }).await;
    rpc(id,result.unwrap_or(Err((-32000,"Snapshot unavailable."))))
}
