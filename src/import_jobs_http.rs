//! Ordinary native archive only; embedded behind the existing loopback grant middleware.
use crate::{
    events::{EventStore, MAX_BATCH_BYTES},
    import_jobs::{self, ApplyObservation, Confirm, Job, ListPage, ListQuery, Prepare, Target},
    legacy_import::{csv,preflight},
    plugins::{AccountScope, Permission, Plugin, PluginKind},
};
use axum::{
    Json, Router, body::{Bytes,to_bytes},
    extract::{DefaultBodyLimit, Extension, FromRequest, Path, Query, RawQuery, Request, State, rejection::{JsonRejection, PathRejection, QueryRejection}},
    http::{HeaderMap,StatusCode,header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const LIST:&str="/api/v1/import-jobs/list";
pub(crate) const PREVIEW: &str = "/api/v1/import-jobs/preview";
pub(crate) const APPLY_EVIDENCE:&str="/api/v1/import-jobs/apply-evidence";
pub(crate) const STATUS: &str = "/api/v1/import-jobs/status";
pub(crate) const COMMIT: &str = "/api/v1/import-jobs/commit";
pub(crate) const CANCEL: &str = "/api/v1/import-jobs/cancel";
pub(crate) const LEGACY_PREVIEW: &str="/api/imports/preview";
pub(crate) const LEGACY_SUBMIT:&str="/api/imports";
const IDEMPOTENCY_KEY:&str="idempotency-key";
const LEGACY_STATUS: &str="/api/imports/{id}";
const LEGACY_CANCEL: &str="/api/imports/{id}/cancel";
const MAX_HTTP: usize = MAX_BATCH_BYTES.div_ceil(3) * 4 + 512;

fn scoped_id(id:&str) -> bool {
    id.len()==64 && id.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub(crate) fn write_route(path:&str) -> bool {
    matches!(path, PREVIEW|COMMIT|CANCEL|LEGACY_PREVIEW|LEGACY_SUBMIT)
        || path.strip_prefix("/api/imports/").and_then(|p|p.strip_suffix("/cancel")).is_some_and(scoped_id)
}

/// Raw preview requires its exact route and a single literal octet type.
pub(crate) fn raw_preview_request(path:&str,headers:&HeaderMap)->bool {
    path==LEGACY_PREVIEW && octet_type(headers)
}
fn octet_type(headers:&HeaderMap)->bool {
    let all_types=headers.get_all(header::CONTENT_TYPE);
    let mut types=all_types.iter();
    types.next().and_then(|value|value.to_str().ok())
        .is_some_and(|value|value.eq_ignore_ascii_case("application/octet-stream"))
        && types.next().is_none()
}

fn idempotency_key(headers:&HeaderMap)->Option<&str> {
    let all=headers.get_all(IDEMPOTENCY_KEY);
    let mut values=all.iter();
    let key=values.next()?.to_str().ok()?;
    (values.next().is_none() && import_jobs::request_key(key)).then_some(key)
}

pub(crate) fn raw_import_request(path:&str,headers:&HeaderMap)->bool {
    raw_preview_request(path,headers)
        || (path==LEGACY_SUBMIT && octet_type(headers) && idempotency_key(headers).is_some())
}

#[derive(Clone)]
struct Host { store: EventStore, permits: Arc<Semaphore> }
pub(crate) fn router(store: EventStore) -> Router {
    Router::new()
        .route(LIST,get(list))
        .route(LEGACY_SUBMIT,get(list).post(submit_stock))
        .route(PREVIEW, post(preview))
        .route(STATUS, get(status))
        .route(APPLY_EVIDENCE,get(apply_evidence))
        .route(COMMIT, post(commit))
        .route(CANCEL, post(cancel))
        .route(LEGACY_PREVIEW, post(preview_alias))
        .route(LEGACY_STATUS, get(status_alias))
        .route(LEGACY_CANCEL, post(cancel_alias).layer(DefaultBodyLimit::max(1)))
        .layer(DefaultBodyLimit::max(MAX_HTTP))
        .with_state(Host { store, permits: Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode, &'static str);

impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":{"code":self.1}}))).into_response()
    }
}

impl From<import_jobs::Error> for Api {
    fn from(error: import_jobs::Error) -> Self {
        use import_jobs::Error::*;
        match error {
            Input => Self(StatusCode::BAD_REQUEST, "bad_import_job"),
            Missing => Self(StatusCode::NOT_FOUND, "not_found"),
            Stale => Self(StatusCode::CONFLICT, "import_state_conflict"),
            Paused => Self(StatusCode::LOCKED, "message_ingress_paused"),
            Quota => Self(StatusCode::TOO_MANY_REQUESTS, "import_job_quota"),
            Storage => Self(StatusCode::SERVICE_UNAVAILABLE, "import_store_unavailable"),
        }
    }
}

fn scope(plugin: &Plugin) -> Result<AccountScope, Api> {
    plugin.account_scope.as_ref()
        .filter(|_| plugin.kind == PluginKind::Desktop &&
            plugin.permissions.contains(&Permission::ImportsManage))
        .cloned().ok_or(Api(StatusCode::FORBIDDEN, "permission_required"))
}

fn json<T>(body: Result<Json<T>, JsonRejection>) -> Result<T, Api> {
    body.map(|Json(value)| value).map_err(|err| match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE => Api(StatusCode::PAYLOAD_TOO_LARGE, "import_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE => Api(StatusCode::UNSUPPORTED_MEDIA_TYPE, "json_required"),
        _ => Api(StatusCode::BAD_REQUEST, "bad_import_job"),
    })
}

async fn run(host: Host, work: impl FnOnce(EventStore) -> Result<Job, import_jobs::Error>
    + Send + 'static) -> Result<Json<Job>, Api> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE, "import_store_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        work(host.store)
    }).await.map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE, "import_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

async fn preview(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<Prepare>, JsonRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let body = json(body)?;
    run(host, move |store| import_jobs::prepare(&store,&plugin.id,&scope,body)).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawPreviewQuery {
    format:String,
    platform:Option<String>,
    account:Option<String>,
    filename:Option<String>,
}

async fn preview_alias(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    req:Request)->Result<Response,Api> {
    let scope=scope(&plugin)?;
    if !raw_preview_request(req.uri().path(),req.headers()) {
        // Preserve the shipped JSON/base64 staging alias and JSON MIME rules.
        if req.uri().query().is_some(){return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));}
        let body=json(Json::<Prepare>::from_request(req,&host).await)?;
        return run(host,move|store|import_jobs::prepare(&store,&plugin.id,&scope,body))
            .await.map(IntoResponse::into_response);
    }
    let is_csv=raw_query(req.uri(),&scope)?;
    // Bound simultaneous upload bodies as well as parse workers.
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_busy"))?;
    let body=to_bytes(req.into_body(),MAX_BATCH_BYTES).await
        .map_err(|_|Api(StatusCode::PAYLOAD_TOO_LARGE,"import_body_too_large"))?;
    let report=tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        if is_csv {csv::preview(&body,&scope.platform,&scope.account_id).map(|r|Json(r).into_response())}
        else {preflight::preview(&body,&scope.platform,&scope.account_id).map(|r|Json(r).into_response())}
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_unavailable"))?
        .map_err(|err|match err {
            preflight::Error::Format=>Api(StatusCode::BAD_REQUEST,if is_csv {"bad_stock_csv"} else {"bad_stock_json"}),
            preflight::Error::Scope=>Api(StatusCode::FORBIDDEN,"account_scope_mismatch"),
            preflight::Error::Limit=>Api(StatusCode::PAYLOAD_TOO_LARGE,"import_body_too_large"),
        })?;
    Ok(report)
}

fn raw_query(uri:&axum::http::Uri,scope:&AccountScope)->Result<bool,Api> {
    let Query(q)=Query::<RawPreviewQuery>::try_from_uri(uri)
        .map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))?;
    if !matches!(q.format.as_str(),"json"|"csv") {return Err(Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"stock_json_required"));}
    if q.platform.as_deref().is_some_and(|p|p!="all"&&p!=scope.platform.as_str())
        ||q.account.as_deref().is_some_and(|a|!a.is_empty()&&a!=scope.account_id.as_str()) {
        return Err(Api(StatusCode::FORBIDDEN,"account_scope_mismatch"));
    }
    // The stock UI includes the upload's display name. Do not open, store,
    // log, or echo it, including path-like or device-name values.
    if q.filename.as_ref().is_some_and(|name|name.len()>512) {
        return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));
    }
    Ok(q.format=="csv")
}

async fn submit_stock(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    req:Request)->Result<(StatusCode,Json<Job>),Api> {
    let scope=scope(&plugin)?;
    if !octet_type(req.headers()) {
        return Err(Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"octet_stream_required"));
    }
    let key=idempotency_key(req.headers()).ok_or(Api(StatusCode::BAD_REQUEST,"idempotency_key_required"))?.to_owned();
    let is_csv=raw_query(req.uri(),&scope)?;
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_busy"))?;
    let body=to_bytes(req.into_body(),MAX_BATCH_BYTES).await
        .map_err(|_|Api(StatusCode::PAYLOAD_TOO_LARGE,"import_body_too_large"))?;
    let job=tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        import_jobs::prepare_stock(&host.store,&plugin.id,&scope,&key,&body,is_csv)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_unavailable"))?
        .map_err(Api::from)?;
    Ok((StatusCode::ACCEPTED,Json(job)))
}

async fn status(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<Target>, QueryRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let Query(target) = query.map_err(|_| Api(StatusCode::BAD_REQUEST, "bad_query"))?;
    run(host, move |store| import_jobs::status(&store,&plugin.id,&scope,target)).await
}
async fn apply_evidence(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<Target>,QueryRejection>)->Result<Json<ApplyObservation>,Api> {
    let scope=scope(&plugin)?;
    let Query(target)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))?;
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_busy"))?;
    tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        import_jobs::observe_apply(&host.store,&plugin.id,&scope,target)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_unavailable"))?
        .map(Json).map_err(Api::from)
}
async fn commit(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<Confirm>, JsonRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let body = json(body)?;
    run(host, move |store| import_jobs::commit(&store,&plugin.id,&scope,body)).await
}
async fn cancel(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<Target>, JsonRejection>) -> Result<Json<Job>, Api> {
    let scope = scope(&plugin)?;
    let body = json(body)?;
    run(host, move |store| import_jobs::cancel(&store,&plugin.id,&scope,body)).await
}

async fn status_alias(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,RawQuery(raw):RawQuery) -> Result<Json<Job>,Api> {
    let scope=scope(&plugin)?;
    if raw.is_some() {return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));}
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    if !scoped_id(&id) {return Err(Api(StatusCode::NOT_FOUND,"not_found"));}
    run(host,move|store| import_jobs::status(&store,&plugin.id,&scope,Target{job_id:id})).await
}
async fn cancel_alias(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,body:Bytes) -> Result<Json<Job>,Api> {
    let scope=scope(&plugin)?;
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    if !scoped_id(&id) {return Err(Api(StatusCode::NOT_FOUND,"not_found"));}
    if !body.is_empty() {return Err(Api(StatusCode::BAD_REQUEST,"unexpected_body"));}
    run(host,move|store| import_jobs::cancel(&store,&plugin.id,&scope,Target{job_id:id})).await
}

async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:Result<Query<ListQuery>,QueryRejection>) -> Result<Json<ListPage>,Api> {
    let scope=scope(&plugin)?;
    let Query(q)=query.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))?;
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_busy"))?;
    tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        import_jobs::list(&host.store,&plugin.id,&scope,q)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"import_store_unavailable"))?
        .map(Json).map_err(Api::from)
}
