//! Mounted only on ordinary live archives behind the native loopback bearer gate.
use crate::{events::EventStore,export_presets::{self as presets,Change,Details,Error,Plan,Save,Summary,Target},
    plugins::{AccountScope,Permission,Plugin,PluginKind}};
use axum::{
    Json,Router,extract::{DefaultBodyLimit,Extension,Query,RawQuery,State,
        rejection::{JsonRejection,QueryRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::{get,post},
};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const LIST:&str="/api/v1/exports/presets";
pub(crate) const DETAILS:&str="/api/v1/exports/presets/details";
pub(crate) const SAVE:&str="/api/v1/exports/presets/save";
pub(crate) const REMOVE:&str="/api/v1/exports/presets/remove";
pub(crate) const PLAN:&str="/api/v1/exports/presets/plan";
pub(crate) fn write_route(path:&str)->bool {matches!(path,SAVE|REMOVE|PLAN)}

#[derive(Clone)]
struct Host {store:EventStore,permits:Arc<Semaphore>}
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(LIST,get(list)).route(DETAILS,get(details)).route(SAVE,post(save))
        .route(REMOVE,post(remove)).route(PLAN,post(plan)).layer(DefaultBodyLimit::max(12*1024))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(2))})
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<Error> for Api {
    fn from(error:Error)->Self {
        match error {
            Error::Invalid=>Self(StatusCode::BAD_REQUEST,"bad_export_preset"),
            Error::Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            Error::Stale=>Self(StatusCode::CONFLICT,"export_preset_revision_conflict"),
            Error::Quota=>Self(StatusCode::TOO_MANY_REQUESTS,"export_preset_quota"),
            Error::Limit=>Self(StatusCode::PAYLOAD_TOO_LARGE,"export_preset_too_large"),
            Error::Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"export_preset_unavailable"),
        }
    }
}
fn scope(plugin:&Plugin)->Result<AccountScope,Api> {
    plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop
        && [Permission::MessageExport,Permission::Messages,Permission::ExportPresets].into_iter()
            .all(|permission|plugin.permissions.contains(&permission)))
        .cloned().ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))
}
fn json<T>(body:Result<Json<T>,JsonRejection>)->Result<T,Api> {
    body.map(|Json(v)|v).map_err(|e|match e.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"export_preset_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_export_preset"),
    })
}
async fn run<T:Send+'static>(host:Host,work:impl FnOnce(&EventStore)->Result<T,Error>+Send+'static)
    ->Result<T,Api> {
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"export_preset_busy"))?;
    tokio::task::spawn_blocking(move ||{let _permit=permit;work(&host.store)})
        .await.map_err(|_|Error::Storage)?.map_err(Api::from)
}
async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,RawQuery(raw):RawQuery)
    ->Result<Response,Api> {
    let scope=scope(&plugin)?;
    if raw.is_some(){return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));}
    let items=run(host,move |store|presets::list(store,&plugin.id,&scope)).await?;
    Ok(Json(json!({"items":items})).into_response())
}
async fn details(State(host):State<Host>,Extension(plugin):Extension<Plugin>,query:Result<Query<Target>,QueryRejection>)
    ->Result<Json<Details>,Api> {
    let scope=scope(&plugin)?;
    let Query(query)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))?;
    run(host,move |store|presets::details(store,&plugin.id,&scope,&query.preset_id)).await.map(Json)
}
async fn save(State(host):State<Host>,Extension(plugin):Extension<Plugin>,body:Result<Json<Save>,JsonRejection>)
    ->Result<Json<Summary>,Api> {
    let scope=scope(&plugin)?;
    let request=json(body)?;
    run(host,move |store|presets::save(store,&plugin.id,&scope,request)).await.map(Json)
}
async fn remove(State(host):State<Host>,Extension(plugin):Extension<Plugin>,body:Result<Json<Change>,JsonRejection>)
    ->Result<StatusCode,Api> {
    let scope=scope(&plugin)?;
    let request=json(body)?;
    run(host,move |store|presets::remove(store,&plugin.id,&scope,request)).await?;
    Ok(StatusCode::NO_CONTENT)
}
async fn plan(State(host):State<Host>,Extension(plugin):Extension<Plugin>,body:Result<Json<Change>,JsonRejection>)
    ->Result<Json<Plan>,Api> {
    let scope=scope(&plugin)?;
    let request=json(body)?;
    run(host,move |store|presets::plan(store,&plugin.id,&scope,request)).await.map(Json)
}
