//! Bounded legacy-shaped settings adapter for exact native Desktop grants.
use crate::{
    client_prefs, client_prefs_http, events::EventStore,
    notifications::{self, ScopedFilters}, notifications_http,
    plugins::{AccountScope, Permission, Plugin, PluginKind},
};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Extension, RawQuery, State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH: &str = "/api/settings";
// Native list caps are 100 IDs/side and 256 bytes/ID. JSON strings contain
// encoded scope triples, so bound the full outer JSON separately.
const MAX_BODY: usize = 320 * 1024;
const UNSUPPORTED: &[&str] = &[
    "capabilities", "auto_sync", "auto_index_at_login", "mcp_write_enabled",
    "auto_download_attachments", "sync_interval",
];
#[derive(Clone)]
struct Host { store: EventStore, permits: Arc<Semaphore> }
pub(crate) fn router(store: EventStore) -> Router {
    Router::new().route(PATH, get(read).post(write))
        .layer(DefaultBodyLimit::max(MAX_BODY))
        .with_state(Host { store, permits: Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode, &'static str);
impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<notifications::Error> for Api {
    fn from(error: notifications::Error) -> Self {
        match error {
            notifications::Error::Input => Self(StatusCode::BAD_REQUEST, "bad_notification_request"),
            notifications::Error::Conflict => Self(StatusCode::CONFLICT, "notification_revision_conflict"),
            notifications::Error::Storage => unavailable(),
        }
    }
}
fn unavailable() -> Api { Api(StatusCode::SERVICE_UNAVAILABLE, "settings_unavailable") }
fn scope(plugin: &Plugin) -> Result<AccountScope, Api> {
    plugin.account_scope.as_ref().filter(|_| plugin.kind == PluginKind::Desktop)
        .cloned().ok_or(Api(StatusCode::FORBIDDEN, "permission_required"))
}
async fn run(host: Host, work: impl FnOnce(&EventStore)->Result<Value,Api> + Send + 'static)
    -> Result<Json<Value>,Api> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_|Api(StatusCode::SERVICE_UNAVAILABLE, "settings_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let response = work(&host.store)?;
        host.store.verify().map_err(|_| unavailable())?;
        Ok(Json(response))
    }).await.map_err(|_|unavailable())?
}

async fn read(State(host): State<Host>, Extension(plugin): Extension<Plugin>, RawQuery(query):RawQuery)
    -> Result<Json<Value>,Api> {
    let scope = scope(&plugin)?;
    if query.is_some() { return Err(Api(StatusCode::BAD_REQUEST, "bad_query")); }
    let notification_read = plugin.permissions.contains(&Permission::NotificationRead);
    let notification_write = notification_read && plugin.permissions.contains(&Permission::NotificationWrite);
    let client_preferences = plugin.permissions.contains(&Permission::ClientPreferences);
    if !notification_read && !client_preferences {
        return Err(Api(StatusCode::FORBIDDEN, "permission_required"));
    }
    run(host, move |store| {
        let mut response = json!({
            "scope":"desktop_grant_account",
            "writable_fields": if notification_write { &["notification_filters"][..] } else { &[] },
            "unsupported_fields": UNSUPPORTED,
            "notification_delivery":false,
        });
        if notification_read {
            let state = notifications::get(store,&scope,&plugin.id)?;
            // Keep the legacy field name; scope, enabled state and revision
            // are distinct so a filter write cannot change either global or
            // per-grant notification enablement.
            if let (Some(out),Some(view)) = (response.as_object_mut(),state.settings_view(&scope).as_object()) {
                out.extend(view.clone());
            }
            response["notification_preferences_path"] = json!(notifications_http::PREFERENCES);
        }
        if client_preferences {
            let current = client_prefs::read(store,&plugin.id,&scope).map_err(|_|unavailable())?;
            response["client_preferences"] = json!(current);
            response["client_preferences_path"] = json!(client_prefs_http::PATH);
        }
        Ok(response)
    }).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Write {
    expected_revision:i64,
    notification_filters:ScopedFilters,
}
async fn write(State(host):State<Host>, Extension(plugin):Extension<Plugin>,
    body:Result<Json<Write>,JsonRejection>) -> Result<Json<Value>,Api> {
    let scope = scope(&plugin)?;
    if !plugin.permissions.contains(&Permission::NotificationRead)
        || !plugin.permissions.contains(&Permission::NotificationWrite) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let Json(write) = body.map_err(|err| match err.status() {
        StatusCode::PAYLOAD_TOO_LARGE => Api(StatusCode::PAYLOAD_TOO_LARGE,"settings_body_too_large"),
        StatusCode::UNSUPPORTED_MEDIA_TYPE => Api(StatusCode::UNSUPPORTED_MEDIA_TYPE,"json_required"),
        _ => Api(StatusCode::BAD_REQUEST,"unsupported_or_invalid_settings"),
    })?;
    run(host,move |store| {
        let state = notifications::put_scoped_filters(store,&scope,&plugin.id,
            write.expected_revision, write.notification_filters)?;
        Ok(state.settings_view(&scope))
    }).await
}
