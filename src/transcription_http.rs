//! Native-live only, including the separately opted-in legacy-shaped lookups.
use crate::{events::EventStore,plugins::{AccountScope,Permission,Plugin,PluginKind},
    transcription::{self,Change,Record,Receipt,Target},transcription_legacy};
use axum::{
    Json,Router,extract::{DefaultBodyLimit,Extension,Query,RawQuery,State,rejection::{JsonRejection,QueryRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::{get,post},
};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH:&str="/api/v1/voice/transcription";
pub(crate) const LEGACY_STATUS:&str="/api/voice/transcription-status";
pub(crate) const LEGACY_TRANSCRIBE:&str="/api/voice/transcribe";
const LEGACY_BODY_LIMIT:usize=32*1024;
const BODY_LIMIT:usize=128*1024;

#[derive(Clone)]
struct Host {store:EventStore, permits:Arc<Semaphore>}

pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(PATH,get(read).post(write).layer(DefaultBodyLimit::max(BODY_LIMIT)))
        .route(LEGACY_STATUS,get(legacy_status))
        .route(LEGACY_TRANSCRIBE,post(legacy_lookup).layer(DefaultBodyLimit::max(LEGACY_BODY_LIMIT)))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}

impl From<transcription::Error> for Api {
    fn from(e:transcription::Error)->Self {
        use transcription::Error::*;
        match e {
            Input=>Self(StatusCode::BAD_REQUEST,"bad_transcription_input"),
            Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            Conflict=>Self(StatusCode::CONFLICT,"transcription_revision_conflict"),
            Quota=>Self(StatusCode::TOO_MANY_REQUESTS,"transcription_quota"),
            Locked=>Self(StatusCode::FORBIDDEN,"permission_required"),
            Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"transcription_store_unavailable"),
        }
    }
}

fn scope(plugin:&Plugin,required:Permission)->Result<AccountScope,Api> {
    plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Messages)
        && plugin.permissions.contains(&Permission::TranscriptionRead)
        && plugin.permissions.contains(&required)).cloned()
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))
}

async fn run<T:Serialize+Send+'static>(host:Host,work:impl FnOnce(&EventStore)
    ->Result<T,transcription::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"transcription_busy"))?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        work(&host.store)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"transcription_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

async fn read(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<Target>,QueryRejection>)->Result<Json<Record>,Api> {
    let scope=scope(&plugin,Permission::TranscriptionRead)?;
    let Query(target)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_transcription_query"))?;
    run(host,move|store|transcription::get(store,&scope,target)).await
}

async fn write(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Change>,JsonRejection>)->Result<Json<Receipt>,Api> {
    let scope=scope(&plugin,Permission::TranscriptionWrite)?;
    let Json(change)=body.map_err(|e|match e.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"transcription_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_transcription_input"),
    })?;
    run(host,move|store|transcription::put(store,&scope,change)).await
}

fn legacy_scope(plugin:&Plugin)->Result<AccountScope,Api> {
    let scope=scope(plugin,Permission::TranscriptionRead)?;
    if !plugin.permissions.contains(&Permission::LegacyHttp){
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    Ok(scope)
}

async fn legacy_status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery)
    ->Result<Json<transcription_legacy::Status>,Api>{
    legacy_scope(&plugin)?;
    if raw.is_some(){return Err(Api(StatusCode::BAD_REQUEST,"bad_transcription_query"));}
    run(host,transcription_legacy::status).await
}

async fn legacy_lookup(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<transcription_legacy::Request>,JsonRejection>)->Result<Json<transcription_legacy::Lookup>,Api>{
    let scope=legacy_scope(&plugin)?;
    let Json(query)=body.map_err(|e|match e.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"transcription_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_transcription_input"),
    })?;
    run(host,move|store|transcription_legacy::lookup(store,&scope,query)).await
}
