//! Scoped projection of an already imported and verified media catalog.
use crate::{
    media_bundle::{Bundle,MessageKey},
    plugins::{AccountScope,Permission,Plugin,PluginKind},
    snapshot::Snapshot,
};
use axum::{
    Json,Router,
    extract::{Extension,Query,State,rejection::QueryRejection},
    http::StatusCode,
    response::{IntoResponse,Response},
    routing::get,
};
use base64::{Engine,engine::general_purpose::URL_SAFE_NO_PAD};
use serde::{Deserialize,Serialize};
use serde_json::json;
use sha2::{Digest,Sha256};
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH:&str="/api/v1/messages/media/catalog";
const MAX_BYTES:u64=8*1024*1024; // existing media reader ceiling
const MAX_CURSOR:usize=4096;

#[derive(Clone)]
struct Host{bundle:Bundle,snapshot:Snapshot,permits:Arc<Semaphore>}
pub(crate) fn router(bundle:Bundle,snapshot:Snapshot)->Router {
    Router::new().route(PATH,get(list))
        .with_state(Host{bundle,snapshot,permits:Arc::new(Semaphore::new(4))})
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PageQuery {
    conversation_id:String,
    limit:Option<usize>,
    min_bytes:Option<u64>,
    max_bytes:Option<u64>,
    after:Option<String>,
}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor{fingerprint:String,message_id:String,attachment_id:String}
#[derive(Serialize)]
struct Item{conversation_id:String,message_id:String,attachment_id:String,size:u64}
#[derive(Serialize)]
struct Page {
    items:Vec<Item>,
    next_cursor:Option<String>,
    provenance:&'static str,
    blob_rechecked:bool,
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
fn bad()->Api {Api(StatusCode::BAD_REQUEST,"bad_media_catalog_query")}
fn unavailable()->Api {Api(StatusCode::SERVICE_UNAVAILABLE,"media_bundle_unavailable")}

async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    raw:Result<Query<PageQuery>,QueryRejection>)->Result<Json<Page>,Api> {
    let scope=plugin.account_scope.as_ref().filter(|scope|plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Messages) && plugin.permissions.contains(&Permission::Media)
        && matches!(scope.platform.as_str(),"wechat"|"qq"))
        .cloned().ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    let Query(query)=raw.map_err(|_|bad())?;
    let permit=host.permits.clone().try_acquire_owned().map_err(|_|unavailable())?;
    tokio::task::spawn_blocking(move || {
        let _permit=permit;
        host.bundle.verify().map_err(|_|unavailable())?;
        host.snapshot.verify().map_err(|_|unavailable())?;
        let page=page(&host.bundle,&plugin.id,&scope,query)?;
        host.bundle.verify().map_err(|_|unavailable())?;
        host.snapshot.verify().map_err(|_|unavailable())?;
        Ok::<_,Api>(Json(page))
    }).await.map_err(|_|unavailable())?
}

fn nonempty_id(id:&str)->bool {
    (1..=512).contains(&id.len()) && !id.chars().any(char::is_control)
}
fn attachment_id(id:&str)->bool {
    id.len()==64 && id.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
fn page(bundle:&Bundle,plugin:&str,scope:&AccountScope,query:PageQuery)->Result<Page,Api> {
    let PageQuery{conversation_id,limit,min_bytes,max_bytes,after}=query;
    let limit=limit.unwrap_or(30);
    let min=min_bytes.unwrap_or(0);
    let max=max_bytes.unwrap_or(MAX_BYTES);
    if !nonempty_id(&conversation_id) || !(1..=100).contains(&limit)
        || min>max || max>MAX_BYTES {return Err(bad());}
    let descriptor=serde_json::to_vec(&("lmh-media-catalog-cursor-v1",bundle.catalog_stamp(),
        plugin,&scope.platform,&scope.account_id,&conversation_id,min,max)).map_err(|_|unavailable())?;
    let fingerprint=hex::encode(Sha256::digest(descriptor));
    let anchor=if let Some(encoded)=after {
        if encoded.len()>MAX_CURSOR{return Err(bad());}
        let bytes=URL_SAFE_NO_PAD.decode(encoded).map_err(|_|bad())?;
        let cursor:Cursor=serde_json::from_slice(&bytes).map_err(|_|bad())?;
        if cursor.fingerprint!=fingerprint || !nonempty_id(&cursor.message_id)
            || !attachment_id(&cursor.attachment_id) {return Err(bad());}
        let key=MessageKey{platform:scope.platform.clone(),account_id:scope.account_id.clone(),
            conversation_id:conversation_id.clone(),message_id:cursor.message_id.clone()};
        if !bundle.list(&key).iter().any(|a|a.id==cursor.attachment_id && (min..=max).contains(&a.size)) {
            return Err(bad());
        }
        Some(cursor)
    } else { None };
    let first=anchor.as_ref().map_or("",|a|a.message_id.as_str());
    let mut items=Vec::with_capacity(limit+1);
    'catalog: for (key,attachments) in bundle.conversation(&scope.platform,&scope.account_id,&conversation_id,first) {
        let mut ordered:Vec<_>=attachments.iter().filter(|a|(min..=max).contains(&a.size)).collect();
        ordered.sort_unstable_by(|l,r|l.id.cmp(&r.id));
        for a in ordered {
            if anchor.as_ref().is_some_and(|anchor|key.message_id==anchor.message_id && a.id<=anchor.attachment_id) {
                continue;
            }
            items.push(Item{conversation_id:conversation_id.clone(),message_id:key.message_id.clone(),
                attachment_id:a.id.clone(),size:a.size});
            if items.len()>limit {break 'catalog;}
        }
    }
    let next_cursor=if items.len()>limit {
        items.pop();
        let last=items.last().ok_or_else(unavailable)?;
        let cursor=Cursor{fingerprint,message_id:last.message_id.clone(),attachment_id:last.attachment_id.clone()};
        let bytes=serde_json::to_vec(&cursor).map_err(|_|unavailable())?;
        let value=URL_SAFE_NO_PAD.encode(bytes);
        if value.len()>MAX_CURSOR {return Err(unavailable());}
        Some(value)
    } else {None};
    Ok(Page {items,next_cursor,provenance:"imported_catalog_exact_binding",blob_rechecked:false})
}
