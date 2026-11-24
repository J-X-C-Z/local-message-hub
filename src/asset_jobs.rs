//! Session-local artifacts for already imported account-scoped attachments.
use crate::{media_bundle::{Bundle, MessageKey}, plugins::AccountScope};
use axum::body::Bytes;
use crate::stored_zip::StoredZip;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}},
    time::{Duration, Instant}};

const SOURCE_LIMIT: u64 = 4 * 1024 * 1024;
const ARTIFACT_LIMIT: usize = SOURCE_LIMIT as usize + 64 * 1024;
const ITEM_LIMIT: usize = 16;
const GLOBAL_JOBS: usize = 8;
const OWNER_JOBS: usize = 2;
const TTL: Duration = Duration::from_secs(300);

#[derive(Debug)]
pub(crate) enum Error { Invalid, Missing, Limit, Busy, Unavailable, Cancelled }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Selection {
    pub(crate) conversation_id: String,
    pub(crate) message_id: String,
    pub(crate) attachment_id: String,
    // Optional content pin from a previous artifact manifest.
    pub(crate) sha256: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Create { pub(crate) items: Vec<Selection> }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target { pub job_id: String }

#[derive(Serialize)]
struct Member {
    file: String,
    attachment_id: String,
    #[serde(skip_serializing_if="Option::is_none")]
    message_id:Option<String>,
    sha256: String,
    bytes: usize,
}
#[derive(Clone, Serialize)]
pub(crate) struct Job {
    job_id: String,
    state: &'static str,
    entries: usize,
    bytes: usize,
    sha256: String,
    expires_in_seconds: u64,
    #[serde(skip_serializing_if="Option::is_none")]
    failure:Option<&'static str>,
}
struct Entry {
    owner: String,
    includes_messages:bool,
    expires: Instant,
    info: Job,
    bytes:Option<Bytes>,
    worker:Option<Arc<AtomicBool>>,
}
impl Entry {
    fn info(&self, now: Instant) -> Job {
        let mut info = self.info.clone();
        info.expires_in_seconds = self.expires.saturating_duration_since(now).as_secs();
        info
    }
}

#[derive(Default)]
pub(crate) struct Jobs(Mutex<BTreeMap<String, Entry>>);

pub(crate) struct Work {
    id:String,
    owner:String,
    cancel:Arc<AtomicBool>,
    deadline:Instant,
}
impl Work {
    pub(crate) fn checkpoint(&self)->Result<(),Error>{
        if self.cancel.load(Ordering::Acquire) || Instant::now()>=self.deadline {
            return Err(Error::Cancelled);
        }
        Ok(())
    }
}
fn checkpoint(work:Option<&Work>)->Result<(),Error>{
    work.map_or(Ok(()),Work::checkpoint)
}

fn hash(bytes: &[u8]) -> String { hex::encode(Sha256::digest(bytes)) }
fn digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub(crate) fn owner(plugin: &str, scope: &AccountScope) -> Result<String, Error> {
    let bytes = serde_json::to_vec(&("lmh-resource-jobs-v1", plugin, &scope.platform, &scope.account_id))
        .map_err(|_|Error::Unavailable)?;
    Ok(hash(&bytes))
}

pub(crate) struct Artifact { bytes: Bytes, sha256: String, entries: usize, includes_messages:bool }

pub(crate) fn create(bundle: &Bundle, scope: &AccountScope, request: Create) -> Result<Artifact, Error> {
    assemble(bundle,scope,request,None,false,None)
}

pub(crate) fn create_with_messages(bundle:&Bundle,scope:&AccountScope,request:Create,
    messages:Vec<u8>,count:usize,work:Option<&Work>)->Result<Artifact,Error> {
    assemble(bundle,scope,request,Some((messages,count)),false,work)
}
pub(crate) fn create_filtered_text_only(bundle:&Bundle,scope:&AccountScope,messages:Vec<u8>,count:usize,work:Option<&Work>)
    ->Result<Artifact,Error> {
    assemble(bundle,scope,Create{items:Vec::new()},Some((messages,count)),true,work)
}
fn assemble(bundle:&Bundle,scope:&AccountScope,request:Create,
    text:Option<(Vec<u8>,usize)>,explicit_text_only:bool,work:Option<&Work>)->Result<Artifact,Error> {
    checkpoint(work)?;
    if request.items.len()>ITEM_LIMIT || (request.items.is_empty()&&!explicit_text_only) {
        return Err(Error::Invalid);
    }
    let mut planned = Vec::with_capacity(request.items.len());
    let mut refs = BTreeSet::new();
    let mut total=text.as_ref().map_or(0,|(v,_)|v.len() as u64);
    if total>SOURCE_LIMIT{return Err(Error::Limit);}
    for item in request.items {
        checkpoint(work)?;
        if !digest(&item.attachment_id) || item.sha256.as_ref().is_some_and(|v| !digest(v)) {
            return Err(Error::Invalid);
        }
        if !refs.insert(item.attachment_id.clone()) { return Err(Error::Invalid); }
        let key = MessageKey { platform:scope.platform.clone(), account_id:scope.account_id.clone(),
            conversation_id:item.conversation_id, message_id:item.message_id };
        if !key.valid() { return Err(Error::Invalid); }
        let selected = bundle.list(&key).iter().find(|a| a.id == item.attachment_id)
            .ok_or(Error::Missing)?;
        total += selected.size;
        if total > SOURCE_LIMIT { return Err(Error::Limit); }
        planned.push((key,item.attachment_id,item.sha256,selected.size as usize));
    }
    let mut zip=StoredZip::flat(total as usize + 4096,ARTIFACT_LIMIT,ITEM_LIMIT+2);
    let includes_messages=text.is_some();
    let mut members = Vec::with_capacity(planned.len());
    for (index,(key,id,pin,size)) in planned.into_iter().enumerate() {
        checkpoint(work)?;
        let file=format!("attachment-{:02}.bin",index+1);
        let check_pin=|bytes:&[u8], verified_sha256:Option<String>| {
            checkpoint(work)?;
            let sha256=verified_sha256.unwrap_or_else(||hash(bytes));
            if pin.as_ref().is_some_and(|expected| expected!=&sha256) {return Err(Error::Missing);}
            Ok(sha256)
        };
        let (sha256,bytes)=match zip.push_flat_direct(&file,size,|dst|{
            let verified_sha256=bundle.read_into(&key,&id,dst).map_err(|_|Error::Unavailable)?.ok_or(Error::Missing)?;
            check_pin(dst,Some(verified_sha256))
        }) {
            Some(result)=>(result?,size),
            None=>{
                let bytes=bundle.read(&key,&id).map_err(|_|Error::Unavailable)?.ok_or(Error::Missing)?;
                let sha256=check_pin(&bytes,None)?;
                zip.push(&file,&bytes).map_err(|_|Error::Limit)?;
                (sha256,bytes.len())
            }
        };
        members.push(Member { file, attachment_id:id, sha256, bytes,
            message_id:includes_messages.then_some(key.message_id) });
    }
    let entries=members.len()+if includes_messages {1} else {0};
    let manifest=if let Some((message_bytes,message_count))=text {
        zip.push("messages.json",&message_bytes).map_err(|_|Error::Limit)?;
        serde_json::json!({"format":"lmh-selected-message-bundle-v1","items":members,
            "messages":{"file":"messages.json","count":message_count,"size":message_bytes.len(),
                "sha256":hash(&message_bytes)}})
    } else {serde_json::json!({"format":"lmh-resource-job-v1","items":members})};
    let manifest=serde_json::to_vec(&manifest).map_err(|_|Error::Unavailable)?;
    zip.push("manifest.json",&manifest).map_err(|_|Error::Limit)?;
    checkpoint(work)?;
    let bytes=zip.finish().map_err(|_|Error::Limit)?;
    checkpoint(work)?;
    if bytes.len() > ARTIFACT_LIMIT { return Err(Error::Limit); }
    Ok(Artifact { sha256:hash(&bytes), bytes:Bytes::from(bytes), entries, includes_messages })
}

impl Jobs {
    fn access<T>(&self, fn_: impl FnOnce(&mut BTreeMap<String,Entry>, Instant) -> Result<T, Error>)
        -> Result<T, Error> {
        let mut entries = self.0.lock().map_err(|_|Error::Unavailable)?;
        let now = Instant::now();
        entries.retain(|_,e|{
            if e.expires>now {true}else{
                if let Some(signal)=&e.worker{signal.store(true,Ordering::Release);}
                false
            }
        });
        fn_(&mut entries,now)
    }

    pub(crate) fn room(&self, owner: &str) -> Result<(), Error> {
        self.access(|entries,_| check_room(entries,owner))
    }

    pub(crate) fn publish(&self, owner: String, artifact: Artifact) -> Result<Job, Error> {
        let mut key = [0_u8;32];
        getrandom::fill(&mut key).map_err(|_|Error::Unavailable)?;
        let job_id = hex::encode(key);
        self.access(|entries, now| {
            check_room(entries,&owner)?;
            if entries.contains_key(&job_id) { return Err(Error::Unavailable); }
            let info = Job { job_id:job_id.clone(), state:"ready", entries:artifact.entries,
                bytes:artifact.bytes.len(), sha256:artifact.sha256, expires_in_seconds:TTL.as_secs(),failure:None };
            entries.insert(job_id, Entry { owner, includes_messages:artifact.includes_messages, expires:now+TTL, info:info.clone(), bytes:Some(artifact.bytes),worker:None });
            Ok(info)
        })
    }

    pub(crate) fn reserve_filtered(&self,owner:String)->Result<(Job,Work),Error>{
        let mut key=[0_u8;32];
        getrandom::fill(&mut key).map_err(|_|Error::Unavailable)?;
        let id=hex::encode(key);
        self.access(|entries,now|{
            check_room(entries,&owner)?;
            if entries.contains_key(&id){return Err(Error::Unavailable);}
            let cancel=Arc::new(AtomicBool::new(false));
            let deadline=now+TTL;
            let info=Job{job_id:id.clone(),state:"queued",entries:0,bytes:0,
                sha256:String::new(),expires_in_seconds:TTL.as_secs(),failure:None};
            let work=Work{id:id.clone(),owner:owner.clone(),cancel:cancel.clone(),deadline};
            entries.insert(id,Entry{owner,includes_messages:true,expires:deadline,
                info:info.clone(),bytes:None,worker:Some(cancel)});
            Ok((info,work))
        })
    }

    pub(crate) fn start(&self,work:&Work)->Result<(),Error>{
        self.access(|entries,_|{
            work.checkpoint()?;
            let entry=work_entry(entries,work)?;
            if entry.info.state!="queued"{return Err(Error::Cancelled);}
            entry.info.state="running";
            Ok(())
        })
    }

    pub(crate) fn finish(&self,work:&Work,result:Result<Artifact,Error>)->Result<(),Error>{
        self.access(|entries,_|{
            let entry=work_entry(entries,work)?;
            // Cancellation, expiry and publication share the same map lock.
            // Starting and finishing a stale worker can never recreate an ID.
            if work.checkpoint().is_err() || entry.info.state!="running" {
                return Err(Error::Cancelled);
            }
            match result {
                Ok(artifact) if artifact.includes_messages=>{
                    entry.info.state="ready";
                    entry.info.entries=artifact.entries;
                    entry.info.bytes=artifact.bytes.len();
                    entry.info.sha256=artifact.sha256;
                    entry.bytes=Some(artifact.bytes);
                }
                other=>{
                    entry.info.state="failed";
                    entry.info.failure=Some(match other {
                        Err(Error::Invalid)=>"invalid_filter",
                        Err(Error::Missing)=>"selection_unavailable",
                        Err(Error::Limit)=>"hard_limit",
                        _=>"local_export_unavailable",
                    });
                }
            }
            entry.worker=None;
            Ok(())
        })
    }

    pub(crate) fn list(&self, owner: &str, include_mixed:bool) -> Result<Vec<Job>, Error> {
        self.access(|entries,now| Ok(entries.values().filter(|e| e.owner == owner && (include_mixed || !e.includes_messages))
            .map(|e|e.info(now)).collect()))
    }

    pub(crate) fn status(&self, owner: &str, id: &str, include_mixed:bool) -> Result<Job, Error> {
        if !digest(id) { return Err(Error::Invalid); }
        self.access(|entries,now| Ok(find(entries, owner, id,include_mixed)?.info(now)))
    }

    pub(crate) fn cancel(&self, owner: &str, id: &str, include_mixed:bool) -> Result<(), Error> {
        if !digest(id) { return Err(Error::Invalid); }
        self.access(|entries,_| {
            let item=find(entries,owner,id,include_mixed)?;
            if matches!(item.info.state,"queued"|"running"|"cancelled") {
                let item=entries.get_mut(id).ok_or(Error::Missing)?;
                if let Some(signal)=item.worker.take(){signal.store(true,Ordering::Release);}
                item.info.state="cancelled";
                item.bytes=None;
            }else{
                entries.remove(id);
            }
            Ok(())
        })
    }

    pub(crate) fn download(&self, owner: &str, id: &str, include_mixed:bool) -> Result<Bytes, Error> {
        if !digest(id) { return Err(Error::Invalid); }
        self.access(|entries,_| find(entries,owner,id,include_mixed)?.bytes.clone().ok_or(Error::Missing))
    }
}

fn check_room(entries: &BTreeMap<String,Entry>, owner: &str) -> Result<(),Error> {
    if entries.len() >= GLOBAL_JOBS || entries.values().filter(|e|e.owner == owner).count() >= OWNER_JOBS {
        Err(Error::Busy)
    } else { Ok(()) }
}
fn find<'a>(entries: &'a BTreeMap<String,Entry>, owner: &str, id: &str, include_mixed:bool) -> Result<&'a Entry,Error> {
    entries.get(id).filter(|e|e.owner == owner && (include_mixed || !e.includes_messages)).ok_or(Error::Missing)
}

fn work_entry<'a>(entries:&'a mut BTreeMap<String,Entry>,work:&Work)->Result<&'a mut Entry,Error>{
    let entry=entries.get_mut(&work.id).ok_or(Error::Missing)?;
    if entry.owner!=work.owner || !entry.includes_messages
        || !entry.worker.as_ref().is_some_and(|signal|Arc::ptr_eq(signal,&work.cancel))
    {return Err(Error::Cancelled);}
    Ok(entry)
}
