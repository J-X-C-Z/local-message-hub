//! Desktop submission/status only. There is no operator endpoint.
use crate::{draft_review::{self,Prepare,Status,Target}, events::EventStore,
    plugins::{AccountScope,Permission,Plugin,PluginKind}};
use axum::{
    Json,Router,
    extract::{DefaultBodyLimit,Extension,Query,State,rejection::{JsonRejection,QueryRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::get,
};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH:&str="/api/v1/draft-review";
const MAX_BODY:usize=16*1024;

#[derive(Clone)]
struct Host { archive:EventStore, permits:Arc<Semaphore> }

pub(crate) fn router(archive:EventStore)->Router {
    Router::new().route(PATH,get(status).post(prepare)).layer(DefaultBodyLimit::max(MAX_BODY))
        .with_state(Host{archive,permits:Arc::new(Semaphore::new(4))})
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);

impl IntoResponse for Api {
    fn into_response(self)->Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}

impl From<draft_review::Error> for Api {
    fn from(e:draft_review::Error)->Self {
        use draft_review::Error::*;
        match e {
            Input=>Self(StatusCode::BAD_REQUEST,"bad_review_input"),
            Permission=>Self(StatusCode::FORBIDDEN,"permission_required"),
            Disabled=>Self(StatusCode::CONFLICT,"review_not_provisioned"),
            Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            Stale=>Self(StatusCode::CONFLICT,"review_draft_changed"),
            Quota=>Self(StatusCode::TOO_MANY_REQUESTS,"review_queue_full"),
            Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"review_store_unavailable"),
        }
    }
}

fn scope(plugin:&Plugin)->Result<AccountScope,Api> {
    plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Conversations)
        && plugin.permissions.contains(&Permission::DraftRead)
        && plugin.permissions.contains(&Permission::DraftReview))
        .cloned().ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))
}

async fn work(host:Host,
    work:impl FnOnce(&EventStore)->Result<Status,draft_review::Error>+Send+'static)
    ->Result<Json<Status>,Api> {
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"review_store_busy"))?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        work(&host.archive)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"review_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

async fn status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<Target>,QueryRejection>)->Result<Json<Status>,Api> {
    let scope=scope(&plugin)?;
    let Query(target)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_review_input"))?;
    work(host,move|archive|draft_review::status(archive,&plugin.id,&scope,&target.review_id)).await
}

async fn prepare(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Prepare>,JsonRejection>)->Result<Json<Status>,Api> {
    let scope=scope(&plugin)?;
    let Json(request)=body.map_err(|err|match err.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"review_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_review_input"),
    })?;
    work(host,move|archive|draft_review::prepare(archive,&plugin.id,&scope,request)).await
}
