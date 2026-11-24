//! Linux-only one-shot operator connection, never a scheduler or a listener.
use crate::{
    events::EventStore,
    export_schedule::{self as schedule, recurring, retention, Artifact, Create},
    plugins::{AccountScope, Permission, PluginKind, Plugins},
};
use rustix::fs::{FileType, Mode, OFlags, fstat, open};
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    error::Error,
    fs::{self,File},
    io::{self, BufRead, Read, Write},
    path::Path,
    sync::mpsc,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const MAX_FRAME: u64 = 4096;
const MAX_GRANTS: u64 = 64 * 1024;
const INTERACTION: Duration = Duration::from_secs(40);
const MAX_FOREGROUND_WAIT:u64=900;
const MAX_DUE_SCHEDULES:usize=8;
const DISPATCH_BUDGET:Duration=Duration::from_secs(30);
const POLL_INTERVAL:Duration=Duration::from_secs(15);

fn error(message:&'static str)->io::Error {io::Error::new(io::ErrorKind::InvalidInput,message)}
fn now()->io::Result<i64> {
    SystemTime::now().duration_since(UNIX_EPOCH).ok()
        .and_then(|t|i64::try_from(t.as_secs()).ok()).ok_or_else(||error("operator clock unavailable"))
}
fn hex(s:&str,n:usize)->bool {
    s.len()==n && s.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
pub(crate) fn private_pipes()->io::Result<()> {
    let a=fstat(io::stdin()).map_err(|_|error("private input unavailable"))?;
    let b=fstat(io::stdout()).map_err(|_|error("private output unavailable"))?;
    let matches_pipe=|fd:&str,inode:u64,mode:u32| {
        FileType::from_raw_mode(mode as _) == FileType::Fifo &&
            fs::read_link(fd).is_ok_and(|target|target.as_os_str()==std::ffi::OsStr::new(&format!("pipe:[{inode}]")))
    };
    if a.st_ino==b.st_ino
        || !matches_pipe("/proc/self/fd/0",a.st_ino as u64,a.st_mode as u32)
        || !matches_pipe("/proc/self/fd/1",b.st_ino as u64,b.st_mode as u32) {
        return Err(error("expected separate private anonymous stdin/stdout pipes"));
    }
    Ok(())
}
fn bounded<T:Send+'static>(work:impl FnOnce()->io::Result<T>+Send+'static)->io::Result<T> {
    let (reply,receive)=mpsc::sync_channel(1);
    thread::Builder::new().spawn(move||{let _=reply.send(work());})?;
    match receive.recv_timeout(INTERACTION) {
        Ok(value)=>value,
        Err(mpsc::RecvTimeoutError::Timeout)=>Err(error("operator pipe timed out")),
        Err(mpsc::RecvTimeoutError::Disconnected)=>Err(error("operator pipe unavailable")),
    }
}
pub(crate) fn read_frame<T:serde::de::DeserializeOwned>()->io::Result<T> {
    let bytes=bounded(|| {
        let mut input=io::stdin().lock();
        let mut bytes=Zeroizing::new(Vec::new());
        input.by_ref().take(MAX_FRAME+1).read_until(b'\n',&mut bytes)?;
        if bytes.len() as u64>MAX_FRAME||!bytes.ends_with(b"\n") {
            return Err(error("expected one bounded JSON line"));
        }
        Ok(bytes)
    })?;
    serde_json::from_slice(&bytes).map_err(|_|error("invalid operator frame"))
}
pub(crate) fn reply(value:impl Serialize,body:Option<Vec<u8>>)->io::Result<()> {
    let mut metadata=serde_json::to_vec(&value).map_err(|_|error("invalid operator reply"))?;
    metadata.push(b'\n');
    if metadata.len()>16*1024 {return Err(error("operator reply too large"));}
    bounded(move|| {
        let mut out=io::stdout().lock();
        out.write_all(&metadata)?;
        if let Some(body)=body { out.write_all(&body)?; }
        out.flush()
    })
}
pub(crate) fn private_grants(path:&Path)->io::Result<Plugins> {
    let file:File=open(path,OFlags::RDONLY|OFlags::NOFOLLOW|OFlags::NONBLOCK|OFlags::CLOEXEC,Mode::empty())
        .map_err(|_|error("private operator grant file unavailable"))?.into();
    let meta=fstat(&file).map_err(|_|error("private operator grant file unavailable"))?;
    if FileType::from_raw_mode(meta.st_mode)!=FileType::RegularFile || meta.st_mode&0o077!=0
        || meta.st_nlink!=1 || meta.st_uid!=rustix::process::geteuid().as_raw() {
        return Err(error("operator grant file must be private and current-owner"));
    }
    let mut bytes=Zeroizing::new(Vec::new());
    file.take(MAX_GRANTS+1).read_to_end(&mut bytes)
        .map_err(|_|error("private operator grant file unavailable"))?;
    Plugins::parse(&bytes).map_err(|_|error("invalid operator grant file"))
}

struct Secret(Zeroizing<String>);
impl<'de> Deserialize<'de> for Secret {
    fn deserialize<D:Deserializer<'de>>(d:D)->Result<Self,D::Error> {
        String::deserialize(d).map(|s|Self(Zeroizing::new(s)))
    }
}
#[derive(Serialize,Deserialize)]
#[serde(tag="kind",rename_all="snake_case",deny_unknown_fields)]
enum Action {
    List,
    InspectSchedule { schedule_id:String },
    InspectRun { slot:retention::Slot },
    ListArtifacts { after:Option<retention::Slot>, limit:Option<usize> },
    RemoveArtifacts { slots:Vec<retention::Slot> },
    Create { preset_id:String, preset_revision:i64, interval_seconds:i64 },
    Cancel { schedule_id:String },
    CancelAttempt { target:schedule::cancel_attempt::Attempt },
    RunDue { schedule_id:String },
    Retrieve { schedule_id:String, due_at:i64 },
    EnableRecurring { schedule_id:String, expires_at:i64, max_artifacts:i64 },
    RevokeRecurring { schedule_id:String },
    RecurringStatus { schedule_id:String },
    RunRecurring { schedule_id:String, max_wait_seconds:u64 },
    RunRecurringDue { schedule_ids:Vec<String> },
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    installation_id:String,
    grant_id:String,
    scope:AccountScope,
    token:Secret,
    action:Action,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Confirmation { confirm:Secret }

fn authorize(path:&Path,request:&Request)->io::Result<()> {
    let grant=private_grants(path)?.authenticate_token(&request.token.0)
        .filter(|g|g.id==request.grant_id&&g.kind==PluginKind::Desktop
            && [Permission::Messages,Permission::MessageExport,Permission::ExportPresets].iter()
                .all(|permission|g.permissions.contains(permission)))
        .ok_or_else(||error("operator grant rejected"))?;
    if !grant.account_scope.as_ref().is_some_and(|scope|
        scope.platform==request.scope.platform&&scope.account_id==request.scope.account_id) {
        return Err(error("operator grant account mismatch"));
    }
    Ok(())
}
fn approval(expected:&str)->io::Result<()> {
    let frame:Confirmation=read_frame()?;
    if !bool::from(frame.confirm.0.as_bytes().ct_eq(expected.as_bytes())) {
        return Err(error("operator confirmation mismatch"));
    }
    Ok(())
}
fn challenge(request:&Request,artifact:Option<&Artifact>)->io::Result<()> {
    challenge_with(request,artifact,None,None)
}
fn challenge_with(request:&Request,artifact:Option<&Artifact>,
                  offer:Option<&recurring::Offer>,
                  removal:Option<&retention::Preview>)->io::Result<()> {
    let mut nonce=[0;32];
    getrandom::fill(&mut nonce).map_err(|_|error("operator confirmation unavailable"))?;
    let commitment=serde_json::to_vec(&("lmh-export-operator-v1",hex::encode(nonce),
        &request.installation_id,&request.grant_id,&request.scope,&request.action,artifact,offer,removal))
        .map_err(|_|error("invalid operator confirmation"))?;
    let digest=hex::encode(Sha256::digest(commitment));
    reply(json!({"event":"confirm_operation","installation_id":request.installation_id,
        "grant_id":request.grant_id,"scope":request.scope,"action":request.action,
        "artifact":artifact,"recurring_offer":offer,"artifact_removal":removal,"confirmation":digest}),None)?;
    approval(&digest)
}

fn action_error(issue:schedule::Error)->io::Error {
    use schedule::Error::*;
    error(match issue {
        Invalid=>"invalid export operation",
        Missing=>"export selection unavailable",
        StalePreset=>"export preset no longer matches",
        Busy=>"an export attempt is already active",
        StaleLease=>"export attempt expired or changed",
        ClockRegression=>"export clock moved backwards",
        Confirmation=>"artifact confirmation mismatch",
        RecurringDenied=>"recurring export not authorized for this grant and schedule",
        Quota=>"local export quota exceeded",
        Render=>"could not render scoped export",
        Storage=>"local export store unavailable",
    })
}

// Rotation of a current grant token requires new explicit recurring approval.
// The archive keeps only a domain-separated fingerprint, never the token.
fn authority(request:&Request)->String {
    let mut digest=Sha256::new();
    digest.update(b"lmh-recurring-operator-authority-v1\0");
    digest.update(request.installation_id.as_bytes());
    digest.update(request.token.0.as_bytes());
    hex::encode(digest.finalize())
}

struct Clock { wall_start:i64, monotonic_start:Instant }
impl Clock {
    fn start()->io::Result<Self> {
        Ok(Self{wall_start:now()?,monotonic_start:Instant::now()})
    }
    fn read(&self)->io::Result<i64> {
        let wall=now()?;
        let elapsed=i64::try_from(self.monotonic_start.elapsed().as_secs())
            .map_err(|_|error("operator clock unavailable"))?;
        let projected=self.wall_start.checked_add(elapsed).ok_or_else(||error("operator clock unavailable"))?;
        if wall.abs_diff(projected)>2 {return Err(error("wall clock changed during foreground export"));}
        Ok(wall)
    }
}

fn require_parent()->io::Result<()> {
    use rustix::process::{getppid,set_parent_process_death_signal,Signal};
    let parent=getppid().filter(|pid|pid.as_raw_pid()>1)
        .ok_or_else(||error("foreground operator parent unavailable"))?;
    set_parent_process_death_signal(Some(Signal::KILL))
        .map_err(|_|error("foreground operator parent unavailable"))?;
    if getppid()!=Some(parent) {return Err(error("foreground operator parent changed"));}
    Ok(())
}

fn run_recurring(archive:&EventStore,grants:&Path,request:&Request,id:&str,wait:u64)->io::Result<()> {
    if wait>MAX_FOREGROUND_WAIT {return Err(error("foreground wait exceeds limit"));}
    require_parent()?;
    let authority=authority(request);
    let clock=Clock::start()?;
    let wait=Duration::from_secs(wait);
    let mut first=true;
    loop {
        if !first && clock.monotonic_start.elapsed()>wait {
            return reply(json!({"event":"no_recurring_run_claimed"}),None);
        }
        first=false;
        authorize(grants,request)?;
        let time=clock.read()?;
        let permit=recurring::current(archive,&request.grant_id,&request.scope,id,&authority,time)
            .map_err(action_error)?;
        match schedule::claim_recurring(archive,&request.grant_id,&request.scope,id,time,&permit) {
            Ok(Some(ticket))=>{
                let prepared=schedule::prepare_recurring(archive,&request.grant_id,&request.scope,ticket,&permit,clock.read()?)
                    .map_err(action_error)?;
                authorize(grants,request)?;
                let sealed=schedule::publish_recurring(archive,&request.grant_id,&request.scope,prepared,&permit,clock.read()?)
                    .map_err(action_error)?;
                return reply(json!({"event":"recurring_artifact_published","artifact":sealed}),None);
            }
            Ok(None)|Err(schedule::Error::Busy)=>(),
            Err(issue)=>return Err(action_error(issue)),
        }
        let remaining=wait.saturating_sub(clock.monotonic_start.elapsed());
        if remaining.is_zero() {
            return reply(json!({"event":"no_recurring_run_claimed"}),None);
        }
        thread::sleep(remaining.min(POLL_INTERVAL));
    }
}

/// Executes at most one foreground operation. The launcher owns the current
/// archive/credentials, private endpoints, and the human confirmation step.
#[derive(Serialize)]
#[serde(tag="status",rename_all="snake_case")]
enum DueOutcome {
    Published { artifact:Artifact },
    NotDue,
    Busy,
    Unavailable,
    Quota,
}

#[derive(Serialize)]
struct DueRecord<'a> {schedule_id:&'a str, #[serde(flatten)] outcome:DueOutcome}

#[derive(Serialize)]
struct DueResult<'a> {
    items:Vec<DueRecord<'a>>,
    // If set, the failed slot may have claimed a lease, but did not report an artifact.
    stopped:Option<&'static str>,
}

fn due_ids(ids:&[String])->io::Result<()> {
    if !(1..=MAX_DUE_SCHEDULES).contains(&ids.len())
        || !ids.iter().all(|id|hex(id,64))
        || ids.iter().collect::<BTreeSet<_>>().len()!=ids.len() {
        return Err(error("expected one to eight distinct recurring schedule IDs"));
    }
    Ok(())
}

fn dispatch_due<'a>(ids:&'a [String],
    mut step:impl FnMut(&str)->Result<DueOutcome,&'static str>)->io::Result<DueResult<'a>> {
    due_ids(ids)?;
    let mut report=DueResult{items:Vec::with_capacity(ids.len()),stopped:None};
    for id in ids {
        match step(id) {
            Ok(outcome)=>report.items.push(DueRecord{schedule_id:id,outcome}),
            Err(reason)=>{report.stopped=Some(reason);break;}
        }
    }
    Ok(report)
}

fn due_error(issue:schedule::Error)->Result<DueOutcome,&'static str> {
    use schedule::Error::*;
    match issue {
        Missing|StalePreset|StaleLease|RecurringDenied=>Ok(DueOutcome::Unavailable),
        Busy=>Ok(DueOutcome::Busy),
        Quota=>Ok(DueOutcome::Quota),
        ClockRegression=>Err("clock_regressed"),
        _=>Err("local_export_failed"),
    }
}

fn drive_due(archive:&EventStore,owner:&str,scope:&AccountScope,authority:&str,id:&str,
    wall:impl Fn()->Result<i64,&'static str>,check_grant:impl Fn()->Result<(),&'static str>)
    ->Result<DueOutcome,&'static str> {
    check_grant()?;
    let time=wall()?;
    let permit=match recurring::current(archive,owner,scope,id,authority,time){
        Ok(permit)=>permit,
        Err(issue)=>return due_error(issue),
    };
    let ticket=match schedule::claim_recurring(archive,owner,scope,id,time,&permit) {
        Ok(Some(ticket))=>ticket,
        Ok(None)=>return Ok(DueOutcome::NotDue),
        Err(issue)=>return due_error(issue),
    };
    check_grant()?;
    let prepared=match schedule::prepare_recurring(archive,owner,scope,ticket,&permit,wall()?) {
        Ok(prepared)=>prepared,
        Err(issue)=>return due_error(issue),
    };
    check_grant()?;
    match schedule::publish_recurring(archive,owner,scope,prepared,&permit,wall()?) {
        Ok(artifact)=>Ok(DueOutcome::Published{artifact}),
        Err(issue)=>due_error(issue),
    }
}

fn run_recurring_due(archive:&EventStore,grants:&Path,request:&Request,ids:&[String])->io::Result<()> {
    require_parent()?;
    let authority=authority(request);
    let clock=Clock::start()?;
    let wall=||clock.read().map_err(|_|"clock_unavailable");
    let check_grant=||authorize(grants,request).map_err(|_|"grant_unavailable");
    let report=dispatch_due(ids,|id|{
        if clock.monotonic_start.elapsed()>=DISPATCH_BUDGET {return Err("dispatcher_budget");}
        drive_due(archive,&request.grant_id,&request.scope,&authority,id,wall,check_grant)
    })?;
    reply(json!({"event":"recurring_due_dispatch","result":report}),None)
}

pub fn run(directory:&Path,grants:&Path)->Result<(),Box<dyn Error>> {
    private_pipes()?;
    let request:Request=read_frame()?;
    if !hex(&request.installation_id,32)||!hex(&request.token.0,64) {
        return Err(error("bad installation id or operator token").into());
    }
    authorize(grants,&request)?;
    let archive=EventStore::open(directory).map_err(|_|error("local export archive unavailable"))?;
    if archive.archive_id()!=request.installation_id {return Err(error("installation id mismatch").into());}
    let id=&request.grant_id;
    let scope=&request.scope;
    match &request.action {
        Action::List=>{
            let items=schedule::schedules(&archive,id,scope).map_err(action_error)?;
            reply(json!({"event":"schedule_list","items":items}),None)?;
        }
        Action::InspectSchedule{schedule_id}=>{
            let observation=schedule::inspect::schedule(&archive,id,scope,schedule_id,now()?)
                .map_err(action_error)?;
            reply(json!({"event":"schedule_inspection","observation":observation}),None)?;
        }
        Action::InspectRun{slot}=>{
            let observation=schedule::inspect::run(&archive,id,scope,slot,now()?)
                .map_err(action_error)?;
            reply(json!({"event":"schedule_run_inspection","observation":observation}),None)?;
        }
        Action::ListArtifacts{after,limit}=>{
            let page=retention::list(&archive,id,scope,after.as_ref(),*limit).map_err(action_error)?;
            reply(json!({"event":"artifact_list","page":page}),None)?;
        }
        Action::RemoveArtifacts{slots}=>{
            let plan=retention::preview(&archive,id,scope,slots).map_err(action_error)?;
            challenge_with(&request,None,None,Some(plan.description()))?;
            authorize(grants,&request)?;
            let receipt=retention::apply(&archive,id,scope,plan,now()?).map_err(action_error)?;
            reply(json!({"event":"artifact_removal_committed","receipt":receipt}),None)?;
        }
        Action::Create{preset_id,preset_revision,interval_seconds}=>{
            challenge(&request,None)?;
            authorize(grants,&request)?;
            let saved=schedule::create(&archive,id,scope,now()?,Create{preset_id:preset_id.clone(),
                preset_revision:*preset_revision,interval_seconds:*interval_seconds}).map_err(action_error)?;
            reply(json!({"event":"schedule_created","schedule":saved}),None)?;
        }
        Action::Cancel{schedule_id}=>{
            challenge(&request,None)?;
            authorize(grants,&request)?;
            schedule::cancel(&archive,id,scope,schedule_id).map_err(action_error)?;
            reply(json!({"event":"schedule_cancelled","schedule_id":schedule_id}),None)?;
        }
        Action::CancelAttempt{target}=>{
            schedule::cancel_attempt::preflight(&archive,id,scope,target,now()?)
                .map_err(action_error)?;
            // Includes the exact chosen lease tuple, archive, grant and scope.
            challenge(&request,None)?;
            authorize(grants,&request)?;
            schedule::cancel_attempt::cancel(&archive,id,scope,target,now()?)
                .map_err(action_error)?;
            reply(json!({"event":"schedule_attempt_cancelled","schedule_id":target.schedule_id,
                "due_at":target.due_at,"attempt":target.attempt}),None)?;
        }
        Action::RunDue{schedule_id}=>{
            challenge(&request,None)?;
            authorize(grants,&request)?;
            let Some(ticket)=schedule::claim(&archive,id,scope,schedule_id,now()?).map_err(action_error)? else {
                reply(json!({"event":"no_due_run"}),None)?;
                return Ok(());
            };
            let prepared=schedule::prepare(&archive,id,scope,ticket,now()?).map_err(action_error)?;
            let (artifact,digest)=prepared.description();
            let confirmation=Zeroizing::new(digest.ok_or_else(||error("manual export receipt unavailable"))?.to_owned());
            reply(json!({"event":"confirm_artifact","artifact":artifact,"confirmation":confirmation.as_str()}),None)?;
            approval(&confirmation)?;
            authorize(grants,&request)?;
            let saved=schedule::publish(&archive,id,scope,prepared,&confirmation,now()?).map_err(action_error)?;
            reply(json!({"event":"artifact_published","artifact":saved}),None)?;
        }
        Action::EnableRecurring{schedule_id,expires_at,max_artifacts}=>{
            let offer=recurring::preview(&archive,id,scope,schedule_id,now()?,*expires_at,*max_artifacts)
                .map_err(action_error)?;
            challenge_with(&request,None,Some(&offer),None)?;
            authorize(grants,&request)?;
            let enabled=recurring::enable(&archive,id,scope,offer,&authority(&request),now()?)
                .map_err(action_error)?;
            reply(json!({"event":"recurring_enabled","authorization":enabled}),None)?;
        }
        Action::RevokeRecurring{schedule_id}=>{
            challenge(&request,None)?;
            authorize(grants,&request)?;
            recurring::revoke(&archive,id,scope,schedule_id).map_err(action_error)?;
            reply(json!({"event":"recurring_revoked"}),None)?;
        }
        Action::RecurringStatus{schedule_id}=>{
            let permit=recurring::current(&archive,id,scope,schedule_id,&authority(&request),now()?)
                .map_err(action_error)?;
            reply(json!({"event":"recurring_status","authorization":permit.state}),None)?;
        }
        Action::RunRecurring{schedule_id,max_wait_seconds}=>{
            run_recurring(&archive,grants,&request,schedule_id,*max_wait_seconds)?;
        }
        Action::RunRecurringDue{schedule_ids}=>{
            run_recurring_due(&archive,grants,&request,schedule_ids)?;
        }
        Action::Retrieve{schedule_id,due_at}=>{
            let metadata = schedule::published_meta(&archive,id,scope,schedule_id,*due_at)
                .map_err(action_error)?.ok_or_else(||error("published artifact unavailable"))?;
            challenge(&request,Some(&metadata))?;
            authorize(grants,&request)?;
            let (checked,body)=schedule::published(&archive,id,scope,schedule_id,*due_at)
                .map_err(action_error)?.ok_or_else(||error("published artifact unavailable"))?;
            if checked.sha256!=metadata.sha256 || checked.byte_count!=metadata.byte_count
                || checked.format!=metadata.format {
                return Err(error("published artifact changed").into());
            }
            reply(json!({"event":"artifact_bytes","artifact":checked}),Some(body))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod due_tests {
    use super::*;
    use crate::{events::{IngestRequest,NormalizedMessage},export_presets};
    use std::{cell::Cell,os::unix::fs::PermissionsExt};

    #[test]
    fn dispatch_refuses_duplicate_or_bad_ids_before_effects_and_surfaces_partial_result() {
        let a="a".repeat(64);
        let b="b".repeat(64);
        let c="c".repeat(64);
        for invalid in [vec![a.clone(),a.clone()],vec![a.clone(),"bad".into()]] {
            assert!(dispatch_due(&invalid,|_|panic!("invalid list started")).is_err());
        }
        let ids=[a.clone(),b.clone(),c.clone()];
        let result=dispatch_due(&ids,|id|match id {
            value if value==a=>Ok(DueOutcome::NotDue),
            value if value==b=>Err("grant_unavailable"),
            _=>panic!("dispatcher continued after grant loss"),
        }).unwrap();
        assert_eq!(result.items.len(),1);
        assert_eq!(result.items[0].schedule_id,a);
        assert_eq!(result.stopped,Some("grant_unavailable"));
    }

    #[test]
    fn mixed_due_dispatch_only_consumes_exact_approved_and_rechecks_before_publish() {
        let dir=tempfile::tempdir().unwrap();
        fs::set_permissions(dir.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let archive=EventStore::init(dir.path()).unwrap();
        let scope=AccountScope{platform:"wechat".into(),account_id:"synthetic".into()};
        let owner="desktop.fixture";
        let proof="f".repeat(64);
        archive.ingest("source.fixture",IngestRequest{idempotency_key:"case".into(),messages:vec![NormalizedMessage{
            platform:scope.platform.clone(),account_id:scope.account_id.clone(),
            conversation_id:"group".into(),message_id:"message".into(),
            title:None,sender_id:None,sender_name:None,timestamp:100,
            is_self:false,text:Some("SYNTHETIC_CONTENT".into()),
        }]}).unwrap();
        let create=|label:&str,allowed| {
            let preset=export_presets::save(&archive,owner,&scope,serde_json::from_value(json!({
                "expected_revision":0,"spec":{"label":label,"format":"txt","conversation_ids":["group"]}
            })).unwrap()).unwrap();
            let preset_id=serde_json::to_value(preset).unwrap()["preset_id"].as_str().unwrap().to_owned();
            let s=schedule::create(&archive,owner,&scope,1000,Create{
                preset_id,preset_revision:1,interval_seconds:900
            }).unwrap();
            if allowed {
                let offer=recurring::preview(&archive,owner,&scope,&s.id,1000,4000,1).unwrap();
                recurring::enable(&archive,owner,&scope,offer,&proof,1000).unwrap();
            }
            s.id
        };
        let granted=create("Granted",true);
        let blocked=create("Blocked",false);
        let revoked_midstep=create("Interrupted",true);
        let ids=[blocked.clone(),granted.clone(),revoked_midstep.clone()];
        let checks=Cell::new(0);
        let result=dispatch_due(&ids,|id|drive_due(&archive,owner,&scope,&proof,id,||Ok(1900),||{
            if id==revoked_midstep {
                checks.set(checks.get()+1);
                if checks.get()==3 {return Err("grant_unavailable");}
            }
            Ok(())
        })).unwrap();
        assert_eq!(result.items.len(),2);
        assert!(matches!(result.items[0].outcome,DueOutcome::Unavailable));
        assert!(matches!(result.items[1].outcome,DueOutcome::Published{..}));
        assert_eq!(result.stopped,Some("grant_unavailable"));
        let text=serde_json::to_string(&result).unwrap();
        assert!(!text.contains("SYNTHETIC_CONTENT"));
        assert!(schedule::published_meta(&archive,owner,&scope,&blocked,1900).unwrap().is_none());
        assert!(schedule::published_meta(&archive,owner,&scope,&revoked_midstep,1900).unwrap().is_none());
        let (_,body)=schedule::published(&archive,owner,&scope,&granted,1900).unwrap().unwrap();
        assert!(String::from_utf8(body).unwrap().contains("SYNTHETIC_CONTENT"));
        assert!(matches!(drive_due(&archive,owner,&scope,&proof,&granted,||Ok(2800),||Ok(())).unwrap(),
            DueOutcome::Unavailable)); // one-artifact allowance was consumed


        // Inspect a particular slot through the same action/body parser used
        // by the private entry point; unknown/foreign are not distinguished.
        let action:Action=serde_json::from_value(json!({
            "kind":"inspect_run","slot":{"schedule_id":granted,"due_at":1900}
        })).unwrap();
        let Action::InspectRun{slot:old}=action else {panic!("wrong action")};
        let note=|scope:&AccountScope,slot:&retention::Slot,time|serde_json::to_value(
            schedule::inspect::run(&archive,owner,scope,slot,time).unwrap()).unwrap();
        assert_eq!(note(&scope,&old,1900)["state"],"published");
        assert_eq!(note(&scope,&old,1900)["artifact_state"],"retained");
        let never=retention::Slot{schedule_id:blocked,due_at:1900};
        assert_eq!(note(&scope,&never,1900)["state"],"not_recorded");
        let pending=retention::Slot{schedule_id:revoked_midstep,due_at:1900};
        assert_eq!(note(&scope,&pending,1900)["state"],"leased");
        assert_eq!(note(&scope,&pending,2200)["state"],"lease_elapsed");
        assert_eq!(note(&scope,&pending,1900)["state"],"leased");
        let foreign=AccountScope{platform:"wechat".into(),account_id:"foreign".into()};
        assert!(matches!(schedule::inspect::run(&archive,owner,&foreign,&old,1900),
            Err(schedule::Error::Missing)));

        // A later real synthetic publication does not overwrite older status.
        let ticket=schedule::claim(&archive,owner,&scope,&old.schedule_id,2800).unwrap().unwrap();
        let prepared=schedule::prepare(&archive,owner,&scope,ticket,2800).unwrap();
        let confirmed=prepared.description().1.unwrap().to_owned();
        schedule::publish(&archive,owner,&scope,prepared,&confirmed,2800).unwrap();
        let later=retention::Slot{schedule_id:old.schedule_id.clone(),due_at:2800};
        let plan=retention::preview(&archive,owner,&scope,std::slice::from_ref(&old)).unwrap();
        retention::apply(&archive,owner,&scope,plan,2800).unwrap();
        let previous=note(&scope,&old,2800);
        assert_eq!(previous["state"],"published");
        assert_eq!(previous["artifact_state"],"removed");
        assert_eq!(note(&scope,&later,2800)["artifact_state"],"retained");
        assert!(!previous.to_string().contains("SYNTHETIC_CONTENT"));
        assert!(previous.get("sha256").is_none());
    }
}
