use crate::{drafts::{self,Change,Target},events::EventStore,plugins::{AccountScope,Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Query,State,
        rejection::{JsonRejection,QueryRejection}},http::StatusCode,response::{IntoResponse,Response},routing::get};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH:&str="/api/v1/drafts";
const LIMIT:usize=104*1024; // Allow worst-case JSON escaping of 16 KiB and bounded target/key.
#[derive(Clone)]
struct Host { store:EventStore, permits:Arc<Semaphore> }
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(PATH,get(read).post(write)).layer(DefaultBodyLimit::max(LIMIT))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl Api {
    fn store(err:drafts::Error)->Self {
        match err {
            drafts::Error::Input=>Self(StatusCode::BAD_REQUEST,"bad_draft"),
            drafts::Error::Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            drafts::Error::Stale=>Self(StatusCode::CONFLICT,"draft_revision_conflict"),
            drafts::Error::Duplicate=>Self(StatusCode::CONFLICT,"draft_request_conflict"),
            drafts::Error::Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"draft_store_unavailable"),
        }
    }
    fn scope(plugin:&Plugin,p:Permission)->Result<AccountScope,Self> {
        plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop
            && plugin.permissions.contains(&Permission::Conversations)
            && plugin.permissions.contains(&Permission::DraftRead) && plugin.permissions.contains(&p))
            .cloned().ok_or(Self(StatusCode::FORBIDDEN,"permission_required"))
    }
}
impl IntoResponse for Api {
    fn into_response(self)->Response { (self.0,Json(json!({"error":{"code":self.1}}))).into_response() }
}

async fn run<T:Serialize+Send+'static>(host:Host,
    work:impl FnOnce(EventStore)->Result<T,drafts::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned().map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"draft_store_busy"))?;
    tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        work(host.store)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"draft_store_unavailable"))?
        .map(Json).map_err(Api::store)
}

async fn read(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<Target>,QueryRejection>)->Result<Json<drafts::State>,Api> {
    let scope=Api::scope(&plugin,Permission::DraftRead)?;
    let Query(target)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))?;
    let key=target.within(scope).map_err(Api::store)?;
    run(host,move|store|drafts::get(&store,&key)).await
}
async fn write(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Change>,JsonRejection>)->Result<Json<drafts::Receipt>,Api> {
    let scope=Api::scope(&plugin,Permission::DraftWrite)?;
    let Json(change)=body.map_err(|e|match e.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"draft_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_draft"),
    })?;
    let (key,change)=change.validate(scope).map_err(Api::store)?;
    run(host,move|store|drafts::put(&store,&key,&plugin.id,change)).await
}
