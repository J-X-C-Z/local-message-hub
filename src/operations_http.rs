//! This router must be mounted on ordinary live archives only.
use crate::{events::EventStore, operations, plugins::Plugin};
use axum::{
    Json,Router,
    extract::{Extension, Query, State, rejection::QueryRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value,json};
use std::sync::Arc;
use tokio::sync::Semaphore;

pub(crate) const PATH: &str = "/api/v1/operations/status";
pub(crate) const LEGACY_PATH: &str = "/api/operations";
pub(crate) fn route(path:&str)->bool {matches!(path,PATH|LEGACY_PATH)}

#[derive(Clone)]
struct Host { store: EventStore, permits: Arc<Semaphore> }

pub(crate) fn router(store: EventStore) -> Router {
    Router::new().route(PATH,get(status)).route(LEGACY_PATH,get(status))
        .with_state(Host { store, permits:Arc::new(Semaphore::new(4)) })
}

#[derive(Debug)]
struct Api(StatusCode,&'static str);
impl IntoResponse for Api {
    fn into_response(self) -> Response {
        (self.0,Json(json!({"error":{"code":self.1}}))).into_response()
    }
}
impl From<operations::Error> for Api {
    fn from(e: operations::Error) -> Self {
        use operations::Error::*;
        match e {
            Invalid => Self(StatusCode::BAD_REQUEST,"bad_operation_query"),
            Permission => Self(StatusCode::FORBIDDEN,"permission_required"),
            Missing => Self(StatusCode::NOT_FOUND,"not_found"),
            Upgrade => Self(StatusCode::CONFLICT,"operation_index_unavailable"),
            Storage => Self(StatusCode::SERVICE_UNAVAILABLE,"operation_store_unavailable"),
        }
    }
}

async fn status(
    State(host): State<Host>, Extension(plugin): Extension<Plugin>,
    query: Result<Query<operations::Query>,QueryRejection>,
) -> Result<Json<Value>,Api> {
    if !operations::has_view(&plugin) {
        return Err(Api(StatusCode::FORBIDDEN,"permission_required"));
    }
    let Query(query) = query.map_err(|_| Api(StatusCode::BAD_REQUEST,"bad_operation_query"))?;
    let permit = host.permits.try_acquire_owned()
        .map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE,"operation_store_busy"))?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        operations::status(&host.store,&plugin,query)
    }).await.map_err(|_| Api(StatusCode::SERVICE_UNAVAILABLE,"operation_store_unavailable"))?
        .map(Json).map_err(Api::from)
}
