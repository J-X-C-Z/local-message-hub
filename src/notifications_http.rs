//! Opt-in desktop routes. The parent host owns authentication and live-only mounting.
use crate::{events::EventStore,notifications::{self,Change,PreviewQuery},notification_pull,
    plugins::{AccountScope,Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Query,State,
    rejection::{JsonRejection,QueryRejection}},http::{StatusCode,Uri},
    response::{IntoResponse,Response},routing::{get,post}};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PREFERENCES:&str="/api/v1/notifications/preferences";
pub(crate) const PREVIEW:&str="/api/v1/notifications/preview";
pub(crate) const PULL:&str="/api/v1/notifications/pull";
pub(crate) const ACK:&str="/api/v1/notifications/ack";
pub(crate) const ACTIVATION_TARGET:&str="/api/v1/notifications/activation-target";
const MAX_BODY:usize=320*1024;

#[derive(Clone)]
struct Host {store:EventStore,permits:Arc<Semaphore>,receipts:Option<Arc<notification_pull::Authority>>}
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(PREFERENCES,get(read).post(write))
        .route(PREVIEW,get(preview)).layer(DefaultBodyLimit::max(MAX_BODY))
        .route(PULL,post(pull).layer(DefaultBodyLimit::max(4*1024)))
        .route(ACK,post(ack).layer(DefaultBodyLimit::max(4*1024)))
        .route(ACTIVATION_TARGET,post(activation_target).layer(DefaultBodyLimit::max(4*1024)))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4)),
            receipts:notification_pull::Authority::new().map(Arc::new)})
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl Api {
    fn scope(plugin:&Plugin,permission:Permission)->Result<AccountScope,Self> {
        plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop
            && plugin.permissions.contains(&Permission::NotificationRead)
            && plugin.permissions.contains(&permission)).cloned()
            .ok_or(Self(StatusCode::FORBIDDEN,"permission_required"))
    }
    fn storage(error:notifications::Error)->Self {
        match error {
            notifications::Error::Input=>Self(StatusCode::BAD_REQUEST,"bad_notification_request"),
            notifications::Error::Conflict=>Self(StatusCode::CONFLICT,"notification_revision_conflict"),
            notifications::Error::Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"notification_store_unavailable"),
        }
    }
}
impl IntoResponse for Api {
    fn into_response(self)->Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
async fn run<T:Serialize+Send+'static>(host:Host,
    op:impl FnOnce(&EventStore)->Result<T,notifications::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"notification_store_busy"))?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        let result=op(&host.store)?;
        host.store.verify().map_err(notifications::Error::from)?;
        Ok(result)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"notification_store_unavailable"))?
    .map(Json).map_err(Api::storage)
}
async fn read(State(host):State<Host>,Extension(plugin):Extension<Plugin>)->Result<Json<notifications::State>,Api> {
    let scope=Api::scope(&plugin,Permission::NotificationRead)?;
    run(host,move|store|notifications::get(store,&scope,&plugin.id)).await
}
async fn write(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Change>,JsonRejection>)->Result<Json<notifications::State>,Api> {
    let scope=Api::scope(&plugin,Permission::NotificationWrite)?;
    let Json(change)=body.map_err(|e| match e.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"notification_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_notification_request"),
    })?;
    run(host,move|store|notifications::put(store,&scope,&plugin.id,change)).await
}
async fn preview(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<PreviewQuery>,QueryRejection>)->Result<Json<notifications::Preview>,Api> {
    let scope=Api::scope(&plugin,Permission::NotificationRead)?;
    let Query(query)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_notification_query"))?;
    run(host,move|store|notifications::preview(store,&scope,&plugin.id,query)).await
}

fn pull_json<T>(input:Result<Json<T>,JsonRejection>)->Result<T,Api>{
    input.map(|Json(value)|value).map_err(|e|match e.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"notification_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_notification_request"),
    })
}

async fn pull(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<notification_pull::Poll>,JsonRejection>)->Result<Json<notification_pull::Batch>,Api>{
    let scope=Api::scope(&plugin,Permission::NotificationWrite)?;
    let request=pull_json(input)?;
    let receipts=host.receipts.clone().ok_or_else(||Api::storage(notifications::Error::Storage))?;
    run(host,move|store|notification_pull::pull(store,&scope,&plugin.id,&receipts,request)).await
}

async fn ack(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<notification_pull::Acknowledge>,JsonRejection>)->Result<Json<notification_pull::Ack>,Api>{
    let scope=Api::scope(&plugin,Permission::NotificationWrite)?;
    let request=pull_json(input)?;
    let receipts=host.receipts.clone().ok_or_else(||Api::storage(notifications::Error::Storage))?;
    run(host,move|store|notification_pull::ack(store,&scope,&plugin.id,&receipts,request)).await
}


async fn activation_target(
    State(host):State<Host>,Extension(plugin):Extension<Plugin>,uri:Uri,
    input:Result<Json<notification_pull::Activation>,JsonRejection>,
)->Result<Json<notification_pull::Target>,Api>{
    let scope=Api::scope(&plugin,Permission::NotificationWrite)?;
    if !plugin.permissions.contains(&Permission::Conversations) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    if uri.query().is_some(){return Err(Api(StatusCode::BAD_REQUEST,"bad_notification_request"));}
    let request=pull_json(input)?;
    let receipts=host.receipts.clone().ok_or_else(||Api::storage(notifications::Error::Storage))?;
    let Json(target)=run(host,move|store|
        notification_pull::activation_target(store,&scope,&plugin.id,&receipts,request)).await?;
    target.map(Json).ok_or(Api(StatusCode::NOT_FOUND,"notification_target_unavailable"))
}
