//! Source job metadata API. The host must mount it only on the ordinary live archive.
use crate::{
    events::EventStore,
    plugins::{AccountScope, Permission, Plugin, PluginKind},
    source_jobs::{self, Action, Change, Handoff, Job, Start, Status},
};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Extension, Query, State, rejection::{JsonRejection, QueryRejection}},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const START: &str = "/api/v1/source/jobs/start";
pub(crate) const STATUS: &str = "/api/v1/source/jobs/status";
pub(crate) const PAUSE: &str = "/api/v1/source/jobs/pause";
pub(crate) const RESUME: &str = "/api/v1/source/jobs/resume";
pub(crate) const CANCEL: &str = "/api/v1/source/jobs/cancel";
pub(crate) const COMPLETE: &str = "/api/v1/source/jobs/complete";
pub(crate) const PROGRESS: &str = "/api/v1/source/jobs/progress";

pub(crate) const SYNC_INTENT: &str = "/api/v1/source/sync-intent";
pub(crate) const SYNC_CLAIM: &str = "/api/v1/source/sync-intent/claim";

pub(crate) fn read_route(path: &str) -> bool { matches!(path, STATUS | SYNC_INTENT) }
pub(crate) fn write_route(path: &str) -> bool {
    matches!(path, START | PAUSE | RESUME | CANCEL | COMPLETE | PROGRESS | SYNC_INTENT | SYNC_CLAIM)
}

#[derive(Clone)]
struct Host { store: EventStore, permits: Arc<Semaphore> }

pub(crate) fn router(store: EventStore) -> Router {
    Router::new()
        .route(START, post(start))
        .route(STATUS, get(status))
        .route(PAUSE, post(pause))
        .route(RESUME, post(resume))
        .route(CANCEL, post(cancel))
        .route(COMPLETE, post(complete))
        .route(PROGRESS, post(progress))
        .route(SYNC_INTENT, get(discover_sync).post(queue_sync))
        .route(SYNC_CLAIM, post(claim_sync))
        .layer(DefaultBodyLimit::max(1024))
        .with_state(Host { store, permits: Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode, &'static str);
impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<source_jobs::Error> for Api {
    fn from(error: source_jobs::Error) -> Self {
        use source_jobs::Error::*;
        match error {
            Input => Self(StatusCode::BAD_REQUEST, "bad_source_job"),
            Missing => Self(StatusCode::NOT_FOUND, "not_found"),
            Stale => Self(StatusCode::CONFLICT, "source_job_state_conflict"),
            Busy => Self(StatusCode::CONFLICT, "source_job_lane_busy"),
            Paused => Self(StatusCode::LOCKED, "message_ingress_paused"),
            Quota => Self(StatusCode::TOO_MANY_REQUESTS, "source_job_quota"),
            Storage => Self(StatusCode::SERVICE_UNAVAILABLE, "source_job_store_unavailable"),
        }
    }
}

fn scope(plugin: &Plugin) -> Result<AccountScope, Api> {
    plugin.account_scope.as_ref()
        .filter(|_| plugin.kind == PluginKind::ScopedProducer
            && plugin.permissions.contains(&Permission::Ingest)
            && plugin.permissions.contains(&Permission::SourceJobsManage))
        .cloned().ok_or(Api(StatusCode::FORBIDDEN, "permission_required"))
}

fn json<T>(body: Result<Json<T>, JsonRejection>) -> Result<T, Api> {
    body.map(|Json(value)| value).map_err(|err| match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE => Api(StatusCode::PAYLOAD_TOO_LARGE, "source_job_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE => Api(StatusCode::UNSUPPORTED_MEDIA_TYPE, "json_required"),
        _ => Api(StatusCode::BAD_REQUEST, "bad_source_job"),
    })
}

async fn run<T: Serialize + Send + 'static>(
    host: Host, work: impl FnOnce(EventStore) -> Result<T, source_jobs::Error> + Send + 'static,
) -> Result<Json<T>, Api> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE, "source_job_store_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        work(host.store)
    }).await.map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE, "source_job_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

async fn start(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<Start>, JsonRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let body = json(body)?;
    run(host, move |store| source_jobs::start(&store, &plugin.id, &scope, body)).await
}

async fn status(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<Status>, QueryRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let Query(body) = query.map_err(|_| Api(StatusCode::BAD_REQUEST, "bad_query"))?;
    run(host, move |store| source_jobs::status(&store, &plugin.id, &scope, body)).await
}

async fn change(host: Host, plugin: Plugin, body: Result<Json<Change>, JsonRejection>,
    action: Action) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let body = json(body)?;
    run(host, move |store| source_jobs::change(&store, &plugin.id, &scope, body, action)).await
}

macro_rules! change_handler {
    ($handler:ident, $action:ident) => {
        async fn $handler(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
            body: Result<Json<Change>, JsonRejection>) -> Result<Json<Job>, Api> {
            change(host, plugin, body, Action::$action).await
        }
    };
}
change_handler!(pause, Pause);
change_handler!(resume, Resume);
change_handler!(cancel, Cancel);
change_handler!(complete, Complete);
change_handler!(progress, Progress);


async fn discover_sync(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::RawQuery(raw):axum::extract::RawQuery)->Result<Json<Handoff>,Api> {
    let scope=scope(&plugin)?;
    if raw.is_some() {return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));}
    run(host,move|store|source_jobs::discover_sync(&store,&plugin.id,&scope)).await
}
async fn queue_sync(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Change>,JsonRejection>)->Result<(StatusCode,Json<Handoff>),Api> {
    let scope=scope(&plugin)?;
    let request=json(body)?;
    run(host,move|store|source_jobs::queue_sync(&store,&plugin.id,&scope,request)).await
        .map(|result|(StatusCode::ACCEPTED,result))
}
async fn claim_sync(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    headers:HeaderMap,body:Result<Json<Change>,JsonRejection>)->Result<Json<Handoff>,Api> {
    let scope=scope(&plugin)?;
    // Host middleware already authenticated this exact header and scope.
    let bearer=headers.get(header::AUTHORIZATION).and_then(|v|v.to_str().ok())
        .and_then(|v|v.split_once(' '))
        .filter(|(scheme,token)|scheme.eq_ignore_ascii_case("bearer") && token.len()==64
            && token.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        .ok_or(Api(StatusCode::UNAUTHORIZED,"unauthorized"))?;
    let credential=source_jobs::handoff_credential(&scope.platform,bearer.1);
    let request=json(body)?;
    run(host,move|store|source_jobs::claim_sync(&store,&plugin.id,&scope,request,credential)).await
}
