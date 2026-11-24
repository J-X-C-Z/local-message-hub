//! Typed native-live import diagnostics and manual jobs. No paths or background work.

use super::{Client as Loopback, Credential, Error as LoopbackError, Post, hex64};
use crate::{
    desktop_sdk::{Connection, Mode},
    events::MAX_BATCH_BYTES,
    import_jobs_http::{APPLY_EVIDENCE, CANCEL, COMMIT, LEGACY_PREVIEW, LEGACY_SUBMIT, LIST, PREVIEW, STATUS},
    plugins::AccountScope,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const MAX_ROWS: usize = 100; // v1 native ingest batch row limit
const MAX_LIST: usize = 50;
const MAX_PREFLIGHT_SAMPLES: usize = 5;
// Five capped samples including worst-case JSON escapes, plus 100 row diagnostics.
const PREFLIGHT_RESPONSE: usize = 32 * 1024;
const CSV_PREFLIGHT_RESPONSE: usize = 2 * 1024;
const JOB_RESPONSE: usize = 1024;
const LIST_RESPONSE: usize = 16 * 1024;
const PREVIEW_REQUEST: usize = MAX_BATCH_BYTES.div_ceil(3) * 4 + 512;

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum Operation {
    Preview,
    StageStockJson,
    StageStockCsv,
    Commit,
    Cancel,
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] LoopbackError),
    #[error("import job is absent or not available under the current grant")]
    Missing,
    #[error("import job state conflict; inspect the original job before taking further action")]
    Conflict,
    #[error("import job quota was reached")]
    Quota,
    #[error("approval does not match this exact job preview or client")]
    Approval,
    #[error("import request may have taken effect ({0:?}); reconcile the same job")]
    OutcomeUnknown(Operation),
}

#[derive(Deserialize)]
struct Advertised {
    preview: String,
    list: String,
    status: String,
    commit: String,
    cancel: String,
    background: bool,
    stock_json_preflight: Option<String>,
    stock_json_stage: Option<String>,
    stock_csv_preflight: Option<String>,
    stock_csv_stage: Option<String>,
    apply_evidence: Option<String>,
}

impl Advertised {
    fn exact(&self) -> bool {
        !self.background
            && self.preview == PREVIEW
            && self.list == LIST
            && self.status == STATUS
            && self.commit == COMMIT
            && self.cancel == CANCEL
    }
}

/// Idempotency key for an explicit staging request. Retain the same key and
/// exact bytes with the same managed archive and grant for recovery. Never
/// generate a new key for a retry.
pub struct RequestId(String);

impl RequestId {
    pub fn parse(value: &str) -> Result<Self, Error> {
        if !crate::import_jobs::request_key(value) {
            return Err(LoopbackError::Input.into());
        }
        Ok(Self(value.to_owned()))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Opaque server job locator, not an authorization or a confirmation digest.
#[derive(Clone, Eq, Ord, PartialEq, PartialOrd, Deserialize)]
#[serde(try_from = "String")]
pub struct JobId(String);

impl JobId {
    pub fn parse(value: &str) -> Result<Self, Error> {
        if !hex64(value) {
            return Err(LoopbackError::Input.into());
        }
        Ok(Self(value.to_owned()))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<String> for JobId {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        if hex64(&value) {
            Ok(Self(value))
        } else {
            Err("invalid import job locator")
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Prepared,
    Committing,
    Committed,
    Cancelled,
    Conflict,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt {
    pub created: usize,
    pub unchanged: usize,
}

#[derive(Debug,Clone,Copy,Eq,PartialEq,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum Evidence { NotCommitting, ReceiptNotObserved, ReceiptVerified }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplyObservation {
    job_id:JobId,
    pub phase:Phase,
    pub evidence:Evidence,
    pub counts:Option<Receipt>,
}
impl ApplyObservation {
    fn valid(&self,id:&JobId)->bool {
        self.job_id==*id&&match self.evidence {
            Evidence::NotCommitting=>self.phase!=Phase::Committing&&self.counts.is_none(),
            Evidence::ReceiptNotObserved=>self.phase==Phase::Committing&&self.counts.is_none(),
            Evidence::ReceiptVerified=>self.phase==Phase::Committing&&self.counts.is_some_and(|counts|
                counts.created.checked_add(counts.unchanged).is_some_and(|n|(1..=MAX_ROWS).contains(&n))),
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Summary {
    job_id: JobId,
    phase: Phase,
    rows: usize,
    created: Option<usize>,
    unchanged: Option<usize>,
}

impl Summary {
    pub fn job_id(&self) -> &JobId {
        &self.job_id
    }
    pub fn phase(&self) -> Phase {
        self.phase
    }
    pub fn rows(&self) -> usize {
        self.rows
    }
    pub fn receipt(&self) -> Option<Receipt> {
        Some(Receipt {
            created: self.created?,
            unchanged: self.unchanged?,
        })
    }

    fn valid(&self) -> bool {
        hex64(self.job_id.as_str())
            && (1..=MAX_ROWS).contains(&self.rows)
            && if self.phase == Phase::Committed {
                self.created
                    .zip(self.unchanged)
                    .and_then(|(created, unchanged)| created.checked_add(unchanged))
                    == Some(self.rows)
            } else {
                self.created.is_none() && self.unchanged.is_none()
            }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireJob {
    job_id: JobId,
    phase: Phase,
    rows: usize,
    created: Option<usize>,
    unchanged: Option<usize>,
    confirmation: Confirmation,
}

struct Confirmation(Zeroizing<String>);

impl<'de> Deserialize<'de> for Confirmation {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        String::deserialize(d).map(|s| Self(Zeroizing::new(s)))
    }
}

pub struct Job {
    session: Arc<()>,
    summary: Summary,
    confirmation: Confirmation,
}

impl Job {
    pub fn summary(&self) -> &Summary {
        &self.summary
    }

    /// Show with the exact job, account, row count and original import selection.
    /// No staged source data is returned by the backend.
    pub fn confirmation_digest(&self) -> &str {
        self.confirmation.0.as_str()
    }

    /// Call only upon explicit operator approval of this job and digest. An
    /// application automatically echoing the digest is not operator approval.
    /// Consuming the job makes the resulting approval refer to exactly this
    /// immutable preview, not to arbitrary future content or jobs.
    pub fn approve(self, reviewed_digest: &str) -> Result<Approval, Error> {
        if !matches!(self.summary.phase, Phase::Prepared | Phase::Committing)
            || !hex64(reviewed_digest)
            || !bool::from(
                self.confirmation
                    .0
                    .as_bytes()
                    .ct_eq(reviewed_digest.as_bytes()),
            )
        {
            return Err(Error::Approval);
        }
        Ok(Approval(self))
    }
}

/// Private exact-job intent, usable for caller-initiated safe recovery under
/// the same client (including its explicit managed-supervisor reconnect).
pub struct Approval(Job);

impl Approval {
    pub fn job_id(&self) -> &JobId {
        self.0.summary.job_id()
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Page {
    items: Vec<Summary>,
    next_after: Option<JobId>,
    has_more: bool,
}

impl Page {
    pub fn items(&self) -> &[Summary] {
        &self.items
    }
    pub fn next_after(&self) -> Option<&JobId> {
        self.next_after.as_ref()
    }
    pub fn has_more(&self) -> bool {
        self.has_more
    }
}

/// Input-derived diagnostics, not a staged job, approval or storage receipt.
/// Samples are private, truncated and should be rendered as untrusted text.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StockJsonPreflight {
    pub total: usize,
    pub valid: usize,
    pub invalid: usize,
    pub errors: Vec<StockJsonRowError>,
    pub sample: Vec<StockJsonSample>,
    platforms: Vec<String>,
    accounts: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StockJsonRowError {
    /// One-based index in the supplied array.
    pub row: usize,
    pub error: StockJsonRowIssue,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StockJsonRowIssue {
    InvalidTextRecord,
    DuplicateMessageId,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StockJsonSample {
    pub conversation_id: String,
    pub sender_name: String,
    pub timestamp: i64,
    pub text: String,
    platform: String,
    account_id: String,
    #[serde(rename = "type")]
    kind: String,
}

impl StockJsonPreflight {
    fn matches_scope(&self, scope: &AccountScope) -> bool {
        let one = |values: &[String], expected: &str| {
            values.len() == 1 && values[0].as_str() == expected
        };
        let confirmed_scope = if self.valid == 0 {
            self.platforms.is_empty() && self.accounts.is_empty()
        } else {
            one(&self.platforms, &scope.platform) && one(&self.accounts, &scope.account_id)
        };
        confirmed_scope && self.sample.iter().all(|row| {
            row.platform == scope.platform && row.account_id == scope.account_id
        })
    }

    fn valid_shape(&self) -> bool {
        self.total <= MAX_ROWS
            && self.invalid <= self.total
            && self.valid == self.total - self.invalid
            && self.errors.len() == self.invalid
            && self.errors.iter().all(|issue| (1..=self.total).contains(&issue.row))
            && self.errors.windows(2).all(|pair| pair[0].row < pair[1].row)
            && self.sample.len() == self.valid.min(MAX_PREFLIGHT_SAMPLES)
            && self.sample.iter().all(|row| {
                !row.conversation_id.is_empty()
                    && row.conversation_id.len() <= 256
                    && !row.conversation_id.chars().any(char::is_control)
                    && row.sender_name.len() <= 96
                    && row.timestamp >= 0
                    && row.kind == "text"
                    && row.text.len() <= 256
            })
    }
}

/// Counts only, for a valid UTF-8 file in the server's explicit-direction
/// stock CSV subset. This report is never a staging receipt or an approval.
pub struct StockCsvPreflight {
    pub total: usize,
    pub valid: usize,
    pub invalid: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CsvPreflightWire {
    total: usize,
    valid: usize,
    invalid: usize,
    #[serde(rename = "errors")]
    _errors: [(); 0],
    platforms: [String; 1],
    accounts: [String; 1],
    #[serde(rename = "sample")]
    _sample: [(); 0],
}

#[derive(Clone, Copy)]
enum StockFormat { Json, Csv }

impl StockFormat {
    fn query(self) -> &'static str {
        match self { Self::Json => "json", Self::Csv => "csv" }
    }

    fn stage_operation(self) -> Operation {
        match self { Self::Json => Operation::StageStockJson, Self::Csv => Operation::StageStockCsv }
    }
}

pub struct Client<'a> {
    wire: Loopback<'a>,
    session: Arc<()>,
    stock_json_preflight: bool,
    stock_json_stage: bool,
    stock_csv_preflight: bool,
    stock_csv_stage: bool,
    apply_evidence: bool,
}

impl<'a> Client<'a> {
    /// Uses only a separately operator-provisioned Desktop grant already
    /// authorized for imports.local.manage on its exact account. Does not
    /// generate, read, publish or alter a grant, or use an arbitrary origin.
    pub fn connect(
        connection: Connection,
        credential: Zeroizing<String>,
        grant_id: String,
        scope: AccountScope,
        is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        if connection.mode() != Mode::NativeLive {
            return Err(LoopbackError::Unsupported.into());
        }
        if !hex64(&credential) {
            return Err(LoopbackError::Input.into());
        }
        let (wire, manifest) = Loopback::connect_exact(
            connection,
            Credential::Installed(credential),
            grant_id,
            scope,
            is_current,
        )?;
        if !manifest.plugin.permissions.iter().any(|p| p == "imports.local.manage") {
            return Err(LoopbackError::Unsupported.into());
        }
        let advertised: Advertised =
            serde_json::from_value(manifest.local_import_jobs.ok_or(LoopbackError::Unsupported)?)
                .map_err(|_| LoopbackError::Protocol)?;
        if !advertised.exact() {
            return Err(LoopbackError::Unsupported.into());
        }
        Ok(Self {
            wire,
            session: Arc::new(()),
            stock_json_preflight: advertised.stock_json_preflight.as_deref() == Some(LEGACY_PREVIEW),
            stock_json_stage: advertised.stock_json_stage.as_deref() == Some(LEGACY_SUBMIT),
            stock_csv_preflight: advertised.stock_csv_preflight.as_deref() == Some(LEGACY_PREVIEW),
            stock_csv_stage: advertised.stock_csv_stage.as_deref() == Some(LEGACY_SUBMIT),
            apply_evidence: advertised.apply_evidence.as_deref() == Some(APPLY_EVIDENCE),
        })
    }

    /// Keep prior approvals usable only within continuation of this logical
    /// client. A later commit must prove the same archive-derived job ID and
    /// digest through a fresh status lookup; reconnect itself does not retry.
    pub fn reconnect(
        self,
        new_connection: Connection,
        is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        let Loopback {
            grant, grant_id, scope, ..
        } = self.wire;
        let Credential::Installed(credential) = grant else {
            return Err(LoopbackError::Identity.into());
        };
        let mut next = Self::connect(new_connection, credential, grant_id, scope, is_current)?;
        next.session = self.session;
        Ok(next)
    }

    /// Reports negotiated availability, not current backend liveness.
    /// Older native-live servers can support staged jobs without this preflight.
    pub fn supports_stock_json_preflight(&self) -> bool {
        self.wire.active && self.stock_json_preflight
    }

    /// Diagnose explicit detached stock JSON text rows without staging, importing,
    /// reading a source path, or contacting a provider. A valid report cannot be
    /// used as a job approval; callers must separately stage and inspect a preview.
    pub fn preflight_stock_json(&mut self, bytes: &[u8]) -> Result<StockJsonPreflight, Error> {
        if !self.stock_json_preflight {
            return Err(LoopbackError::Unsupported.into());
        }
        if bytes.is_empty() || bytes.len() > MAX_BATCH_BYTES {
            return Err(LoopbackError::Input.into());
        }
        let target = self.stock_target(LEGACY_PREVIEW, StockFormat::Json);
        let report: StockJsonPreflight = self.wire.call_typed(
            &target, true, Some(Post::StockPreflight(bytes)), PREFLIGHT_RESPONSE,
        )?;
        if !report.valid_shape() {
            return self.invalid_response(None);
        }
        if !report.matches_scope(&self.wire.scope) {
            self.wire.active = false;
            return Err(LoopbackError::Identity.into());
        }
        Ok(report)
    }

    /// Independently advertised read-only support; no JSON fallback.
    pub fn supports_stock_csv_preflight(&self) -> bool {
        self.wire.active && self.stock_csv_preflight
    }

    /// Submit only explicitly selected detached CSV bytes for read-only
    /// diagnostics. Invalid or out-of-scope rows fail the entire request.
    /// No direction/date inference, staging, or automatic format selection.
    pub fn preflight_stock_csv(&mut self, bytes: &[u8]) -> Result<StockCsvPreflight, Error> {
        if !self.stock_csv_preflight {
            return Err(LoopbackError::Unsupported.into());
        }
        if bytes.is_empty() || bytes.len() > MAX_BATCH_BYTES {
            return Err(LoopbackError::Input.into());
        }
        let target = self.stock_target(LEGACY_PREVIEW, StockFormat::Csv);
        let report: CsvPreflightWire = self.wire.call_typed(
            &target, true, Some(Post::StockPreflight(bytes)), CSV_PREFLIGHT_RESPONSE,
        )?;
        if !(1..=MAX_ROWS).contains(&report.total)
            || report.valid != report.total || report.invalid != 0 {
            return self.invalid_response(None);
        }
        if report.platforms[0].as_str() != self.wire.scope.platform.as_str()
            || report.accounts[0].as_str() != self.wire.scope.account_id.as_str() {
            self.wire.active = false;
            return Err(LoopbackError::Identity.into());
        }
        Ok(StockCsvPreflight { total: report.total, valid: report.valid, invalid: report.invalid })
    }

    /// Negotiated support for explicit idempotent raw stock JSON staging, not
    /// a backend liveness or storage check.
    pub fn supports_stock_json_stage(&self) -> bool {
        self.wire.active && self.stock_json_stage
    }

    /// Stage only the caller's selected bounded bytes: no file lookup,
    /// read-only preflight, automatic approval, import, or fallback.
    ///
    /// Keep the original request key and exact bytes until resolved. If the
    /// outcome is unknown and no job ID was received, call again explicitly
    /// only with those same values under the same managed archive and grant,
    /// after restoring readiness as needed. If the exact job ID is already
    /// known, use the read-only status first.
    /// An exact repeat can return the job's current phase, not just Prepared.
    pub fn stage_stock_json(&mut self, request_id: &RequestId, bytes: &[u8]) -> Result<Job, Error> {
        self.stage_stock(StockFormat::Json, request_id, bytes)
    }

    /// Independently advertised explicit detached CSV staging, not preflight.
    pub fn supports_stock_csv_stage(&self) -> bool {
        self.wire.active && self.stock_csv_stage
    }

    /// Explicit staging only. The prepared/committed distinction and existing
    /// review, approve and commit APIs still apply. No implicit preflight,
    /// fallback or retry. To reconcile uncertainty retain the same format,
    /// archive, grant, original bytes and request key; if known, read the
    /// exact job ID's status before any decision to repeat.
    pub fn stage_stock_csv(&mut self, request_id: &RequestId, bytes: &[u8]) -> Result<Job, Error> {
        self.stage_stock(StockFormat::Csv, request_id, bytes)
    }

    fn stage_stock(&mut self, format: StockFormat, request_id: &RequestId, bytes: &[u8])
        -> Result<Job, Error>
    {
        let supported = match format {
            StockFormat::Json => self.stock_json_stage,
            StockFormat::Csv => self.stock_csv_stage,
        };
        if !supported {
            return Err(LoopbackError::Unsupported.into());
        }
        if bytes.is_empty() || bytes.len() > MAX_BATCH_BYTES {
            return Err(LoopbackError::Input.into());
        }
        let target = self.stock_target(LEGACY_SUBMIT, format);
        let operation = format.stage_operation();
        let reply = self.wire.call_typed(
            &target, true, Some(Post::StockStage { bytes, request_id }), JOB_RESPONSE,
        ).map_err(|err| mutation_error(err, operation))?;
        self.checked(reply, Some(operation))
    }

    fn stock_target(&self, path: &'static str, format: StockFormat) -> String {
        // Scope is derived from the validated grant, not caller argument.
        let mut query = url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("format", format.query())
            .append_pair("platform", &self.wire.scope.platform)
            .append_pair("account", &self.wire.scope.account_id);
        format!("{path}?{}", query.finish())
    }

    /// Explicitly stage bounded detached JSON bytes supplied by the caller.
    /// No file is opened; staging does not ingest messages.
    pub fn preview(&mut self, request_id: &RequestId, bytes: &[u8]) -> Result<Job, Error> {
        let body = preview_body(request_id, bytes)?;
        self.send_mutation(PREVIEW, &body, Operation::Preview)
    }

    pub fn status(&mut self, id: &JobId) -> Result<Job, Error> {
        let target = format!("{STATUS}?job_id={}", id.as_str());
        if !hex64(id.as_str()) {
            return Err(LoopbackError::Input.into());
        }
        let job = self
            .wire
            .call(&target, true, JOB_RESPONSE)
            .map_err(read_error)?;
        let job = self.checked(job, None)?;
        if job.summary.job_id() != id {
            return self.invalid_response(None);
        }
        Ok(job)
    }

    /// Explicit read-only evidence for a stuck committing job. Absence of a
    /// receipt at the read snapshot is not rollback, and cannot retry a job.
    pub fn observe_apply(&mut self,id:&JobId)->Result<ApplyObservation,Error> {
        if !self.apply_evidence {return Err(LoopbackError::Unsupported.into());}
        let target=format!("{APPLY_EVIDENCE}?job_id={}",id.as_str());
        let view:ApplyObservation=self.wire.call(&target,true,JOB_RESPONSE).map_err(read_error)?;
        if !view.valid(id){return self.invalid_response(None);}
        Ok(view)
    }

    /// One bounded metadata-only page; no confirmation or staged data.
    pub fn list(&mut self, limit: usize, after: Option<&JobId>) -> Result<Page, Error> {
        if !(1..=MAX_LIST).contains(&limit)
            || after.is_some_and(|id| !hex64(id.as_str()))
        {
            return Err(LoopbackError::Input.into());
        }
        let mut target = format!("{LIST}?limit={limit}");
        if let Some(id) = after {
            target.push_str("&after=");
            target.push_str(id.as_str());
        }
        let page: Page = self
            .wire
            .call(&target, true, LIST_RESPONSE)
            .map_err(read_error)?;
        let full_cursor = page.items.len() == limit
            && page.next_after.as_ref() == page.items.last().map(Summary::job_id);
        if page.items.len() > limit
            || page.items.iter().any(|item| !item.valid())
            || !page.items.windows(2).all(|pair| pair[0].job_id < pair[1].job_id)
            || after.is_some_and(|id| page.items.first().is_some_and(|first| first.job_id() <= id))
            || (page.has_more && !full_cursor)
            || (!page.has_more && page.next_after.is_some())
        {
            return self.invalid_response(None);
        }
        Ok(page)
    }

    /// Explicit apply/recovery. Recheck the original job after reconnect or
    /// any uncertainty; a different/missing job cannot trigger a POST.
    pub fn commit(&mut self, approval: &Approval) -> Result<Job, Error> {
        if !Arc::ptr_eq(&self.session, &approval.0.session) {
            return Err(Error::Approval);
        }
        let current = self.status(approval.job_id())?;
        if !same_job(&current, &approval.0) {
            return Err(Error::Approval);
        }
        match current.summary.phase {
            Phase::Conflict | Phase::Committed => return Ok(current),
            Phase::Cancelled => return Err(Error::Conflict),
            Phase::Prepared | Phase::Committing => {}
        }
        #[derive(Serialize)]
        struct Input<'a> {
            job_id: &'a str,
            confirmation: &'a str,
        }
        let job = self.mutation(
            COMMIT,
            &Input {
                job_id: approval.job_id().as_str(),
                confirmation: approval.0.confirmation_digest(),
            },
            JOB_RESPONSE,
            Operation::Commit,
        )?;
        if !same_job(&job, &current) || !matches!(job.summary.phase, Phase::Committed | Phase::Conflict) {
            return self.invalid_response(Some(Operation::Commit));
        }
        Ok(job)
    }

    /// Explicit cancel of a selected job before commit. The backend also
    /// checks the phase transactionally if another operator wins the race.
    pub fn cancel(&mut self, selected: &Job) -> Result<Job, Error> {
        if !Arc::ptr_eq(&self.session, &selected.session) {
            return Err(Error::Approval);
        }
        let current = self.status(selected.summary.job_id())?;
        if !same_job(&current, selected) {
            return Err(Error::Approval);
        }
        match current.summary.phase {
            Phase::Cancelled => return Ok(current),
            Phase::Prepared => {}
            _ => return Err(Error::Conflict),
        }
        #[derive(Serialize)]
        struct Input<'a> {
            job_id: &'a str,
        }
        let job = self.mutation(
            CANCEL,
            &Input { job_id: current.summary.job_id().as_str() },
            JOB_RESPONSE,
            Operation::Cancel,
        )?;
        if !same_job(&job, &current) || job.summary.phase != Phase::Cancelled {
            return self.invalid_response(Some(Operation::Cancel));
        }
        Ok(job)
    }

    fn mutation(
        &mut self,
        target: &'static str,
        input: &impl Serialize,
        max_input: usize,
        operation: Operation,
    ) -> Result<Job, Error> {
        let mut body = Zeroizing::new(Vec::with_capacity(max_input));
        serde_json::to_writer(&mut *body, input).map_err(|_| LoopbackError::Input)?;
        if body.len() > max_input {
            return Err(LoopbackError::Input.into());
        }
        self.send_mutation(target, &body, operation)
    }

    fn send_mutation(&mut self, target: &'static str, body: &[u8], operation: Operation)
        -> Result<Job, Error>
    {
        let reply = self
            .wire
            .call_json(target, true, Some(body), JOB_RESPONSE)
            .map_err(|err| mutation_error(err, operation))?;
        self.checked(reply, Some(operation))
    }

    fn checked(&mut self, wire: WireJob, op: Option<Operation>) -> Result<Job, Error> {
        let summary = Summary {
            job_id: wire.job_id,
            phase: wire.phase,
            rows: wire.rows,
            created: wire.created,
            unchanged: wire.unchanged,
        };
        if !summary.valid() || !hex64(wire.confirmation.0.as_str()) {
            return self.invalid_response(op);
        }
        Ok(Job { session: self.session.clone(), summary, confirmation: wire.confirmation })
    }

    fn invalid_response<T>(&mut self, op: Option<Operation>) -> Result<T, Error> {
        self.wire.active = false;
        Err(op.map_or(Error::Session(LoopbackError::Protocol), Error::OutcomeUnknown))
    }
}

// Both variable fields are JSON-safe ASCII: a validated RequestId and
// standard Base64. Encode straight into the zeroizing final wire allocation.
fn preview_body(request_id: &RequestId, bytes: &[u8]) -> Result<Zeroizing<Vec<u8>>, Error> {
    if bytes.is_empty() || bytes.len() > MAX_BATCH_BYTES {
        return Err(LoopbackError::Input.into());
    }
    let key = request_id.as_str();
    if !crate::import_jobs::request_key(key) {
        return Err(LoopbackError::Input.into());
    }
    const START: &[u8] = br#"{"request_id":""#;
    const BETWEEN: &[u8] = br#"","input_base64":""#;
    const END: &[u8] = br#""}"#;
    let encoded_len = bytes.len().div_ceil(3) * 4;
    let len = START.len() + key.len() + BETWEEN.len() + encoded_len + END.len();
    if len > PREVIEW_REQUEST {
        return Err(LoopbackError::Input.into());
    }
    let mut body = Zeroizing::new(Vec::with_capacity(len));
    body.extend_from_slice(START);
    body.extend_from_slice(key.as_bytes());
    body.extend_from_slice(BETWEEN);
    let encoded_at = body.len();
    body.resize(encoded_at + encoded_len, 0);
    let written = STANDARD.encode_slice(bytes, &mut body[encoded_at..])
        .map_err(|_| LoopbackError::Input)?;
    if written != encoded_len {
        return Err(LoopbackError::Input.into());
    }
    body.extend_from_slice(END);
    Ok(body)
}

fn same_job(a: &Job, b: &Job) -> bool {
    a.summary.job_id == b.summary.job_id
        && a.summary.rows == b.summary.rows
        && bool::from(a.confirmation_digest().as_bytes().ct_eq(b.confirmation_digest().as_bytes()))
}

fn read_error(err: LoopbackError) -> Error {
    match err {
        LoopbackError::Status(404) => Error::Missing,
        err => Error::Session(err),
    }
}

fn mutation_error(err: LoopbackError, operation: Operation) -> Error {
    match err {
        LoopbackError::Status(409) => Error::Conflict,
        LoopbackError::Status(429) => Error::Quota,
        LoopbackError::Transport
        | LoopbackError::Protocol
        | LoopbackError::Stale
        | LoopbackError::TooLarge
        | LoopbackError::Status(408 | 500..=599) => Error::OutcomeUnknown(operation),
        err => read_error(err),
    }
}

#[cfg(test)]
mod preview_body_tests {
    use super::*;

    #[test]
    fn direct_preview_body_preserves_legacy_json_and_input_bounds() {
        #[derive(Serialize)]
        struct Old<'a> {
            request_id: &'a str,
            input_base64: &'a str,
        }

        for key in ["a".to_owned(), "AB9._-".repeat(13) + "xy"] {
            let id = RequestId::parse(&key).unwrap();
            for n in [1, 2, 3, 4, 63, 64, 65, MAX_BATCH_BYTES - 2, MAX_BATCH_BYTES - 1, MAX_BATCH_BYTES] {
                let bytes = (0..n).map(|i| (i % 251) as u8).collect::<Vec<_>>();
                let encoded = STANDARD.encode(&bytes);
                let old = serde_json::to_vec(&Old {
                    request_id: &key,
                    input_base64: &encoded,
                }).unwrap();
                let direct = preview_body(&id, &bytes).unwrap();
                assert_eq!(&*direct, &old, "key length {}, input length {n}", key.len());
            }
            assert!(matches!(preview_body(&id, &[]), Err(Error::Session(LoopbackError::Input))));
            assert!(matches!(preview_body(&id, &vec![0; MAX_BATCH_BYTES + 1]),
                Err(Error::Session(LoopbackError::Input))));
        }
    }
}
