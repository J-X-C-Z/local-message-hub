//! Private local export scheduler ledger. Only an explicitly enabled operator
//! driver can call this module; opening an archive does not start scheduling.
//! Bounded text rendering and SQLite publication are private, in-process
//! operations. No scheduler runtime, public endpoint, path or transport.
use crate::{events::EventStore, export, export_presets, plugins::AccountScope, store::{PLATFORMS,StoreError}};
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};
use serde::Serialize;
use sha2::{Digest, Sha256};

pub(crate) mod recurring;
pub(crate) mod inspect;
pub(crate) mod cancel_attempt;
pub(crate) mod retention;

const MIN_INTERVAL: i64 = 15 * 60;
const MAX_INTERVAL: i64 = 31 * 86400;
const MAX_TIME: i64 = 253_402_300_799;
const LEASE: i64 = 5 * 60;
const MAX_ATTEMPTS: i64 = 3;
const SCHEDULE_LIMIT: i64 = 256;
const OWNER_LIMIT: i64 = 16;
const JOB_LIMIT: i64 = 4096;
const ARTIFACT_MAX: usize = 1024 * 1024;
const ARCHIVE_BYTES: i64 = 64 * 1024 * 1024;
const ACCOUNT_BYTES: i64 = 8 * 1024 * 1024;
const ARCHIVE_ARTIFACTS: i64 = 256;
const ACCOUNT_ARTIFACTS: i64 = 32;
const ARTIFACT_TABLE: &str = "
CREATE TABLE IF NOT EXISTS native_export_artifact_v1(
    owner TEXT NOT NULL, account TEXT NOT NULL, schedule_id TEXT NOT NULL,
    due_at INTEGER NOT NULL, preset_id TEXT NOT NULL, preset_revision INTEGER NOT NULL,
    format TEXT NOT NULL CHECK(format IN ('json','csv','txt','md')),
    byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 0 AND 1048576),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    body BLOB NOT NULL CHECK(typeof(body)='blob' AND length(body)=byte_count),
    PRIMARY KEY(owner,schedule_id,due_at)
) WITHOUT ROWID;";
const TABLES: &str = "
CREATE TABLE IF NOT EXISTS native_export_schedule_v1(
    owner TEXT NOT NULL, account TEXT NOT NULL, id TEXT NOT NULL,
    preset_id TEXT NOT NULL, preset_revision INTEGER NOT NULL CHECK(preset_revision BETWEEN 1 AND 9007199254740991),
    interval_seconds INTEGER NOT NULL CHECK(interval_seconds BETWEEN 900 AND 2678400),
    next_due INTEGER NOT NULL CHECK(next_due BETWEEN 0 AND 253402300799),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    PRIMARY KEY(owner,id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS native_export_run_v1(
    owner TEXT NOT NULL, account TEXT NOT NULL, schedule_id TEXT NOT NULL,
    due_at INTEGER NOT NULL CHECK(due_at BETWEEN 0 AND 253402300799),
    state TEXT NOT NULL CHECK(state IN ('leased','cancelled','expired','rendered','failed')),
    attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 3),
    lease_until INTEGER NOT NULL CHECK((state='leased' AND lease_until>0) OR (state<>'leased' AND lease_until=0)),
    PRIMARY KEY(owner,schedule_id,due_at)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS native_export_clock_v1(
    account TEXT PRIMARY KEY, observed_at INTEGER NOT NULL
        CHECK(observed_at BETWEEN 0 AND 253402300799)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS native_export_account_run_v1
ON native_export_run_v1(account,lease_until) WHERE state='leased';
CREATE UNIQUE INDEX IF NOT EXISTS native_export_single_schedule_run_v1
ON native_export_run_v1(owner,schedule_id) WHERE state='leased';";

#[derive(Debug)]
pub(crate) enum Error { Invalid, Missing, StalePreset, Busy, StaleLease, ClockRegression, Confirmation, RecurringDenied, Quota, Render, Storage }
type Result<T> = std::result::Result<T, Error>;
impl From<rusqlite::Error> for Error {
    fn from(_:rusqlite::Error)->Self {Self::Storage}
}
fn preset_error(e:export_presets::Error)->Error {
    match e {
        export_presets::Error::Invalid=>Error::Invalid,
        export_presets::Error::Missing|export_presets::Error::Stale=>Error::StalePreset,
        _=>Error::Storage,
    }
}

#[derive(Debug, Serialize)]
pub(crate) struct Schedule {
    pub id:String,
    pub preset_id:String,
    pub preset_revision:i64,
    pub interval_seconds:i64,
    pub next_due:i64,
    pub enabled:bool,
}
#[derive(Debug)]
pub(crate) struct Create {
    pub preset_id:String,
    pub preset_revision:i64,
    pub interval_seconds:i64,
}
#[derive(Debug, Serialize)]
pub(crate) struct Claim {
    pub schedule_id:String,
    pub preset_id:String,
    pub preset_revision:i64,
    pub due_at:i64,
    pub attempt:i64,
    pub lease_until:i64,
}

struct Principal { owner:String, account:String }
fn principal(store:&EventStore,plugin:&str,scope:&AccountScope)->Result<Principal> {
    let valid_plugin=plugin.len()<=64 && plugin.split('.').all(|part|{
        part.as_bytes().first().is_some_and(u8::is_ascii_lowercase)
            && part.bytes().all(|b|b.is_ascii_lowercase()||b.is_ascii_digit()||b==b'-')
            && !part.ends_with('-')
    });
    if !valid_plugin || scope.platform=="all" || !PLATFORMS.contains(&scope.platform.as_str())
        || !(1..=256).contains(&scope.account_id.len()) || scope.account_id.trim()!=scope.account_id
        || scope.account_id.chars().any(char::is_control) {return Err(Error::Invalid);}
    let dat=serde_json::to_vec(&("lmh-export-schedule-account-v1",store.archive_id(),
        &scope.platform,&scope.account_id)).map_err(|_|Error::Storage)?;
    let account=hex::encode(Sha256::digest(&dat));
    let dat=serde_json::to_vec(&("lmh-export-schedule-owner-v1",&account,plugin))
        .map_err(|_|Error::Storage)?;
    Ok(Principal{owner:hex::encode(Sha256::digest(dat)),account})
}

fn valid_id(id:&str)->bool {
    id.len()==64 && id.bytes().all(|b| b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
fn valid_now(now:i64)->bool {(0..=MAX_TIME-MAX_INTERVAL).contains(&now)}
fn writer(store:&EventStore)->Result<Connection> {
    store.write_connection().map_err(|_|Error::Storage)
}
fn begin<'a>(store:&EventStore,db:&'a mut Connection)->Result<Transaction<'a>> {
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    Ok(tx)
}
/// Once a later claim is admitted, an earlier wall clock reading must
/// not bring a stale worker's privilege back, including after restart.
fn advance_clock(tx:&Transaction<'_>,account:&str,now:i64)->Result<()> {
    let changed=tx.execute("INSERT INTO native_export_clock_v1(account,observed_at) VALUES(?1,?2)
        ON CONFLICT(account) DO UPDATE SET observed_at=excluded.observed_at
        WHERE excluded.observed_at>=native_export_clock_v1.observed_at",
        params![account,now])?;
    if changed!=1 {return Err(Error::ClockRegression);}
    Ok(())
}
fn decode_schedule(row:&rusqlite::Row<'_>)->rusqlite::Result<Schedule> {
    Ok(Schedule {id:row.get(0)?,preset_id:row.get(1)?,preset_revision:row.get(2)?,
        interval_seconds:row.get(3)?,next_due:row.get(4)?,enabled:row.get(5)?})
}
fn get(db:&Connection,owner:&str,id:&str)->Result<Schedule> {
    db.query_row("SELECT id,preset_id,preset_revision,interval_seconds,next_due,enabled
        FROM native_export_schedule_v1 WHERE owner=?1 AND id=?2",
        params![owner,id],decode_schedule).optional()?.ok_or(Error::Missing)
}

/// Restore the authorized principal's own schedule inventory without making
/// any writes or enabling a driver on archive open.
pub(crate) fn schedules(store:&EventStore,plugin:&str,scope:&AccountScope)->Result<Vec<Schedule>> {
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    let kind:Option<String>=tx.query_row(
        "SELECT type FROM sqlite_schema WHERE name='native_export_schedule_v1' COLLATE NOCASE",
        [],|r|r.get(0)).optional()?;
    match kind.as_deref() {
        None=>return Ok(Vec::new()),
        Some("table")=>(),
        _=>return Err(Error::Storage),
    }
    let mut stmt=tx.prepare("SELECT id,preset_id,preset_revision,interval_seconds,next_due,enabled
        FROM native_export_schedule_v1 WHERE owner=?1 ORDER BY next_due,id LIMIT ?2")?;
    let rows=stmt.query_map(params![key.owner,OWNER_LIMIT+1],decode_schedule)?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if rows.len()>OWNER_LIMIT as usize {return Err(Error::Storage);}
    Ok(rows)
}
fn require_preset(tx:&Transaction<'_>,store:&EventStore,plugin:&str,scope:&AccountScope,
    id:&str,revision:i64)->Result<()> {
    export_presets::require_revision_in(tx,store,plugin,scope,id,revision).map_err(preset_error)
}

/// Explicit opt-in; first slot is one interval in the future. Schedule edits
/// require cancellation and a new schedule so old due-slot IDs stay stable.
pub(crate) fn create(store:&EventStore,plugin:&str,scope:&AccountScope,now:i64,input:Create)->Result<Schedule> {
    if !valid_now(now)||!(MIN_INTERVAL..=MAX_INTERVAL).contains(&input.interval_seconds) {
        return Err(Error::Invalid);
    }
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    require_preset(&tx,store,plugin,scope,&input.preset_id,input.preset_revision)?;
    tx.execute_batch(TABLES)?;
    advance_clock(&tx,&key.account,now)?;
    let counts:(i64,i64)=tx.query_row("SELECT count(*),count(CASE WHEN owner=?1 THEN 1 END)
        FROM native_export_schedule_v1",[&key.owner],|r|Ok((r.get(0)?,r.get(1)?)))?;
    if counts.0>=SCHEDULE_LIMIT||counts.1>=OWNER_LIMIT {return Err(Error::Quota);}
    let mut bytes=[0_u8;32];
    getrandom::fill(&mut bytes).map_err(|_|Error::Storage)?;
    let id=hex::encode(bytes);
    let next_due=now+input.interval_seconds;
    tx.execute("INSERT INTO native_export_schedule_v1
        (owner,account,id,preset_id,preset_revision,interval_seconds,next_due,enabled)
        VALUES (?1,?2,?3,?4,?5,?6,?7,1)",params![key.owner,key.account,id,
        input.preset_id,input.preset_revision,input.interval_seconds,next_due])?;
    let saved=get(&tx,&key.owner,&id)?;
    tx.commit()?;
    Ok(saved)
}

pub(crate) fn cancel(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str)->Result<()> {
    if !valid_id(id){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    tx.execute_batch(TABLES)?;
    if tx.execute("UPDATE native_export_schedule_v1 SET enabled=0 WHERE owner=?1 AND id=?2",
       params![key.owner,id])?!=1 {return Err(Error::Missing);}
    tx.execute("UPDATE native_export_run_v1 SET state='cancelled',lease_until=0
        WHERE owner=?1 AND schedule_id=?2 AND state='leased'",params![key.owner,id])?;
    tx.commit()?;
    Ok(())
}

/// Explicit tick for a selected schedule: coalesce missed slots, retain the
/// slot key for crash retries, and never create multiple live account claims.
/// An explicit publisher must revalidate the lease before artifact publication.
pub(crate) fn claim(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,now:i64)->Result<Option<Claim>> {
    claim_inner(store,plugin,scope,id,now,None)
}

pub(crate) fn claim_recurring(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,now:i64,
    permit:&recurring::Permit)->Result<Option<Claim>> {
    claim_inner(store,plugin,scope,id,now,Some(permit))
}

fn claim_inner(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,now:i64,
    permit:Option<&recurring::Permit>)->Result<Option<Claim>> {
    if !valid_id(id)||!valid_now(now){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    tx.execute_batch(TABLES)?;
    let schedule=get(&tx,&key.owner,id)?;
    if !schedule.enabled {return Ok(None);}
    if !(MIN_INTERVAL..=MAX_INTERVAL).contains(&schedule.interval_seconds)
        || !(0..=MAX_TIME).contains(&schedule.next_due) {return Err(Error::Storage);}
    require_preset(&tx,store,plugin,scope,&schedule.preset_id,schedule.preset_revision)?;
    if let Some(permit)=permit{ recurring::check(&tx,&key,&schedule,permit,None,now)?; }
    advance_clock(&tx,&key.account,now)?;
    // The IMMEDIATE transaction serializes admission across principals.
    // After expiry the old worker is fenced by its lease, not by changes to
    // another principal's records. The driver supplies trusted UTC seconds.
    let busy:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM native_export_run_v1
        WHERE account=?1 AND state='leased' AND lease_until>?2)",
        params![key.account,now],|r|r.get(0))?;
    if busy {return Err(Error::Busy);}
    let expired:Option<(i64,i64)>=tx.query_row(
        "SELECT due_at,attempt FROM native_export_run_v1
          WHERE owner=?1 AND schedule_id=?2 AND state='leased'",params![key.owner,id],
        |r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    let lease_until=now+LEASE;
    let (due_at,attempt)=if let Some((due_at,attempt))=expired {
        if let Some(permit)=permit{ recurring::check(&tx,&key,&schedule,permit,Some(due_at),now)?; }
        if attempt>=MAX_ATTEMPTS {
            tx.execute("UPDATE native_export_run_v1 SET state='expired',lease_until=0
                WHERE owner=?1 AND schedule_id=?2 AND due_at=?3 AND state='leased'",
                params![key.owner,id,due_at])?;
            tx.commit()?;
            return Ok(None);
        }
        tx.execute("UPDATE native_export_run_v1 SET attempt=attempt+1,lease_until=?1
            WHERE owner=?2 AND schedule_id=?3 AND due_at=?4 AND state='leased'",
            params![lease_until,key.owner,id,due_at])?;
        (due_at,attempt+1)
    } else {
        if schedule.next_due>now {return Ok(None);}
        let due_at=schedule.next_due+((now-schedule.next_due)/schedule.interval_seconds)*schedule.interval_seconds;
        if let Some(permit)=permit{ recurring::check(&tx,&key,&schedule,permit,Some(due_at),now)?; }
        let rows:i64=tx.query_row("SELECT count(*) FROM native_export_run_v1",[],|r|r.get(0))?;
        if rows>=JOB_LIMIT {return Err(Error::Quota);}
        tx.execute("INSERT INTO native_export_run_v1
          (owner,account,schedule_id,due_at,state,attempt,lease_until)
          VALUES (?1,?2,?3,?4,'leased',1,?5)",params![key.owner,key.account,id,due_at,lease_until])?;
        tx.execute("UPDATE native_export_schedule_v1 SET next_due=?1 WHERE owner=?2 AND id=?3",
            params![due_at+schedule.interval_seconds,key.owner,id])?;
        (due_at,1)
    };
    tx.commit()?;
    Ok(Some(Claim{schedule_id:id.to_owned(),preset_id:schedule.preset_id,
        preset_revision:schedule.preset_revision,due_at,attempt,lease_until}))
}

/// Ticket validity is verified both in the read snapshot and again inside
/// the writer transaction. No later job can use an earlier attempt.
fn require_claim(db:&Connection,store:&EventStore,key:&Principal,plugin:&str,
    scope:&AccountScope,ticket:&Claim,now:i64)->Result<()> {
    if !valid_id(&ticket.schedule_id)||!valid_now(now)
        || !(1..=MAX_ATTEMPTS).contains(&ticket.attempt)
        || ticket.due_at<0 || ticket.due_at>now || ticket.lease_until<=now {
        return Err(Error::StaleLease);
    }
    let schedule=get(db,&key.owner,&ticket.schedule_id)?;
    if !schedule.enabled||schedule.preset_id!=ticket.preset_id||schedule.preset_revision!=ticket.preset_revision {
        return Err(Error::StaleLease);
    }
    export_presets::require_revision_in(db,store,plugin,scope,&ticket.preset_id,ticket.preset_revision)
        .map_err(preset_error)?;
    let clock:i64=db.query_row(
        "SELECT observed_at FROM native_export_clock_v1 WHERE account=?1",
        [&key.account],|r|r.get(0))?;
    if now<clock{return Err(Error::ClockRegression);}
    let active:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM native_export_run_v1
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND due_at=?4 AND attempt=?5
        AND state='leased' AND lease_until=?6 AND lease_until>?7)",
        params![key.owner,key.account,ticket.schedule_id,ticket.due_at,ticket.attempt,ticket.lease_until,now],
        |r|r.get(0))?;
    if !active{return Err(Error::StaleLease);}
    Ok(())
}

fn consume(tx:&Transaction<'_>,key:&Principal,ticket:&Claim,now:i64,state:&str)->Result<()> {
    if tx.execute("UPDATE native_export_run_v1 SET state=?1,lease_until=0
        WHERE owner=?2 AND account=?3 AND schedule_id=?4 AND due_at=?5 AND attempt=?6 AND state='leased'
        AND lease_until=?7 AND lease_until>?8",
        params![state,key.owner,key.account,ticket.schedule_id,ticket.due_at,
            ticket.attempt,ticket.lease_until,now])?!=1 {return Err(Error::StaleLease);}
    Ok(())
}

pub(crate) fn fail(store:&EventStore,plugin:&str,scope:&AccountScope,ticket:&Claim,now:i64)->Result<()> {
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    require_claim(&tx,store,&key,plugin,scope,ticket,now)?;
    advance_clock(&tx,&key.account,now)?;
    consume(&tx,&key,ticket,now,"failed")?;
    tx.commit()?;
    Ok(())
}

#[derive(Debug, Serialize)]
pub(crate) struct Artifact {
    pub schedule_id:String,
    pub due_at:i64,
    pub format:String,
    pub byte_count:usize,
    pub sha256:String,
}

/// A bounded in-memory preview. Its type, receipt and bytes are intentionally
/// not serializable or constructible outside this implementation.
pub(crate) struct PreparedArtifact {
    ticket:Claim,
    key:Principal,
    artifact:Artifact,
    body:Vec<u8>,
    receipt:Option<String>,
    recurring_generation:Option<String>,
}
impl PreparedArtifact {
    pub(crate) fn description(&self)->(&Artifact,Option<&str>) {(&self.artifact,self.receipt.as_deref())}
}

fn artifact_format(format:export::Format)->Result<&'static str> {
    match format {
        export::Format::Json=>Ok("json"),export::Format::Csv=>Ok("csv"),
        export::Format::Txt=>Ok("txt"),export::Format::Md=>Ok("md"),
        _=>Err(Error::Invalid)
    }
}

/// Reads one archive snapshot, bound to the owned preset and lease. Preview
/// only: it writes neither a file nor an artifact record or scheduler state.
pub(crate) fn prepare(store:&EventStore,plugin:&str,scope:&AccountScope,ticket:Claim,now:i64)
    ->Result<PreparedArtifact> {
    prepare_inner(store,plugin,scope,ticket,None,now)
}

pub(crate) fn prepare_recurring(store:&EventStore,plugin:&str,scope:&AccountScope,ticket:Claim,
    permit:&recurring::Permit,now:i64)->Result<PreparedArtifact> {
    prepare_inner(store,plugin,scope,ticket,Some(permit),now)
}

fn prepare_inner(store:&EventStore,plugin:&str,scope:&AccountScope,ticket:Claim,
    permit:Option<&recurring::Permit>,now:i64)->Result<PreparedArtifact> {
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    require_claim(&tx,store,&key,plugin,scope,&ticket,now)?;
    if let Some(permit)=permit{
        let schedule=get(&tx,&key.owner,&ticket.schedule_id)?;
        recurring::check(&tx,&key,&schedule,permit,Some(ticket.due_at),now)?;
    }
    let spec=export_presets::render_spec_in(&tx,store,plugin,scope,&ticket.preset_id,ticket.preset_revision)
        .map_err(preset_error)?;
    let format=artifact_format(spec.format)?;
    let body=export::render_selection(&tx,scope,spec.conversation_ids,spec.format,spec.start,spec.end,ARTIFACT_MAX)
        .map_err(|err|match err {StoreError::TooLarge=>Error::Quota,_=>Error::Render})?
        .ok_or(Error::Missing)?;
    let sha256=hex::encode(Sha256::digest(&body));
    let artifact=Artifact{schedule_id:ticket.schedule_id.clone(),due_at:ticket.due_at,
        format:format.to_owned(),byte_count:body.len(),sha256};
    let receipt=if permit.is_none() {
        let confirmation=serde_json::to_vec(&("lmh-export-schedule-prepared-v1",
            &key.owner,&key.account,&ticket,&artifact)).map_err(|_|Error::Storage)?;
        Some(hex::encode(Sha256::digest(confirmation)))
    } else {None};
    let recurring_generation=permit.map(|rule|rule.generation().to_owned());
    tx.commit()?;
    Ok(PreparedArtifact{ticket,key,artifact,body,receipt,recurring_generation})
}

/// Operator-confirmed publication only; blob and successful run state share
/// one transaction. A lost reply can be reconciled by reading the same slot.
pub(crate) fn publish(store:&EventStore,plugin:&str,scope:&AccountScope,prepared:PreparedArtifact,
    confirmation:&str,now:i64)->Result<Artifact> {
    if prepared.receipt.as_deref()!=Some(confirmation) {return Err(Error::Confirmation);}
    publish_inner(store,plugin,scope,prepared,None,now)
}

/// Separate durable recurring authority, never a synthetic manual receipt.
pub(crate) fn publish_recurring(store:&EventStore,plugin:&str,scope:&AccountScope,prepared:PreparedArtifact,
    permit:&recurring::Permit,now:i64)->Result<Artifact> {
    if prepared.recurring_generation.as_deref()!=Some(permit.generation()) {
        return Err(Error::RecurringDenied);
    }
    publish_inner(store,plugin,scope,prepared,Some(permit),now)
}

fn publish_inner(store:&EventStore,plugin:&str,scope:&AccountScope,prepared:PreparedArtifact,
    permit:Option<&recurring::Permit>,now:i64)->Result<Artifact> {
    let PreparedArtifact{ticket,key:prepared_key,artifact,body,..}=prepared;
    let key=principal(store,plugin,scope)?;
    if key.owner!=prepared_key.owner||key.account!=prepared_key.account {return Err(Error::Missing);}
    if body.len()>ARTIFACT_MAX || body.len()!=artifact.byte_count{return Err(Error::Quota);}
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    require_claim(&tx,store,&key,plugin,scope,&ticket,now)?;
    advance_clock(&tx,&key.account,now)?;
    if let Some(permit)=permit {
        let schedule=get(&tx,&key.owner,&ticket.schedule_id)?;
        recurring::spend(&tx,&key,&schedule,permit,ticket.due_at,now)?;
    }
    tx.execute_batch(ARTIFACT_TABLE)?;
    let used:(i64,i64,i64,i64)=tx.query_row("SELECT count(*),coalesce(sum(byte_count),0),
        count(CASE WHEN account=?1 THEN 1 END),coalesce(sum(CASE WHEN account=?1 THEN byte_count END),0)
        FROM native_export_artifact_v1",[&key.account],
        |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
    let bytes=artifact.byte_count as i64;
    if used.0>=ARCHIVE_ARTIFACTS||used.1>ARCHIVE_BYTES-bytes
        ||used.2>=ACCOUNT_ARTIFACTS||used.3>ACCOUNT_BYTES-bytes {return Err(Error::Quota);}
    tx.execute("INSERT INTO native_export_artifact_v1
        (owner,account,schedule_id,due_at,preset_id,preset_revision,format,byte_count,sha256,body)
        VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",params![key.owner,key.account,ticket.schedule_id,
        ticket.due_at,ticket.preset_id,ticket.preset_revision,artifact.format,bytes,artifact.sha256,body])?;
    consume(&tx,&key,&ticket,now,"rendered")?;
    tx.commit()?;
    Ok(artifact)
}

/// Outline only; the operator can review this before retrieving the bytes.
pub(crate) fn published_meta(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,slot:i64)
    ->Result<Option<Artifact>> {
    Ok(published_entry(store,plugin,scope,id,slot,false)?.map(|(artifact,_)|artifact))
}

/// Read only a committed artifact from the same account/principal. There's no
/// raw pathname, arbitrary key, or unverified download here.
pub(crate) fn published(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,slot:i64)
    ->Result<Option<(Artifact,Vec<u8>)>> {
    published_entry(store,plugin,scope,id,slot,true)?
        .map(|(artifact,body)| Ok((artifact,body.ok_or(Error::Storage)?))).transpose()
}

fn published_entry(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,slot:i64,read_body:bool)
    ->Result<Option<(Artifact,Option<Vec<u8>>)>> {
    if !valid_id(id)||!(0..=MAX_TIME).contains(&slot) {return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    let kind:Option<String>=tx.query_row(
        "SELECT type FROM sqlite_schema WHERE name='native_export_artifact_v1' COLLATE NOCASE",
        [],|r|r.get(0)).optional()?;
    match kind.as_deref() {
        None=>return Ok(None),Some("table")=>(),_=>return Err(Error::Storage),
    }
    let row:Option<(String,usize,String,Option<Vec<u8>>)>=tx.query_row(
        "SELECT a.format,a.byte_count,a.sha256,CASE WHEN ?6 THEN a.body END
        FROM native_export_artifact_v1 a
        JOIN native_export_run_v1 r ON r.owner=a.owner AND r.account=a.account
         AND r.schedule_id=a.schedule_id AND r.due_at=a.due_at AND r.state='rendered'
        WHERE a.owner=?1 AND a.account=?2 AND a.schedule_id=?3 AND a.due_at=?4
         AND a.byte_count BETWEEN 0 AND ?5 AND length(a.body)=a.byte_count",
        params![key.owner,key.account,id,slot,ARTIFACT_MAX,read_body],
        |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).optional()?;
    tx.commit()?;
    row.map(|(format,byte_count,sha256,body)|{
        if !valid_id(&sha256)||!matches!(format.as_str(),"json"|"csv"|"txt"|"md")
            || body.as_ref().is_some_and(|bytes|
                bytes.len()!=byte_count||hex::encode(Sha256::digest(bytes))!=sha256) {
            return Err(Error::Storage);
        }
        Ok((Artifact{schedule_id:id.to_owned(),due_at:slot,format,byte_count,sha256},body))
    }).transpose()
}
