mod legacy_http;
mod desktop_contract;
#[cfg(target_os = "linux")]
pub(crate) mod browser_session;
#[cfg(target_os = "linux")]
pub(crate) mod reader_assets;

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Extension, Query, Request, State, rejection::QueryRejection},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode, header},
    middleware::{Next, from_fn_with_state},
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value, json};
use std::{net::SocketAddrV4, sync::Arc};
use tokio::sync::Semaphore;

use crate::{
    annual_report, pair_report, chat_analytics, relationship,
    events::EventStore,
    export::{self, ExportQuery, FilteredQuery},
    grouped_exports::{self,ContactExport,Observed,Selection},
    index_overview::{self,Selector as IndexSelector},
    operations_http, transcription_http, export_presets_http, moments_http,
    live_http::{self, LiveHttp},
    metadata_http,
    drafts_http, draft_review_http, client_prefs_http,
    notifications_http,
    import_jobs_http, source_jobs_http,
    campus_http, campus_events_http, campus_preferences_http, campus_scoring_http, campus_display_http, campus_registration_http,
    plugins::{AccountScope, Permission, Plugin, Plugins},
    snapshot::Snapshot,
    profile_lock::Gate,
    store::{ContextQuery, Query as ArchiveQuery, Store, StoreError},
};

const MAX_QUERY_BYTES: usize = 8 * 1024;
const MAX_ARCHIVE_WORK: usize = 4;
const MAX_INGEST_BODY: usize = 1024 * 1024;

#[derive(Clone)]
struct Host {
    authority: HeaderValue,
    plugins: Plugins,
    archive: Archive,
    store: Store,
    permits: Arc<Semaphore>,
    addons: HostAddons,
}

#[derive(Clone, Default)]
struct HostAddons {
    #[cfg(target_os = "linux")]
    imported_media: Option<crate::live_imported_media::Link>,
    webui_dir: Option<std::path::PathBuf>,
}

#[derive(Clone)]
enum Archive {
    Snapshot(Snapshot),
    Live(EventStore),
    Guarded(EventStore,Gate,bool),
    #[cfg(target_os = "linux")]
    Media(crate::media_bundle::Bundle, Snapshot),
}

impl Archive {
    fn is_live(&self) -> bool {
        matches!(self, Self::Live(_))
    }

    fn guarded(&self) -> Option<&Gate> {
        match self { Self::Guarded(_, gate,_) => Some(gate), _ => None }
    }

    fn legacy(&self) -> bool { matches!(self,Self::Guarded(_,_,true)) }

    fn has_media(&self) -> bool {
        #[cfg(target_os = "linux")]
        { matches!(self, Self::Media(..)) }
        #[cfg(not(target_os = "linux"))]
        { false }
    }

    fn mode(&self) -> &'static str {
        if self.is_live() { "native-live" }
        else if self.legacy() { "guarded-legacy-read-only" }
        else if self.guarded().is_some() { "guarded-read-only" }
        else if self.has_media() { "imported-media-read-only" }
        else { "snapshot-read-only" }
    }

    fn verify(&self) -> Result<(), ApiError> {
        match self {
            Self::Snapshot(snapshot) => snapshot
                .verify()
                .map_err(|_| ApiError(StatusCode::LOCKED, "snapshot_unavailable")),
            #[cfg(target_os = "linux")]
            Self::Media(bundle, snapshot) => bundle.verify().map_err(|_| media_unavailable())
                .and_then(|_| snapshot.verify().map_err(|_| media_unavailable())),
            Self::Guarded(owner,gate,_) => owner.verify().map_err(|_| {gate.lock(); account_locked()}),
            Self::Live(owner) => owner
                .verify()
                .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable")),
        }
    }
}

#[derive(Debug)]
struct ApiError(StatusCode, &'static str);

impl ApiError {
    fn bad_query() -> Self {
        Self(StatusCode::BAD_REQUEST, "bad_query")
    }

    fn store(error: StoreError) -> Self {
        match error {
            StoreError::BadQuery(..) => Self::bad_query(),
            StoreError::TooLarge => {
                Self(StatusCode::PAYLOAD_TOO_LARGE, "archive_response_too_large")
            }
            _ => Self(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable"),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let mut response = (self.0, Json(json!({"error":{"code":self.1}}))).into_response();
        if self.0 == StatusCode::UNAUTHORIZED {
            response
                .headers_mut()
                .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        }
        response
    }
}

fn one(headers: &HeaderMap, key: HeaderName) -> Option<&HeaderValue> {
    let all = headers.get_all(key);
    let mut iter = all.iter();
    let first = iter.next()?;
    iter.next().is_none().then_some(first)
}

fn account_locked() -> ApiError { ApiError(StatusCode::LOCKED, "account_locked") }

fn asset_job_write(host:&Host,path:&str) -> bool {
    #[cfg(target_os = "linux")]
    {host.archive.has_media() && crate::asset_jobs_http::write_route(path)}
    #[cfg(not(target_os = "linux"))]
    {let _=(host,path);false}
}
/// Reject malformed escaped octets before a query decoder can replace them
/// with U+FFFD, which may itself be part of a normalized selector.
pub(crate) fn valid_legacy_query_encoding(query: &str) -> bool {
    let mut data = Vec::with_capacity(query.len());
    let mut bytes = query.bytes();
    while let Some(byte) = bytes.next() {
        if byte != b'%' {
            data.push(byte);
            continue;
        }
        let hex = |ch: u8| (ch as char).to_digit(16).map(|value| value as u8);
        let (Some(high), Some(low)) = (bytes.next().and_then(hex), bytes.next().and_then(hex)) else {
            return false;
        };
        data.push((high << 4) | low);
    }
    std::str::from_utf8(&data).is_ok_and(|s| !s.chars().any(char::is_control))
}

async fn authorize(State(host): State<Host>, mut request: Request, next: Next) -> Response {
    let headers = request.headers();
    let uri = request.uri();
    let write_permission = if host.archive.is_live() && request.method() == axum::http::Method::POST {
        if matches!(uri.path(), "/api/v1/ingest" | "/api/v1/source/ingest") { Some(Permission::Ingest) }
        else if source_jobs_http::write_route(uri.path()) { Some(Permission::SourceJobsManage) }
        else if uri.path() == drafts_http::PATH { Some(Permission::DraftWrite) }
        else if cfg!(unix) && uri.path() == draft_review_http::PATH { Some(Permission::DraftReview) }
        else if uri.path() == client_prefs_http::PATH { Some(Permission::ClientPreferences) }
        else if uri.path() == transcription_http::PATH { Some(Permission::TranscriptionWrite) }
        else if uri.path() == transcription_http::LEGACY_TRANSCRIBE { Some(Permission::TranscriptionRead) }
        else if matches!(uri.path(), notifications_http::PREFERENCES | notifications_http::PULL | notifications_http::ACK | notifications_http::ACTIVATION_TARGET) {
            Some(Permission::NotificationWrite)
        }
        else if uri.path() == crate::settings_http::PATH { Some(Permission::NotificationWrite) }
        else if export_presets_http::write_route(uri.path()) { Some(Permission::ExportPresets) }
        else if import_jobs_http::write_route(uri.path()) {
            Some(Permission::ImportsManage)
        }
        else if campus_preferences_http::write_route(uri.path()) { Some(Permission::CampusSourcePreferenceWrite) }
        else if matches!(uri.path(),campus_scoring_http::CONFIG|campus_scoring_http::RUN) { Some(Permission::CampusScoringWrite) }
        else if campus_registration_http::write_route(uri.path()) { Some(Permission::CampusRegistrationWrite) }
        else if uri.path()==campus_display_http::DISPLAY { Some(Permission::CampusDisplayWrite) }
        else if campus_events_http::write_route(uri.path()) { Some(Permission::CampusEventWrite) }
        else if campus_http::write_route(uri.path()) { Some(Permission::CampusBookmarkWrite) }
        else { metadata_http::write_permission(uri.path()) }
    } else if request.method()==axum::http::Method::POST && asset_job_write(&host,uri.path()) {
        Some(Permission::Media)
    } else {
        None
    };
    let write = write_permission.is_some();
    let guard = host.archive.guarded().filter(|_| uri.path() != "/healthz");
    // This one read-only status route can authenticate while locked. Browser
    // uses separate middleware; no cookie bootstrap/route is changed.
    let lock_status_path=if host.archive.legacy() {"/api/account-safety/status"} else {"/api/v1/account-safety/status"};
    let lock_status=guard.is_some() && uri.path()==lock_status_path;
    let generation = guard.and_then(Gate::generation);
    let webui_enabled = host.addons.webui_dir.is_some();
    let same_origin_ok = webui_enabled
        && headers
            .get(header::ORIGIN)
            .and_then(|v| v.to_str().ok())
            .zip(host.authority.to_str().ok())
            .is_some_and(|(orig, auth)| orig == format!("http://{auth}"));
    let result = if one(headers, header::HOST) != Some(&host.authority)
        || uri.authority().is_some()
        || uri.scheme().is_some()
        || (headers.contains_key(header::ORIGIN) && !same_origin_ok)
        || (host.archive.legacy() && headers.contains_key(header::COOKIE))
        || headers
            .get_all("sec-fetch-site")
            .iter()
            .any(|value| value != "none" && !(webui_enabled && value == "same-origin"))
    {
        Err(ApiError(
            StatusCode::FORBIDDEN,
            "local_native_client_required",
        ))
    } else if uri
        .query()
        .is_some_and(|value| value.len() > MAX_QUERY_BYTES)
    {
        Err(ApiError(StatusCode::URI_TOO_LONG, "query_too_long"))
    } else if !write && request.method() != axum::http::Method::GET {
        Err(ApiError(
            StatusCode::METHOD_NOT_ALLOWED,
            "method_not_allowed",
        ))
    } else if !write
        && (headers.contains_key(header::TRANSFER_ENCODING)
            || headers
                .get_all(header::CONTENT_LENGTH)
                .iter()
                .any(|value| value != "0"))
    {
        Err(ApiError(StatusCode::BAD_REQUEST, "unexpected_body"))
    } else if write && uri.query().is_some()
        && !(write_permission==Some(Permission::ImportsManage)
            && import_jobs_http::raw_import_request(uri.path(),headers)) {
        Err(ApiError::bad_query())
    } else if uri.path() == "/healthz" {
        Ok(None)
    } else if webui_enabled && request.method() == axum::http::Method::GET && !uri.path().starts_with("/api/") {
        Ok(None)
    } else if guard.is_some() && generation.is_none() && !lock_status {
        Err(account_locked())
    } else {
        let auth_plugin = one(headers, header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| host.plugins.authenticate(value));
        let resolved = if auth_plugin.is_some() {
            auth_plugin
        } else if webui_enabled {
            Some(webui_default_plugin())
        } else {
            None
        };
        resolved
            .filter(|plugin| if guard.is_some() {
                plugin.kind == crate::plugins::PluginKind::Desktop && plugin.account_scope.is_some()
                    && (!host.archive.legacy() || plugin.permissions.contains(&Permission::LegacyHttp))
            } else {
                plugin.kind != crate::plugins::PluginKind::ScopedTool
            })
            .ok_or(ApiError(StatusCode::UNAUTHORIZED, "unauthorized"))
            .and_then(|plugin| {
                let campus_reader=plugin.kind.is_campus();
                let campus_route=campus_http::route(uri.path())||campus_events_http::route(uri.path())
                    ||campus_preferences_http::route(uri.path())||campus_scoring_http::route(uri.path())
                    ||campus_display_http::route(uri.path())||campus_registration_http::route(uri.path());
                if (campus_reader && (!host.archive.is_live() || !(
                    campus_route || matches!(uri.path(),"/api/v1/manifest"|"/api/v1/openapi.json"))))
                    || (!campus_reader && campus_route)
                { return Err(ApiError(StatusCode::FORBIDDEN,"permission_required")); }
                let scoped_source = plugin.kind == crate::plugins::PluginKind::ScopedProducer;
                let source_job_read=source_jobs_http::read_route(uri.path());
                let source_job_route=source_job_read || source_jobs_http::write_route(uri.path());
                if (source_job_route && (!scoped_source || !plugin.permissions.contains(&Permission::SourceJobsManage)))
                    || (!scoped_source && matches!(uri.path(), "/api/v1/source/ingest" | "/api/v1/source/checkpoint"))
                    || (scoped_source && (!host.archive.is_live()
                        || (write_permission != Some(Permission::Ingest)
                            && write_permission != Some(Permission::SourceJobsManage) && !source_job_read
                            && !(operations_http::route(uri.path()) && plugin.permissions.contains(&Permission::OperationsRead))
                            && !matches!(uri.path(), "/api/v1/manifest" | "/api/v1/openapi.json" | "/api/v1/source/checkpoint"))))
                {
                    return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
                }
                if write_permission.is_some_and(|p| !plugin.permissions.contains(&p)) {
                    return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
                }
                Ok(Some(plugin))
            })
    };
    let mut response = match result {
        Ok(plugin) => {
            if let Some(plugin) = plugin {
                request.extensions_mut().insert(plugin);
            }
            next.run(request).await
        }
        Err(error) => error.into_response(),
    };
    // Do not publish a buffered handler result from a previous unlock generation.
    if let Some(gate) = guard {
        if generation.is_some() && gate.generation() != generation {
            response=account_locked().into_response();
        }
        response.headers_mut().insert(header::CONNECTION,HeaderValue::from_static("close"));
    }
    if host.archive.legacy() && (response.status().is_client_error() || response.status().is_server_error()) {
        let (parts,_)=response.into_parts();
        let status=parts.status;
        response=(parts,legacy_http::error(status)).into_response();
    }
    if host.archive.legacy() {
        response.headers_mut().insert(header::REFERRER_POLICY,HeaderValue::from_static("no-referrer"));
    }
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    response
}

async fn health() -> Json<Value> {
    Json(json!({"status":"ok"}))
}

async fn manifest(State(host): State<Host>, Extension(plugin): Extension<Plugin>) -> Json<Value> {
    let live = host.archive.is_live();
    let standard = !host.archive.has_media();
    let full = standard && host.archive.guarded().is_none();
    let filtered_export = matches!(&host.archive, Archive::Snapshot(_) | Archive::Live(_))
        && plugin.kind == crate::plugins::PluginKind::Desktop && plugin.account_scope.is_some()
        && plugin.permissions.contains(&Permission::Messages)
        && plugin.permissions.contains(&Permission::MessageExport);
    let events = live && plugin.permissions.contains(&Permission::Events);
    let ingest = live && plugin.permissions.contains(&Permission::Ingest);
    let source = ingest && plugin.kind == crate::plugins::PluginKind::ScopedProducer;
    let mut manifest = json!({
        "api_version": 1,
        "plugin_protocol_version": 1,
        "backend": "local-message-hub-rs",
        "mode": host.archive.mode(),
        "plugin": plugin,
        "openapi": "/api/v1/openapi.json",
        "resources": {
            "conversations": (standard && plugin.permissions.contains(&Permission::Conversations)).then_some("/api/v1/conversations"),
            "message_media": (host.archive.has_media() && plugin.permissions.contains(&Permission::Media)).then_some("/api/v1/messages/media"),
            "messages": (standard && plugin.permissions.contains(&Permission::Messages)).then_some("/api/v1/messages"),
            "annual_activity": (standard && plugin.permissions.contains(&Permission::AnnualReport))
                .then_some("/api/v1/analytics/annual"),
            "pair_activity": (standard && plugin.permissions.contains(&Permission::PairReport))
                .then_some("/api/v1/analytics/pair"),
            "message_export": (standard && plugin.permissions.contains(&Permission::MessageExport)).then_some("/api/v1/messages/export"),
            "filtered_message_export": filtered_export.then_some(export::FILTERED_PATH),
            "search_text_export": filtered_export.then_some(export::search::PATH),
            "filtered_message_sql_export": filtered_export.then_some(export::FILTERED_PATH),
            "filtered_message_xlsx_export": filtered_export.then_some(export::FILTERED_PATH),
            "search": (full && plugin.permissions.contains(&Permission::Messages)).then_some("/api/v1/search"),
            "message_context": (full && plugin.permissions.contains(&Permission::Messages)).then_some("/api/v1/messages/context"),
            "quoted_message_context": (full && plugin.permissions.contains(&Permission::Messages)
                && plugin.account_scope.as_ref().is_none_or(|scope| scope.platform=="wechat"))
                .then_some("/api/v1/messages/quoted-context"),
            "contacts": (full && plugin.permissions.contains(&Permission::Contacts)).then_some("/api/v1/contacts"),
            "event_checkpoint": events.then_some("/api/v1/events/checkpoint"),
            "events": events.then_some("/api/v1/events"),
            "event_stream": events.then_some("/api/v1/events/stream"),
            "ingest": ingest.then_some("/api/v1/ingest"),
            "conversation_metadata": (live && plugin.permissions.contains(&Permission::ConversationEdit)).then_some("/api/v1/metadata/conversation"),
            "contact_identity": (live && plugin.permissions.contains(&Permission::ContactEdit)).then_some("/api/v1/metadata/contact"),
            "metadata_audit": (live && plugin.permissions.contains(&Permission::MetadataAudit)).then_some("/api/v1/metadata/audit")
        }
    });
    if can_lookup_conversation(&host.archive, &plugin) {
        manifest["resources"]["conversation_lookup"] = json!("/api/v1/conversations");
    }
    #[cfg(target_os="linux")]
    if can_read_wecom_display(&host.archive,&plugin) {
        manifest["resources"]["wecom_rendered_display"] =
            json!(crate::wecom_sync::FROZEN_CONVERSATION_DISPLAY);
    }
    if standard && scoped_desktop_read(&plugin).is_ok() {
        manifest["local_discovery"]=json!({
            "mcp":plugin.permissions.contains(&Permission::McpDiscovery).then_some("/api/v1/mcp/discovery"),
            "backup":"/api/v1/backups/capabilities",
            "global_settings_write":false,"provider_send":false,"remote_provider_configuration":false
        });
    }
    if standard && plugin.kind==crate::plugins::PluginKind::Desktop && plugin.account_scope.is_some()
        && plugin.permissions.contains(&Permission::Messages) {
        let conversations=plugin.permissions.contains(&Permission::Conversations);
        manifest["indexed_overview"]=json!({"settings":"/api/v1/settings/effective",
            "accounts":conversations.then_some("/api/v1/accounts/indexed"),
            "source_availability":conversations.then_some("/api/v1/sources/local-availability"),
            "stats":(conversations && plugin.permissions.contains(&Permission::Contacts)).then_some("/api/v1/stats/indexed"),
            "guard_status":host.archive.guarded().is_some().then_some("/api/v1/account-safety/status")});
    }
    if standard && plugin.account_scope.is_some() {
        let can_export=plugin.permissions.contains(&Permission::MessageExport);
        let observed=plugin.permissions.contains(&Permission::Conversations)
            && plugin.permissions.contains(&Permission::Messages);
        if can_export || observed {
            manifest["grouped_reads"] = json!({
                "export_selection":can_export.then_some("/api/v1/messages/export/manifest"),
                "contact_export":(can_export && plugin.permissions.contains(&Permission::Contacts)).then_some("/api/v1/contacts/export"),
                "observed_participants":observed.then_some("/api/v1/conversations/participants"),
                "observed_participants_export":(observed && can_export).then_some("/api/v1/conversations/participants/export"),
                "official_roster":false,
            });
        }
    }
    if source {
        manifest["source_resume"] = json!({
            "checkpoint": "/api/v1/source/checkpoint",
            "ingest": "/api/v1/source/ingest"
        });
    }
    if source && plugin.permissions.contains(&Permission::SourceJobsManage) {
        manifest["source_jobs"] = json!({"start":source_jobs_http::START,"status":source_jobs_http::STATUS,
            "pause":source_jobs_http::PAUSE,"resume":source_jobs_http::RESUME,"cancel":source_jobs_http::CANCEL,
            "complete":source_jobs_http::COMPLETE,"progress":source_jobs_http::PROGRESS,
            "sync_intent":source_jobs_http::SYNC_INTENT,"claim_sync_intent":source_jobs_http::SYNC_CLAIM,
            "background":false});
    }
    if standard && plugin.permissions.contains(&Permission::AnalyticsProfiles) {
        manifest["chat_analytics"] = json!({"private":"/api/v1/analytics/private","groups":"/api/v1/analytics/groups",
            "self_sent_daily":"/api/v1/analytics/self-sent-daily"});
    }
    if standard && plugin.permissions.contains(&Permission::RelationshipRead) {
        manifest["relationships"]=json!({"summary":"/api/v1/analytics/relationships",
            "timeline":"/api/v1/analytics/relationships/timeline",
            "achievements":"/api/v1/analytics/relationships/achievements"});
    }
    if live && crate::operations::has_view(&plugin) {
        manifest["operation_status"]=json!({"path":operations_http::PATH,"raw_logs":false});
    }
    if host.archive.has_media() && plugin.permissions.contains(&Permission::Media) {
        manifest["imported_attachment_verification"]=json!({"list":"/api/v1/messages/media",
            "verify":"/api/v1/messages/media/{id}/verify","reference":"/api/v1/messages/media/{id}/reference",
            "provider_paths":false});
    }
    #[cfg(target_os = "linux")]
    if host.archive.has_media() && plugin.kind == crate::plugins::PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Messages) && plugin.permissions.contains(&Permission::Media)
        && plugin.account_scope.as_ref().is_some_and(|s| matches!(s.platform.as_str(),"wechat"|"qq")) {
        manifest["imported_media_catalog"] = json!({"path":crate::media_catalog_http::PATH,
            "provenance":"imported_catalog_exact_binding","blob_rechecked":false,"provider_paths":false});
    }
    #[cfg(target_os = "linux")]
    if let Some(link) = host.addons.imported_media.as_ref().filter(|link|link.eligible(&plugin)) {
        let bytes = link.bytes_eligible(&plugin);
        manifest["live_imported_media"] = json!({"browse":crate::live_imported_media::BROWSE,
            "reference":crate::live_imported_media::REFERENCE,"bytes":bytes,"provider":false,
            "download":bytes.then_some(crate::live_imported_media::BYTES),
            "max_verified_bytes":bytes.then_some(crate::live_imported_media::MAX_VERIFIED_BYTES)});
    }
    if host.archive.guarded().is_some() && !host.archive.legacy() && plugin.permissions.contains(&Permission::ConversationEdit) {
        manifest["guarded_conversation_metadata"]=json!({"path":"/api/v1/metadata/conversation","write":false});
    }
    if host.archive.has_media() && plugin.permissions.contains(&Permission::Media) {
        manifest["local_asset_jobs"]=json!({"create":"/api/v1/resource-jobs/create","list":"/api/v1/resource-jobs/list",
            "status":"/api/v1/resource-jobs/status","download":"/api/v1/resource-jobs/download",
            "cancel":"/api/v1/resource-jobs/cancel","background":false});
    }
    #[cfg(target_os = "linux")]
    if host.archive.has_media() && plugin.kind == crate::plugins::PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Messages)
        && plugin.permissions.contains(&Permission::Media)
        && plugin.permissions.contains(&Permission::MessageExport)
        && plugin.account_scope.as_ref().is_some_and(|s| matches!(s.platform.as_str(),"wechat"|"qq")) {
        manifest["local_selected_message_bundle"]=json!({
            "create":crate::asset_jobs_http::CREATE_MESSAGE_BUNDLE,
            "create_filtered":crate::asset_jobs_http::CREATE_FILTERED_BUNDLE,
            "filtered_deferred":true,"background":false,"provider_fetch":false,"source":"imported_media"});
    }
    if live && plugin.permissions.contains(&Permission::DraftRead) {
        manifest["local_drafts"] = json!({
            "url":drafts_http::PATH,"can_write":plugin.permissions.contains(&Permission::DraftWrite)
        });
    }
    if live && plugin.permissions.contains(&Permission::LegacyEventsRead) {
        manifest["legacy_incoming_stream"]=json!({"path":live_http::LEGACY_EVENTS,"metadata_only":true,"delivery":false});
    }
    if cfg!(unix) && live && plugin.permissions.contains(&Permission::DraftReview) {
        manifest["local_draft_review"]=json!({"path":draft_review_http::PATH,"requests_only":true,"delivery":false});
    }
    if live && plugin.permissions.contains(&Permission::ClientPreferences) {
        manifest["client_preferences"]=json!({"path":client_prefs_http::PATH,"ui_only":true});
    }
    if live && plugin.permissions.contains(&Permission::TranscriptionRead) {
        manifest["local_transcription"]=json!({"path":transcription_http::PATH,
            "can_write":plugin.permissions.contains(&Permission::TranscriptionWrite),"automatic":false,
            "legacy_manual_status":plugin.permissions.contains(&Permission::LegacyHttp).then_some(transcription_http::LEGACY_STATUS),
            "legacy_manual_lookup":plugin.permissions.contains(&Permission::LegacyHttp).then_some(transcription_http::LEGACY_TRANSCRIBE)});
    }
    if live && plugin.permissions.contains(&Permission::NotificationRead) {
        manifest["local_notifications"] = json!({
            "preferences":notifications_http::PREFERENCES,
            "preview":notifications_http::PREVIEW,
            "stream":live_http::can_stream_notifications(&plugin).then_some(live_http::NOTIFICATION_STREAM),
            "pull":plugin.permissions.contains(&Permission::NotificationWrite).then_some(notifications_http::PULL),
            "ack":plugin.permissions.contains(&Permission::NotificationWrite).then_some(notifications_http::ACK),
            "activation_target":(plugin.permissions.contains(&Permission::NotificationWrite)
                && plugin.permissions.contains(&Permission::Conversations)).then_some(notifications_http::ACTIVATION_TARGET),
            "can_write":plugin.permissions.contains(&Permission::NotificationWrite),
            "delivery":false
        });
    }
    if live && plugin.permissions.contains(&Permission::ExportPresets) {
        manifest["export_presets"]=json!({"list":"/api/v1/exports/presets",
            "details":"/api/v1/exports/presets/details","save":"/api/v1/exports/presets/save",
            "remove":"/api/v1/exports/presets/remove","plan":"/api/v1/exports/presets/plan","automatic":false});
    }
    if live && plugin.permissions.contains(&Permission::ImportsManage) {
        manifest["local_import_jobs"] = json!({"preview":import_jobs_http::PREVIEW,
            "list":import_jobs_http::LIST,"status":import_jobs_http::STATUS,"commit":import_jobs_http::COMMIT,
            "apply_evidence":import_jobs_http::APPLY_EVIDENCE,
            "cancel":import_jobs_http::CANCEL,"background":false,
            "stock_json_stage":import_jobs_http::LEGACY_SUBMIT,
            "stock_json_preflight":import_jobs_http::LEGACY_PREVIEW,
            "stock_csv_stage":import_jobs_http::LEGACY_SUBMIT,
            "stock_csv_preflight":import_jobs_http::LEGACY_PREVIEW});
    }
    if live && plugin.permissions.contains(&Permission::CampusRead) {
        manifest["campus_source_registration"]=json!({
            "list":campus_registration_http::LIST,"can_write":plugin.permissions.contains(&Permission::CampusRegistrationWrite),
            "fetch_supported":false
        });
        manifest["campus_display_profile"]=json!({
            "settings":campus_display_http::DISPLAY,"feed":campus_display_http::FEED,
            "can_write":plugin.permissions.contains(&Permission::CampusDisplayWrite),"visibility_only":true
        });
        manifest["campus_local_scoring"]=json!({
            "status":campus_scoring_http::CONFIG,"items":campus_scoring_http::ITEMS,"run":campus_scoring_http::RUN,
            "can_write":plugin.permissions.contains(&Permission::CampusScoringWrite),"remote_models":false
        });
        manifest["campus_source_preferences"]=json!({"path":"/api/v1/campus/source-preferences",
            "visible_items":"/api/v1/campus/visible-items",
            "can_write":plugin.permissions.contains(&Permission::CampusSourcePreferenceWrite),"inert":true});
        manifest["campus_local_events"]=json!({"path":campus_events_http::EVENTS,
            "can_write":plugin.permissions.contains(&Permission::CampusEventWrite),"offline":true});
        manifest["campus_offline"]=json!({
            "items":campus_http::ITEMS,"offline":true,"bookmarks":false,"fetch":false,
            "models":false,"autosync":false
        });
        manifest["campus_catalog"]=json!({
            "profile":campus_http::PROFILE,"sources":campus_http::SOURCES,"updates":campus_http::UPDATES,
            "preferences_supported":false,"fetch_supported":false,
            "legacy_daily_briefing":campus_http::LEGACY_BRIEFING,
            "legacy_refresh_status":campus_http::LEGACY_REFRESH,"refresh_supported":false
        });
        manifest["campus_local_state"]=json!({
            "bookmarks":campus_http::BOOKMARKS,"digest":campus_http::DIGEST,
            "public_topic_shortlist":campus_http::PUBLIC_SHORTLIST,"article_state":campus_http::DETAIL_STATE,
            "can_write_bookmarks":plugin.permissions.contains(&Permission::CampusBookmarkWrite)
        });
    }
    if live && plugin.permissions.contains(&Permission::MomentsRead) {
        let can_export=plugin.permissions.contains(&Permission::MomentsExport);
        manifest["detached_moments"]=json!({"list":moments_http::LIST,
            "can_export":can_export,"export_archive":can_export.then_some(moments_http::EXPORT_ARCHIVE),
            "offline":true});
    }
    if desktop_contract::eligible(&plugin) {
        manifest["desktop_contract"] = json!(desktop_contract::NATIVE_PATH);
    }
    Json(manifest)
}

async fn openapi() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "application/json; charset=utf-8")],
        include_str!("../openapi.json"),
    )
}

async fn not_found() -> ApiError {
    ApiError(StatusCode::NOT_FOUND, "not_found")
}

async fn run_archive<F>(host: Host, op: F) -> Result<Json<Value>, ApiError>
where
    F: FnOnce(Store) -> Result<Value, StoreError> + Send + 'static,
{
    archive_op(host, op).await.map(Json)
}

async fn archive_op<T, F>(host: Host, op: F) -> Result<T, ApiError>
where
    T: Send + 'static,
    F: FnOnce(Store) -> Result<T, StoreError> + Send + 'static,
{
    let permit = host
        .permits
        .try_acquire_owned()
        .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        host.archive.verify()?;
        let value = op(host.store).map_err(ApiError::store)?;
        host.archive.verify()?;
        Ok(value)
    })
    .await
    .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable"))?
}

/// Reuse only the freshly opened preflight reader for this single request;
/// do not cache readers or verification across requests, scopes or locks.
async fn guarded_messages_op(
    host: Host, query: ArchiveQuery, native_grant: Option<String>,
    project: impl FnOnce(Value) -> Value + Send + 'static,
) -> Result<Value, ApiError> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_busy"))?;
    let Archive::Guarded(archive, gate, _) = host.archive else {
        return Err(ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable"));
    };
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        guarded_read(&archive, &gate, |reader| {
            let page = match native_grant.as_deref() {
                Some(grant_id) => Store::legacy_messages_on(reader, &archive, &query, grant_id),
                None => Store::messages_on(reader, &query),
            };
            page.map(project)
        })
    }).await.map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable"))?
}

/// Compatibility lookup for the guarded bearer listener only; the caller
/// already checked exact grant scope and chose this from its native route.
async fn guarded_legacy_context_op(
    host: Host, query: ContextQuery, project: impl FnOnce(Value) -> Value + Send + 'static,
) -> Result<Json<Value>, ApiError> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_busy"))?;
    let Archive::Guarded(archive, gate, true) = host.archive else {
        return Err(ApiError(StatusCode::NOT_FOUND, "not_found"));
    };
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        guarded_read(&archive, &gate, |reader|
            Store::legacy_context_on(reader, query).map(|row| row.map(project)))
    }).await.map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "archive_unavailable"))??;
    result.map(Json).ok_or(ApiError(StatusCode::NOT_FOUND, "not_found"))
}

fn guarded_read<T>(
    archive: &EventStore,
    gate: &Gate,
    query: impl FnOnce(rusqlite::Connection) -> Result<T, StoreError>,
) -> Result<T, ApiError> {
    let closed = |_| { gate.lock(); account_locked() };
    let reader = archive.read_connection().map_err(closed)?;
    let output = query(reader).map_err(ApiError::store)?;
    // Fresh independent path/identity/verifier checks after all rows are read.
    // The HTTP middleware still fences any result against the gate generation.
    archive.verify().map_err(closed)?;
    Ok(output)
}

async fn status(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
) -> Result<Json<Value>, ApiError> {
    if !plugin
        .permissions
        .iter()
        .any(|permission| *permission != Permission::Ingest)
    {
        return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
    }
    run_archive(host, |store| store.status()).await
}

type ExtractedQuery<T = ArchiveQuery> = Result<Query<T>, QueryRejection>;

fn require<T>(
    plugin: &Plugin,
    permission: Permission,
    query: ExtractedQuery<T>,
) -> Result<T, ApiError> {
    if !plugin.permissions.contains(&permission) {
        return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
    }
    query
        .map(|Query(query)| query)
        .map_err(|_| ApiError::bad_query())
}

fn scoped_query(plugin: &Plugin, mut query: ArchiveQuery) -> Result<ArchiveQuery, ApiError> {
    if let Some(scope) = &plugin.account_scope {
        if !scope.apply(&mut query.platform, &mut query.account_id) {
            return Err(ApiError(StatusCode::FORBIDDEN, "account_scope_mismatch"));
        }
        query.account_scoped = true;
    }
    Ok(query)
}

fn scoped_context(plugin: &Plugin, mut query: ContextQuery) -> Result<ContextQuery, ApiError> {
    if let Some(scope) = &plugin.account_scope {
        if !scope.apply(&mut query.platform, &mut query.account_id) {
            return Err(ApiError(StatusCode::NOT_FOUND, "not_found"));
        }
    }
    Ok(query)
}

fn can_lookup_conversation(archive: &Archive, plugin: &Plugin) -> bool {
    matches!(archive, Archive::Live(_) | Archive::Snapshot(_))
        && plugin.kind == crate::plugins::PluginKind::Desktop
        && plugin.account_scope.is_some()
        && plugin.permissions.contains(&Permission::Conversations)
}

#[cfg(target_os="linux")]
fn can_read_wecom_display(archive:&Archive,plugin:&Plugin)->bool{
    matches!(archive,Archive::Live(_))
        && plugin.kind==crate::plugins::PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Conversations)
        && plugin.account_scope.as_ref().is_some_and(|scope|scope.platform=="wecom")
}

#[cfg(target_os="linux")]
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderedConversationQuery { conversation_id:String }

#[cfg(target_os="linux")]
async fn wecom_rendered_display(
    State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<RenderedConversationQuery>,
)->Result<Json<crate::wecom_sync::FrozenConversationDisplay>,ApiError>{
    if !can_read_wecom_display(&host.archive,&plugin) {return Err(not_found().await);}
    let Archive::Live(archive)=&host.archive else{return Err(not_found().await);};
    let archive=archive.clone();
    let query=require(&plugin,Permission::Conversations,query)?;
    let scope=plugin.account_scope.ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))?;
    if !crate::wecom_sync::display_conversation_id_valid(&query.conversation_id) {
        return Err(ApiError::bad_query());
    }
    archive_op(host,move |_| {
        crate::wecom_sync::frozen_conversation_display(&archive,&scope.account_id,&query.conversation_id)
            .map_err(|_|StoreError::Schema)
    }).await?.map(Json).ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}

async fn conversations(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_query(&plugin, require(&plugin, Permission::Conversations, query)?)?;
    let ordinary = matches!(&host.archive, Archive::Live(_) | Archive::Snapshot(_));
    if ordinary && !query.conversation_id.is_empty() {
        if !can_lookup_conversation(&host.archive, &plugin) {
            return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
        }
        return run_archive(host, move |store| store.exact_conversation(&query)).await;
    }
    run_archive(host, move |store| store.conversations(&query)).await
}
async fn messages(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_query(&plugin, require(&plugin, Permission::Messages, query)?)?;
    #[cfg(target_os="linux")]
    if matches!(&host.archive,Archive::Live(_)) && plugin.kind==crate::plugins::PluginKind::Desktop
        && plugin.account_scope.as_ref().is_some_and(|scope|scope.platform=="wechat")
        && !query.conversation_id.is_empty() {
        return run_archive(host,move|store|store.messages_with_local_wechat_replies(&query)).await;
    }
    run_archive(host, move |store| store.messages(&query)).await
}
async fn search(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_query(&plugin, require(&plugin, Permission::Messages, query)?)?;
    run_archive(host, move |store| store.search(&query)).await
}
async fn export_messages(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<ExportQuery>,
) -> Result<Response, ApiError> {
    let query = require(&plugin, Permission::MessageExport, query)?;
    serve_export(host, plugin, query).await
}

// Both the native and legacy handlers parse and authorize their own wire
// forms before sharing the exact Desktop-account export path.
async fn serve_export(
    host: Host, plugin: Plugin, mut query: ExportQuery,
) -> Result<Response, ApiError> {
    let scope = plugin.account_scope
        .filter(|_| plugin.kind == crate::plugins::PluginKind::Desktop
            && plugin.permissions.contains(&Permission::Messages))
        .ok_or(ApiError(StatusCode::FORBIDDEN, "permission_required"))?;
    if query.platform == "all" || !scope.apply(&mut query.platform, &mut query.account_id) {
        return Err(ApiError(StatusCode::NOT_FOUND, "not_found"));
    }
    let format = query.format;
    let output = archive_op(host, move |store| export::render(store, scope, query)).await?
        .ok_or(ApiError(StatusCode::NOT_FOUND, "not_found"))?;
    Ok(message_export_response(format, output))
}

async fn export_filtered_messages(
    State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<FilteredQuery>,
) -> Result<Response, ApiError> {
    if !matches!(&host.archive, Archive::Snapshot(_) | Archive::Live(_)) {
        return Err(not_found().await);
    }
    let mut query = require(&plugin, Permission::MessageExport, query)?;
    if !plugin.permissions.contains(&Permission::Messages) {
        return Err(ApiError(StatusCode::FORBIDDEN, "permission_required"));
    }
    let scope = export_scope(&plugin, &mut query.platform, &mut query.account_id)?;
    let format = query.format;
    let output = archive_op(host, move |store| export::render_filtered(store, scope, query)).await?
        .ok_or(ApiError(StatusCode::NOT_FOUND, "not_found"))?;
    Ok(message_export_response(format, output))
}

async fn export_search_text(
    State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<export::search::Selection>,
) -> Result<Response, ApiError> {
    if !matches!(&host.archive, Archive::Snapshot(_) | Archive::Live(_)) {
        return Err(not_found().await);
    }
    // The grant is the only selector for platform/account. The form never
    // accepts an account override or falls back to an unscoped search.
    let scope = plugin.account_scope.as_ref()
        .filter(|_| plugin.kind == crate::plugins::PluginKind::Desktop
            && plugin.permissions.contains(&Permission::Messages)
            && plugin.permissions.contains(&Permission::MessageExport))
        .ok_or(ApiError(StatusCode::FORBIDDEN, "permission_required"))?
        .clone();
    let query = require(&plugin, Permission::MessageExport, query)?;
    if !query.valid() {
        return Err(ApiError::bad_query());
    }
    let format = query.format.into();
    let output = archive_op(host, move |store| export::search::render(store, scope, query)).await?;
    Ok(message_export_response(format, output))
}

fn message_export_response(format: export::Format, output: Vec<u8>) -> Response {
    let mut response=([(header::CONTENT_TYPE, format.content_type()),
         (header::CONTENT_DISPOSITION, format.disposition()),
         (header::REFERRER_POLICY, "no-referrer")], output).into_response();
    if format.html() {
        response.headers_mut().insert(HeaderName::from_static("content-security-policy"),
            HeaderValue::from_static("sandbox; default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"));
    }
    response
}

fn export_scope(plugin:&Plugin, platform:&mut String, account:&mut String) -> Result<AccountScope,ApiError> {
    let scope=plugin.account_scope.as_ref()
        .filter(|_|plugin.kind==crate::plugins::PluginKind::Desktop)
        .ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    if platform=="all" || !scope.apply(platform,account) {return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));}
    Ok(scope.clone())
}

async fn export_contacts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<ContactExport>) -> Result<Response,ApiError> {
    let mut q=require(&plugin,Permission::MessageExport,query)?;
    if !plugin.permissions.contains(&Permission::Contacts) {return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));}
    let scope=export_scope(&plugin,&mut q.platform,&mut q.account_id)?;
    let format=q.format;
    let bytes=archive_op(host,move |store| {
        grouped_exports::contact_records(store,scope,q)
            .and_then(|records|grouped_exports::download(records,format,true))
    }).await?;
    Ok(([(header::CONTENT_TYPE,format.mime()),(header::CONTENT_DISPOSITION,format.disposition(true)),
         (header::REFERRER_POLICY,"no-referrer")],bytes).into_response())
}

async fn observed_contacts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<Observed>) -> Result<Response,ApiError> {
    let q=require(&plugin,Permission::Messages,query)?;
    observed_response(host,plugin,q,false).await
}
async fn export_observed_contacts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<Observed>) -> Result<Response,ApiError> {
    let q=require(&plugin,Permission::MessageExport,query)?;
    observed_response(host,plugin,q,true).await
}
async fn observed_response(host:Host,plugin:Plugin,mut query:Observed,download:bool) -> Result<Response,ApiError> {
    if !plugin.permissions.contains(&Permission::Messages) || !plugin.permissions.contains(&Permission::Conversations) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let scope=export_scope(&plugin,&mut query.platform,&mut query.account_id)?;
    let format=query.format;
    let result=archive_op(host,move |store| {
        let response=if download {
            let Some(bytes)=grouped_exports::observed_download(store,scope,query)? else {return Ok(None)};
            ([(header::CONTENT_TYPE,format.mime()),
               (header::CONTENT_DISPOSITION,format.disposition(false)),
               (header::REFERRER_POLICY,"no-referrer")],bytes).into_response()
        } else {
            let Some(records)=grouped_exports::observed_records(store,scope,query)? else {return Ok(None)};
            Json(json!({"source":"observed","complete_roster":false,"total":records.len(),"items":records})).into_response()
        };
        Ok(Some(response))
    }).await?;
    result.ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}
async fn export_manifest(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<Selection>) -> Result<Json<Value>,ApiError> {
    let mut q=require(&plugin,Permission::MessageExport,query)?;
    if !plugin.permissions.contains(&Permission::Messages) {return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));}
    let scope=export_scope(&plugin,&mut q.platform,&mut q.account_id)?;
    archive_op(host,move |store|grouped_exports::describe(store,scope,q)).await?
        .map(Json).ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}

async fn lock_status(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::RawQuery(raw):axum::extract::RawQuery) -> Result<Json<Value>,ApiError> {
    if raw.is_some() {return Err(ApiError::bad_query());}
    if plugin.account_scope.is_none() || plugin.kind!=crate::plugins::PluginKind::Desktop
        || !plugin.permissions.contains(&Permission::Messages) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let gate=host.archive.guarded().cloned().ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))?;
    archive_op(host,move |_|Ok(json!({"configured":true,"locked":gate.generation().is_none(),
        "idle_timeout_seconds":null,"lease_seconds":crate::profile_lock::LEASE.as_secs(),
        "scope":"guarded native local HTTP only","unlock_transport":"private_parent_pipe"}))).await.map(Json)
}

async fn indexed_accounts(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<IndexSelector>) -> Result<Json<Value>,ApiError> {
    let mut q=require(&plugin,Permission::Messages,query)?;
    if !plugin.permissions.contains(&Permission::Conversations) {return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));}
    let scope=export_scope(&plugin,&mut q.platform,&mut q.account_id)?;
    let contacts=plugin.permissions.contains(&Permission::Contacts);
    run_archive(host,move |store|index_overview::accounts(store,scope,contacts)).await
}
async fn indexed_source_availability(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<IndexSelector>) -> Result<Json<Value>,ApiError> {
    let mut q=require(&plugin,Permission::Messages,query)?;
    if !plugin.permissions.contains(&Permission::Conversations) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let scope=export_scope(&plugin,&mut q.platform,&mut q.account_id)?;
    let use_contacts=plugin.permissions.contains(&Permission::Contacts);
    let mode=host.archive.mode();
    run_archive(host,move|store| {
        let items=index_overview::accounts(store,scope.clone(),use_contacts)?;
        let indexed=items["items"].as_array().is_some_and(|rows|!rows.is_empty());
        let source_contract=crate::provider_manifest::local_chat_boundary(&scope.platform);
        Ok(json!({"platform":scope.platform,"account_id":scope.account_id,
            "indexed":indexed,"mode":mode,"basis":"normalized_local_archive",
            "provider_connection_checked":false,"provider_supported_by_this_route":false,
            "has_credentials":null,"can_sync":false,"source_contract":source_contract}))
    }).await
}

async fn indexed_stats(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<IndexSelector>) -> Result<Json<index_overview::Stats>,ApiError> {
    let mut q=require(&plugin,Permission::Messages,query)?;
    if !plugin.permissions.contains(&Permission::Conversations) || !plugin.permissions.contains(&Permission::Contacts) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let scope=export_scope(&plugin,&mut q.platform,&mut q.account_id)?;
    archive_op(host,move |store|index_overview::stats(store,scope)).await.map(Json)
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct GuardedConversationState {
    platform:String,account_id:String,
    #[serde(alias="id")] conversation_id:String,
}
async fn guarded_conversation_state(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<GuardedConversationState>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::ConversationEdit,query)?;
    let scope=plugin.account_scope.filter(|_|plugin.kind==crate::plugins::PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Conversations))
        .ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    if q.platform!=scope.platform || q.account_id!=scope.account_id {
        return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));
    }
    let Archive::Guarded(owner,_,_)=&host.archive else {return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));};
    let owner=owner.clone();
    let target=crate::metadata::Target{id:q.conversation_id,platform:q.platform,account_id:q.account_id};
    // Keep the result nested until the shared archive postflight finishes,
    // then let guard middleware enforce the original unlock generation.
    archive_op(host,move |_| Ok(crate::metadata::state(&owner,&scope,target,crate::metadata::Kind::Conversation))).await?
        .map(Json).map_err(|error| match error {
            crate::metadata::MetaError::Input=>ApiError::bad_query(),
            crate::metadata::MetaError::Missing=>ApiError(StatusCode::NOT_FOUND,"not_found"),
            _=>ApiError(StatusCode::SERVICE_UNAVAILABLE,"metadata_store_unavailable"),
        })
}

fn scoped_desktop_read(plugin:&Plugin)->Result<(),ApiError> {
    if plugin.kind!=crate::plugins::PluginKind::Desktop || plugin.account_scope.is_none()
        || !plugin.permissions.contains(&Permission::Messages) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    Ok(())
}
async fn mcp_discovery(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::RawQuery(raw):axum::extract::RawQuery) -> Result<Json<Value>,ApiError> {
    scoped_desktop_read(&plugin)?;
    if !plugin.permissions.contains(&Permission::McpDiscovery) {return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));}
    if raw.is_some() {return Err(ApiError::bad_query());}
    let available=host.archive.guarded().is_none() && !host.archive.has_media();
    let incoming=host.archive.is_live();
    let separate_http=cfg!(target_os="linux") && available && !incoming;
    run_archive(host,move |_|Ok(json!({"available":available,
        "enabled":false,"launch_check":"client_must_provision_separate_scoped_tool_grant",
        "lifecycle":"explicit_local_stdio","transport":"stdio","mode":"read-only",
        "write_enabled":false,"discovery_only":true,"incoming_supported_by_mode":incoming,
        "http_launch":false,"standalone_snapshot_http_subset":separate_http,
        "configuration_export":false,"provider_access":false}))).await
}
async fn backup_capabilities(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::RawQuery(raw):axum::extract::RawQuery) -> Result<Json<Value>,ApiError> {
    scoped_desktop_read(&plugin)?;
    if raw.is_some() {return Err(ApiError::bad_query());}
    let guarded=host.archive.guarded().is_some();
    run_archive(host,move |_|Ok(json!({"guarded_runtime":guarded,
        "protected_capsule_operator_cli":cfg!(target_os="linux"),
        "http_catalog":false,"http_restore":false,"http_create":false,
        "remote_backup":false,"operator_credentials_required":true,
        "capsules_encrypted_only":true}))).await
}

async fn effective_settings(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::RawQuery(raw):axum::extract::RawQuery) -> Result<Json<Value>,ApiError> {
    if raw.is_some() {return Err(ApiError::bad_query());}
    if plugin.account_scope.is_none() || plugin.kind!=crate::plugins::PluginKind::Desktop
        || !plugin.permissions.contains(&Permission::Messages) {
        return Err(ApiError(StatusCode::FORBIDDEN,"permission_required"));
    }
    let guarded=host.archive.guarded().is_some();
    let live=host.archive.is_live();
    let mode=host.archive.mode();
    let manual_import_jobs=live && plugin.permissions.contains(&Permission::ImportsManage);
    let ingress=match &host.archive {
        Archive::Live(events) if manual_import_jobs=>Some(events.clone()),
        _=>None,
    };
    run_archive(host,move |store| {
        let mut answer=json!({"mode":mode,"guarded":guarded,
        "fts_enabled":store.status()?["fts_enabled"],"provider_connected":false,
        "auto_sync":false,"web_unlock":false,"mcp_write_enabled":false,
        "messages_read":true,"contacts_read":!guarded && plugin.permissions.contains(&Permission::Contacts),
        "statistics":plugin.permissions.contains(&Permission::Conversations) && plugin.permissions.contains(&Permission::Contacts),
        "archived_search":!guarded,
        "message_export":plugin.permissions.contains(&Permission::MessageExport),
        "manual_import_jobs":manual_import_jobs,
        "notification_delivery":false,"pull_notification_preview":live && plugin.permissions.contains(&Permission::NotificationRead)});
        if let Some(events)=ingress {
            let paused=crate::ingress_policy::observe_paused(&events).map_err(|_|StoreError::Schema)?;
            answer["manual_import_admission"]=json!(if paused{"paused"}else{"open"});
        }
        Ok(answer)
    }).await
}

async fn contacts(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_query(&plugin, require(&plugin, Permission::Contacts, query)?)?;
    run_archive(host, move |store| store.contacts(&query)).await
}

fn profile_scope(plugin:&Plugin,platform:Option<&str>,account:Option<&str>) -> Result<AccountScope,ApiError> {
    let scope=plugin.account_scope.as_ref()
        .filter(|_|plugin.kind==crate::plugins::PluginKind::Desktop)
        .ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    if platform.is_some_and(|v|v!=scope.platform.as_str()) || account.is_some_and(|v|v!=scope.account_id.as_str()) {
        return Err(ApiError(StatusCode::NOT_FOUND,"not_found"));
    }
    Ok(scope.clone())
}

async fn private_profiles(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<chat_analytics::ProfileQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::AnalyticsProfiles,query)?;
    let scope=profile_scope(&plugin,q.platform.as_deref(),q.account_id.as_deref())?;
    run_archive(host,move |store|chat_analytics::profiles(store,scope,q,chat_analytics::ProfileKind::Private)).await
}
async fn group_profiles(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<chat_analytics::ProfileQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::AnalyticsProfiles,query)?;
    let scope=profile_scope(&plugin,q.platform.as_deref(),q.account_id.as_deref())?;
    run_archive(host,move |store|chat_analytics::profiles(store,scope,q,chat_analytics::ProfileKind::Group)).await
}
async fn self_sent_daily(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<chat_analytics::DailyQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::AnalyticsProfiles,query)?;
    let scope=profile_scope(&plugin,q.platform.as_deref(),q.account_id.as_deref())?;
    run_archive(host,move |store|chat_analytics::self_sent_daily(store,scope,q)).await
}

fn relationship_scope(plugin:&Plugin,platform:&str,account:&str) -> Result<AccountScope,ApiError> {
    profile_scope(plugin,Some(platform),Some(account)).map_err(|_|ApiError(StatusCode::FORBIDDEN,"permission_required"))
}

async fn relationship_summary(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<relationship::SummaryQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::RelationshipRead,query)?;
    let scope=relationship_scope(&plugin,&q.platform,&q.account_id)?;
    archive_op(host,move|store|relationship::footprint(store,scope,q)).await?
        .map(Json).ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}
async fn relationship_timeline(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<relationship::TimelineQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::RelationshipRead,query)?;
    let scope=relationship_scope(&plugin,&q.platform,&q.account_id)?;
    archive_op(host,move|store|relationship::timeline(store,scope,q)).await?
        .map(Json).ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}
async fn relationship_achievements(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<relationship::AchievementQuery>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::RelationshipRead,query)?;
    let scope=relationship_scope(&plugin,&q.platform,&q.account_id)?;
    archive_op(host,move|store|relationship::achievements(store,scope,q)).await?
        .map(Json).ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}

async fn annual_report_http(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<annual_report::Query>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::AnnualReport,query)?;
    annual_report_scoped(host,plugin,q.into(),annual_report::Detail::Activity).await
}

async fn annual_report_scoped(host:Host,plugin:Plugin,q:annual_report::LegacyQuery,detail:annual_report::Detail)
    -> Result<Json<Value>,ApiError> {
    let scope=plugin.account_scope.filter(|_| plugin.kind==crate::plugins::PluginKind::Desktop)
        .ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    if q.platform.as_deref().is_some_and(|value| value!="all" && value!=scope.platform)
        || q.account_id.as_deref().is_some_and(|value|value!=scope.account_id)
    {return Err(ApiError(StatusCode::FORBIDDEN,"account_scope_mismatch"));}
    archive_op(host,move |store|annual_report::report(store,scope,q,detail)).await.map(Json)
}

async fn pair_report_http(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    query:ExtractedQuery<pair_report::Query>) -> Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::PairReport,query)?;
    pair_report_scoped(host,plugin,q.into(),annual_report::Detail::Activity).await
}

async fn pair_report_scoped(host:Host,plugin:Plugin,q:pair_report::LegacyQuery,detail:annual_report::Detail)
    -> Result<Json<Value>,ApiError> {
    let scope=plugin.account_scope.filter(|_| plugin.kind==crate::plugins::PluginKind::Desktop)
        .ok_or(ApiError(StatusCode::FORBIDDEN,"permission_required"))?;
    if q.platform!=scope.platform || q.account_id!=scope.account_id {
        return Err(ApiError(StatusCode::FORBIDDEN,"account_scope_mismatch"));
    }
    fetch_context(host,move |store|pair_report::report(store,scope,q,detail)).await
}

async fn message_context(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<ContextQuery>,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_context(&plugin, require(&plugin, Permission::Messages, query)?)?;
    let native_desktop = matches!(&host.archive, Archive::Live(_))
        && plugin.kind == crate::plugins::PluginKind::Desktop && plugin.account_scope.is_some();
    fetch_context(host, move |store| {
        if native_desktop { store.native_desktop_message_context(&query) }
        else { store.message_context(&query) }
    }).await
}

async fn quoted_message_context(
    State(host): State<Host>,
    Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<ContextQuery>,
) -> Result<Json<Value>, ApiError> {
    let query = scoped_context(&plugin, require(&plugin, Permission::Messages, query)?)?;
    fetch_context(host, move |store| store.quoted_message_context(&query)).await
}

async fn fetch_context<F>(host: Host, op: F) -> Result<Json<Value>, ApiError>
where
    F: FnOnce(Store) -> Result<Option<Value>, StoreError> + Send + 'static,
{
    let result = run_archive(host, move |store| {
        op(store).map(|value| value.unwrap_or(Value::Null))
    })
    .await?;
    if result.0.is_null() {
        Err(ApiError(StatusCode::NOT_FOUND, "not_found"))
    } else {
        Ok(result)
    }
}

pub fn router(
    address: SocketAddrV4,
    snapshot: Snapshot,
    store: Store,
    plugins: Plugins,
) -> Result<Router, &'static str> {
    router_impl(address, Archive::Snapshot(snapshot), store, plugins)
}

pub(crate) fn guarded_router(address: SocketAddrV4, owner: EventStore, store: Store, plugins: Plugins, gate: Gate, legacy: bool)
    -> Result<Router,&'static str> {
    router_impl(address,Archive::Guarded(owner,gate,legacy),store,plugins)
}

pub fn live_router(
    address: SocketAddrV4,
    events: EventStore,
    store: Store,
    plugins: Plugins,
) -> Result<Router, &'static str> {
    router_impl(address, Archive::Live(events), store, plugins)
}

pub fn live_router_with_webui(
    address: SocketAddrV4,
    events: EventStore,
    store: Store,
    plugins: Plugins,
    webui_dir: std::path::PathBuf,
) -> Result<Router, &'static str> {
    router_impl_with_addons(
        address,
        Archive::Live(events),
        store,
        plugins,
        HostAddons {
            webui_dir: Some(webui_dir),
            ..HostAddons::default()
        },
    )
}

fn webui_default_plugin() -> Plugin {
    use std::collections::BTreeSet;
    let mut permissions = BTreeSet::new();
    for p in [
        Permission::Conversations,
        Permission::Messages,
        Permission::Contacts,
        Permission::MessageExport,
        Permission::McpDiscovery,
        Permission::LegacyHttp,
        Permission::LegacyBrowser,
        Permission::LegacySearch,
        Permission::AnnualReport,
        Permission::RelationshipRead,
        Permission::AnalyticsProfiles,
        Permission::PairReport,
        Permission::MomentsRead,
        Permission::CampusRead,
        Permission::OperationsRead,
    ] {
        permissions.insert(p);
    }
    Plugin {
        id: "local.webui".to_string(),
        kind: crate::plugins::PluginKind::Desktop,
        permissions,
        account_scope: None,
        campus_profile: None,
    }
}

#[cfg(target_os = "linux")]
pub fn live_router_with_imported_media(
    address: SocketAddrV4, events: EventStore, store: Store, plugins: Plugins,
    link: crate::live_imported_media::Link,
) -> Result<Router, &'static str> {
    if !link.matches_archive(&events) { return Err("live media link unavailable"); }
    router_impl_with_addons(address, Archive::Live(events), store, plugins,
        HostAddons{imported_media:Some(link)})
}

#[cfg(target_os = "linux")]
pub fn media_router(
    address: SocketAddrV4, bundle: crate::media_bundle::Bundle, snapshot: Snapshot,
    store: Store, plugins: Plugins,
) -> Result<Router, &'static str> {
    router_impl(address, Archive::Media(bundle, snapshot), store, plugins)
}

#[cfg(target_os = "linux")]
fn media_unavailable() -> ApiError {
    ApiError(StatusCode::SERVICE_UNAVAILABLE, "media_bundle_unavailable")
}

#[cfg(target_os = "linux")]
fn media_query(
    plugin: &Plugin, query: ExtractedQuery<crate::media_bundle::MessageKey>,
) -> Result<crate::media_bundle::MessageKey, ApiError> {
    let query = require(plugin, Permission::Media, query)?;
    let scope = plugin.account_scope.as_ref().filter(|_| plugin.kind == crate::plugins::PluginKind::Desktop
        && plugin.permissions.contains(&Permission::Messages))
        .ok_or(ApiError(StatusCode::FORBIDDEN, "permission_required"))?;
    if query.platform != scope.platform || query.account_id != scope.account_id {
        return Err(ApiError(StatusCode::NOT_FOUND, "not_found"));
    }
    if !query.valid() { return Err(ApiError::bad_query()); }
    Ok(query)
}

#[cfg(target_os = "linux")]
async fn list_media(
    State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: ExtractedQuery<crate::media_bundle::MessageKey>,
) -> Result<Json<Value>, ApiError> {
    let query = media_query(&plugin, query)?;
    let Archive::Media(bundle, _) = &host.archive else { return Err(not_found().await); };
    host.archive.verify()?;
    let data = json!({"items":bundle.list(&query)});
    host.archive.verify()?;
    Ok(Json(data))
}

#[cfg(target_os = "linux")]
async fn verify_media(
    State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::Path(id):axum::extract::Path<String>,
    query:ExtractedQuery<crate::media_bundle::MessageKey>,
) -> Result<Json<Value>,ApiError> {
    let query=media_query(&plugin,query)?;
    verified_attachment(host,query,id).await.map(Json)
}

#[cfg(target_os="linux")]
async fn verified_attachment(host:Host,query:crate::media_bundle::MessageKey,id:String)->Result<Value,ApiError> {
    use sha2::{Digest,Sha256};
    let Archive::Media(bundle,_)=&host.archive else {return Err(not_found().await);};
    if id.len()!=64 || !id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !bundle.list(&query).iter().any(|item|item.id==id) {
        return Err(not_found().await);
    }
    let bundle=bundle.clone();
    archive_op(host,move |_| {
        // Existing reader verifies the full bounded object before any metadata.
        // Return a nested option so archive re-verification always runs first.
        Ok(bundle.read(&query,&id).map(|blob|blob.map(|bytes|json!({
            "id":id,"size":bytes.len(),"sha256":hex::encode(Sha256::digest(&bytes)),
            "verified":true
        }))))
    }).await?.map_err(|_|media_unavailable())?
        .ok_or(ApiError(StatusCode::NOT_FOUND,"not_found"))
}

#[cfg(target_os="linux")]
async fn attachment_reference(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    axum::extract::Path(id):axum::extract::Path<String>,
    query:ExtractedQuery<crate::media_bundle::MessageKey>) -> Result<Json<Value>,ApiError> {
    let query=media_query(&plugin,query)?;
    reference_response(host,query,id).await
}

#[cfg(target_os="linux")]
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ImportedReference {
    source:String,platform:String,account_id:String,conversation_id:String,message_id:String,attachment_id:String,
}

#[cfg(target_os="linux")]
async fn legacy_imported_reference(State(host):State<Host>,Extension(plugin):Extension<Plugin>,
    input:ExtractedQuery<ImportedReference>)->Result<Json<Value>,ApiError> {
    let q=require(&plugin,Permission::Media,input)?;
    if q.source!="imported_bundle" {return Err(ApiError::bad_query());}
    let key=media_query(&plugin,Ok(axum::extract::Query(crate::media_bundle::MessageKey{
        platform:q.platform,account_id:q.account_id,conversation_id:q.conversation_id,message_id:q.message_id,
    })))?;
    reference_response(host,key,q.attachment_id).await
}

#[cfg(target_os="linux")]
async fn reference_response(host:Host,key:crate::media_bundle::MessageKey,id:String)->Result<Json<Value>,ApiError> {
    let verified=verified_attachment(host,key.clone(),id.clone()).await?;
    Ok(Json(json!({"source":"imported_bundle","local_available":true,"available":true,
        "mime_type":"application/octet-stream","size":verified["size"],"sha256":verified["sha256"],
        "selector":{"platform":key.platform,"account_id":key.account_id,
            "conversation_id":key.conversation_id,"message_id":key.message_id,"attachment_id":id},
        "download_route":format!("/api/v1/messages/media/{id}"),"requires_bearer":true,
        "provider_path":false,"blob_verified":true,"persistent_url":false})))
}

#[cfg(target_os = "linux")]
async fn download_media(
    State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    axum::extract::Path(id): axum::extract::Path<String>, headers: HeaderMap,
    query: ExtractedQuery<crate::media_bundle::MessageKey>,
) -> Result<Response, ApiError> {
    let query = media_query(&plugin, query)?;
    let Archive::Media(bundle, _) = &host.archive else { return Err(not_found().await); };
    let bundle = bundle.clone();
    if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !bundle.list(&query).iter().any(|item| item.id == id)
    { return Err(not_found().await); }
    let use_range=headers.contains_key(header::RANGE) && !headers.contains_key(header::IF_RANGE);
    let requested=if use_range {one(&headers,header::RANGE).and_then(|h|h.to_str().ok())} else {None};
    let permit = host.permits.clone().try_acquire_owned().map_err(|_| media_unavailable())?;
    struct Body(Vec<u8>, #[allow(dead_code)] tokio::sync::OwnedSemaphorePermit);
    impl AsRef<[u8]> for Body { fn as_ref(&self) -> &[u8] { &self.0 } }
    let body = tokio::task::spawn_blocking(move || {
        host.archive.verify()?;
        let bytes = bundle.read(&query,&id).map_err(|_| media_unavailable())?
            .ok_or(ApiError(StatusCode::NOT_FOUND, "not_found"))?;
        host.archive.verify()?;
        Ok::<_, ApiError>(axum::body::Bytes::from_owner(Body(bytes, permit)))
    }).await.map_err(|_| media_unavailable())??;
    let size=body.len();
    let (status,body,content_range)=if use_range {
        if let Some(span)=requested.and_then(|value|crate::media_bundle::byte_range(value,size)) {
            let label=format!("bytes {}-{}/{}",span.start,span.end-1,size);
            (StatusCode::PARTIAL_CONTENT,body.slice(span),Some(label))
        } else {
            let mut res=ApiError(StatusCode::RANGE_NOT_SATISFIABLE,"invalid_range").into_response();
            res.headers_mut().insert(header::CONTENT_RANGE,
                HeaderValue::from_str(&format!("bytes */{size}")).map_err(|_|media_unavailable())?);
            res.headers_mut().insert(header::ACCEPT_RANGES,HeaderValue::from_static("bytes"));
            return Ok(res);
        }
    } else {(StatusCode::OK,body,None)};
    let mut res=(status,[(header::CONTENT_TYPE, "application/octet-stream"),
         (header::CONTENT_DISPOSITION, "attachment; filename=hub-attachment.bin"),
         (header::ACCEPT_RANGES, "bytes"),
         (HeaderName::from_static("cross-origin-resource-policy"), "same-origin")], body).into_response();
    if let Some(label)=content_range {
        res.headers_mut().insert(header::CONTENT_RANGE,HeaderValue::from_str(&label).map_err(|_|media_unavailable())?);
    }
    Ok(res)
}

fn router_impl(
    address: SocketAddrV4,
    archive: Archive,
    store: Store,
    plugins: Plugins,
) -> Result<Router, &'static str> {
    router_impl_with_addons(address, archive, store, plugins, HostAddons::default())
}

fn router_impl_with_addons(
    address: SocketAddrV4, archive: Archive, store: Store, plugins: Plugins, addons: HostAddons,
) -> Result<Router, &'static str> {
    if !address.ip().is_loopback() || address.ip().octets() != [127, 0, 0, 1] || address.port() == 0
    {
        return Err("host must use bound 127.0.0.1 TCP address");
    }
    let authority = HeaderValue::from_str(&address.to_string()).map_err(|_| "invalid authority")?;
    let host = Host {
        authority,
        plugins,
        archive,
        store,
        permits: Arc::new(Semaphore::new(MAX_ARCHIVE_WORK)),
        addons,
    };
    let routes = Router::new().route("/healthz", get(health))
        .route(desktop_contract::NATIVE_PATH, get(desktop_contract::native)).fallback(not_found);
    let routes = if host.archive.legacy() { legacy_http::routes(routes) } else {
        routes.route("/api/v1/manifest", get(manifest))
            .route("/api/v1/openapi.json", get(openapi))
            .route("/api/v1/status", get(status))
    };
    let routes = if host.archive.has_media() {
        #[cfg(target_os = "linux")]
        { routes.route("/api/v1/messages/media", get(list_media))
                .route("/api/media",get(list_media)).route("/api/media/resolve",get(legacy_imported_reference))
                .route("/api/v1/messages/media/{id}", get(download_media))
                .route("/api/v1/messages/media/{id}/verify",get(verify_media))
                .route("/api/v1/messages/media/{id}/reference",get(attachment_reference)) }
        #[cfg(not(target_os = "linux"))]
        { routes }
    } else if host.archive.legacy() { routes
    } else if host.archive.guarded().is_some() {
        routes.route("/api/v1/conversations", get(conversations))
            .route("/api/v1/messages", get(messages))
            .route("/api/v1/messages/export", get(export_messages))
            .route("/api/v1/metadata/conversation", get(guarded_conversation_state))
    } else {
        routes.route("/api/v1/conversations", get(conversations))
            .route("/api/v1/messages", get(messages))
            .route("/api/v1/messages/export", get(export_messages))
            .route(export::FILTERED_PATH, get(export_filtered_messages))
            .route(export::search::PATH, get(export_search_text))
            .route("/api/v1/messages/context", get(message_context))
            .route("/api/v1/messages/quoted-context", get(quoted_message_context))
            .route("/api/v1/search", get(search))
            .route("/api/v1/contacts", get(contacts))
    };
    let routes=if host.archive.has_media() || host.archive.legacy() {routes}
        else {routes.route("/api/v1/accounts/indexed",get(indexed_accounts))
            .route("/api/v1/sources/local-availability",get(indexed_source_availability))
            .route("/api/mcp",get(mcp_discovery)).route("/api/v1/mcp/discovery",get(mcp_discovery))
            .route("/api/v1/backups/capabilities",get(backup_capabilities))
            .route("/api/v1/stats/indexed",get(indexed_stats))
            .route("/api/v1/settings/effective",get(effective_settings))};
    let routes=if host.archive.guarded().is_some() && !host.archive.legacy() {
        routes.route("/api/v1/account-safety/status",get(lock_status))
    } else {routes};
    let routes=if host.archive.has_media() || host.archive.legacy() {routes}
        else {routes.route("/api/v1/messages/export/manifest",get(export_manifest))
            .route("/api/v1/contacts/export",get(export_contacts))
            .route("/api/v1/conversations/participants",get(observed_contacts))
            .route("/api/v1/conversations/participants/export",get(export_observed_contacts))};
    let routes=if host.archive.has_media() || host.archive.legacy() {routes}
        else {routes.route("/api/v1/analytics/annual",get(annual_report_http))
            .route("/api/v1/analytics/pair",get(pair_report_http))
            .route("/api/v1/analytics/private",get(private_profiles))
            .route("/api/v1/analytics/groups",get(group_profiles))
            .route("/api/v1/analytics/self-sent-daily",get(self_sent_daily))
            .route("/api/v1/analytics/relationships",get(relationship_summary))
            .route("/api/v1/analytics/relationships/timeline",get(relationship_timeline))
            .route("/api/v1/analytics/relationships/achievements",get(relationship_achievements))};
    let routes=if matches!(&host.archive,Archive::Live(_)) {
        legacy_http::native_organizer_reads(routes)
    } else {routes};
    let routes = if matches!(&host.archive, Archive::Live(_)) && host.addons.webui_dir.is_some() {
        legacy_http::webui_compat_routes(routes)
    } else {
        routes
    };
    #[cfg(target_os="linux")]
    let routes=if matches!(&host.archive,Archive::Live(_)) {
        routes.route(crate::wecom_sync::FROZEN_CONVERSATION_DISPLAY,get(wecom_rendered_display))
    } else {routes};
    let mut routes = routes.with_state(host.clone());
    if let Archive::Live(events) = &host.archive {
        routes = routes.merge(live_http::router(LiveHttp::new(events.clone())))
            .merge(metadata_http::router(events.clone()))
            .merge(drafts_http::router(events.clone()))
            .merge(client_prefs_http::router(events.clone()));
        if host.addons.webui_dir.is_none() {
            routes = routes.merge(crate::settings_http::router(events.clone()));
        }
        routes = routes.merge(transcription_http::router(events.clone()))
            .merge(export_presets_http::router(events.clone()))
            .merge(moments_http::router(events.clone()))
            .merge(notifications_http::router(events.clone()))
            .merge(import_jobs_http::router(events.clone()))
            .merge(source_jobs_http::router(events.clone()))
            .merge(operations_http::router(events.clone()))
            .merge(campus_http::router(events.clone()))
            .merge(campus_events_http::router(events.clone()))
            .merge(campus_preferences_http::router(events.clone()))
            .merge(campus_scoring_http::router(events.clone()))
            .merge(campus_display_http::router(events.clone()))
            .merge(campus_registration_http::router(events.clone()));
        #[cfg(unix)]
        { routes=routes.merge(draft_review_http::router(events.clone())); }

    }
    #[cfg(target_os = "linux")]
    if let Archive::Media(bundle,snapshot)=&host.archive {
        routes=routes.merge(crate::asset_jobs_http::router(bundle.clone(),snapshot.clone()))
            .merge(crate::media_catalog_http::router(bundle.clone(),snapshot.clone()));
    }
    #[cfg(target_os = "linux")]
    if let (Archive::Live(events), Some(link)) = (&host.archive, &host.addons.imported_media) {
        routes = routes.merge(crate::live_imported_media::router(events.clone(),link.clone()));
    }
    Ok(routes
        .layer(DefaultBodyLimit::max(MAX_INGEST_BODY))
        .layer(from_fn_with_state(host, authorize)))
}

#[cfg(all(test, target_os = "linux"))]
mod guarded_message_tests {
    use super::*;
    const PASSWORD: &str = "private synthetic guarded read";

    fn private_profile() -> tempfile::TempDir {
        use std::{fs,os::unix::fs::PermissionsExt};
        let path = tempfile::tempdir().unwrap();
        fs::set_permissions(path.path(),fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    #[test]
    fn guarded_reused_preflight_still_disallows_a_changed_verifier_in_flight() {
        let profile = private_profile();
        let archive = EventStore::init_guarded(profile.path(), PASSWORD).unwrap();
        let gate = Gate::default();
        assert!(gate.unlock());
        let result = guarded_read(&archive,&gate,|reader| {
            let query_only: i64 = reader.pragma_query_value(None,"query_only",|r| r.get(0)).unwrap();
            assert_eq!(query_only,1);
            drop(reader);
            let writer = rusqlite::Connection::open(archive.path()).unwrap();
            writer.execute("UPDATE native_profile_lock SET digest=zeroblob(32)",[]).unwrap();
            Ok("must not be returned")
        });
        assert!(matches!(result,Err(ApiError(StatusCode::LOCKED,"account_locked"))));
        assert_eq!(gate.generation(),None);
        assert!(gate.unlock());
        let result = guarded_read(&archive,&gate,|_| -> Result<(), StoreError> {panic!("bad verifier reached query")});
        assert!(matches!(result,Err(ApiError(StatusCode::LOCKED,"account_locked"))));
    }

    #[test]
    fn guarded_reused_preflight_still_disallows_archive_replacement_in_flight() {
        let profile = private_profile();
        let replacement = private_profile();
        let archive = EventStore::init_guarded(profile.path(), PASSWORD).unwrap();
        let other = EventStore::init_guarded(replacement.path(), PASSWORD).unwrap();
        let gate = Gate::default();
        assert!(gate.unlock());
        let result = guarded_read(&archive,&gate,|reader| {
            drop(reader);
            std::fs::rename(other.path(),archive.path()).unwrap();
            Ok("stale archive result")
        });
        assert!(matches!(result,Err(ApiError(StatusCode::LOCKED,"account_locked"))));
        assert_eq!(gate.generation(),None);
    }

    #[test]
    #[ignore = "local SQLite stage microprobe; explicitly run with --nocapture"]
    fn guarded_sql_read_stage_probe() {
        use std::time::Instant;
        let profile = private_profile();
        let archive = EventStore::init_guarded(profile.path(), PASSWORD).unwrap();
        {
            let mut writer = rusqlite::Connection::open(archive.path()).unwrap();
            let tx=writer.transaction().unwrap();
            for i in 0..125 {
                tx.execute("INSERT INTO messages (platform,account_id,conversation_id,id,timestamp,type,text)
                    VALUES ('qq','bench','room',?1,?2,'text','synthetic performance fixture')",
                    (format!("fixture-{i:03}"),i)).unwrap();
            }
            tx.commit().unwrap();
        }
        let store = Store::open_live(archive.clone()).unwrap();
        let query = ArchiveQuery { platform:"qq".into(), account_id:"bench".into(),
            conversation_id:"room".into(), limit:Some(40), account_scoped:true, ..Default::default() };
        let mut all=[Vec::new(),Vec::new(),Vec::new(),Vec::new(),Vec::new(),Vec::new()];
        let measure = |run: &mut dyn FnMut()| {
            let begin = Instant::now(); run(); begin.elapsed().as_nanos()
        };
        for index in 0..220 {
            let mut row=[0;6];
            for candidate in [index % 2 == 0, index % 2 != 0] {
                if candidate {
                    let before=Instant::now();
                    let db=archive.read_connection().unwrap();
                    row[3]=before.elapsed().as_nanos();
                    let start = Instant::now();
                    let out = Store::messages_on(db,&query).unwrap();
                    assert_eq!(out["total"],125);
                    row[4] = start.elapsed().as_nanos();
                    row[5]=measure(&mut || archive.verify().unwrap());
                } else {
                    row[0]=measure(&mut || archive.verify().unwrap());
                    row[1]=measure(&mut || { let out=store.messages(&query).unwrap(); assert_eq!(out["total"],125); });
                    row[2]=measure(&mut || archive.verify().unwrap());
                }
            }
            if index >=20 {for (samples,ns) in all.iter_mut().zip(row) {samples.push(ns);}}
        }
        for (stage, samples) in ["old_preflight","old_new_reader_and_query","old_postflight",
            "candidate_preflight_reader","candidate_query_on_preflight_reader","candidate_postflight"].iter().zip(&mut all) {
            samples.sort_unstable();
            println!("{stage}: n={} p50_ns={} p95_ns={}",samples.len(),samples[99],samples[189]);
        }
    }
}
