//! Exact Campus-profile detached source registration, not a new provider route.
use crate::{campus,campus_registration::{self,Catalog,Change,Scope,Status},
    events::EventStore,plugins::{Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Path,Query,State,rejection::{JsonRejection,QueryRejection,PathRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::{get,post}};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const LIST:&str="/api/v1/campus/source-registrations";
const DETAIL:&str="/api/v1/campus/source-registrations/{id}";
const PREFIX:&str="/api/v1/campus/source-registrations/";
#[derive(Clone)]
struct Host{store:EventStore,limit:Arc<Semaphore>}
pub(crate) fn router(store:EventStore)->Router{
    Router::new().route(LIST,get(list)).route(DETAIL,post(set).layer(DefaultBodyLimit::max(2048)))
        .with_state(Host{store,limit:Arc::new(Semaphore::new(4))})
}
pub(crate) fn write_route(path:&str)->bool{path.strip_prefix(PREFIX).is_some_and(campus::slug)}
pub(crate) fn route(path:&str)->bool{path==LIST||write_route(path)}
struct Api(StatusCode,&'static str);
fn forbidden()->Api{Api(StatusCode::FORBIDDEN,"permission_required")}
fn missing()->Api{Api(StatusCode::NOT_FOUND,"not_found")}
fn bad()->Api{Api(StatusCode::BAD_REQUEST,"bad_campus_registration_input")}
fn unavailable()->Api{Api(StatusCode::SERVICE_UNAVAILABLE,"campus_registration_unavailable")}
impl IntoResponse for Api{
    fn into_response(self)->Response{(self.0,Json(json!({"error":{"code":self.1}}))).into_response()}
}
fn profile(plugin:&Plugin,asked:Option<&str>,write:bool)->Result<String,Api>{
    let selected=plugin.campus_profile.as_ref().filter(|_|plugin.kind.is_campus()
        && plugin.permissions.contains(&Permission::CampusRead)
        && (!write || (plugin.kind==PluginKind::CampusEditor
            && plugin.permissions.contains(&Permission::CampusRegistrationWrite)))).ok_or_else(forbidden)?;
    if asked.is_some_and(|v|v!=selected.as_str()){return Err(missing());}
    Ok(selected.clone())
}
async fn run<T:Serialize+Send+'static>(host:Host,
    work:impl FnOnce(&EventStore)->Result<T,campus_registration::Error>+Send+'static)->Result<Json<T>,Api>{
    let permit=host.limit.try_acquire_owned().map_err(|_|unavailable())?;
    tokio::task::spawn_blocking(move||{let _permit=permit;work(&host.store)})
        .await.map_err(|_|unavailable())?.map(Json).map_err(|e| match e{
            campus_registration::Error::Input=>bad(),campus_registration::Error::Missing=>missing(),
            campus_registration::Error::Conflict=>Api(StatusCode::CONFLICT,"campus_registration_revision_conflict"),
            campus_registration::Error::Storage=>unavailable(),
        })
}
async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Scope>,QueryRejection>)->Result<Json<Catalog>,Api>{
    let Query(q)=input.map_err(|_|bad())?;
    let key=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_registration::list(store,&key,q)).await
}
async fn set(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Json<Change>,JsonRejection>)->Result<Json<Status>,Api>{
    profile(&plugin,None,true)?;
    let Path(id)=id.map_err(|_|missing())?;
    let Json(q)=input.map_err(|e|match e.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_registration_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>bad(),
    })?;
    let key=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_registration::set(store,&key,&id,q)).await
}
