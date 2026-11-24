//! Exact opted-in Desktop preferences for ordinary native-live Hub.
use crate::{
    client_prefs::{self, Error, Set, State as PreferencesState},
    events::EventStore,
    plugins::{AccountScope,Permission,Plugin,PluginKind},
};
use axum::{
    Json,Router,
    extract::{DefaultBodyLimit, Extension, State, rejection::JsonRejection},
    http::{StatusCode,Uri},
    response::{IntoResponse,Response},
    routing::get,
};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH: &str = "/api/v1/client/preferences";

#[derive(Clone)]
struct Host { store: EventStore, permits: Arc<Semaphore> }
pub(crate) fn router(store: EventStore) -> Router {
    Router::new().route(PATH,get(read).post(set))
        .layer(DefaultBodyLimit::max(1024))
        .with_state(Host { store, permits: Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<Error> for Api {
    fn from(error:Error) -> Self {
        match error {
            Error::Input => Self(StatusCode::BAD_REQUEST,"bad_client_preferences"),
            Error::Stale => Self(StatusCode::CONFLICT,"client_preferences_version_conflict"),
            Error::Quota => Self(StatusCode::TOO_MANY_REQUESTS,"client_preferences_quota"),
            Error::Storage => Self(StatusCode::SERVICE_UNAVAILABLE,"client_preferences_unavailable"),
        }
    }
}

fn scope(plugin:&Plugin) -> Result<AccountScope,Api> {
    plugin.account_scope.as_ref().filter(|_| plugin.kind==PluginKind::Desktop
        && plugin.permissions.contains(&Permission::ClientPreferences)).cloned()
        .ok_or(Api(StatusCode::FORBIDDEN,"permission_required"))
}

async fn run(host:Host,work:impl FnOnce(&EventStore)->Result<PreferencesState,Error>+Send+'static)
    -> Result<Json<PreferencesState>,Api> {
    let permit=host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE,"client_preferences_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit=permit;
        work(&host.store)
    }).await.map_err(|_|Error::Storage)?.map(Json).map_err(Api::from)
}

async fn read(State(host):State<Host>,Extension(plugin):Extension<Plugin>,uri:Uri)
    -> Result<Json<PreferencesState>,Api> {
    let scope=scope(&plugin)?;
    if uri.query().is_some() { return Err(Api(StatusCode::BAD_REQUEST,"bad_query")); }
    run(host,move |store|client_prefs::read(store,&plugin.id,&scope)).await
}

async fn set(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<Set>,JsonRejection>) -> Result<Json<PreferencesState>,Api> {
    let scope=scope(&plugin)?;
    let Json(body)=body.map_err(|err|match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE => Api(StatusCode::PAYLOAD_TOO_LARGE,"client_preferences_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE => Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _ => Api(StatusCode::BAD_REQUEST,"bad_client_preferences"),
    })?;
    run(host,move |store|client_prefs::set(store,&plugin.id,&scope,body)).await
}
