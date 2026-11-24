//! Optional Linux-only link from an ordinary live account to a verified detached
//! media bundle. Matching source IDs alone never authorizes metadata disclosure.
use crate::{
    events::EventStore,legacy_import::read_stable_source,media_bundle::{Bundle,MessageKey},
    plugins::{AccountScope,Permission,Plugin,PluginKind},snapshot::Snapshot,store::Store,
};
use axum::{
    Json,Router,body::Bytes,extract::{Extension,Path as AxumPath,Query,State,
        rejection::{PathRejection,QueryRejection}},
    http::{HeaderMap,HeaderName,StatusCode,header},response::{IntoResponse,Response},routing::get,
};
use rusqlite::{Connection,params};
use serde::{Deserialize,Serialize};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{io,os::unix::fs::MetadataExt,path::{Path,PathBuf},sync::Arc};
use subtle::ConstantTimeEq;
use tokio::sync::{OwnedSemaphorePermit,Semaphore};

pub(crate) const BROWSE:&str="/api/v1/messages/live-imported-media";
pub(crate) const REFERENCE:&str="/api/v1/messages/live-imported-media/{id}/reference";
pub(crate) const BYTES:&str="/api/v1/messages/live-imported-media/{id}/bytes";
pub(crate) const MAX_VERIFIED_BYTES:usize=1024*1024;
const BYTES_PURPOSE:&str="lmh-live-imported-media-verified-bytes-v1";
const PURPOSE:&str="lmh-live-imported-media-link-v1";
const CONFIG_LIMIT:usize=8*1024;
const MAX_ATTACHMENT_ROWS:usize=4094;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    version:u8,
    bundle_directory:PathBuf,
    platform:String,
    account_id:String,
    binding:String,
    verified_bytes_binding:Option<String>,
}

#[derive(Clone)]
pub struct Link {
    bundle:Bundle,
    bundle_index:Store,
    snapshot:Snapshot,
    scope:AccountScope,
    archive_id:String,
    verified_bytes_enabled:bool,
}
fn invalid()->io::Error{io::Error::other("private live media link unavailable")}
fn digest(bytes:impl AsRef<[u8]>)->String {hex::encode(Sha256::digest(bytes))}
fn hex_id(id:&str)->bool {
    id.len()==64 && id.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn scope(platform:&str,account:&str)->io::Result<AccountScope> {
    if !matches!(platform,"qq"|"wechat") || !(1..=256).contains(&account.len())
        || account.trim()!=account || account.chars().any(char::is_control)
    {return Err(invalid());}
    Ok(AccountScope{platform:platform.to_owned(),account_id:account.to_owned()})
}

fn config_file(path:&Path)->io::Result<Config> {
    let before=std::fs::symlink_metadata(path).map_err(|_|invalid())?;
    let private=|m:&std::fs::Metadata| {
        m.is_file() && m.nlink()==1 && m.mode()&0o077==0
            && m.uid()==rustix::process::geteuid().as_raw()
    };
    if !private(&before){return Err(invalid());}
    let bytes=read_stable_source(path,CONFIG_LIMIT).map_err(|_|invalid())?;
    let after=std::fs::symlink_metadata(path).map_err(|_|invalid())?;
    if !private(&after) || before.dev()!=after.dev() || before.ino()!=after.ino()
        || before.ctime()!=after.ctime() || before.ctime_nsec()!=after.ctime_nsec()
    {return Err(invalid());}
    serde_json::from_slice(&bytes).map_err(|_|invalid())
}

fn open_bundle(events:&EventStore,requested:&Path)->io::Result<(Bundle,Snapshot,Store)> {
    events.require_ordinary().map_err(|_|invalid())?;
    events.verify().map_err(|_|invalid())?;
    let live=events.path().parent().ok_or_else(invalid)?.canonicalize().map_err(|_|invalid())?;
    if !requested.is_absolute() || !std::fs::symlink_metadata(requested).map_err(|_|invalid())?.is_dir(){
        return Err(invalid());
    }
    let root=requested.canonicalize().map_err(|_|invalid())?;
    if live.starts_with(&root) || root.starts_with(&live){return Err(invalid());}
    Bundle::open(&root).map_err(|_|invalid())
}

fn approval(events:&EventStore,bundle:&Bundle,scope:&AccountScope)->io::Result<String> {
    let payload=serde_json::to_vec(&(PURPOSE,events.archive_id(),bundle.catalog_stamp(),
        &scope.platform,&scope.account_id)).map_err(|_|invalid())?;
    Ok(digest(payload))
}

fn bytes_approval(events:&EventStore,bundle:&Bundle,scope:&AccountScope)->io::Result<String>{
    let payload=serde_json::to_vec(&(BYTES_PURPOSE,events.archive_id(),bundle.catalog_stamp(),
        &scope.platform,&scope.account_id,MAX_VERIFIED_BYTES as u64)).map_err(|_|invalid())?;
    Ok(digest(payload))
}

impl Link {
    /// Preview of exact archive/catalog/account linkage; contains no paths or
    /// message/attachment listings. The operator must put the result into a
    /// private config and opt in when launching the ordinary listener.
    pub fn preview(events:&EventStore,bundle:&Path,platform:&str,account:&str)->io::Result<Value> {
        let scope=scope(platform,account)?;
        let (bundle,snapshot,_)=open_bundle(events,bundle)?;
        snapshot.verify().map_err(|_|invalid())?;
        bundle.verify()?;
        events.verify().map_err(|_|invalid())?;
        Ok(json!({"version":1,"binding":approval(events,&bundle,&scope)?,
            "optional_verified_bytes_binding":bytes_approval(events,&bundle,&scope)?,
            "verified_bytes_limit":MAX_VERIFIED_BYTES,
            "method":"operator_confirmed_exact_account","message_match_at_request":true}))
    }

    pub fn open(events:&EventStore,config:&Path)->io::Result<Self> {
        let configured=config_file(config)?;
        if configured.version!=1 || !hex_id(&configured.binding){return Err(invalid());}
        let scope=scope(&configured.platform,&configured.account_id)?;
        let (bundle,snapshot,bundle_index)=open_bundle(events,&configured.bundle_directory)?;
        if approval(events,&bundle,&scope)?!=configured.binding{return Err(invalid());}
        let verified_bytes_enabled=if let Some(binding)=configured.verified_bytes_binding{
            if !hex_id(&binding) || binding!=bytes_approval(events,&bundle,&scope)?{return Err(invalid());}
            true
        }else{false};
        snapshot.verify().map_err(|_|invalid())?;
        bundle.verify()?;
        events.verify().map_err(|_|invalid())?;
        Ok(Self{bundle,bundle_index,snapshot,scope,archive_id:events.archive_id().to_owned(),
            verified_bytes_enabled})
    }

    pub fn matches_archive(&self,events:&EventStore)->bool {
        events.require_ordinary().is_ok() && self.archive_id==events.archive_id()
    }

    pub(crate) fn eligible(&self,plugin:&Plugin)->bool {
        plugin.kind==PluginKind::Desktop
            && plugin.permissions.contains(&Permission::Messages)
            && plugin.permissions.contains(&Permission::Media)
            && plugin.permissions.contains(&Permission::LiveImportedMedia)
            && plugin.account_scope.as_ref().is_some_and(|scope|
                scope.platform==self.scope.platform && scope.account_id==self.scope.account_id)
    }

    pub(crate) fn bytes_eligible(&self,plugin:&Plugin)->bool{
        self.verified_bytes_enabled && self.eligible(plugin)
            && plugin.permissions.contains(&Permission::LiveImportedMediaBytes)
    }

    fn verify(&self,events:&EventStore)->Result<(),Api>{
        if !self.matches_archive(events){return Err(unavailable());}
        self.bundle.verify().map_err(|_|unavailable())?;
        self.snapshot.verify().map_err(|_|unavailable())?;
        events.verify().map_err(|_|unavailable())?;
        Ok(())
    }
}

/// The indexer in this release writes normalized live rows as text. For media
/// linking do not reinterpret a typed detached voice/image as that text row.
/// Require strong equal inputs from both stores, not just a matching selector.
#[derive(PartialEq,Eq)]
struct Witness { timestamp:i64, owner:i64, sender:String, text:String }
fn witness(db:&Connection,key:&MessageKey)->rusqlite::Result<Option<Witness>> {
    let mut stmt=db.prepare(
        "SELECT CASE WHEN m.type COLLATE BINARY='text' THEN 1 ELSE 0 END,
         CASE WHEN typeof(m.timestamp)='integer' AND m.timestamp>0 THEN m.timestamp END,
         CASE WHEN typeof(m.is_self)='integer' AND m.is_self IN (0,1) THEN m.is_self END,
         CASE WHEN typeof(m.sender_id)='text' AND length(CAST(m.sender_id AS BLOB)) BETWEEN 1 AND 512
              THEN m.sender_id END,
         CASE WHEN typeof(m.text)='text' AND length(CAST(m.text AS BLOB)) BETWEEN 1 AND 65536
              THEN m.text END
         FROM messages m JOIN conversations c ON c.platform COLLATE BINARY=m.platform
            AND c.account_id COLLATE BINARY=m.account_id AND c.id COLLATE BINARY=m.conversation_id
         WHERE m.platform COLLATE BINARY=?1 AND m.account_id COLLATE BINARY=?2 AND m.id COLLATE BINARY=?3
            AND m.conversation_id COLLATE BINARY=?4
         LIMIT 2")?;
    let mut rows=stmt.query(params![key.platform,key.account_id,key.message_id,key.conversation_id])?;
    let Some(row)=rows.next()? else{return Ok(None);};
    let data=(
        row.get::<_,bool>(0)?,row.get::<_,Option<i64>>(1)?,row.get::<_,Option<i64>>(2)?,
        row.get::<_,Option<String>>(3)?,row.get::<_,Option<String>>(4)?,
    );
    // Reject competing rows even when they themselves would not qualify.
    if rows.next()?.is_some(){return Ok(None);}
    match data {
        (true,Some(timestamp),Some(owner),Some(sender),Some(text))
            if !sender.trim().is_empty() && !text.trim().is_empty()
            =>Ok(Some(Witness{timestamp,owner,sender,text})),
        _=>Ok(None),
    }
}

fn exact_match(link:&Link,events:&EventStore,key:&MessageKey)->Result<Witness,Api>{
    let mut db=events.read_connection().map_err(|_|unavailable())?;
    let tx=db.transaction().map_err(|_|unavailable())?;
    events.verify_connection(&tx).map_err(|_|unavailable())?;
    let live=witness(&tx,key).map_err(|_|unavailable())?.ok_or_else(missing)?;
    let detached=link.bundle_index.connection().map_err(|_|unavailable())?;
    let detached=witness(&detached,key).map_err(|_|unavailable())?;
    if detached.as_ref()!=Some(&live){return Err(missing());}
    Ok(live)
}

fn recheck(link:&Link,events:&EventStore,key:&MessageKey,before:Witness)->Result<(),Api>{
    let db=events.read_connection().map_err(|_|unavailable())?;
    let after=witness(&db,key).map_err(|_|unavailable())?;
    if after.as_ref()!=Some(&before){return Err(missing());}
    link.verify(events)
}

#[derive(Clone)]
struct Server{events:EventStore,link:Link,permits:Arc<Semaphore>}

pub(crate) fn router(events:EventStore,link:Link)->Router {
    let routes=Router::new().route(BROWSE,get(browse)).route(REFERENCE,get(reference));
    let routes=if link.verified_bytes_enabled{routes.route(BYTES,get(verified_bytes))}else{routes};
    routes.with_state(Server{events,link,permits:Arc::new(Semaphore::new(2))})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Browse {
    conversation_id:String,message_id:String,limit:Option<usize>,offset:Option<usize>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Selected {conversation_id:String,message_id:String}
#[derive(Serialize)]
struct Item {id:String,size:u64}
#[derive(Serialize)]
struct Page {
    items:Vec<Item>,next_offset:Option<usize>,blob_rechecked:bool,provenance:&'static str,
}
#[derive(Serialize)]
struct Reference {
    id:String,size:usize,sha256:String,blob_rechecked:bool,provenance:&'static str,
}
const PROVENANCE:&str="verified_imported_catalog_exact_live_match";

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api{
    fn into_response(self)->Response{(self.0,Json(json!({"error":{"code":self.1}}))).into_response()}
}
fn missing()->Api{Api(StatusCode::NOT_FOUND,"not_found")}
fn bad()->Api{Api(StatusCode::BAD_REQUEST,"bad_live_imported_media_query")}
fn unavailable()->Api{Api(StatusCode::SERVICE_UNAVAILABLE,"live_imported_media_unavailable")}

fn key(link:&Link,conversation_id:String,message_id:String)->Result<MessageKey,Api>{
    let key=MessageKey{platform:link.scope.platform.clone(),account_id:link.scope.account_id.clone(),
        conversation_id,message_id};
    if !key.valid(){return Err(bad());}
    Ok(key)
}
fn allowed(server:&Server,plugin:&Plugin)->Result<(),Api>{
    if !server.link.eligible(plugin){return Err(Api(StatusCode::FORBIDDEN,"permission_required"));}
    Ok(())
}
async fn run<T:Serialize+Send+'static>(
    server:Server,work:impl FnOnce(&Link,&EventStore)->Result<T,Api>+Send+'static,
)->Result<Json<T>,Api>{
    let permit=server.permits.clone().try_acquire_owned().map_err(|_|unavailable())?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        server.link.verify(&server.events)?;
        work(&server.link,&server.events).map(Json)
    }).await.map_err(|_|unavailable())?
}
async fn browse(State(server):State<Server>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Browse>,QueryRejection>)->Result<Json<Page>,Api>{
    allowed(&server,&plugin)?;
    let Query(q)=input.map_err(|_|bad())?;
    let limit=q.limit.unwrap_or(25);
    let offset=q.offset.unwrap_or(0);
    if !(1..=50).contains(&limit) || offset>MAX_ATTACHMENT_ROWS{return Err(bad());}
    let key=key(&server.link,q.conversation_id,q.message_id)?;
    run(server,move|link,events|{
        let before=exact_match(link,events,&key)?;
        let catalogue=link.bundle.list(&key);
        if catalogue.is_empty(){return Err(missing());}
        let items=catalogue.iter().skip(offset).take(limit)
            .map(|a|Item{id:a.id.clone(),size:a.size}).collect();
        let next_offset=(offset+limit<catalogue.len()).then_some(offset+limit);
        recheck(link,events,&key,before)?;
        Ok(Page{items,next_offset,blob_rechecked:false,provenance:PROVENANCE})
    }).await
}
async fn reference(State(server):State<Server>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Selected>,QueryRejection>,id:Result<AxumPath<String>,PathRejection>)
    ->Result<Json<Reference>,Api>{
    allowed(&server,&plugin)?;
    let Query(q)=input.map_err(|_|bad())?;
    let AxumPath(id)=id.map_err(|_|missing())?;
    if !hex_id(&id){return Err(missing());}
    let key=key(&server.link,q.conversation_id,q.message_id)?;
    run(server,move|link,events|{
        let before=exact_match(link,events,&key)?;
        let bytes=link.bundle.read(&key,&id).map_err(|_|unavailable())?.ok_or_else(missing)?;
        let size=bytes.len();
        let sha256=digest(bytes);
        recheck(link,events,&key,before)?;
        Ok(Reference{id,size,sha256,blob_rechecked:true,provenance:PROVENANCE})
    }).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExactBytes {
    conversation_id:String,message_id:String,expected_sha256:String,
}

// Retain the admission permit with the immutable body so a slow caller
// cannot accumulate multiple bounded blobs outside our concurrency limit.
struct BoundedBody {
    value:Vec<u8>,
    _permit:OwnedSemaphorePermit,
}
impl AsRef<[u8]> for BoundedBody {
    fn as_ref(&self)->&[u8]{self.value.as_slice()}
}

async fn verified_bytes(
    State(server):State<Server>,Extension(plugin):Extension<Plugin>,headers:HeaderMap,
    input:Result<Query<ExactBytes>,QueryRejection>,id:Result<AxumPath<String>,PathRejection>,
)->Result<Response,Api>{
    if !server.link.bytes_eligible(&plugin){return Err(Api(StatusCode::FORBIDDEN,"permission_required"));}
    if headers.contains_key(header::RANGE)||headers.contains_key(header::IF_RANGE){
        return Err(Api(StatusCode::RANGE_NOT_SATISFIABLE,"live_imported_media_full_blob_only"));
    }
    let Query(q)=input.map_err(|_|bad())?;
    let AxumPath(id)=id.map_err(|_|missing())?;
    if !hex_id(&id){return Err(missing());}
    if !hex_id(&q.expected_sha256){return Err(bad());}
    let key=key(&server.link,q.conversation_id,q.message_id)?;
    let permit=server.permits.clone().try_acquire_owned().map_err(|_|unavailable())?;
    let body=tokio::task::spawn_blocking(move||{
        let link=&server.link;
        let events=&server.events;
        link.verify(events)?;
        let live=exact_match(link,events,&key)?;
        let selected=link.bundle.list(&key).iter().find(|item|item.id==id).ok_or_else(missing)?;
        if selected.size>MAX_VERIFIED_BYTES as u64 {
            return Err(Api(StatusCode::PAYLOAD_TOO_LARGE,"live_imported_media_bytes_limit"));
        }
        // Read/hash the whole catalog-approved object, not a byte interval.
        // Buffered bytes remain immutable following the final scope/row fence.
        let (value,digest)=link.bundle.read_with_verified_digest(&key,&id)
            .map_err(|_|unavailable())?.ok_or_else(missing)?;
        if value.len()>MAX_VERIFIED_BYTES{return Err(unavailable());}
        if !bool::from(digest.as_bytes().ct_eq(q.expected_sha256.as_bytes())){
            return Err(Api(StatusCode::CONFLICT,"live_imported_media_digest_mismatch"));
        }
        recheck(link,events,&key,live)?;
        Ok(Bytes::from_owner(BoundedBody{value,_permit:permit}))
    }).await.map_err(|_|unavailable())??;
    Ok((StatusCode::OK,[
        (header::CONTENT_TYPE,"application/octet-stream"),
        (header::CONTENT_DISPOSITION,"attachment"),
        (header::CACHE_CONTROL,"no-store"),
        (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
        (HeaderName::from_static("cross-origin-resource-policy"),"same-origin"),
    ],body).into_response())
}
