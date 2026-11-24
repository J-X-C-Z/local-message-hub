//! Exact-profile read/write admission for opt-in local Campus ranking.
use crate::{campus_scoring::{self, Change, Material, Page, Run, Scope, Status}, events::EventStore,
    plugins::{Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Query,State,
    rejection::{JsonRejection,QueryRejection}},http::StatusCode,response::{IntoResponse,Response},routing::get};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const CONFIG:&str="/api/v1/campus/scoring";
pub(crate) const RUN:&str="/api/v1/campus/scoring/run";
pub(crate) const ITEMS:&str="/api/v1/campus/scored-items";

#[derive(Clone)]
struct Host { store:EventStore, permits:Arc<Semaphore> }
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(CONFIG,get(status).post(configure).layer(DefaultBodyLimit::max(4*1024)))
        .route(RUN,get(run_status).post(materialize).layer(DefaultBodyLimit::max(1024)))
        .route(ITEMS,get(items)).with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}
pub(crate) fn route(path:&str)->bool { matches!(path,CONFIG|ITEMS|RUN) }

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response { (self.0,Json(json!({"error":{"code":self.1}}))).into_response() }
}
fn bad()->Api { Api(StatusCode::BAD_REQUEST,"bad_campus_scoring_input") }
fn forbidden()->Api { Api(StatusCode::FORBIDDEN,"permission_required") }
fn missing()->Api { Api(StatusCode::NOT_FOUND,"not_found") }
fn storage()->Api { Api(StatusCode::SERVICE_UNAVAILABLE,"campus_scoring_unavailable") }
fn profile(plugin:&Plugin,requested:Option<&str>,write:bool)->Result<String,Api> {
    let grant=plugin.campus_profile.as_ref().filter(|_|plugin.kind.is_campus()
        && plugin.permissions.contains(&Permission::CampusRead)
        && (!write || (plugin.kind==PluginKind::CampusEditor
            && plugin.permissions.contains(&Permission::CampusScoringWrite)))).ok_or_else(forbidden)?;
    if requested.is_some_and(|scope|scope!=grant.as_str()) { return Err(missing()); }
    Ok(grant.clone())
}
async fn run<T:Serialize+Send+'static>(host:Host,
    f:impl FnOnce(&EventStore)->Result<T,campus_scoring::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned().map_err(|_|storage())?;
    tokio::task::spawn_blocking(move||{ let _permit=permit; f(&host.store) })
        .await.map_err(|_|storage())?.map(Json).map_err(|err|match err {
            campus_scoring::Error::Input=>bad(),campus_scoring::Error::Missing=>missing(),
            campus_scoring::Error::Conflict=>Api(StatusCode::CONFLICT,"campus_scoring_revision_conflict"),
            campus_scoring::Error::Storage=>storage(),
        })
}
async fn status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Scope>,QueryRejection>)->Result<Json<Status>,Api> {
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_scoring::status(store,&profile,q)).await
}
async fn configure(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<Change>,JsonRejection>)->Result<Json<Status>,Api> {
    profile(&plugin,None,true)?;
    let Json(q)=input.map_err(|err|match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_scoring_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>bad(),
    })?;
    let profile=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_scoring::configure(store,&profile,q)).await
}
async fn items(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_scoring::Query>,QueryRejection>)->Result<Json<Page>,Api> {
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_scoring::items(store,&profile,q)).await
}

async fn run_status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Scope>,QueryRejection>)->Result<Json<Material>,Api> {
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_scoring::material_status(store,&profile,q)).await
}
async fn materialize(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<Run>,JsonRejection>)->Result<Json<Material>,Api> {
    profile(&plugin,None,true)?;
    let Json(q)=input.map_err(|err|match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_scoring_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>bad(),
    })?;
    let profile=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_scoring::materialize(store,&profile,q)).await
}
