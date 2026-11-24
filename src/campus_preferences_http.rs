//! Offline view preferences. No scheduler, provider or access-policy operation.
use crate::{
    campus,
    campus_preferences::{self,Change,Page,Preference,Scope},
    events::EventStore,
    plugins::{Permission,Plugin,PluginKind},
};
use axum::{
    Json,Router,extract::{DefaultBodyLimit,Extension,Path,Query,State,
        rejection::{JsonRejection,PathRejection,QueryRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::get,
};
use serde::Serialize;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PREFERENCES:&str="/api/v1/campus/source-preferences";
pub(crate) const VISIBLE_ITEMS:&str="/api/v1/campus/visible-items";
const DETAIL:&str="/api/v1/campus/source-preferences/{id}";
const PREFIX:&str="/api/v1/campus/source-preferences/";

#[derive(Clone)]
struct Host{store:EventStore,permits:Arc<Semaphore>}

pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(PREFERENCES,get(list))
        .route(DETAIL,get(detail).post(update).layer(DefaultBodyLimit::max(4*1024)))
        .route(VISIBLE_ITEMS,get(visible_items))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}

pub(crate) fn route(path:&str)->bool {
    matches!(path,PREFERENCES|VISIBLE_ITEMS) || write_route(path)
}

pub(crate) fn write_route(path:&str)->bool {
    path.strip_prefix(PREFIX).is_some_and(campus::slug)
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self)->Response { (self.0,Json(json!({"error":{"code":self.1}}))).into_response() }
}

impl From<campus_preferences::Error> for Api {
    fn from(e:campus_preferences::Error)->Self {
        use campus_preferences::Error::*;
        match e {
            Input=>bad(),
            Missing=>missing(),
            Conflict=>Self(StatusCode::CONFLICT,"campus_source_preference_revision_conflict"),
            Locked=>Self(StatusCode::FORBIDDEN,"permission_required"),
            Storage=>Self(StatusCode::SERVICE_UNAVAILABLE,"campus_source_preference_store_unavailable"),
        }
    }
}
fn bad()->Api {Api(StatusCode::BAD_REQUEST,"bad_campus_source_preference")}
fn missing()->Api {Api(StatusCode::NOT_FOUND,"not_found")}
fn profile(plugin:&Plugin,requested:Option<&str>,write:bool)->Result<String,Api> {
    let scope=plugin.campus_profile.as_ref().filter(|_|plugin.kind.is_campus()
        && plugin.permissions.contains(&Permission::CampusRead)
        && (!write || (plugin.kind==PluginKind::CampusEditor
            && plugin.permissions.contains(&Permission::CampusSourcePreferenceWrite))))
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))?;
    if requested.is_some_and(|key|key!=scope.as_str()){return Err(missing());}
    Ok(scope.clone())
}

async fn run<T:Serialize+Send+'static>(host:Host,work:impl FnOnce(&EventStore)
    ->Result<T,campus_preferences::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned().map_err(|_|
        Api(StatusCode::SERVICE_UNAVAILABLE,"campus_source_preference_busy"))?;
    tokio::task::spawn_blocking(move||{
        let _permit=permit;
        work(&host.store)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"campus_source_preference_store_unavailable"))?
        .map(Json).map_err(Api::from)
}

async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_preferences::Query>,QueryRejection>)->Result<Json<Page>,Api>{
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus_preferences::list(store,&profile,q)).await
}

async fn detail(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Scope>,QueryRejection>,id:Result<Path<String>,PathRejection>)->Result<Json<Preference>,Api>{
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    let Path(id)=id.map_err(|_|missing())?;
    run(host,move|store|campus_preferences::get(store,&profile,&id,q)).await
}

async fn update(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Json<Change>,JsonRejection>)->Result<Json<Preference>,Api>{
    profile(&plugin,None,true)?;
    let Path(id)=id.map_err(|_|missing())?;
    let Json(q)=input.map_err(|e|match e.status(){
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_source_preference_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>bad(),
    })?;
    let profile=profile(&plugin,q.profile.as_deref(),true)?;
    run(host,move|store|campus_preferences::put(store,&profile,&id,q)).await
}

async fn visible_items(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus::Query>,QueryRejection>)->Result<Json<campus::Page>,Api>{
    let Query(q)=input.map_err(|_|bad())?;
    let profile=profile(&plugin,q.profile.as_deref(),false)?;
    run(host,move|store|campus::list_visible(store,&profile,q).map_err(Into::into)).await
}
