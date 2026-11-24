use crate::{
    events::EventStore,
    metadata::{self, AuditPage, AuditQuery, ContactWrite, ConversationWrite, Kind, MetaError, Target},
    plugins::{AccountScope, Permission, Plugin, PluginKind},
};
use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Extension, Query, RawQuery, State, rejection::{JsonRejection, QueryRejection}},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const CONVERSATION: &str = "/api/v1/metadata/conversation";
pub(crate) const CONTACT: &str = "/api/v1/metadata/contact";
const AUDIT: &str = "/api/v1/metadata/audit";
pub(crate) const LEGACY_CONVERSATION:&str="/api/conversation-metadata";
const LEGACY_CONTACT:&str="/api/contact-identities";
const LEGACY_UNLINK:&str="/api/contact-identities/unlink";
pub(crate) const MAX_BODY: usize = 64 * 1024;

pub(crate) fn write_permission(path: &str) -> Option<Permission> {
    match path {
        CONVERSATION | LEGACY_CONVERSATION => Some(Permission::ConversationEdit),
        CONTACT | LEGACY_CONTACT | LEGACY_UNLINK => Some(Permission::ContactEdit),
        _ => None,
    }
}

#[derive(Clone)]
struct MetaHost {
    events: EventStore,
    permits: Arc<Semaphore>,
}

pub(crate) fn router(events: EventStore) -> Router {
    Router::new()
        .route(CONVERSATION, get(conversation_state).post(conversation_write))
        .route(CONTACT, get(contact_state).post(contact_write))
        .route(AUDIT, get(audit))
        .route(LEGACY_CONVERSATION,get(legacy_conversation_state).post(legacy_conversation))
        .route(LEGACY_CONTACT,axum::routing::post(legacy_contact))
        .route(LEGACY_UNLINK,axum::routing::post(legacy_unlink))
        .layer(DefaultBodyLimit::max(MAX_BODY))
        .with_state(MetaHost { events, permits: Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode, &'static str);

impl Api {
    fn store(error: MetaError) -> Self {
        match error {
            MetaError::Input => Self(StatusCode::BAD_REQUEST, "bad_metadata"),
            MetaError::Missing => Self(StatusCode::NOT_FOUND, "not_found"),
            MetaError::Conflict => Self(StatusCode::CONFLICT, "metadata_revision_conflict"),
            MetaError::Upgrade => Self(StatusCode::SERVICE_UNAVAILABLE, "metadata_upgrade_required"),
            MetaError::Storage => Self(StatusCode::SERVICE_UNAVAILABLE, "metadata_store_unavailable"),
        }
    }

    fn scope(plugin: Plugin, permission: Permission) -> Result<AccountScope, Self> {
        plugin.account_scope.filter(|_| plugin.kind == PluginKind::Desktop
                && plugin.permissions.contains(&permission))
            .ok_or(Self(StatusCode::FORBIDDEN,"permission_required"))
    }

    fn query<T>(input: Result<Query<T>, QueryRejection>) -> Result<T, Self> {
        input.map(|Query(query)| query).map_err(|_| Self(StatusCode::BAD_REQUEST, "bad_query"))
    }

    fn body<T>(input: Result<Json<T>, JsonRejection>) -> Result<T, Self> {
        input.map(|Json(body)| body).map_err(|error| match error.status() {
            StatusCode::PAYLOAD_TOO_LARGE => Self(StatusCode::PAYLOAD_TOO_LARGE, "metadata_body_too_large"),
            StatusCode::UNSUPPORTED_MEDIA_TYPE => Self(StatusCode::UNSUPPORTED_MEDIA_TYPE, "json_required"),
            _ => Self(StatusCode::BAD_REQUEST, "bad_metadata"),
        })
    }
}

impl IntoResponse for Api {
    fn into_response(self) -> Response { (self.0,Json(json!({"error":{"code":self.1}}))).into_response() }
}

async fn run<T: Send + 'static>(
    host: MetaHost, operation: impl FnOnce(EventStore) -> Result<T,MetaError> + Send + 'static,
) -> Result<Json<T>,Api> {
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE,"metadata_store_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        operation(host.events).map(Json).map_err(Api::store)
    }).await.map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE,"metadata_store_unavailable"))?
}

async fn conversation_state(
    State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<Target>,QueryRejection>,
) -> Result<Json<Value>,Api> {
    let scope = Api::scope(plugin,Permission::ConversationEdit)?;
    let target = Api::query(query)?;
    run(host, move |store| metadata::state(&store,&scope,target,Kind::Conversation)).await
}

async fn contact_state(
    State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<Target>,QueryRejection>,
) -> Result<Json<Value>,Api> {
    let scope = Api::scope(plugin,Permission::ContactEdit)?;
    let target = Api::query(query)?;
    run(host, move |store| metadata::state(&store,&scope,target,Kind::Contact)).await
}

async fn conversation_write(
    State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<ConversationWrite>,JsonRejection>,
) -> Result<Json<Value>,Api> {
    let scope = Api::scope(plugin,Permission::ConversationEdit)?;
    let body = Api::body(body)?;
    run(host, move |store| metadata::write_conversation(&store,&scope,body)).await
}

async fn contact_write(
    State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<ContactWrite>,JsonRejection>,
) -> Result<Json<Value>,Api> {
    let scope = Api::scope(plugin,Permission::ContactEdit)?;
    let body = Api::body(body)?;
    run(host, move |store| metadata::write_contact(&store,&scope,body)).await
}

async fn audit(
    State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<AuditQuery>,QueryRejection>,
) -> Result<Json<AuditPage>,Api> {
    let scope = Api::scope(plugin,Permission::MetadataAudit)?;
    let q = Api::query(query)?;
    run(host, move |store| metadata::audit(&store,&scope,q)).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyConversationSelector {
    platform: String,
    account_id: String,
    conversation_id: String,
}

async fn legacy_conversation_state(State(host): State<MetaHost>, Extension(plugin): Extension<Plugin>,
    RawQuery(raw): RawQuery, query: Result<Query<LegacyConversationSelector>,QueryRejection>,
) -> Result<Json<Value>,Api> {
    if !plugin.permissions.contains(&Permission::LegacyHttp) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let scope = Api::scope(plugin,Permission::ConversationEdit)?;
    if raw.as_deref().is_some_and(|query| !crate::host::valid_legacy_query_encoding(query)) {
        return Err(Api(StatusCode::BAD_REQUEST,"bad_query"));
    }
    let q = Api::query(query)?;
    let target = exact_target(&scope,q.platform,q.account_id,q.conversation_id)?;
    run(host, move |store| metadata::state(&store,&scope,target,Kind::Conversation)).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyConversation {
    platform:String,account_id:String,conversation_id:String,
    expected_revision:i64,note:Option<String>,tags:Option<Vec<String>>,archived:Option<bool>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyContact {
    platform:String,account_id:String,contact_id:String,
    expected_revision:i64,identity_id:String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyUnlink {
    platform:String,account_id:String,contact_id:String,expected_revision:i64,
}
fn exact_target(scope:&AccountScope,platform:String,account_id:String,id:String)->Result<Target,Api> {
    if platform!=scope.platform||account_id!=scope.account_id {
        return Err(Api(StatusCode::NOT_FOUND,"not_found"));
    }
    Ok(Target{platform,account_id,id})
}
async fn legacy_conversation(State(host):State<MetaHost>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<LegacyConversation>,JsonRejection>)->Result<Json<Value>,Api> {
    let scope=Api::scope(plugin,Permission::ConversationEdit)?;
    let q=Api::body(body)?;
    let target=exact_target(&scope,q.platform,q.account_id,q.conversation_id)?;
    run(host,move|store|metadata::write_conversation(&store,&scope,ConversationWrite{
        target,expected_revision:q.expected_revision,note:q.note,tags:q.tags,archived:q.archived,
    })).await
}
async fn legacy_contact(State(host):State<MetaHost>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<LegacyContact>,JsonRejection>)->Result<Json<Value>,Api> {
    let scope=Api::scope(plugin,Permission::ContactEdit)?;
    let q=Api::body(body)?;
    let target=exact_target(&scope,q.platform,q.account_id,q.contact_id)?;
    run(host,move|store|metadata::write_contact(&store,&scope,ContactWrite{
        target,expected_revision:q.expected_revision,identity_id:Value::String(q.identity_id),
    })).await
}
async fn legacy_unlink(State(host):State<MetaHost>,Extension(plugin):Extension<Plugin>,
    body:Result<Json<LegacyUnlink>,JsonRejection>)->Result<Json<Value>,Api> {
    let scope=Api::scope(plugin,Permission::ContactEdit)?;
    let q=Api::body(body)?;
    let target=exact_target(&scope,q.platform,q.account_id,q.contact_id)?;
    run(host,move|store|metadata::write_contact(&store,&scope,ContactWrite{
        target,expected_revision:q.expected_revision,identity_id:Value::Null,
    })).await
}
