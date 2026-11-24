//! Linux imported-media router mounted behind native bearer authentication.
use crate::{
    asset_jobs::{self, Create, Error, Job, Jobs, Target},
    media_bundle::Bundle,
    plugins::{AccountScope, Permission, Plugin, PluginKind},
    snapshot::Snapshot,
    selected_message_bundle::{self,FilteredRequest,Request as MessageBundle},
    store::Store,
};
use axum::{
    Json, Router, body::Bytes,
    extract::{DefaultBodyLimit, Extension, OriginalUri, Path, Query, RawQuery, State, rejection::{JsonRejection,PathRejection,QueryRejection}},
    http::{StatusCode, header},
    response::{IntoResponse,Response},
    routing::{get,post},
};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

pub(crate) const CREATE: &str = "/api/v1/resource-jobs/create";
pub(crate) const CREATE_MESSAGE_BUNDLE:&str="/api/v1/resource-jobs/create-message-bundle";
pub(crate) const CREATE_FILTERED_BUNDLE:&str="/api/v1/resource-jobs/create-filtered-bundle";
pub(crate) const CANCEL: &str = "/api/v1/resource-jobs/cancel";
pub(crate) const LIST: &str = "/api/v1/resource-jobs/list";
pub(crate) const STATUS: &str = "/api/v1/resource-jobs/status";
pub(crate) const DOWNLOAD: &str = "/api/v1/resource-jobs/download";
const LEGACY_STATUS:&str="/api/resource-jobs/{id}";
const LEGACY_DOWNLOAD:&str="/api/resource-jobs/{id}/download";
const LEGACY_CANCEL:&str="/api/resource-jobs/{id}/cancel";
fn id_valid(id:&str)->bool {id.len()==64 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))}
pub(crate) fn write_route(path:&str)->bool {
    matches!(path,CREATE|CREATE_MESSAGE_BUNDLE|CREATE_FILTERED_BUNDLE|CANCEL)
        || path.strip_prefix("/api/resource-jobs/").and_then(|r|r.strip_suffix("/cancel")).is_some_and(id_valid)
}

#[derive(Clone)]
struct Host {
    bundle: Bundle,
    snapshot: Snapshot,
    jobs: Arc<Jobs>,
    operations: Arc<Semaphore>,
    transfers: Arc<Semaphore>,
    deferred: Arc<Semaphore>,
}

pub(crate) fn router(bundle: Bundle, snapshot: Snapshot) -> Router {
    Router::new()
        .route(CREATE,post(create))
        .route(CREATE_MESSAGE_BUNDLE,post(create_message_bundle))
        .route(CREATE_FILTERED_BUNDLE,post(create_filtered_bundle))
        .route(CANCEL,post(cancel))
        .route(LIST,get(list))
        .route("/api/resource-jobs",get(list))
        .route(STATUS,get(status))
        .route(DOWNLOAD,get(download))
        .route(LEGACY_STATUS,get(legacy_status))
        .route(LEGACY_DOWNLOAD,get(legacy_download))
        .route(LEGACY_CANCEL,post(legacy_cancel).layer(DefaultBodyLimit::max(1)))
        .layer(DefaultBodyLimit::max(24 * 1024))
        .with_state(Host { bundle, snapshot, jobs:Arc::new(Jobs::default()),
            operations:Arc::new(Semaphore::new(2)), transfers:Arc::new(Semaphore::new(2)),deferred:Arc::new(Semaphore::new(1)) })
}

#[derive(Debug)]
struct Api(StatusCode, &'static str);
impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<Error> for Api {
    fn from(error: Error) -> Self {
        match error {
            Error::Invalid => Self(StatusCode::BAD_REQUEST,"bad_resource_job"),
            Error::Missing => Self(StatusCode::NOT_FOUND,"not_found"),
            Error::Limit => Self(StatusCode::PAYLOAD_TOO_LARGE,"resource_job_too_large"),
            Error::Busy => Self(StatusCode::TOO_MANY_REQUESTS,"resource_job_limit"),
            Error::Unavailable => Self(StatusCode::SERVICE_UNAVAILABLE,"media_bundle_unavailable"),
            Error::Cancelled => Self(StatusCode::CONFLICT,"resource_job_cancelled"),
        }
    }
}
fn scoped(plugin: &Plugin) -> Result<(AccountScope,String),Api> {
    let scope = plugin.account_scope.as_ref()
        .filter(|_| plugin.kind == PluginKind::Desktop && plugin.permissions.contains(&Permission::Messages)
            && plugin.permissions.contains(&Permission::Media))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    Ok((scope.clone(),asset_jobs::owner(&plugin.id,scope)?))
}
fn json<T>(body: Result<Json<T>,JsonRejection>) -> Result<T,Api> {
    body.map(|Json(v)|v).map_err(|e|match e.status() {
        StatusCode::PAYLOAD_TOO_LARGE => Api(StatusCode::PAYLOAD_TOO_LARGE,"resource_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE => Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _ => Api(StatusCode::BAD_REQUEST,"bad_resource_job"),
    })
}
fn query(target: Result<Query<Target>,QueryRejection>) -> Result<Target,Api> {
    target.map(|Query(t)|t).map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_query"))
}
impl Host {
    fn verify(&self) -> Result<(),Error> {
        self.bundle.verify().map_err(|_|Error::Unavailable)?;
        self.snapshot.verify().map_err(|_|Error::Unavailable)
    }
    async fn operate<T: Send + 'static>(self,
        work: impl FnOnce(&Host) -> Result<T,Error> + Send + 'static) -> Result<T,Api> {
        let permit = self.operations.clone().try_acquire_owned().map_err(|_|Error::Busy)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            self.verify()?;
            let value = work(&self)?;
            self.verify()?;
            Ok::<_,Error>(value)
        }).await.map_err(|_|Error::Unavailable)?.map_err(Api::from)
    }
}

async fn create(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<Create>, JsonRejection>) -> Result<Json<Job>,Api> {
    let (scope,owner) = scoped(&plugin)?;
    let request = json(body)?;
    host.jobs.room(&owner)?;
    let jobs = host.jobs.clone();
    let artifact = host.operate(move |host|asset_jobs::create(&host.bundle,&scope,request)).await?;
    // Publish only after the awaited verification. Dropping the request while
    // the blocking read runs cannot leave an unclaimed artifact in the cache.
    Ok(Json(jobs.publish(owner,artifact)?))
}
async fn create_message_bundle(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<MessageBundle>,JsonRejection>)->Result<Json<Job>,Api> {
    let (scope,owner)=scoped(&plugin)?;
    if !plugin.permissions.contains(&Permission::MessageExport) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let request=json(body)?;
    host.jobs.room(&owner)?;
    let jobs=host.jobs.clone();
    let artifact=host.operate(move |host| {
        let store=Store::open(host.snapshot.path()).map_err(|_|Error::Unavailable)?;
        selected_message_bundle::create(store,&host.bundle,&scope,request)
    }).await?;
    Ok(Json(jobs.publish(owner,artifact)?))
}
async fn create_filtered_bundle(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<FilteredRequest>,JsonRejection>)->Result<Json<Job>,Api> {
    let (scope,owner)=scoped(&plugin)?;
    if !can_export(&plugin){return Err(Api(StatusCode::FORBIDDEN,"permission_required"));}
    let request=json(body)?;
    if request.deferred {
        selected_message_bundle::validate_filtered_scope(&request,&scope)?;
        host.verify()?;
        let slot=host.deferred.clone().try_acquire_owned().map_err(|_|Error::Busy)?;
        let (info,work)=host.jobs.reserve_filtered(owner)?;
        let work=Arc::new(work);
        let task=work.clone();
        let jobs=host.jobs.clone();
        tokio::spawn(async move {
            let executor=jobs.clone();
            let result=tokio::task::spawn_blocking(move||{
                let _slot=slot;
                executor.start(&task)?;
                host.verify()?;
                task.checkpoint()?;
                let store=Store::open(host.snapshot.path()).map_err(|_|Error::Unavailable)?;
                let artifact=selected_message_bundle::create_filtered(store,&host.bundle,&scope,request,Some(&task))?;
                task.checkpoint()?;
                host.verify()?;
                Ok::<_,Error>(artifact)
            }).await.unwrap_or(Err(Error::Unavailable));
            let _=jobs.finish(&work,result);
        });
        return Ok(Json(info));
    }
    host.jobs.room(&owner)?;
    let jobs=host.jobs.clone();
    let artifact=host.operate(move|host|{
        let store=Store::open(host.snapshot.path()).map_err(|_|Error::Unavailable)?;
        selected_message_bundle::create_filtered(store,&host.bundle,&scope,request,None)
    }).await?;
    Ok(Json(jobs.publish(owner,artifact)?))
}

fn can_export(plugin:&Plugin)->bool {plugin.permissions.contains(&Permission::MessageExport)}
async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,OriginalUri(uri):OriginalUri)
    ->Result<Response,Api> {
    let (_,owner)=scoped(&plugin)?;
    let include_mixed=uri.path()==LIST && can_export(&plugin);
    let items=host.operate(move |host|host.jobs.list(&owner,include_mixed)).await?;
    Ok(Json(json!({"items":items})).into_response())
}
async fn status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    target:Result<Query<Target>,QueryRejection>)->Result<Json<Job>,Api> {
    let (_,owner)=scoped(&plugin)?;
    let target=query(target)?;
    let include_mixed=can_export(&plugin);
    Ok(Json(host.operate(move |host|host.jobs.status(&owner,&target.job_id,include_mixed)).await?))
}
async fn cancel(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Target>,JsonRejection>)->Result<StatusCode,Api> {
    let (_,owner)=scoped(&plugin)?;
    let target=json(body)?;
    let include_mixed=can_export(&plugin);
    host.operate(move |host|host.jobs.cancel(&owner,&target.job_id,include_mixed)).await?;
    Ok(StatusCode::NO_CONTENT)
}
struct Transfer(Bytes,#[allow(dead_code)] OwnedSemaphorePermit);
impl AsRef<[u8]> for Transfer {fn as_ref(&self)->&[u8]{&self.0}}
async fn download(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    target:Result<Query<Target>,QueryRejection>)->Result<Response,Api> {
    let (_,owner)=scoped(&plugin)?;
    download_job(host,owner,query(target)?,can_export(&plugin)).await
}
async fn download_job(host:Host,owner:String,target:Target,include_mixed:bool)->Result<Response,Api> {
    let permit=host.transfers.clone().try_acquire_owned().map_err(|_|Error::Busy)?;
    let bytes=host.operate(move |host|host.jobs.download(&owner,&target.job_id,include_mixed)).await?;
    Ok(([
        (header::CONTENT_TYPE,"application/zip"),
        (header::CONTENT_DISPOSITION,"attachment; filename=hub-resource-bundle.zip"),
        (header::CACHE_CONTROL,"no-store"),
        (header::X_CONTENT_TYPE_OPTIONS,"nosniff"),
    ],Bytes::from_owner(Transfer(bytes,permit))).into_response())
}

fn legacy_target(id:Result<Path<String>,PathRejection>,raw:Option<String>)->Result<Target,Api> {
    if raw.is_some() {return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));}
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    if !id_valid(&id) {return Err(Api(StatusCode::NOT_FOUND,"not_found"));}
    Ok(Target{job_id:id})
}
async fn legacy_status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,RawQuery(raw):RawQuery)->Result<Json<Job>,Api> {
    let (_,owner)=scoped(&plugin)?;
    let target=legacy_target(id,raw)?;
    Ok(Json(host.operate(move |host|host.jobs.status(&owner,&target.job_id,false)).await?))
}
async fn legacy_download(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,RawQuery(raw):RawQuery)->Result<Response,Api> {
    let (_,owner)=scoped(&plugin)?;
    let target=legacy_target(id,raw)?;
    download_job(host,owner,target,false).await
}
async fn legacy_cancel(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,RawQuery(raw):RawQuery,body:Bytes)->Result<StatusCode,Api> {
    let (_,owner)=scoped(&plugin)?;
    let target=legacy_target(id,raw)?;
    if !body.is_empty() {return Err(Api(StatusCode::BAD_REQUEST,"unexpected_body"));}
    host.operate(move|host|host.jobs.cancel(&owner,&target.job_id,false)).await?;
    Ok(StatusCode::NO_CONTENT)
}
