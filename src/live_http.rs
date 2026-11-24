use crate::{
    events::{EventError, EventKind, EventPage, EventStore, IngestRequest, MessageEvent},
    plugins::{AccountScope, Permission, Plugin, PluginKind},
    notifications,
    source_progress,
};
use axum::{
    Json, Router,
    extract::{
        DefaultBodyLimit, Extension, Query, State,
        rejection::{JsonRejection, QueryRejection},
    },
    http::{HeaderMap, StatusCode},
    response::{
        IntoResponse, Response, Sse,
        sse::{Event, KeepAlive},
    },
    routing::{get, post},
};
use serde::{Deserialize, Serialize, Serializer};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{convert::Infallible, sync::Arc, time::Duration};
use tokio::{sync::Semaphore, task::spawn_blocking, time::Instant};

// Only the managed macOS/Windows ordinary live router installs this extension.
#[derive(Clone)]
struct Drain(tokio::sync::watch::Receiver<bool>);

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub fn with_owner_drain(router:Router,watch:tokio::sync::watch::Receiver<bool>)->Router {
    router.layer(axum::middleware::from_fn_with_state(Drain(watch), reject_after_owner_close))
}
#[cfg(any(target_os = "macos", target_os = "windows"))]
async fn reject_after_owner_close(State(drain):State<Drain>,mut request:axum::extract::Request,
    next:axum::middleware::Next)->Response {
    if *drain.0.borrow() || drain.0.has_changed().is_err() {
        return (StatusCode::SERVICE_UNAVAILABLE,Json(json!({"error":{"code":"backend_draining"}}))).into_response();
    }
    request.extensions_mut().insert(drain);
    next.run(request).await
}

fn drained(drain:&Option<Extension<Drain>>)->bool {
    drain.as_ref().is_some_and(|Extension(d)|*d.0.borrow() || d.0.has_changed().is_err())
}
async fn until_drain<T>(drain:&mut Option<Extension<Drain>>,work:impl std::future::Future<Output=T>)->Option<T> {
    if let Some(Extension(Drain(watch)))=drain {
        tokio::select! {
            biased;
            _=watch.wait_for(|closed|*closed)=>None,
            done=work=>Some(done),
        }
    } else {Some(work.await)}
}

const MAX_STREAMS: usize = 16;
const MAX_DB_WORK: usize = 4;
const MAX_INGEST_BYTES: usize = 530 * 1024;
const MAX_REPLAY: usize = 100;
const STREAM_REPLAY: usize = 100;
const IDLE_POLL: Duration = Duration::from_millis(250);
const STREAM_TTL: Duration = Duration::from_secs(5 * 60);

#[derive(Clone)]
pub struct LiveHttp {
    events: EventStore,
    queries: Arc<Semaphore>,
    streams: Arc<Semaphore>,
}

impl LiveHttp {
    pub fn new(events: EventStore) -> Self {
        Self {
            events,
            queries: Arc::new(Semaphore::new(MAX_DB_WORK)),
            streams: Arc::new(Semaphore::new(MAX_STREAMS)),
        }
    }

    // Shares the existing live HTTP DB work limit rather than spawning a
    // dedicated polling task per stream. The permit follows the blocking work.
    async fn notification_query<T,F>(&self,call:F)->Result<T,ApiError>
    where F:FnOnce(EventStore)->Result<T,notifications::Error>+Send+'static,T:Send+'static {
        let permit=self.queries.clone().try_acquire_owned().map_err(|_|ApiError::Busy)?;
        let store=self.events.clone();
        spawn_blocking(move|| {
            let _permit=permit;
            let result=call(store.clone()).map_err(|error|match error {
                notifications::Error::Input=>ApiError::BadCursor,
                _=>ApiError::Unavailable,
            })?;
            store.verify().map_err(ApiError::Store)?;
            Ok(result)
        }).await.map_err(|_|ApiError::Unavailable)?
    }

    async fn query<T, F>(&self, call: F) -> Result<T, ApiError>
    where
        F: FnOnce(EventStore) -> Result<T, EventError> + Send + 'static,
        T: Send + 'static,
    {
        let permit = self
            .queries
            .clone()
            .try_acquire_owned()
            .map_err(|_| ApiError::Busy)?;
        let events = self.events.clone();
        spawn_blocking(move || {
            let _permit = permit;
            call(events).map_err(ApiError::Store)
        })
        .await
        .map_err(|_| ApiError::Unavailable)?
    }
}

pub(crate) const LEGACY_EVENTS: &str = "/api/events/messages";
pub(crate) const NOTIFICATION_STREAM: &str = "/api/v1/notifications/stream";

pub(crate) fn can_stream_notifications(plugin:&Plugin)->bool {
    plugin.kind==PluginKind::Desktop && plugin.account_scope.is_some()
        && [Permission::Events,Permission::LegacyEventsRead,Permission::NotificationRead]
            .iter().all(|permission|plugin.permissions.contains(permission))
}
pub fn router(live: LiveHttp) -> Router {
    Router::new()
        .route("/api/v1/events/checkpoint", get(checkpoint))
        .route("/api/v1/events", get(replay))
        .route("/api/v1/events/stream", get(stream))
        .route(LEGACY_EVENTS, get(legacy_stream))
        .route(NOTIFICATION_STREAM,get(notification_stream))
        .route(
            "/api/v1/ingest",
            post(ingest).layer(DefaultBodyLimit::max(MAX_INGEST_BYTES)),
        )
        .route("/api/v1/source/checkpoint", get(source_checkpoint))
        .route("/api/v1/source/ingest", post(ingest_source).layer(DefaultBodyLimit::max(MAX_INGEST_BYTES)))
        .with_state(live)
}

#[derive(Debug)]
enum ApiError {
    Permission,
    BadCursor,
    BadQuery,
    BadBody,
    UnsupportedMediaType,
    BodyTooLarge,
    Busy,
    Unavailable,
    Store(EventError),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, code) = match self {
            Self::Permission => (StatusCode::FORBIDDEN, "permission_required"),
            Self::BadCursor => (StatusCode::BAD_REQUEST, "bad_event_cursor"),
            Self::BadQuery => (StatusCode::BAD_REQUEST, "bad_query"),
            Self::BadBody => (StatusCode::BAD_REQUEST, "bad_ingest"),
            Self::UnsupportedMediaType => (StatusCode::UNSUPPORTED_MEDIA_TYPE, "json_required"),
            Self::BodyTooLarge => (StatusCode::PAYLOAD_TOO_LARGE, "ingest_body_too_large"),
            Self::Busy => (StatusCode::SERVICE_UNAVAILABLE, "event_store_busy"),
            Self::Store(EventError::BadRequest(_)) => {
                (StatusCode::BAD_REQUEST, "bad_event_request")
            }
            Self::Store(EventError::SourceCheckpointConflict) => (StatusCode::CONFLICT, "source_checkpoint_conflict"),
            Self::Store(EventError::IngressPaused) => (StatusCode::LOCKED, "message_ingress_paused"),
            Self::Store(EventError::Conflict) => (StatusCode::CONFLICT, "idempotency_conflict"),
            Self::Store(EventError::ScopeUnavailable) => {
                (StatusCode::SERVICE_UNAVAILABLE, "account_event_upgrade_required")
            }
            _ => (StatusCode::SERVICE_UNAVAILABLE, "event_store_unavailable"),
        };
        (status, Json(json!({"error":{"code":code}}))).into_response()
    }
}

fn authorize(plugin: &Plugin, permission: Permission) -> Result<(), ApiError> {
    if plugin.permissions.contains(&permission) {
        Ok(())
    } else {
        Err(ApiError::Permission)
    }
}

fn cursor_namespace(archive: &str, plugin: &Plugin) -> String {
    if let Some(scope) = &plugin.account_scope {
        let encoded = serde_json::to_vec(&["account-event-v1", archive, &plugin.id, &scope.platform, &scope.account_id])
            .expect("strings serialize");
        let digest = Sha256::digest(encoded);
        format!("a-{}", hex::encode(&digest[..16]))
    } else {
        archive.to_owned()
    }
}

fn parse_cursor(archive: &str, raw: &str) -> Result<i64, ApiError> {
    let (generation, raw) = raw.split_once(':').ok_or(ApiError::BadCursor)?;
    if generation != archive {
        return Err(ApiError::BadCursor);
    }
    if raw.is_empty() || raw.len() > 19 || !raw.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(ApiError::BadCursor);
    }
    raw.parse().map_err(|_| ApiError::BadCursor)
}

fn cursor(archive: &str, event_id: i64) -> String {
    format!("{archive}:{event_id}")
}

fn last_event_id(headers: &HeaderMap) -> Result<Option<&str>, ApiError> {
    let values = headers.get_all("last-event-id");
    let mut values = values.iter();
    match values.next() {
        Some(value) if values.next().is_none() => {
            value.to_str().map_err(|_| ApiError::BadCursor).map(Some)
        }
        Some(_) => Err(ApiError::BadCursor),
        None => Ok(None),
    }
}

async fn checkpoint(
    State(live): State<LiveHttp>,
    Extension(plugin): Extension<Plugin>,
) -> Result<Json<Value>, ApiError> {
    authorize(&plugin, Permission::Events)?;
    let namespace = cursor_namespace(live.events.archive_id(), &plugin);
    let scope = plugin.account_scope;
    let checkpoint = live.query(move |events| events.checkpoint_for(scope.as_ref())).await?;
    Ok(Json(json!({"checkpoint":checkpoint,"cursor":cursor(&namespace,checkpoint)})))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayQuery {
    cursor: Option<String>,
    limit: Option<usize>,
}

async fn read_replay(live: &LiveHttp, scope: Option<AccountScope>, after: i64, limit: usize) -> Result<EventPage, ApiError> {
    live.query(move |events| events.replay_for(scope.as_ref(), after, limit))
        .await
        .map_err(|error| {
            // The HTTP operation has validated range-independent replay arguments. Storage
            // rejects a future cursor using the same read snapshot as its page.
            if matches!(error, ApiError::Store(EventError::BadRequest(_))) {
                ApiError::BadCursor
            } else {
                error
            }
        })
}

async fn replay(
    State(live): State<LiveHttp>,
    Extension(plugin): Extension<Plugin>,
    query: Result<Query<ReplayQuery>, QueryRejection>,
) -> Result<Json<ReplayPayload>, ApiError> {
    authorize(&plugin, Permission::Events)?;
    let Query(q) = query.map_err(|_| ApiError::BadQuery)?;
    let archive = cursor_namespace(live.events.archive_id(), &plugin);
    let after = parse_cursor(&archive, q.cursor.as_deref().ok_or(ApiError::BadCursor)?)?;
    let limit = q.limit.unwrap_or(STREAM_REPLAY);
    if !(1..=MAX_REPLAY).contains(&limit) {
        return Err(ApiError::BadQuery);
    }
    let page = read_replay(&live, plugin.account_scope, after, limit).await?;
    Ok(Json(ReplayPayload::new(page, &archive)))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StreamQuery {
    cursor: Option<String>,
}

// Keep the alphabetic order of the historical serde_json::Value maps on
// the wire. Other MessageEvent consumers retain their own serialization.
#[derive(Serialize)]
struct EventPayload<'a> {
    account_id: &'a str,
    conversation_id: &'a str,
    event_id: i64,
    kind: &'a EventKind,
    message_id: &'a str,
    platform: &'a str,
    timestamp: i64,
}

impl<'a> From<&'a MessageEvent> for EventPayload<'a> {
    fn from(item: &'a MessageEvent) -> Self {
        Self {
            account_id: &item.account_id,
            conversation_id: &item.conversation_id,
            event_id: item.event_id,
            kind: &item.kind,
            message_id: &item.message_id,
            platform: &item.platform,
            timestamp: item.timestamp,
        }
    }
}

struct ReplayItems(Vec<MessageEvent>);

impl Serialize for ReplayItems {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_seq(self.0.iter().map(EventPayload::from))
    }
}

#[derive(Serialize)]
struct ReplayPayload {
    checkpoint: i64,
    has_more: bool,
    items: ReplayItems,
    next_after: i64,
    next_cursor: String,
}

impl ReplayPayload {
    fn new(page: EventPage, archive: &str) -> Self {
        Self {
            checkpoint: page.checkpoint,
            has_more: page.has_more,
            items: ReplayItems(page.items),
            next_after: page.next_after,
            next_cursor: cursor(archive, page.next_after),
        }
    }
}

fn wire_event(archive: &str, item: &MessageEvent) -> Option<Event> {
    let name = match item.kind {
        EventKind::Created => "message.created",
        EventKind::Revised => "message.revised",
    };
    Event::default()
        .id(cursor(archive, item.event_id))
        .event(name)
        .json_data(EventPayload::from(item))
        .ok()
}

async fn stream(
    State(live): State<LiveHttp>,
    Extension(plugin): Extension<Plugin>,
    mut drain:Option<Extension<Drain>>,
    headers: HeaderMap,
    query: Result<Query<StreamQuery>, QueryRejection>,
) -> Result<Response, ApiError> {
    authorize(&plugin, Permission::Events)?;
    let Query(q) = query.map_err(|_| ApiError::BadQuery)?;
    let after = last_event_id(&headers)?;
    if q.cursor.is_some() && after.is_some() {
        return Err(ApiError::BadCursor);
    }
    let archive = cursor_namespace(live.events.archive_id(), &plugin);
    let scope = plugin.account_scope;
    let after = q
        .cursor
        .as_deref()
        .or(after)
        .map(|raw| parse_cursor(&archive, raw))
        .transpose()?;
    let slot = live
        .streams
        .clone()
        .try_acquire_owned()
        .map_err(|_| ApiError::Busy)?;
    let (mut next, mut prefetched) = if let Some(after) = after {
        (after, Some(read_replay(&live, scope.clone(), after, STREAM_REPLAY).await?))
    } else {
        let scope = scope.clone();
        (live.query(move |events| events.checkpoint_for(scope.as_ref())).await?, None)
    };
    let expires = Instant::now() + STREAM_TTL;
    let stream = async_stream::stream! {
        let _slot = slot;
        if drained(&drain) {return;}
        let initial_cursor = cursor(&archive, next);
        yield Ok::<_, Infallible>(
            Event::default().event("checkpoint").id(initial_cursor.clone())
                .retry(Duration::from_secs(2)).data(json!({"checkpoint":next,"cursor":initial_cursor}).to_string())
        );
        loop {
            if drained(&drain)||Instant::now() >= expires { break; }
            let page = if let Some(page) = prefetched.take() {
                page
            } else {
                match until_drain(&mut drain,read_replay(&live, scope.clone(), next, STREAM_REPLAY)).await {
                    Some(Ok(page)) => page,
                    Some(Err(ApiError::Busy)) => {
                        if until_drain(&mut drain,tokio::time::sleep(IDLE_POLL)).await.is_none(){break;}
                        continue;
                    }
                    _ => break, // reconnect and obtain the normal status response
                }
            };
            for item in &page.items {
                if drained(&drain) {return;}
                let Some(event) = wire_event(&archive, item) else { return; };
                yield Ok(event);
            }
            next = page.next_after;
            if !page.has_more && until_drain(&mut drain,tokio::time::sleep(IDLE_POLL)).await.is_none(){break;}
        }
    };
    Ok(Sse::new(stream)
        .keep_alive(
            KeepAlive::new()
                .interval(Duration::from_secs(15))
                .text("keep-alive"),
        )
        .into_response())
}

// Compatibility literal uses only the existing account-local created-incoming
// journal, not the archive-global rowid or native created+revised event lane.
async fn legacy_stream(State(live):State<LiveHttp>,Extension(plugin):Extension<Plugin>,
    mut drain:Option<Extension<Drain>>,headers:HeaderMap, query:Result<Query<StreamQuery>,QueryRejection>) -> Result<Response,ApiError> {
    authorize(&plugin,Permission::LegacyEventsRead)?;
    authorize(&plugin,Permission::Events)?;
    let scope=plugin.account_scope.as_ref().filter(|_|plugin.kind==PluginKind::Desktop)
        .cloned().ok_or(ApiError::Permission)?;
    let Query(q)=query.map_err(|_|ApiError::BadQuery)?;
    let header=last_event_id(&headers)?;
    if q.cursor.is_some() && header.is_some() {return Err(ApiError::BadCursor);}
    // Never accept stock bare rowids or native event IDs on this endpoint.
    let namespace=format!("legacy-incoming-{}",cursor_namespace(live.events.archive_id(),&plugin));
    let after=q.cursor.as_deref().or(header).map(|value|parse_cursor(&namespace,value)).transpose()?;
    let slot=live.streams.clone().try_acquire_owned().map_err(|_|ApiError::Busy)?;
    let scope_for_first=scope.clone();
    let first=live.query(move|events| {
        let page=events.incoming(&scope_for_first,after,50)?;
        events.verify()?;
        Ok(page)
    }).await?;
    let expiry=Instant::now()+STREAM_TTL;
    let stream=async_stream::stream! {
        let _slot=slot;
        if drained(&drain) {return;}
        let mut next=after.unwrap_or(first.next_after);
        let mut initial=after.map(|_|first);
        yield Ok::<_,Infallible>(Event::default().event("checkpoint")
            .id(cursor(&namespace,next)).retry(Duration::from_secs(2)).data("{}"));
        loop {
            if drained(&drain)||Instant::now()>=expiry {break;}
            let page=if let Some(first)=initial.take() {first} else {
                let scope=scope.clone();
                match until_drain(&mut drain,live.query(move|events| {
                    let page=events.incoming(&scope,Some(next),50)?;
                    events.verify()?;
                    Ok(page)
                })).await {
                    Some(Ok(page))=>page,
                    Some(Err(ApiError::Busy))=>{
                        if until_drain(&mut drain,tokio::time::sleep(IDLE_POLL)).await.is_none(){break;}
                        continue;
                    }
                    _=>break,
                }
            };
            for item in &page.items {
                if drained(&drain)||Instant::now()>=expiry {return;}
                let data=json!({"platform":scope.platform,"account_id":scope.account_id,
                    "conversation_id":item.conversation_id,"conversation_title":null,"conversation_kind":null});
                yield Ok(Event::default().id(cursor(&namespace,item.event_id))
                    .event("message").data(data.to_string()));
            }
            if drained(&drain){break;}
            if next!=page.next_after {
                next=page.next_after;
                yield Ok(Event::default().event("checkpoint").id(cursor(&namespace,next)).data("{}"));
            }
            if !page.has_more&&until_drain(&mut drain,tokio::time::sleep(Duration::from_secs(1))).await.is_none(){break;}
        }
    };
    Ok(Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)).text("keep-alive")).into_response())
}

// A filtered cursor lane distinct from the bare legacy stream and the
// acknowledged notification pull. Never writes frontiers or OS state.
async fn notification_stream(
    State(live):State<LiveHttp>,Extension(plugin):Extension<Plugin>,
    mut drain:Option<Extension<Drain>>,headers:HeaderMap,
    query:Result<Query<StreamQuery>,QueryRejection>,
)->Result<Response,ApiError> {
    if !can_stream_notifications(&plugin){return Err(ApiError::Permission);}
    let scope=plugin.account_scope.as_ref().cloned().ok_or(ApiError::Permission)?;
    let Query(q)=query.map_err(|_|ApiError::BadQuery)?;
    let header=last_event_id(&headers)?;
    if q.cursor.is_some() && header.is_some(){return Err(ApiError::BadCursor);}
    let namespace=format!("notification-stream-{}",cursor_namespace(live.events.archive_id(),&plugin));
    let after=q.cursor.as_deref().or(header).map(|value|parse_cursor(&namespace,value)).transpose()?;
    let slot=live.streams.clone().try_acquire_owned().map_err(|_|ApiError::Busy)?;
    let initial_scope=scope.clone();
    let mut next=until_drain(&mut drain,live.notification_query(move|store|
        notifications::stream_start(&store,&initial_scope,after)))
        .await.ok_or(ApiError::Unavailable)??;
    let plugin_id=plugin.id;
    let expires=Instant::now()+STREAM_TTL;
    let delay=Duration::from_secs(1);
    let stream=async_stream::stream! {
        let _slot=slot;
        if drained(&drain){return;}
        yield Ok::<_,Infallible>(Event::default().event("checkpoint")
            .id(cursor(&namespace,next)).retry(Duration::from_secs(2)).data("{}"));
        loop {
            if drained(&drain)||Instant::now()>=expires{break;}
            let after=next;
            let scope=scope.clone();
            let plugin_id=plugin_id.clone();
            let page=match until_drain(&mut drain,live.notification_query(move|store|
                notifications::stream_scan(&store,&scope,&plugin_id,after))).await {
                Some(Ok(page))=>page,
                Some(Err(ApiError::Busy))=>{
                    if until_drain(&mut drain,tokio::time::sleep(delay)).await.is_none(){break;}
                    continue;
                }
                _=>break,
            };
            for item in page.items {
                if drained(&drain)||Instant::now()>=expires{return;}
                let Ok(data)=serde_json::to_string(&item) else {return;};
                // Only checkpoints carry IDs. Disconnecting before a page
                // checkpoint permits replay of that uncommitted page.
                yield Ok(Event::default().event("message").data(data));
            }
            if drained(&drain)||Instant::now()>=expires{break;}
            if page.through>next {
                next=page.through;
                yield Ok(Event::default().event("checkpoint").id(cursor(&namespace,next)).data("{}"));
            }
            if !page.has_more && until_drain(&mut drain,tokio::time::sleep(delay)).await.is_none(){break;}
        }
    };
    let mut response=Sse::new(stream)
        .keep_alive(KeepAlive::new().interval(Duration::from_secs(15)).text("keep-alive"))
        .into_response();
    response.headers_mut().insert(axum::http::header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("no-store"));
    Ok(response)
}

async fn ingest(
    State(live): State<LiveHttp>,
    Extension(plugin): Extension<Plugin>,
    body: Result<Json<IngestRequest>, JsonRejection>,
) -> Result<Json<Value>, ApiError> {
    if !matches!(plugin.kind, PluginKind::Producer | PluginKind::ScopedProducer) {
        return Err(ApiError::Permission);
    }
    authorize(&plugin, Permission::Ingest)?;
    let Json(request) = body.map_err(ingest_rejection)?;
    if plugin.account_scope.as_ref().is_some_and(|scope| {
        !request.matches_account(scope)
    }) {
        return Err(ApiError::Permission);
    }
    let receipt = live
        .query(move |events| match plugin.account_scope.as_ref() {
            Some(scope) => events.ingest_scoped(&plugin.id, scope, request),
            None => events.ingest(&plugin.id, request),
        })
        .await?;
    Ok(Json(json!(receipt)))
}

fn ingest_rejection(reason: JsonRejection) -> ApiError {
    match reason.status() {
        StatusCode::PAYLOAD_TOO_LARGE => ApiError::BodyTooLarge,
        StatusCode::UNSUPPORTED_MEDIA_TYPE => ApiError::UnsupportedMediaType,
        _ => ApiError::BadBody,
    }
}

fn source_scope(plugin: &Plugin) -> Result<AccountScope, ApiError> {
    authorize(plugin,Permission::Ingest)?;
    plugin.account_scope.clone().filter(|_|plugin.kind == PluginKind::ScopedProducer)
        .ok_or(ApiError::Permission)
}

async fn source_checkpoint(State(live): State<LiveHttp>, Extension(plugin): Extension<Plugin>,
    uri: axum::http::Uri) -> Result<Json<source_progress::Checkpoint>, ApiError> {
    let scope = source_scope(&plugin)?;
    if uri.query().is_some() { return Err(ApiError::BadQuery); }
    Ok(Json(live.query(move |events| events.source_checkpoint(&plugin.id,&scope)).await?))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceIngestRequest {
    batch: IngestRequest,
    advance: source_progress::Advance,
}

async fn ingest_source(State(live): State<LiveHttp>, Extension(plugin): Extension<Plugin>,
    body: Result<Json<SourceIngestRequest>,JsonRejection>) -> Result<Json<Value>,ApiError> {
    let scope = source_scope(&plugin)?;
    let Json(SourceIngestRequest {batch,advance}) = body.map_err(ingest_rejection)?;
    if !batch.matches_account(&scope) { return Err(ApiError::Permission); }
    let (receipt,source_checkpoint) = live.query(move |events|
        events.ingest_source(&plugin.id,&scope,batch,&advance)).await?;
    Ok(Json(json!({ "receipt":receipt,"source_checkpoint":source_checkpoint })))
}


#[cfg(all(test, any(target_os = "macos", target_os = "windows")))]
mod managed_owner_tests {
    use super::*;
    use axum::{body::{Body, to_bytes}, http::Request};
    use tower::ServiceExt;

    #[tokio::test]
    async fn live_owner_rejects_new_requests_after_loss_or_sender_drop() {
        for drop_sender in [false, true] {
            let (signal, watch) = tokio::sync::watch::channel(false);
            let app = with_owner_drain(Router::new().route("/probe", get(||async {"admitted"})), watch);
            let response = app.clone().oneshot(Request::builder().uri("/probe").body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            if drop_sender { drop(signal); } else { signal.send_replace(true); }
            let response = app.oneshot(Request::builder().uri("/probe").body(Body::empty()).unwrap()).await.unwrap();
            assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
            let body = to_bytes(response.into_body(), 1024).await.unwrap();
            assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(), json!({"error":{"code":"backend_draining"}}));
        }
    }

    #[tokio::test]
    async fn live_stream_wait_ends_on_owner_loss_without_polling_work() {
        let (signal, watch) = tokio::sync::watch::channel(false);
        let mut drain = Some(Extension(Drain(watch)));
        signal.send_replace(true);
        let polled = std::cell::Cell::new(false);
        let result = until_drain(&mut drain, async { polled.set(true); }).await;
        assert!(result.is_none());
        assert!(!polled.get());
    }
}

#[cfg(test)]
mod event_payload_tests {
    use super::*;
    use axum::body::to_bytes;

    fn old_payload(item: &MessageEvent) -> Value {
        json!({
            "event_id":item.event_id, "kind":item.kind, "platform":item.platform,
            "account_id":item.account_id, "conversation_id":item.conversation_id,
            "message_id":item.message_id, "timestamp":item.timestamp
        })
    }

    fn item(ordinal: i64) -> MessageEvent {
        MessageEvent {
            event_id: ordinal,
            kind: if ordinal % 2 == 0 { EventKind::Created } else { EventKind::Revised },
            platform: "wechat".into(),
            account_id: "合成 account".into(),
            conversation_id: "测试\u{2028}\t\r\n".into(),
            message_id: format!(r#"escape \ " 😀 {ordinal}"#),
            timestamp: if ordinal % 2 == 0 { i64::MIN } else { i64::MAX },
        }
    }

    #[tokio::test]
    async fn native_event_json_and_sse_match_historical_wire_bytes() {
        for archive in ["a-012345678901234567890123456789ab", "global-generation"] {
            for size in [0, 1, 100] {
                let page = EventPage {
                    items: (1..=size).map(item).collect(),
                    next_after: size,
                    has_more: size == 100,
                    checkpoint: i64::MAX,
                };
                let old = Json(json!({"items":page.items.iter().map(old_payload).collect::<Vec<_>>(),
                    "next_after":page.next_after, "next_cursor":cursor(archive,page.next_after),
                    "has_more":page.has_more, "checkpoint":page.checkpoint})).into_response();
                let new = Json(ReplayPayload::new(page, archive)).into_response();
                assert_eq!(old.status(), new.status());
                assert_eq!(old.headers(), new.headers());
                assert_eq!(
                    to_bytes(old.into_body(), 1024*1024).await.unwrap(),
                    to_bytes(new.into_body(), 1024*1024).await.unwrap(),
                );
            }
            for ordinal in [0, 1, i64::MAX] {
                let item = item(ordinal);
                let name = match item.kind {
                    EventKind::Created => "message.created",
                    EventKind::Revised => "message.revised",
                };
                let old = Event::default().id(cursor(archive, item.event_id)).event(name)
                    .json_data(old_payload(&item)).unwrap();
                let new = wire_event(archive, &item).unwrap();
                async fn body(event: Event) -> axum::body::Bytes {
                    let stream = async_stream::stream! { yield Ok::<_, Infallible>(event); };
                    to_bytes(Sse::new(stream).into_response().into_body(), 1024*1024).await.unwrap()
                }
                assert_eq!(body(old).await, body(new).await);
            }
        }
    }
}
