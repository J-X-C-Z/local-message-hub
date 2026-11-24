//! Typed deferred filtered exports from the managed imported-media archive.
use super::{hex64, valid_identifier, Client, Error as SessionError, Manifest, ResponseKind};
use crate::{desktop_sdk::Mode, plugins::AccountScope, store::parse_bound};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::sync::{Arc, Weak};
use zeroize::Zeroizing;

const CREATE: &str = "/api/v1/resource-jobs/create-filtered-bundle";
const STATUS: &str = "/api/v1/resource-jobs/status";
const CANCEL: &str = "/api/v1/resource-jobs/cancel";
const DOWNLOAD: &str = "/api/v1/resource-jobs/download";
const MAX_ARTIFACT: usize = 4 * 1024 * 1024 + 64 * 1024;
const MAX_REQUEST: usize = 24 * 1024;
const MAX_REPLY: usize = 2048;

pub(super) fn advertised(manifest: &Manifest) -> bool {
    let Some(mixed) = &manifest.local_selected_message_bundle else { return false };
    let Some(jobs) = &manifest.local_asset_jobs else { return false };
    mixed["create_filtered"].as_str() == Some(CREATE)
        && mixed["filtered_deferred"].as_bool() == Some(true)
        && mixed["background"].as_bool() == Some(false)
        && mixed["provider_fetch"].as_bool() == Some(false)
        && mixed["source"].as_str() == Some("imported_media")
        && jobs["status"].as_str() == Some(STATUS)
        && jobs["cancel"].as_str() == Some(CANCEL)
        && jobs["download"].as_str() == Some(DOWNLOAD)
        && jobs["background"].as_bool() == Some(false)
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] SessionError),
    #[error("filtered export may have been queued; no job ID was verified, do not retry")]
    CreateOutcomeUnknown,
    #[error("cancellation may have taken effect; inspect the same job if possible")]
    CancelOutcomeUnknown,
    #[error("an explicit ready status is required to download this export")]
    NotReady,
}

/// Exact conversation; date strings follow the native reader's bound syntax.
/// A text-only ZIP is allowed only when explicitly selected.
pub struct Filters<'a> {
    pub conversation_id: &'a str,
    pub keyword: Option<&'a str>,
    pub message_type: Option<&'a str>,
    pub start: Option<&'a str>,
    pub end: Option<&'a str>,
    pub allow_text_only: bool,
}

#[derive(Serialize)]
struct Query<'a> {
    platform: &'a str,
    account_id: &'a str,
    conversation_id: &'a str,
    q: &'a str,
    #[serde(rename = "type")]
    message_type: &'a str,
    start: &'a str,
    end: &'a str,
}

#[derive(Serialize)]
struct Create<'a> {
    query: Query<'a>,
    allow_text_only: bool,
    deferred: bool,
}

#[derive(Serialize)]
struct Target<'a> {
    job_id: &'a str,
}

/// No caller-supplied job IDs. Only a checked create reply can mint a handle.
/// Neither the handle nor its ready-byte pin transfers across reconnects.
pub struct Job {
    session: Weak<()>,
    scope: AccountScope,
    id: String,
    ready: Option<Pin>,
}

#[derive(Clone, Copy, Eq, PartialEq)]
struct Pin {
    bytes: usize,
    digest: [u8; 32],
}

#[derive(Debug, Clone, Copy, Eq, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure {
    InvalidFilter,
    SelectionUnavailable,
    HardLimit,
    LocalExportUnavailable,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum State {
    Queued,
    Running,
    Ready { bytes: usize, entries: usize },
    Failed(Failure),
    Cancelled,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub struct Status {
    pub state: State,
    pub expires_in_seconds: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reply {
    job_id: String,
    state: String,
    entries: usize,
    bytes: usize,
    sha256: String,
    expires_in_seconds: u64,
    failure: Option<Failure>,
}

impl Reply {
    fn checked(&self) -> Result<(Status, Option<Pin>), SessionError> {
        if !hex64(&self.job_id) || self.expires_in_seconds > 300 {
            return Err(SessionError::Protocol);
        }
        let (state, pin) = match self.state.as_str() {
            "ready" if self.failure.is_none()
                && (1..=17).contains(&self.entries)
                && (1..=MAX_ARTIFACT).contains(&self.bytes)
                && hex64(&self.sha256) =>
            {
                let mut digest = [0; 32];
                hex::decode_to_slice(&self.sha256, &mut digest)
                    .map_err(|_| SessionError::Protocol)?;
                (State::Ready { bytes: self.bytes, entries: self.entries },
                 Some(Pin { bytes: self.bytes, digest }))
            }
            state if self.entries == 0 && self.bytes == 0 && self.sha256.is_empty() => {
                let state = match (state, self.failure) {
                    ("queued", None) => State::Queued,
                    ("running", None) => State::Running,
                    ("cancelled", None) => State::Cancelled,
                    ("failed", Some(failure)) => State::Failed(failure),
                    _ => return Err(SessionError::Protocol),
                };
                (state, None)
            }
            _ => return Err(SessionError::Protocol),
        };
        Ok((Status { state, expires_in_seconds: self.expires_in_seconds }, pin))
    }
}

impl Client<'_> {
    fn export_scope(&mut self) -> Result<(), SessionError> {
        if !self.filtered_export || self.connection.mode() != Mode::ImportedMedia {
            return Err(SessionError::Unsupported);
        }
        self.live()
    }

    fn export_job_scope(&mut self, job: &Job) -> Result<(), SessionError> {
        self.export_scope()?;
        if !job.session.ptr_eq(&Arc::downgrade(&self.session))
            || job.scope.platform != self.scope.platform
            || job.scope.account_id != self.scope.account_id
        {
            return Err(SessionError::Identity);
        }
        Ok(())
    }

    /// Explicitly queue one filtered local export. No synchronous fallback,
    /// polling, idempotency key, or automatic retry.
    pub fn create_deferred_filtered_export(&mut self, filters: Filters<'_>) -> Result<Job, Error> {
        self.export_scope()?;
        let q = filters.keyword.unwrap_or_default();
        let kind = filters.message_type.unwrap_or_default();
        let start = filters.start.unwrap_or_default();
        let end = filters.end.unwrap_or_default();
        if !valid_identifier(filters.conversation_id, 512) || q.len() > 4096
            || kind.len() > 128 || start.len() > 128 || end.len() > 128
            || [q, kind, start, end].into_iter().any(|s| s.chars().any(char::is_control))
        {
            return Err(SessionError::Input.into());
        }
        let from = parse_bound(start, false).map_err(|_| SessionError::Input)?;
        let to = parse_bound(end, true).map_err(|_| SessionError::Input)?;
        if from.zip(to).is_some_and(|(from, to)| from > to) {
            return Err(SessionError::Input.into());
        }
        let body = Zeroizing::new(serde_json::to_vec(&Create {
            query: Query {
                platform: &self.scope.platform,
                account_id: &self.scope.account_id,
                conversation_id: filters.conversation_id,
                q,
                message_type: kind,
                start,
                end,
            },
            allow_text_only: filters.allow_text_only,
            deferred: true,
        }).map_err(|_| SessionError::Input)?);
        if body.len() > MAX_REQUEST {
            return Err(SessionError::Input.into());
        }
        let reply: Reply = match self.call_json(CREATE, true, Some(body.as_slice()), MAX_REPLY) {
            Ok(reply) => reply,
            Err(error @ SessionError::Status(400..=407 | 409..=499)) => return Err(error.into()),
            Err(_) => return Err(Error::CreateOutcomeUnknown),
        };
        if !matches!(reply.checked(), Ok((Status { state: State::Queued, .. }, None)))
            || self.live().is_err()
        {
            self.active = false;
            return Err(Error::CreateOutcomeUnknown);
        }
        Ok(Job {
            session: Arc::downgrade(&self.session),
            scope: self.scope.clone(),
            id: reply.job_id,
            ready: None,
        })
    }

    /// One explicit owner-bound status read. Only a confirmed ready record can
    /// pin a digest for the subsequent download.
    pub fn filtered_export_status(&mut self, job: &mut Job) -> Result<Status, Error> {
        self.export_job_scope(job)?;
        let previous = job.ready.take();
        let target = format!("{STATUS}?job_id={}", job.id);
        let reply: Reply = self.call(&target, true, MAX_REPLY)?;
        if reply.job_id != job.id {
            self.active = false;
            return Err(SessionError::Identity.into());
        }
        let (status, ready) = reply.checked().inspect_err(|_| self.active = false)?;
        if previous.is_some() && previous != ready {
            self.active = false;
            return Err(SessionError::Protocol.into());
        }
        self.live()?;
        job.ready = ready;
        Ok(status)
    }

    /// One explicit cancellation; clears any cached ready pin before writing.
    /// A successful cancellation has no JSON result (HTTP 204).
    pub fn cancel_filtered_export(&mut self, job: &mut Job) -> Result<(), Error> {
        self.export_job_scope(job)?;
        let body = serde_json::to_vec(&Target { job_id: &job.id })
            .map_err(|_| SessionError::Input)?;
        job.ready = None;
        match self.call_wire(
            CANCEL, true, Some(("application/json", &body)), None, 204, 0,
            ResponseKind::Empty, |_| Ok(()),
        ) {
            Ok(()) => Ok(()),
            Err(error @ SessionError::Status(400..=407 | 409..=499)) => Err(error.into()),
            Err(_) => Err(Error::CancelOutcomeUnknown),
        }
    }

    /// One complete ZIP download after an explicit ready status. Consumes the
    /// pin before transfer. No extraction, file write, range, or auto-retry.
    pub fn download_filtered_export(
        &mut self, job: &mut Job,
    ) -> Result<Zeroizing<Vec<u8>>, Error> {
        self.export_job_scope(job)?;
        let pin = job.ready.take().ok_or(Error::NotReady)?;
        let target = format!("{DOWNLOAD}?job_id={}", job.id);
        Ok(self.call_wire(
            &target, true, None, None, 200, MAX_ARTIFACT,
            ResponseKind::Zip { exact: pin.bytes },
            |bytes| {
                let digest: [u8; 32] = Sha256::digest(bytes.as_slice()).into();
                if digest != pin.digest {
                    return Err(SessionError::Protocol);
                }
                Ok(bytes)
            },
        )?)
    }
}
