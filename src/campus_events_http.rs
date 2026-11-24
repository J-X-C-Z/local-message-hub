//! Ordinary native Campus profile routes only; no automatic grouping or delivery.
use crate::{campus,campus_events::{self,Change,Create,Event,Page,Summary},
    events::EventStore,plugins::{Permission,Plugin,PluginKind}};
use axum::{
    Json,Router,extract::{DefaultBodyLimit,Extension,Path,Query,State,
        rejection::{JsonRejection,PathRejection,QueryRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::{get,post},
};
use serde::{Deserialize,Serialize};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const EVENTS:&str="/api/v1/campus/events";
const DETAIL:&str="/api/v1/campus/events/{id}";
const STATE:&str="/api/v1/campus/events/{id}/state";
const PREFIX:&str="/api/v1/campus/events/";
const MAX_BODY:usize=16*1024;

#[derive(Clone)]
struct Host{store:EventStore,permits:Arc<Semaphore>}

pub(crate) fn router(store:EventStore)->Router{
    Router::new().route(EVENTS,get(list).post(create))
        .route(DETAIL,get(detail)).route(STATE,post(set_state))
        .layer(DefaultBodyLimit::max(MAX_BODY))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}

pub(crate) fn route(path:&str)->bool{
    path==EVENTS || path.strip_prefix(PREFIX).is_some_and(|suffix|
        campus::item_id(suffix) || suffix.strip_suffix("/state").is_some_and(campus::item_id))
}

pub(crate) fn write_route(path:&str)->bool{
    path==EVENTS || path.strip_prefix(PREFIX).and_then(|suffix|suffix.strip_suffix("/state"))
        .is_some_and(campus::item_id)
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api{
    fn into_response(self)->Response{(self.0,Json(json!({"error":{"code":self.1}}))).into_response()}
}

impl From<campus_events::Error> for Api{
    fn from(error:campus_events::Error)->Self{
        use campus_events::Error::*;
        match error{
            Input=>Self(StatusCode::BAD_REQUEST,"bad_campus_event"),
            Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            Stale=>Self(StatusCode::CONFLICT,"campus_event_revision_conflict"),
            Quota=>Self(StatusCode::TOO_MANY_REQUESTS,"campus_event_quota"),
            Locked=>Self(StatusCode::FORBIDDEN,"permission_required"),
            Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"campus_event_store_unavailable"),
        }
    }
}

fn profile(plugin:&Plugin,requested:Option<&str>,write:bool)->Result<String,Api>{
    let grant=plugin.campus_profile.as_ref().filter(|_|plugin.kind.is_campus()
        && plugin.permissions.contains(&Permission::CampusRead)
        && (!write || (plugin.kind==PluginKind::CampusEditor
            && plugin.permissions.contains(&Permission::CampusEventWrite))))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    if requested.is_some_and(|s|s!=grant.as_str()){
        return Err(Api(StatusCode::NOT_FOUND,"not_found"));
    }
    Ok(grant.clone())
}

fn json<T>(input:Result<Json<T>,JsonRejection>)->Result<T,Api>{
    input.map(|Json(value)|value).map_err(|error|match error.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_event_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_campus_event"),
    })
}

async fn run<T:Serialize+Send+'static>(host:Host,work:impl FnOnce(&EventStore)
    ->Result<T,campus_events::Error>+Send+'static)->Result<Json<T>,Api>{
    let permit=host.permits.try_acquire_owned().map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"campus_event_busy"))?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        work(&host.store)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"campus_event_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Scope{profile:Option<String>}

async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_events::Query>,QueryRejection>)->Result<Json<Page>,Api>{
    let Query(q)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_campus_event"))?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_events::list(store,&profile,q)).await
}

async fn create(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Json<Create>,JsonRejection>)->Result<Json<Event>,Api>{
    profile(&plugin,None,true)?;
    let q=json(input)?;
    let profile=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_events::create(store,&profile,q)).await
}

async fn detail(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Query<Scope>,QueryRejection>)->Result<Json<Event>,Api>{
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    let Query(q)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_campus_event"))?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_events::get(store,&profile,&id)).await
}

async fn set_state(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Json<Change>,JsonRejection>)->Result<Json<Summary>,Api>{
    profile(&plugin,None,true)?;
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    let q=json(input)?;
    let profile=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_events::set_state(store,&profile,&id,q)).await
}
