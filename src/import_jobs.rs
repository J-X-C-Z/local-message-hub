//! Manual, bounded staging of detached legacy JSON. Never opens caller paths.
use crate::{
    events::{self, EventError, EventStore, IngestReceipt, IngestRequest, MAX_BATCH_BYTES},
    legacy_import::{OFFLINE_PRODUCER, ORDINARY, Plan},
    plugins::AccountScope,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_OWNER_PENDING: i64 = 8;
const MAX_ARCHIVE_PENDING: i64 = 64;
const MAX_ARCHIVE_JOBS: i64 = 4096;
const TABLE: &str = "
CREATE TABLE native_local_import_jobs (
    owner TEXT NOT NULL, job TEXT NOT NULL, confirmation TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('prepared','committing','committed','cancelled','conflict')),
    payload TEXT, total INTEGER NOT NULL CHECK(total > 0), created INTEGER, unchanged INTEGER,
    PRIMARY KEY(owner, job),
    CHECK ((phase IN ('prepared','committing') AND payload IS NOT NULL) OR
           (phase NOT IN ('prepared','committing') AND payload IS NULL)),
    CHECK ((phase='committed' AND created IS NOT NULL AND unchanged IS NOT NULL) OR
           (phase!='committed' AND created IS NULL AND unchanged IS NULL))
) WITHOUT ROWID;
CREATE INDEX native_local_import_pending ON native_local_import_jobs(phase, owner);
";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Stale, Quota, Paused, Storage }
impl From<EventError> for Error {
    fn from(value: EventError) -> Self {
        if matches!(value,EventError::IngressPaused) {Self::Paused} else {Self::Storage}
    }
}
impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}

#[derive(Clone, Copy, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Phase { Prepared, Committing, Committed, Cancelled, Conflict }
impl Phase {
    fn decode(value: &str) -> rusqlite::Result<Self> {
        Ok(match value {
            "prepared" => Self::Prepared,
            "committing" => Self::Committing,
            "committed" => Self::Committed,
            "cancelled" => Self::Cancelled,
            "conflict" => Self::Conflict,
            _ => return Err(rusqlite::Error::InvalidQuery),
        })
    }
}

#[derive(Serialize)]
pub(crate) struct Job {
    job_id: String,
    confirmation: String,
    phase: Phase,
    rows: usize,
    created: Option<usize>,
    unchanged: Option<usize>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prepare {
    request_id: String,
    // Base64 keeps the detached bytes exact without re-rendering.
    input_base64: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    pub job_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Confirm {
    job_id: String,
    confirmation: String,
}

fn owner(archive: &EventStore, plugin: &str, scope: &AccountScope) -> Result<String, Error> {
    let data = serde_json::to_vec(&("lmh-local-import-owner-v1", archive.archive_id(),
        plugin, &scope.platform, &scope.account_id)).map_err(|_| Error::Storage)?;
    Ok(hex::encode(Sha256::digest(data)))
}
fn digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn installed(conn: &Connection) -> Result<bool, Error> {
    let objects: (i64,i64) = conn.query_row(
        "SELECT count(*),count(CASE WHEN
            (name='native_local_import_jobs' AND type='table') OR
            (name='native_local_import_pending' AND type='index') THEN 1 END)
         FROM sqlite_schema WHERE name IN ('native_local_import_jobs','native_local_import_pending')
         COLLATE NOCASE", [], |r| Ok((r.get(0)?,r.get(1)?)))?;
    match objects { (0,0) => Ok(false), (2,2) => Ok(true), _ => Err(Error::Storage) }
}
fn get(conn: &Connection, owner: &str, id: &str) -> Result<Option<Job>, Error> {
    Ok(conn.query_row(
        "SELECT confirmation,phase,total,created,unchanged
         FROM native_local_import_jobs WHERE owner=? AND job=?", (owner,id), |r|
         Ok(Job { job_id:id.to_owned(), confirmation:r.get(0)?, phase:Phase::decode(r.get_ref(1)?.as_str()?)?,
            rows:r.get(2)?, created:r.get(3)?, unchanged:r.get(4)? })
    ).optional()?)
}
pub(crate) fn request_key(key: &str) -> bool {
    (1..=80).contains(&key.len()) && key.bytes().all(|b|
        b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

pub(crate) fn prepare(store: &EventStore, plugin: &str, scope: &AccountScope,
    request: Prepare) -> Result<Job, Error> {
    if !request_key(&request.request_id) { return Err(Error::Input); }
    if request.input_base64.len() > MAX_BATCH_BYTES.div_ceil(3) * 4 { return Err(Error::Input); }
    let bytes = STANDARD.decode(&request.input_base64).map_err(|_| Error::Input)?;
    let plan = Plan::from_bytes(&bytes,store,&scope.platform,&scope.account_id,ORDINARY)
        .map_err(|_| Error::Input)?;
    prepare_plan(store,plugin,scope,&request.request_id,plan)
}

pub(crate) fn prepare_stock(store:&EventStore,plugin:&str,scope:&AccountScope,
    request_id:&str,bytes:&[u8],csv:bool)->Result<Job,Error> {
    if !request_key(request_id) {return Err(Error::Input);}
    let plan=if csv {Plan::from_csv_bytes(bytes,store,&scope.platform,&scope.account_id)}
        else {Plan::from_stock_bytes(bytes,store,&scope.platform,&scope.account_id)}
        .map_err(|_|Error::Input)?;
    prepare_plan(store,plugin,scope,request_id,plan)
}

fn prepare_plan(store:&EventStore,plugin:&str,scope:&AccountScope,request_id:&str,plan:Plan)
    ->Result<Job,Error> {
    let payload = serde_json::to_string(&plan.request).map_err(|_| Error::Storage)?;
    if payload.len() > MAX_BATCH_BYTES + 256 { return Err(Error::Input); }
    let key = owner(store,plugin,scope)?;
    let id = hex::encode(Sha256::digest(
        format!("lmh-local-import-job-v1:{key}:{request_id}")));
    let consent = plan.request.idempotency_key;
    let mut conn = store.write_connection()?;
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let initialized=installed(&tx)?;
    if initialized {
        if let Some(job)=get(&tx,&key,&id)? {
            return if job.confirmation==consent {Ok(job)} else {Err(Error::Stale)};
        }
    }
    crate::ingress_policy::admit(&tx)?;
    if !initialized {tx.execute_batch(TABLE)?;}
    // An upper bound on persisted detached bytes plus tombstones. No implicit eviction:
    // an old idempotency key must never silently identify a different proposed import.
    let (all, pending, mine): (i64,i64,i64) = tx.query_row(
        "SELECT count(*),
             count(CASE WHEN phase IN ('prepared','committing') THEN 1 END),
             count(CASE WHEN phase IN ('prepared','committing') AND owner=? THEN 1 END)
         FROM native_local_import_jobs", [&key], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?)))?;
    if all >= MAX_ARCHIVE_JOBS || pending >= MAX_ARCHIVE_PENDING || mine >= MAX_OWNER_PENDING {
        return Err(Error::Quota);
    }
    tx.execute(
        "INSERT INTO native_local_import_jobs(owner,job,confirmation,phase,payload,total)
         VALUES(?,?,?,'prepared',?,?)", params![key,id,consent,payload,plan.request.messages.len()])?;
    let job = get(&tx,&key,&id)?.ok_or(Error::Storage)?;
    tx.commit()?;
    Ok(job)
}

pub(crate) fn status(store: &EventStore, plugin: &str, scope: &AccountScope,
    target: Target) -> Result<Job, Error> {
    if !digest(&target.job_id) { return Err(Error::Input); }
    let key = owner(store,plugin,scope)?;
    let db = store.read_connection()?;
    if !installed(&db)? { return Err(Error::Missing); }
    get(&db,&key,&target.job_id)?.ok_or(Error::Missing)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ListQuery { after:Option<String>,limit:Option<usize> }

#[derive(Serialize)]
struct Summary { job_id:String, phase:Phase, rows:usize, created:Option<usize>, unchanged:Option<usize> }

#[derive(Serialize)]
pub(crate) struct ListPage { items:Vec<Summary>, next_after:Option<String>, has_more:bool }

pub(crate) fn list(store:&EventStore,plugin:&str,scope:&AccountScope,query:ListQuery)->Result<ListPage,Error> {
    let limit=query.limit.unwrap_or(20);
    if !(1..=50).contains(&limit) || query.after.as_deref().is_some_and(|id|!digest(id)) {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let key=owner(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {
        if query.after.is_some() {return Err(Error::Missing);}
        return Ok(ListPage{items:vec![],next_after:None,has_more:false});
    }
    if let Some(after)=query.after.as_deref() {
        let anchor:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM native_local_import_jobs
            WHERE owner COLLATE BINARY=?1 AND job COLLATE BINARY=?2)",(&key,after),|r|r.get(0))?;
        if !anchor {return Err(Error::Missing);}
    }
    let mut sql=tx.prepare("SELECT job,phase,total,created,unchanged FROM native_local_import_jobs
        WHERE owner COLLATE BINARY=?1 AND job COLLATE BINARY>?2 ORDER BY job COLLATE BINARY LIMIT ?3")?;
    let mut items=sql.query_map(params![key,query.after.unwrap_or_default(),limit+1],|row| {
        let job_id:String=row.get(0)?;
        if !digest(&job_id) {return Err(rusqlite::Error::InvalidQuery);}
        Ok(Summary{job_id,phase:Phase::decode(row.get_ref(1)?.as_str()?)?,
            rows:row.get(2)?,created:row.get(3)?,unchanged:row.get(4)?})
    })?.collect::<rusqlite::Result<Vec<_>>>()?;
    let has_more=items.len()>limit;
    items.truncate(limit);
    let next_after=items.last().filter(|_|has_more).map(|last|last.job_id.clone());
    Ok(ListPage{items,next_after,has_more})
}

/// Read only the coarse phase for the exact desktop grant; never fetch payload
/// metadata, confirmation tokens, or result counts for the operations endpoint.
pub(crate) fn redacted_phase(store: &EventStore, plugin: &str, scope: &AccountScope,
    id: &str) -> Result<Phase, Error> {
    if !digest(id) { return Err(Error::Input); }
    let key = owner(store,plugin,scope)?;
    store.require_ordinary()?;
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Missing); }
    tx.query_row("SELECT phase FROM native_local_import_jobs
        WHERE owner COLLATE BINARY=?1 AND job COLLATE BINARY=?2", (&key,id),
        |r| Phase::decode(r.get_ref(0)?.as_str()?)
    ).optional()?.ok_or(Error::Missing)
}

pub(crate) fn cancel(store: &EventStore, plugin: &str, scope: &AccountScope,
    target: Target) -> Result<Job, Error> {
    if !digest(&target.job_id) { return Err(Error::Input); }
    let key = owner(store,plugin,scope)?;
    let mut conn = store.write_connection()?;
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Missing); }
    let job = get(&tx,&key,&target.job_id)?.ok_or(Error::Missing)?;
    match job.phase {
        Phase::Cancelled => return Ok(job),
        Phase::Prepared => (),
        _ => return Err(Error::Stale),
    }
    if tx.execute("UPDATE native_local_import_jobs SET phase='cancelled',payload=NULL
           WHERE owner=? AND job=? AND phase='prepared'", (&key,&target.job_id))? != 1 {
        return Err(Error::Storage);
    }
    let job = get(&tx,&key,&target.job_id)?.ok_or(Error::Storage)?;
    tx.commit()?;
    Ok(job)
}

// Read and validate the same scoped, bounded payload for commit and
// explicit read-only proof. Never use the stored JSON string as the digest.
fn pending_request(db:&Connection,key:&str,job:&Job,scope:&AccountScope)->Result<IngestRequest,Error> {
    let request: IngestRequest = db.query_row_and_then("SELECT payload FROM native_local_import_jobs
          WHERE owner=? AND job=? AND length(CAST(payload AS BLOB))<=?",
        params![key,job.job_id,MAX_BATCH_BYTES + 256], |r| -> Result<IngestRequest,Error> {
            let value = r.get_ref(0)?;
            let payload = value.as_str().map_err(|_| Error::Storage)?;
            serde_json::from_str(payload).map_err(|_| Error::Storage)
        })?;
    request.validate(OFFLINE_PRODUCER).map_err(|_| Error::Storage)?;
    if request.messages.len() != job.rows || request.idempotency_key != job.confirmation ||
        !request.messages.iter().all(|m| m.platform == scope.platform && m.account_id == scope.account_id) {
        return Err(Error::Storage);
    }
    Ok(request)
}

#[derive(Serialize)]
pub(crate) struct ApplyObservation {
    job_id:String,
    phase:Phase,
    evidence:&'static str,
    counts:Option<AppliedCounts>,
}
#[derive(Serialize)]
struct AppliedCounts {created:usize,unchanged:usize}

/// This is an observation, not a finalization or retry of the staged job.
pub(crate) fn observe_apply(store:&EventStore,plugin:&str,scope:&AccountScope,target:Target)
    ->Result<ApplyObservation,Error> {
    if !digest(&target.job_id){return Err(Error::Input);}
    store.require_ordinary()?;
    let key=owner(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)?{return Err(Error::Missing);}
    let job=get(&tx,&key,&target.job_id)?.ok_or(Error::Missing)?;
    let (evidence,counts)=if job.phase!=Phase::Committing {
        ("not_committing",None)
    } else {
        if !digest(&job.confirmation){return Err(Error::Storage);}
        // Avoid reading the staged body if there is nothing to prove yet.
        if let Some(proof)=events::legacy_import_proof(&tx,&job.confirmation)? {
            let request=pending_request(&tx,&key,&job,scope)?;
            let receipt=proof.verify(&request)?;
            if receipt.revised!=0||receipt.created.checked_add(receipt.unchanged)!=Some(job.rows) {
                return Err(Error::Storage);
            }
            ("receipt_verified",Some(AppliedCounts{created:receipt.created,unchanged:receipt.unchanged}))
        } else {("receipt_not_observed",None)}
    };
    Ok(ApplyObservation{job_id:job.job_id,phase:job.phase,evidence,counts})
}

pub(crate) fn commit(store: &EventStore, plugin: &str, scope: &AccountScope,
    confirm: Confirm) -> Result<Job, Error> {
    if !digest(&confirm.job_id) || !digest(&confirm.confirmation) { return Err(Error::Input); }
    let key = owner(store,plugin,scope)?;
    let id = &confirm.job_id;
    let mut conn = store.write_connection()?;
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Missing); }
    let job = get(&tx,&key,id)?.ok_or(Error::Missing)?;
    if job.confirmation != confirm.confirmation { return Err(Error::Stale); }
    match job.phase {
        Phase::Committed | Phase::Conflict => return Ok(job),
        Phase::Cancelled => return Err(Error::Stale),
        Phase::Committing | Phase::Prepared => (),
    }
    if job.phase==Phase::Prepared {crate::ingress_policy::admit(&tx)?;}
    let request=pending_request(&tx,&key,&job,scope)?;
    if job.phase == Phase::Prepared &&
        tx.execute("UPDATE native_local_import_jobs SET phase='committing'
               WHERE owner=? AND job=? AND phase='prepared'", (&key,id))? != 1 {
        return Err(Error::Storage);
    }
    // Publish 'committing' before the atomic ingest. The ingest receipt itself is stable:
    // retry after a crash or lost response either performs the same import or reuses its result.
    tx.commit()?;
    let completion = match store.import_legacy(request) {
        Ok(IngestReceipt { created, unchanged, .. }) => ("committed",Some(created),Some(unchanged)),
        Err(EventError::Conflict) => ("conflict",None,None),
        Err(EventError::IngressPaused)=>return Err(Error::Paused),
        Err(_) => return Err(Error::Storage),
    };
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    tx.execute("UPDATE native_local_import_jobs
        SET phase=?,payload=NULL,created=?,unchanged=?
        WHERE owner=? AND job=? AND phase='committing'",
        params![completion.0,completion.1,completion.2,key,id])?;
    let job = get(&tx,&key,id)?.ok_or(Error::Storage)?;
    tx.commit()?;
    Ok(job)
}
