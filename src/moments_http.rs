//! Exact-account read/export of already detached Moments text.
use crate::{events::EventStore,moments::{self,Page},plugins::{Permission,Plugin,PluginKind}};
use axum::{Json,Router,body::Bytes,extract::{Extension,Query,State,rejection::QueryRejection},http::{StatusCode,header},
    response::{IntoResponse,Response},routing::get};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::{OwnedSemaphorePermit,Semaphore};

pub(crate) const LIST:&str="/api/v1/moments";
const LEGACY_LIST:&str="/api/moments";
const LEGACY_EXPORT:&str="/api/moments/export";
pub(crate) const EXPORT:&str="/api/v1/moments/export";
pub(crate) const EXPORT_ARCHIVE:&str="/api/v1/moments/export/archive";
#[derive(Clone)]
struct Host {store:EventStore,permit:Arc<Semaphore>}
pub(crate) fn router(store:EventStore)->Router{
    Router::new().route(LIST,get(list)).route(LEGACY_LIST,get(legacy_list))
        .route(LEGACY_EXPORT,get(legacy_export)).route(EXPORT,get(export))
        .route(EXPORT_ARCHIVE,get(export_archive))
        .with_state(Host{store,permit:Arc::new(Semaphore::new(4))})
}
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response{(self.0,Json(json!({"error":{"code":self.1}}))).into_response()}
}
fn unavailable()->Api{Api(StatusCode::SERVICE_UNAVAILABLE,"detached_moments_unavailable")}
async fn query(host:Host,plugin:Plugin,input:Result<Query<moments::Query>,QueryRejection>,
    exporting:bool)->Result<Json<Page>,Api>{
    let scope=plugin.account_scope.filter(|s|s.platform=="wechat" && plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::MomentsRead)
        && (!exporting || plugin.permissions.contains(&Permission::MomentsExport)))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    let Query(q)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_moments_query"))?;
    if q.account_id.as_deref().is_some_and(|account|account!=scope.account_id) {
        return Err(Api(StatusCode::NOT_FOUND,"not_found"));
    }
    let permit=host.permit.try_acquire_owned().map_err(|_|unavailable())?;
    tokio::task::spawn_blocking(move||{let _permit=permit;moments::read(&host.store,&scope.account_id,q,exporting)})
        .await.map_err(|_|unavailable())?.map(Json).map_err(|e|match e {
            moments::Error::Input=>Api(StatusCode::BAD_REQUEST,"bad_moments_query"),
            _=>unavailable(),
        })
}
async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<moments::Query>,QueryRejection>)->Result<Json<Page>,Api>{
    query(host,plugin,input,false).await
}
async fn export(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<moments::Query>,QueryRejection>)->Result<Json<Page>,Api>{
    query(host,plugin,input,true).await
}

// The legacy read never performs the old on-demand provider/catalog lookup.
async fn legacy_list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<moments::legacy::Query>,QueryRejection>)->Result<Response,Api>{
    let (account,q)=legacy_request(plugin,input,false)?;
    let permit=host.permit.try_acquire_owned().map_err(|_|unavailable())?;
    let (body,account)=tokio::task::spawn_blocking(move||{
        moments::legacy::read(&host.store,&account,q)
            .map(|bytes|(bytes.map(|data|Bytes::from_owner(BodyWithPermit(data,permit))),account))
    }).await.map_err(|_|unavailable())?.map_err(|error|match error{
        moments::Error::Input=>Api(StatusCode::BAD_REQUEST,"bad_moments_query"),
        moments::Error::TooLarge=>Api(StatusCode::PAYLOAD_TOO_LARGE,"moments_page_too_large"),
        _=>unavailable(),
    })?;
    match body {
        Some(body)=>Ok(([
            (header::CONTENT_TYPE,"application/json; charset=utf-8"),
            (header::CACHE_CONTROL,"no-store"),
            (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
        ],body).into_response()),
        None=>Ok(legacy_unindexed(account,"detached-text-legacy-list")),
    }
}

fn legacy_request(plugin:Plugin,input:Result<Query<moments::legacy::Query>,QueryRejection>,exporting:bool)
    ->Result<(String,moments::legacy::Query),Api>{
    let scope=plugin.account_scope.filter(|s|s.platform=="wechat" && plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::MomentsRead)
        && (!exporting || plugin.permissions.contains(&Permission::MomentsExport)))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    let Query(q)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_moments_query"))?;
    if q.account_id.as_deref().is_some_and(|account|!account.is_empty()&&account!=scope.account_id) {
        return Err(Api(StatusCode::NOT_FOUND,"not_found"));
    }
    Ok((scope.account_id,q))
}
fn legacy_unindexed(account:String,mode:&'static str)->Response{
    (StatusCode::SERVICE_UNAVAILABLE,[
        (header::CACHE_CONTROL,"no-store"),
        (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
    ],Json(json!({"mode":mode,"available":false,"catalogued":false,
        "reason":"moments_not_indexed","error":"本机未导入此账号的独立朋友圈文本目录",
        "account_id":account,"accounts":[],"total":0,"items":[]
    }))).into_response()
}

async fn legacy_export(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<moments::legacy::Query>,QueryRejection>)->Result<Response,Api>{
    let (account,q)=legacy_request(plugin,input,true)?;
    let permit=host.permit.try_acquire_owned().map_err(|_|unavailable())?;
    let (artifact,account)=tokio::task::spawn_blocking(move||{
        moments::legacy::export(&host.store,&account,q)
            .map(|output|(output.map(|(format,bytes)|(format,Bytes::from_owner(BodyWithPermit(bytes,permit)))),account))
    }).await.map_err(|_|unavailable())?.map_err(|error|match error{
        moments::Error::Input=>Api(StatusCode::BAD_REQUEST,"bad_moments_export_query_or_format"),
        moments::Error::TooLarge=>Api(StatusCode::PAYLOAD_TOO_LARGE,"moments_export_too_large"),
        _=>unavailable(),
    })?;
    let Some((format,body))=artifact else{
        return Ok(legacy_unindexed(account,"detached-text-legacy-export"));
    };
    Ok(([
        (header::CONTENT_TYPE,format.content_type()),
        (header::CONTENT_DISPOSITION,format.disposition()),
        (header::CACHE_CONTROL,"no-store"),
        (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
    ],body).into_response())
}

struct BodyWithPermit(Vec<u8>,#[allow(dead_code)] OwnedSemaphorePermit);
impl AsRef<[u8]> for BodyWithPermit {fn as_ref(&self)->&[u8]{self.0.as_slice()}}

async fn export_archive(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<moments::ArchiveQuery>,QueryRejection>)->Result<Response,Api>{
    let scope=plugin.account_scope.filter(|s|s.platform=="wechat" && plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::MomentsRead)
        && plugin.permissions.contains(&Permission::MomentsExport))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    let Query(q)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_moments_query"))?;
    if q.account_id.as_deref().is_some_and(|account|account!=scope.account_id){
        return Err(Api(StatusCode::NOT_FOUND,"not_found"));
    }
    let format=q.format;
    let permit=host.permit.try_acquire_owned().map_err(|_|unavailable())?;
    let body=tokio::task::spawn_blocking(move||{
        moments::export_archive(&host.store,&scope.account_id,q)
            .map(|bytes|Bytes::from_owner(BodyWithPermit(bytes,permit)))
    }).await.map_err(|_|unavailable())?.map_err(|error|match error{
        moments::Error::Input=>Api(StatusCode::BAD_REQUEST,"bad_moments_query"),
        moments::Error::TooLarge=>Api(StatusCode::PAYLOAD_TOO_LARGE,"moments_export_too_large"),
        _=>unavailable(),
    })?;
    Ok(([
        (header::CONTENT_TYPE,format.content_type()),
        (header::CONTENT_DISPOSITION,format.disposition()),
        (header::CACHE_CONTROL,"no-store"),
        (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
    ],body).into_response())
}
