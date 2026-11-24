use crate::{campus::{self,Query as Filters},campus_catalog,events::EventStore,plugins::{Permission,Plugin,PluginKind}};
use axum::{Json,Router,extract::{DefaultBodyLimit,Extension,Path,Query,State,rejection::{QueryRejection,PathRejection,JsonRejection}},
    http::StatusCode,response::{IntoResponse,Response},routing::get};
use serde::{Deserialize,Serialize};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const ITEMS:&str="/api/v1/campus/items";
const DETAIL:&str="/api/v1/campus/items/{id}";
pub(crate) const DETAIL_STATE:&str="/api/v1/campus/items/{id}/state";
const LEGACY_DETAIL_STATE:&str="/api/campus/items/{id}/state";
pub(crate) const BOOKMARKS:&str="/api/v1/campus/bookmarks";
pub(crate) const DIGEST:&str="/api/v1/campus/digest";
const BOOKMARK_DETAIL:&str="/api/v1/campus/bookmarks/{id}";
pub(crate) const PROFILE:&str="/api/v1/campus/profile";
pub(crate) const SOURCES:&str="/api/v1/campus/sources";
pub(crate) const UPDATES:&str="/api/v1/campus/updates";
pub(crate) const LEGACY_BRIEFING:&str="/api/campus/briefing";
pub(crate) const PUBLIC_SHORTLIST:&str="/api/v1/campus/public-topic-shortlist";
pub(crate) const LEGACY_REFRESH:&str="/api/campus/refresh";
const WRITE_LIMIT:usize=4*1024;

#[derive(Clone)]
struct Host { store:EventStore,permits:Arc<Semaphore> }
pub(crate) fn router(store:EventStore)->Router {
    Router::new().route(ITEMS,get(list)).route(DETAIL,get(get_item))
        .route(DETAIL_STATE,get(get_article_state).post(write_article_state).layer(DefaultBodyLimit::max(WRITE_LIMIT)))
        .route(BOOKMARKS,get(list_bookmarks))
        .route(BOOKMARK_DETAIL,get(get_bookmark).post(write_bookmark).layer(DefaultBodyLimit::max(WRITE_LIMIT)))
        .route(DIGEST,get(dated_digest))
        .route(PROFILE,get(catalog_profile)).route(SOURCES,get(catalog_sources))
        .route(UPDATES,get(catalog_updates))
        .route("/api/campus/items",get(list)).route("/api/campus/items/{id}",get(get_item))
        .route(LEGACY_DETAIL_STATE,axum::routing::post(write_article_state)
            .layer(DefaultBodyLimit::max(WRITE_LIMIT)))
        .route("/api/campus/profile",get(catalog_profile)).route("/api/campus/sources",get(catalog_sources))
        .route("/api/campus/updates",get(catalog_updates))
        .route(PUBLIC_SHORTLIST,get(public_shortlist))
        .route(LEGACY_BRIEFING,get(legacy_briefing)).route(LEGACY_REFRESH,get(legacy_refresh))
        .with_state(Host{store,permits:Arc::new(Semaphore::new(4))})
}
pub(crate) fn route(path:&str)->bool {
    matches!(path,ITEMS|BOOKMARKS|DIGEST|PROFILE|SOURCES|UPDATES|LEGACY_BRIEFING|LEGACY_REFRESH|PUBLIC_SHORTLIST
        |"/api/campus/items"|"/api/campus/profile"|"/api/campus/sources"|"/api/campus/updates")
        || path.strip_prefix("/api/campus/items/").is_some_and(campus::item_id)
        || article_state_route(path)
        || path.strip_prefix("/api/v1/campus/items/")
        .or_else(||path.strip_prefix("/api/v1/campus/bookmarks/"))
        .is_some_and(|s|!s.is_empty()&&!s.contains('/'))
}
fn article_state_route(path:&str)->bool {
    path.strip_prefix("/api/v1/campus/items/").or_else(||path.strip_prefix("/api/campus/items/"))
        .and_then(|p|p.strip_suffix("/state")).is_some_and(campus::item_id)
}
pub(crate) fn write_route(path:&str)->bool {
    article_state_route(path) || path.strip_prefix("/api/v1/campus/bookmarks/").is_some_and(campus::item_id)
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl Api {
    fn translate(e:campus::Error)->Self {
        match e {
            campus::Error::Input=>Self(StatusCode::BAD_REQUEST,"bad_campus_query"),
            campus::Error::StaleBookmark=>Self(StatusCode::CONFLICT,"campus_bookmark_revision_conflict"),
            campus::Error::Missing=>Self(StatusCode::NOT_FOUND,"not_found"),
            _=>Self(StatusCode::SERVICE_UNAVAILABLE,"campus_store_unavailable"),
        }
    }
    fn scope(plugin:Plugin,target:Option<&str>)->Result<String,Self> {
        let profile=plugin.campus_profile.filter(|_|plugin.kind.is_campus()
            && plugin.permissions.contains(&Permission::CampusRead))
            .ok_or(Self(StatusCode::FORBIDDEN,"permission_required"))?;
        if target.is_some_and(|key|key!=profile) { return Err(Self(StatusCode::NOT_FOUND,"not_found")); }
        Ok(profile)
    }
}
impl IntoResponse for Api {
    fn into_response(self)->Response { (self.0,Json(json!({"error":{"code":self.1}}))).into_response() }
}
async fn run<T:Serialize+Send+'static>(host:Host,
    work:impl FnOnce(EventStore)->Result<T,campus::Error>+Send+'static)->Result<Json<T>,Api> {
    let permit=host.permits.try_acquire_owned().map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"campus_store_busy"))?;
    tokio::task::spawn_blocking(move|| {
        let _permit=permit;
        work(host.store)
    }).await.map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"campus_store_unavailable"))?
        .map(Json).map_err(Api::translate)
}
async fn list(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<Filters>,QueryRejection>)->Result<Json<campus::Page>,Api> {
    let Query(query)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_campus_query"))?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus::list(&store,&profile,query)).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Scope { profile:Option<String> }
async fn get_item(State(host):State<Host>,Extension(plugin):Extension<Plugin>,id:Result<Path<String>,PathRejection>,
    input:Result<Query<Scope>,QueryRejection>)->Result<Json<campus::Article>,Api> {
    let Query(query)=input.map_err(|_|Api(StatusCode::BAD_REQUEST,"bad_campus_query"))?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    let Path(id)=id.map_err(|_|Api(StatusCode::NOT_FOUND,"not_found"))?;
    run(host,move|store|campus::get(&store,&profile,&id)).await
}

fn not_found()->Api { Api(StatusCode::NOT_FOUND,"not_found") }
fn bad_query()->Api { Api(StatusCode::BAD_REQUEST,"bad_campus_query") }

async fn list_bookmarks(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus::BookmarkQuery>,QueryRejection>)->Result<Json<campus::Bookmarks>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus::bookmarks(&store,&profile,query)).await
}
async fn get_bookmark(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Query<Scope>,QueryRejection>)->Result<Json<campus::Bookmark>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    let Path(id)=id.map_err(|_|not_found())?;
    run(host,move|store|campus::bookmark(&store,&profile,&id)).await
}
async fn write_bookmark(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Json<campus::BookmarkChange>,JsonRejection>)->Result<Json<campus::Bookmark>,Api> {
    if plugin.kind!=PluginKind::CampusEditor || !plugin.permissions.contains(&Permission::CampusBookmarkWrite) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let Json(change)=input.map_err(|err|match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_campus_bookmark")
    })?;
    let profile=Api::scope(plugin,change.profile.as_deref())?;
    let Path(id)=id.map_err(|_|not_found())?;
    run(host,move|store|campus::set_bookmark(&store,&profile,&id,change)).await
}
async fn get_article_state(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    id:Result<Path<String>,PathRejection>,input:Result<Query<Scope>,QueryRejection>)
    ->Result<Json<campus::article_state::ArticleState>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    let Path(id)=id.map_err(|_|not_found())?;
    run(host,move|store|campus::article_state::get(&store,&profile,&id)).await
}
async fn write_article_state(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::OriginalUri(uri):axum::extract::OriginalUri,
    id:Result<Path<String>,PathRejection>,input:Result<Json<campus::article_state::Change>,JsonRejection>)
    ->Result<Response,Api> {
    if plugin.kind!=PluginKind::CampusEditor || !plugin.permissions.contains(&Permission::CampusBookmarkWrite) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let Json(change)=input.map_err(|err|match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE=>Api(StatusCode::PAYLOAD_TOO_LARGE,"campus_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE=>Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _=>Api(StatusCode::BAD_REQUEST,"bad_campus_bookmark")
    })?;
    let profile=Api::scope(plugin,change.profile.as_deref())?;
    let Path(id)=id.map_err(|_|not_found())?;
    let old_shape=uri.path().starts_with("/api/campus/items/")&&change.read.is_none();
    let Json(value)=run(host,move|store|campus::article_state::set(&store,&profile,&id,change)).await?;
    if old_shape {Ok(Json(value.bookmark_only()).into_response())}
    else {Ok(Json(value).into_response())}
}
async fn dated_digest(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus::DayQuery>,QueryRejection>)->Result<Json<campus::Digest>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus::digest(&store,&profile,query)).await
}

async fn catalog_profile(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_catalog::Scope>,QueryRejection>)->Result<Json<campus_catalog::Profile>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus_catalog::profile(&store,&profile)).await
}
async fn catalog_sources(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_catalog::SourceQuery>,QueryRejection>)->Result<Json<campus_catalog::Sources>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus_catalog::sources(&store,&profile,query)).await
}
async fn catalog_updates(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus_catalog::Scope>,QueryRejection>)->Result<Json<campus_catalog::Updates>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus_catalog::updates(&store,&profile)).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyBriefingQuery {
    profile: Option<String>,
    section: String,
    date: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
    q: Option<String>,
    topic: Option<String>,
}

#[derive(Serialize)]
struct LegacyBriefing {
    mode: &'static str,
    section: &'static str,
    ranking_supported: bool,
    refresh_supported: bool,
    #[serde(flatten)]
    digest: campus::Digest,
}

async fn public_shortlist(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:Result<Query<campus::briefing::shortlist::Query>,QueryRejection>)
    ->Result<Json<campus::briefing::shortlist::Page>,Api> {
    let Query(query)=input.map_err(|_|bad_query())?;
    let profile=Api::scope(plugin,query.profile.as_deref())?;
    run(host,move|store|campus::briefing::shortlist::list(&store,&profile,query)).await
}

async fn legacy_briefing(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    input: Result<Query<LegacyBriefingQuery>, QueryRejection>) -> Result<Response, Api> {
    let Query(query) = input.map_err(|_| bad_query())?;
    let profile = Api::scope(plugin, query.profile.as_deref())?;
    if query.section=="all" {
        if query.topic.as_deref().is_some_and(|s|!s.is_empty()) {
            return Err(Api(StatusCode::BAD_REQUEST,"campus_briefing_topic_unsupported"));
        }
        return run(host, move |store|campus::briefing::all(&store,&profile,
            query.date.unwrap_or_default(),query.q.unwrap_or_default(),query.limit,query.offset))
            .await.map(IntoResponse::into_response);
    }
    // Do not relabel the stock ranked selection as an unranked detached list.
    if query.section != "daily" || query.offset.unwrap_or(0) != 0
        || query.q.as_deref().is_some_and(|s| !s.is_empty())
        || query.topic.as_deref().is_some_and(|s| !s.is_empty()) {
        return Err(Api(StatusCode::BAD_REQUEST, "campus_briefing_requires_local_daily_subset"));
    }
    let day = campus::DayQuery { profile: None, date: query.date.ok_or_else(bad_query)?, limit: query.limit };
    run(host, move |store| Ok(LegacyBriefing {
        mode: "detached-local", section: "daily", ranking_supported: false, refresh_supported: false,
        digest: campus::digest(&store, &profile, day)?,
    })).await.map(IntoResponse::into_response)
}

async fn legacy_refresh(State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    input: Result<Query<Scope>, QueryRejection>) -> Result<Json<serde_json::Value>, Api> {
    let Query(query) = input.map_err(|_| bad_query())?;
    let profile = Api::scope(plugin, query.profile.as_deref())?;
    run(host, move |store| {
        let updates = campus_catalog::updates(&store, &profile)?;
        Ok(json!({
            "mode": "detached-local", "state": "idle", "refresh_supported": false, "reason": "offline_only",
            "started_at": null, "finished_at": null, "processed": 0, "total": 0, "added": 0,
            "body_success": 0, "body_failed": 0, "backlog": 0, "errors": [], "updates": updates
        }))
    }).await
}
