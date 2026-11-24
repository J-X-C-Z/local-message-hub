//! Durable manual source-operation state, not an ingest lease or scheduler.
use crate::{events::{EventError, EventStore}, plugins::AccountScope};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_JOBS: i64 = 4096;
const MAX_LANE_JOBS: i64 = 128;
const TABLE: &str = "
CREATE TABLE native_manual_source_jobs (
    lane TEXT NOT NULL, job TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('active','paused','cancelled','completed')),
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 9007199254740991),
    start_sequence INTEGER NOT NULL CHECK(start_sequence >= 0),
    observed_sequence INTEGER NOT NULL CHECK(observed_sequence >= start_sequence),
    observed_checkpoint TEXT NOT NULL,
    last_action TEXT NOT NULL,
    PRIMARY KEY(lane, job)
) WITHOUT ROWID;
CREATE UNIQUE INDEX native_manual_source_open_lane ON native_manual_source_jobs(lane)
    WHERE phase IN ('active','paused');";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Stale, Busy, Quota, Paused, Storage }

impl From<EventError> for Error {
    fn from(value: EventError) -> Self {
        if matches!(value,EventError::IngressPaused) {Self::Paused} else {Self::Storage}
    }
}
impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Action { Pause, Resume, Cancel, Complete, Progress }
impl Action {
    fn name(self) -> &'static str {
        match self {
            Self::Pause => "pause", Self::Resume => "resume", Self::Cancel => "cancel",
            Self::Complete => "complete", Self::Progress => "progress",
        }
    }

    fn next(self, phase: &str) -> Option<&'static str> {
        match (self, phase) {
            (Self::Pause, "active") => Some("paused"),
            (Self::Resume, "paused") => Some("active"),
            (Self::Cancel, "active" | "paused") => Some("cancelled"),
            (Self::Complete, "active") => Some("completed"),
            (Self::Progress, "active") => Some("active"),
            _ => None,
        }
    }
}

#[derive(Serialize)]
pub(crate) struct Job {
    job_id: String,
    phase: String,
    version: i64,
    start_sequence: i64,
    observed_sequence: i64,
    observed_checkpoint: String,
    #[serde(skip)]
    last_action: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Start { request_id: String }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Status { job_id: String }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    job_id: String,
    expected_version: i64,
}

fn digest(input: &[u8]) -> String {
    hex::encode(Sha256::digest(input))
}

pub(crate) fn lane(store: &EventStore, plugin: &str, scope: &AccountScope) -> Result<String, Error> {
    let owner = serde_json::to_vec(&("lmh-source-job-lane-v1", store.archive_id(),
        plugin, &scope.platform, &scope.account_id)).map_err(|_| Error::Storage)?;
    Ok(digest(&owner))
}

fn job_id_valid(id: &str) -> bool {
    id.len() == 64 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn installed(conn: &Connection) -> Result<bool, Error> {
    let objects: (i64,i64) = conn.query_row(
        "SELECT count(*),count(CASE WHEN
            (name='native_manual_source_jobs' AND type='table') OR
            (name='native_manual_source_open_lane' AND type='index') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
           ('native_manual_source_jobs','native_manual_source_open_lane')",
        [], |r| Ok((r.get(0)?,r.get(1)?)))?;
    match objects { (0,0) => Ok(false), (2,2) => Ok(true), _ => Err(Error::Storage) }
}

fn get(db: &Connection, lane: &str, id: &str) -> Result<Option<Job>, Error> {
    Ok(db.query_row(
        "SELECT phase,version,start_sequence,observed_sequence,observed_checkpoint,last_action
         FROM native_manual_source_jobs WHERE lane=? AND job=?", (lane,id), |r| Ok(Job {
             job_id:id.to_owned(), phase:r.get(0)?, version:r.get(1)?, start_sequence:r.get(2)?,
             observed_sequence:r.get(3)?, observed_checkpoint:r.get(4)?, last_action:r.get(5)?,
         })).optional()?)
}

pub(crate) fn start(store: &EventStore, plugin: &str, scope: &AccountScope,
    request: Start) -> Result<Job, Error> {
    if !(1..=80).contains(&request.request_id.len()) || !request.request_id.bytes().all(|b|
       b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')) { return Err(Error::Input); }
    let lane = lane(store, plugin, scope)?;
    let id = digest(format!("lmh-source-job-v1:{lane}:{}", request.request_id).as_bytes());
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let checkpoint = store.source_checkpoint_in(&tx, plugin, scope)?;
    let initialized=installed(&tx)?;
    if initialized {
        if let Some(job)=get(&tx,&lane,&id)? {return Ok(job);}
    }
    crate::ingress_policy::admit(&tx)?;
    if !initialized{tx.execute_batch(TABLE)?;}
    let busy: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM native_manual_source_jobs
         WHERE lane=? AND phase IN ('active','paused'))", [&lane], |r|r.get(0))?;
    if busy { return Err(Error::Busy); }
    let (all,mine): (i64,i64) = tx.query_row(
        "SELECT count(*),count(CASE WHEN lane=? THEN 1 END) FROM native_manual_source_jobs",
        [&lane], |r|Ok((r.get(0)?,r.get(1)?)))?;
    if all >= MAX_JOBS || mine >= MAX_LANE_JOBS { return Err(Error::Quota); }
    tx.execute("INSERT INTO native_manual_source_jobs
        (lane,job,phase,version,start_sequence,observed_sequence,observed_checkpoint,last_action)
        VALUES (?,?,'active',1,?,? ,?,'start')",
        params![lane,id,checkpoint.sequence,checkpoint.sequence,checkpoint.revision])?;
    let job = get(&tx,&lane,&id)?.ok_or(Error::Storage)?;
    tx.commit()?;
    Ok(job)
}

pub(crate) fn status(store: &EventStore, plugin: &str, scope: &AccountScope,
    request: Status) -> Result<Job, Error> {
    if !job_id_valid(&request.job_id) { return Err(Error::Input); }
    let lane = lane(store, plugin, scope)?;
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.require_ordinary()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Missing); }
    get(&tx,&lane,&request.job_id)?.ok_or(Error::Missing)
}

pub(crate) fn change(store: &EventStore, plugin: &str, scope: &AccountScope,
    request: Change, action: Action) -> Result<Job, Error> {
    const MAX_VERSION: i64 = 9_007_199_254_740_991;
    if !job_id_valid(&request.job_id) || !(1..=MAX_VERSION).contains(&request.expected_version) {
        return Err(Error::Input);
    }
    let lane = lane(store, plugin, scope)?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.require_ordinary()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Missing); }
    let job = get(&tx,&lane,&request.job_id)?.ok_or(Error::Missing)?;
    if job.version == request.expected_version + 1 && job.last_action == action.name() {
        return Ok(job);
    }
    if job.version != request.expected_version || job.version >= MAX_VERSION {
        return Err(Error::Stale);
    }
    let next = action.next(&job.phase).ok_or(Error::Stale)?;
    if action==Action::Resume {crate::ingress_policy::admit(&tx)?;}
    // Pause/cancel must remain possible even if ingest checkpoint reads are unavailable.
    let (sequence, revision) = if matches!(action, Action::Resume | Action::Complete | Action::Progress) {
        let checkpoint = store.source_checkpoint_in(&tx, plugin, scope)?;
        if checkpoint.sequence < job.observed_sequence { return Err(Error::Storage); }
        (checkpoint.sequence, checkpoint.revision)
    } else {
        (job.observed_sequence, job.observed_checkpoint)
    };
    if tx.execute("UPDATE native_manual_source_jobs
        SET phase=?,version=version+1,observed_sequence=?,observed_checkpoint=?,last_action=?
        WHERE lane=? AND job=? AND version=?",
        params![next,sequence,revision,action.name(),lane,request.job_id,request.expected_version])? != 1 {
        return Err(Error::Storage);
    }
    let job = get(&tx,&lane,&request.job_id)?.ok_or(Error::Storage)?;
    tx.commit()?;
    Ok(job)
}

// A one-shot handoff on top of a specific existing manual job. This is
// discovery metadata, not an ingest lease or permission to run an adapter.
const HANDOFF_TTL: i64 = 120;
const HANDOFF_TABLE: &str = "
CREATE TABLE native_source_sync_handoff (
    lane TEXT PRIMARY KEY NOT NULL, job TEXT NOT NULL,
    job_version INTEGER NOT NULL CHECK(job_version > 0),
    queued_at INTEGER NOT NULL CHECK(queued_at >= 0),
    claimed INTEGER NOT NULL CHECK(claimed IN (0,1))
) WITHOUT ROWID;";

struct Pickup { job_id: String, job_version: i64, queued_at: i64, claimed: bool }

#[derive(Serialize)]
pub(crate) struct Handoff {
    state: &'static str,
    job_id: Option<String>,
    job_version: Option<i64>,
    expires_at_unix_seconds: Option<i64>,
    launches_adapter: bool,
    observes_adapter_installation: bool,
    completes_source_job: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    claim_replayed: Option<bool>,
}

fn clock_seconds() -> Result<i64, Error> {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map_err(|_|Error::Storage)?.as_secs().try_into().map_err(|_|Error::Storage)
}

fn handoff_installed(db:&Connection)->Result<bool,Error> {
    let kind:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name='native_source_sync_handoff' COLLATE NOCASE",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match kind.as_ref().map(|(n,t)|(n.as_str(),t.as_str())) {
        None => Ok(false),
        Some(("native_source_sync_handoff","table")) => Ok(true),
        _ => Err(Error::Storage),
    }
}

fn pickup(db:&Connection,lane:&str)->Result<Option<Pickup>,Error> {
    Ok(db.query_row(
        "SELECT job,job_version,queued_at,claimed FROM native_source_sync_handoff WHERE lane=?",
        [lane],|r|Ok(Pickup{job_id:r.get(0)?,job_version:r.get(1)?,queued_at:r.get(2)?,claimed:r.get(3)?})).optional()?)
}

fn handoff_view(pickup:Option<Pickup>, state:&'static str)->Result<Handoff,Error> {
    Ok(Handoff {
        expires_at_unix_seconds:pickup.as_ref().map(|p|p.queued_at.checked_add(HANDOFF_TTL)
            .ok_or(Error::Storage)).transpose()?,
        job_version:pickup.as_ref().map(|p|p.job_version),
        job_id:pickup.map(|p|p.job_id),
        state,launches_adapter:false,observes_adapter_installation:false,completes_source_job:false,
        claim_replayed:None,
    })
}

fn check_handoff_job(db:&Connection,lane:&str, job_id:&str, version:i64)->Result<Job,Error> {
    if !installed(db)? {return Err(Error::Missing);}
    let job=get(db,lane,job_id)?.ok_or(Error::Missing)?;
    if job.phase!="active" || job.version!=version {return Err(Error::Stale);}
    Ok(job)
}
fn timely(pickup:&Pickup,now:i64)->bool {
    now.checked_sub(pickup.queued_at).is_some_and(|age|(0..HANDOFF_TTL).contains(&age))
}

pub(crate) fn queue_sync(store:&EventStore,plugin:&str,scope:&AccountScope,
    request:Change)->Result<Handoff,Error> {
    if !job_id_valid(&request.job_id) || request.expected_version<1 {return Err(Error::Input);}
    let lane=lane(store,plugin,scope)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.require_ordinary()?;
    store.verify_connection(&tx)?;
    let now=clock_seconds()?;
    let job=check_handoff_job(&tx,&lane,&request.job_id,request.expected_version)?;
    let initialized=handoff_installed(&tx)?;
    if initialized {
        if let Some(previous)=pickup(&tx,&lane)? && previous.job_id==job.job_id {
            // A read-only exact retry never renews the existing deadline.
            if previous.job_version==job.version && !previous.claimed && timely(&previous,now) {
                return handoff_view(Some(previous),"pending_operator");
            }
            return Err(Error::Stale);
        }
    }
    crate::ingress_policy::admit(&tx)?;
    if !initialized{tx.execute_batch(HANDOFF_TABLE)?;}
    tx.execute("INSERT INTO native_source_sync_handoff (lane,job,job_version,queued_at,claimed)
                VALUES (?1,?2,?3,?4,0) ON CONFLICT(lane) DO UPDATE SET
                job=excluded.job,job_version=excluded.job_version,queued_at=excluded.queued_at,claimed=0",
        params![lane,job.job_id,job.version,now])?;
    let result=handoff_view(Some(Pickup{job_id:job.job_id,job_version:job.version,
        queued_at:now,claimed:false}),"pending_operator")?;
    tx.commit()?;
    Ok(result)
}

pub(crate) fn discover_sync(store:&EventStore,plugin:&str,scope:&AccountScope)->Result<Handoff,Error> {
    let lane=lane(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.require_ordinary()?;
    store.verify_connection(&tx)?;
    if !handoff_installed(&tx)? {return handoff_view(None,"no_intent");}
    let Some(request)=pickup(&tx,&lane)? else {return handoff_view(None,"no_intent");};
    let status=match check_handoff_job(&tx,&lane,&request.job_id,request.job_version) {
        Ok(_) if !timely(&request,clock_seconds()?) => "expired",
        Ok(_) if request.claimed => "claimed_by_grant",
        Ok(_) => "pending_operator",
        Err(Error::Missing|Error::Stale) => "no_longer_current",
        Err(e) => return Err(e),
    };
    handoff_view(Some(request),status)
}

pub(crate) fn claim_sync(store:&EventStore,plugin:&str,scope:&AccountScope,
    request:Change,credential:String)->Result<Handoff,Error> {
    let kind=ClaimCredentialKind::for_handoff(&scope.platform);
    if !job_id_valid(&credential) {return Err(Error::Input);}
    if !job_id_valid(&request.job_id) || request.expected_version<1 {return Err(Error::Input);}
    let lane=lane(store,plugin,scope)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.require_ordinary()?;
    store.verify_connection(&tx)?;
    let now=clock_seconds()?;
    if !handoff_installed(&tx)? {return Err(Error::Missing);}
    let mut intent=pickup(&tx,&lane)?.filter(|p|p.job_id==request.job_id).ok_or(Error::Missing)?;
    if intent.job_version!=request.expected_version || !timely(&intent,now) {
        return Err(Error::Stale);
    }
    check_handoff_job(&tx,&lane,&intent.job_id,intent.job_version)?;
    if intent.claimed {
        if !same_credential(&tx,kind,&lane,&intent.job_id,intent.job_version,&credential)? {
            return Err(Error::Stale);
        }
        let mut response=handoff_view(Some(intent),"claimed_by_grant")?;
        response.claim_replayed=Some(true);
        return Ok(response); // No row mutations and no lease extension.
    }
    crate::ingress_policy::admit(&tx)?;
    if tx.execute("UPDATE native_source_sync_handoff SET claimed=1
        WHERE lane=? AND job=? AND job_version=? AND claimed=0",
        params![lane,intent.job_id,intent.job_version])?!=1 {return Err(Error::Storage);}
    if !credential_table(&tx,kind)? {
        tx.execute_batch(&format!("CREATE TABLE {} (
            lane TEXT PRIMARY KEY NOT NULL,job TEXT NOT NULL,job_version INTEGER NOT NULL,
            credential TEXT NOT NULL CHECK(length(credential)=64)
        ) WITHOUT ROWID;",kind.table()))?;
    }
    tx.execute(&format!("INSERT INTO {}(lane,job,job_version,credential)
                VALUES(?,?,?,?) ON CONFLICT(lane) DO UPDATE SET
                job=excluded.job,job_version=excluded.job_version,credential=excluded.credential",kind.table()),
        params![lane,intent.job_id,intent.job_version,credential])?;
    intent.claimed=true;
    let mut result=handoff_view(Some(intent),"claimed_by_grant")?;
    result.claim_replayed=Some(false);
    tx.commit()?;
    Ok(result)
}

// Scope-specific bindings of the already authenticated 256-bit bearer.
// Neither binding is returned by HTTP or a reusable authentication token.
#[derive(Clone,Copy)]
enum ClaimCredentialKind { Wecom, Wechat, General }
impl ClaimCredentialKind {
    fn for_platform(platform:&str)->Option<Self> {
        match platform {"wecom"=>Some(Self::Wecom),"wechat"=>Some(Self::Wechat),_=>None}
    }
    fn for_handoff(platform:&str)->Self {
        Self::for_platform(platform).unwrap_or(Self::General)
    }
    fn table(self)->&'static str {
        match self {
            Self::Wecom=>"native_wecom_claim_credentials_v1",
            Self::Wechat=>"native_wechat_claim_credentials_v1",
            Self::General=>"native_source_claim_credentials_v1",
        }
    }
    fn bind(self,token:&str)->String {
        let mut hash=Sha256::new();
        hash.update(match self {
            Self::Wecom=>b"lmh-wecom-handoff-credential-v1\0".as_slice(),
            Self::Wechat=>b"lmh-wechat-handoff-credential-v1\0".as_slice(),
            Self::General=>b"lmh-generic-handoff-credential-v1\0".as_slice(),
        });
        hash.update(token.as_bytes());
        hex::encode(hash.finalize())
    }
}
pub(crate) fn handoff_credential(platform:&str,token:&str)->String {
    ClaimCredentialKind::for_handoff(platform).bind(token)
}

fn credential_table(db:&Connection,expected:ClaimCredentialKind)->Result<bool,Error>{
    let name=expected.table();
    let kind:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
        [name],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match kind.as_ref().map(|(n,t)|(n.as_str(),t.as_str())) {
        None=>Ok(false),
        Some((actual,"table")) if actual==name=>Ok(true),
        _=>Err(Error::Storage),
    }
}

fn same_credential(db:&Connection,kind:ClaimCredentialKind,lane:&str,job:&str,version:i64,
    expected:&str)->Result<bool,Error> {
    if !credential_table(db,kind)? {return Ok(false);}
    let credential:Option<String>=db.query_row(
        &format!("SELECT credential FROM {} WHERE lane=? AND job=? AND job_version=?",kind.table()),
        params![lane,job,version],|r|r.get(0)).optional()?;
    use subtle::ConstantTimeEq;
    Ok(credential.as_deref().is_some_and(|stored|
        bool::from(stored.as_bytes().ct_eq(expected.as_bytes()))))
}

// The linked offline importer can observe an existing claim, not create one.
#[cfg(target_os="linux")]
pub(crate) struct LocalDetachedClaim {
    pub(crate) lane:String,
    pub(crate) job:String,
    pub(crate) version:i64,
    pub(crate) credential:String,
    kind:ClaimCredentialKind,
}
#[cfg(target_os="linux")]
impl LocalDetachedClaim {
    pub(crate) fn new(store:&EventStore,plugin:&crate::plugins::Plugin,platform:&str,
        job:&str,version:i64,token:&str)->Result<Self,Error> {
        let kind=ClaimCredentialKind::for_platform(platform).ok_or(Error::Input)?;
        use crate::plugins::{Permission,PluginKind};
        let scope=plugin.account_scope.as_ref().filter(|scope|
            scope.platform==platform && plugin.kind==PluginKind::ScopedProducer
            && plugin.permissions.contains(&Permission::Ingest)
            && plugin.permissions.contains(&Permission::SourceJobsManage))
            .ok_or(Error::Input)?;
        if !job_id_valid(job) || !(1..=9_007_199_254_740_991).contains(&version) {
            return Err(Error::Input);
        }
        Ok(Self{lane:lane(store,&plugin.id,scope)?,job:job.to_owned(),version,
            credential:kind.bind(token),kind})
    }

    pub(crate) fn verify_in(&self,db:&Connection)->Result<(),EventError> {
        let result=(|| -> Result<(),Error> {
            check_handoff_job(db,&self.lane,&self.job,self.version)?;
            if !handoff_installed(db)? {return Err(Error::Missing);}
            let marker=pickup(db,&self.lane)?.ok_or(Error::Missing)?;
            if marker.job_id!=self.job || marker.job_version!=self.version
                || !marker.claimed || !timely(&marker,clock_seconds()?) {
                return Err(Error::Stale);
            }
            if !same_credential(db,self.kind,&self.lane,&self.job,self.version,&self.credential)? {
                return Err(Error::Stale);
            }
            Ok(())
        })();
        result.map_err(|error|match error {
            Error::Missing|Error::Stale => EventError::SourceCheckpointConflict,
            _ => EventError::Storage,
        })
    }
}
