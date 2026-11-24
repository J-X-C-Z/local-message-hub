//! Exact-profile admission for opted-in Campus display state.
use crate::{campus_display::{self, Change, Display, Feed, FeedQuery, Scope},
    events::EventStore, plugins::{Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Query,State,
    rejection::{JsonRejection,QueryRejection}},http::StatusCode,response::{IntoResponse,Response},routing::get};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const DISPLAY:&str="/api/v1/campus/profile/display";
pub(crate) const FEED:&str="/api/v1/campus/profile/feed";

#[derive(Clone)]
struct Host { store:EventStore, permits:Arc<Semaphore> }
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(DISPLAY,get(display).post(configure).layer(DefaultBodyLimit::max(2048)))
        .route(FEED,get(feed)).with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}
pub(crate) fn route(path:&str)->bool {matches!(path,DISPLAY|FEED)}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response {(self.0,Json(json!({"error":{"code":self.1}}))).into_response()}
}
fn bad()->Api {Api(StatusCode::BAD_REQUEST,"bad_campus_display_input")}
fn unavailable()->Api {Api(StatusCode::SERVICE_UNAVAILABLE,"campus_display_unavailable")}
fn profile(plugin:&Plugin,requested:Option<&str>,write:bool)->Result<String,Api> {
    let scope=plugin.campus_profile.as_ref().filter(|_|plugin.kind.is_campus()
        && plugin.permissions.contains(&Permission::CampusRead)
        && (!write || (plugin.kind==PluginKind::CampusEditor
            && plugin.permissions.contains(&Permission::CampusDisplayWrite))))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    if requested.is_some_and(|v|v!=scope.as_str()) {return Err(Api(StatusCode::NOT_FOUND,"not_found"));}
    Ok(scope.clone())
}
async fn run<T:Serialize+Send+'static>(host:Host,
    work:impl FnOnce(&EventStore)->Result<T,campus_display::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned().map_err(|_|unavailable())?;
    tokio::task::spawn_blocking(move||{let _permit=permit;work(&host.store)})
        .await.map_err(|_|unavailable())?.map(Json).map_err(|e|match e {
            campus_display::Error::Input=>bad(),
            campus_display::Error::Missing=>Api(StatusCode::NOT_FOUND,"not_found"),
            campus_display::Error::Conflict=>Api(StatusCode::CONFLICT,"campus_display_revision_conflict"),
            campus_display::Error::Storage=>unavailable(),
        })
}
async fn display(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Scope>,QueryRejection>)->Result<Json<Display>,Api>{
    let Query(q)=input.map_err(|_|bad())?;
    let scope=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_display::get(store,&scope,q)).await
}
async fn configure(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<Change>,JsonRejection>)->Result<Json<Display>,Api> {
    profile(&plugin,None,true)?;
    let Json(q)=input.map_err(|e|match e.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_display_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>bad(),
    })?;
    let scope=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_display::put(store,&scope,q)).await
}
async fn feed(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<FeedQuery>,QueryRejection>)->Result<Json<Feed>,Api> {
    let Query(q)=input.map_err(|_|bad())?;
    let scope=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_display::feed(store,&scope,q)).await
}
