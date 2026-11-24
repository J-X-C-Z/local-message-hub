//! Exact-owner lifecycle for generated text export BLOBs only.
//! Removing an artifact never reopens its run, edits source records, or
//! modifies backup files. SQLite logical deletion is not secure erasure.
use super::{ARTIFACT_MAX, Artifact, Connection, Error, EventStore, MAX_TIME, OptionalExtension,
    Principal, Result, Sha256, Digest, AccountScope, begin, principal, valid_id, valid_now, writer, params};
use serde::{Deserialize,Serialize};
use std::collections::BTreeSet;
use zeroize::Zeroizing;

const MAX_PAGE:usize=16;
const MAX_BATCH:usize=8;
const REMOVALS:&str="
CREATE TABLE IF NOT EXISTS native_export_artifact_removal_v1(
    owner TEXT NOT NULL, account TEXT NOT NULL, schedule_id TEXT NOT NULL,
    due_at INTEGER NOT NULL CHECK(due_at BETWEEN 0 AND 253402300799),
    format TEXT NOT NULL CHECK(format IN ('json','csv','txt','md')),
    byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 0 AND 1048576),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    removed_at INTEGER NOT NULL CHECK(removed_at BETWEEN 0 AND 253402300799),
    PRIMARY KEY(owner,schedule_id,due_at)
) WITHOUT ROWID;";
const PUBLISHED:&str="native_export_artifact_v1";
const REMOVED:&str="native_export_artifact_removal_v1";

#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Slot {
    pub schedule_id:String,
    pub due_at:i64,
}
impl Slot {
    pub(super) fn valid(&self)->bool {valid_id(&self.schedule_id)&&(0..=MAX_TIME).contains(&self.due_at)}
}
#[derive(Serialize)]
struct Entry {
    #[serde(flatten)]
    slot:Slot,
    format:String,
    bytes:usize,
}
#[derive(Serialize)]
pub(crate) struct Page {
    items:Vec<Entry>,
    next_after:Option<Slot>,
}
fn exists(db:&Connection,name:&str)->Result<bool> {
    let kind:Option<String>=db.query_row(
        "SELECT type FROM sqlite_schema WHERE name=?1 COLLATE NOCASE",[name],|r|r.get(0)).optional()?;
    match kind.as_deref(){None=>Ok(false),Some("table")=>Ok(true),_=>Err(Error::Storage)}
}
fn format_valid(value:&str)->bool {matches!(value,"json"|"csv"|"txt"|"md")}

/// Redacted inventory: no payload, checksum, preset/selection metadata, or
/// filesystem path. An arbitrary anchor only positions within the caller's lane.
pub(crate) fn list(store:&EventStore,plugin:&str,scope:&AccountScope,
    after:Option<&Slot>,limit:Option<usize>)->Result<Page> {
    let limit=limit.unwrap_or(MAX_PAGE);
    if !(1..=MAX_PAGE).contains(&limit)||after.is_some_and(|s|!s.valid()) {return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    if !exists(&tx,PUBLISHED)? {return Ok(Page{items:Vec::new(),next_after:None});}
    let mut query=tx.prepare("SELECT a.schedule_id,a.due_at,a.format,a.byte_count FROM native_export_artifact_v1 a
        JOIN native_export_run_v1 r ON r.owner=a.owner AND r.account=a.account
         AND r.schedule_id=a.schedule_id AND r.due_at=a.due_at AND r.state='rendered'
        WHERE a.owner=?1 AND a.account=?2
        AND (?3 IS NULL OR a.due_at<?3 OR (a.due_at=?3 AND a.schedule_id>?4 COLLATE BINARY))
        ORDER BY a.due_at DESC,a.schedule_id COLLATE BINARY ASC LIMIT ?5")?;
    let mut items=query.query_map(params![key.owner,key.account,after.map(|s|s.due_at),
        after.map(|s|s.schedule_id.as_str()),limit+1],|r|
        Ok(Entry{slot:Slot{schedule_id:r.get(0)?,due_at:r.get(1)?},format:r.get(2)?,bytes:r.get(3)?}))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if items.iter().any(|item|!item.slot.valid()||!format_valid(&item.format)||item.bytes>ARTIFACT_MAX) {
        return Err(Error::Storage);
    }
    let has_more=items.len()>limit;
    items.truncate(limit);
    let next_after=if has_more {items.last().map(|item|item.slot.clone())} else {None};
    Ok(Page{items,next_after})
}

#[derive(Serialize)]
struct Selection {
    artifact:Artifact,
    state:&'static str,
}
#[derive(Serialize)]
pub(crate) struct Preview {
    operation:&'static str,
    scope_boundary:&'static str,
    removes_source_or_backups:bool,
    secure_erasure:bool,
    selected:Vec<Selection>,
    reclaimable_bytes:usize,
}

// Kept in the confirming one-shot process, never interpreted from user
// JSON. The operator-frame digest binds the actual immutable preview fields.
pub(crate) struct Plan {
    key:Principal,
    preview:Preview,
}
impl Plan {
    pub(crate) fn description(&self)->&Preview {&self.preview}
}

struct Found { artifact:Artifact, removed:bool }

fn decode(slot:&Slot,format:String,byte_count:usize,sha256:String,body:Option<&[u8]>)->Result<Artifact> {
    if !format_valid(&format)||byte_count>ARTIFACT_MAX||!valid_id(&sha256)
        ||body.is_some_and(|bytes|bytes.len()!=byte_count||hex::encode(Sha256::digest(bytes))!=sha256) {
        return Err(Error::Storage);
    }
    Ok(Artifact{schedule_id:slot.schedule_id.clone(),due_at:slot.due_at,format,byte_count,sha256})
}

fn find(db:&Connection,key:&Principal,slot:&Slot,has_removed:bool,read_body:bool)->Result<Found> {
    let current:Option<(String,usize,String,Option<Vec<u8>>,bool)>=db.query_row(
        "SELECT a.format,a.byte_count,a.sha256,
            CASE WHEN ?5 AND a.byte_count BETWEEN 0 AND ?6
                      AND length(a.body)=a.byte_count AND typeof(a.body)='blob' THEN a.body END,
            a.byte_count BETWEEN 0 AND ?6 AND length(a.body)=a.byte_count AND typeof(a.body)='blob'
         FROM native_export_artifact_v1 a JOIN native_export_run_v1 r
          ON r.owner=a.owner AND r.account=a.account AND r.schedule_id=a.schedule_id
          AND r.due_at=a.due_at AND r.state='rendered'
         WHERE a.owner=?1 AND a.account=?2 AND a.schedule_id=?3 AND a.due_at=?4",
        params![key.owner,key.account,slot.schedule_id,slot.due_at,read_body,ARTIFACT_MAX],
        |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?))).optional()?;
    let removed:Option<(String,usize,String)>=if has_removed {
        db.query_row("SELECT d.format,d.byte_count,d.sha256 FROM native_export_artifact_removal_v1 d
            JOIN native_export_run_v1 r ON r.owner=d.owner AND r.account=d.account
             AND r.schedule_id=d.schedule_id AND r.due_at=d.due_at AND r.state='rendered'
            WHERE d.owner=?1 AND d.account=?2 AND d.schedule_id=?3 AND d.due_at=?4",
            params![key.owner,key.account,slot.schedule_id,slot.due_at],
            |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?
    }else{None};
    match (current,removed) {
        (Some((format,count,sha,body,true)),None) if !read_body||body.is_some()=>{
            let body=body.map(Zeroizing::new);
            let bytes=body.as_ref().map(|value|value.as_slice());
            Ok(Found{artifact:decode(slot,format,count,sha,bytes)?,removed:false})
        }
        (None,Some((format,count,sha)))=>Ok(Found{
            artifact:decode(slot,format,count,sha,None)?,removed:true}),
        (None,None)=>Err(Error::Missing),
        _=>Err(Error::Storage),
    }
}

/// Project only the metadata discriminator for a rendered run. A rendered
/// ledger must have one retained artifact or one retention tombstone.
pub(super) fn artifact_state_in(db:&Connection,key:&Principal,slot:&Slot)->Result<&'static str> {
    if !exists(db,PUBLISHED)? {return Err(Error::Storage);}
    match find(db,key,slot,exists(db,REMOVED)?,false) {
        Ok(found)=>Ok(if found.removed {"removed"} else {"retained"}),
        Err(Error::Missing)=>Err(Error::Storage),
        Err(err)=>Err(err),
    }
}

pub(crate) fn preview(store:&EventStore,plugin:&str,scope:&AccountScope,slots:&[Slot])->Result<Plan> {
    let mut unique=BTreeSet::new();
    if !(1..=MAX_BATCH).contains(&slots.len())||slots.iter().any(|slot|
        !slot.valid()||!unique.insert((&slot.schedule_id,slot.due_at))) {return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    if !exists(&tx,PUBLISHED)? {return Err(Error::Missing);}
    let has_removed=exists(&tx,REMOVED)?;
    let mut selected=Vec::new();
    let mut reclaimable_bytes=0;
    for slot in slots {
        let item=find(&tx,&key,slot,has_removed,false)?;
        if !item.removed {reclaimable_bytes+=item.artifact.byte_count;}
        selected.push(Selection{artifact:item.artifact,state:if item.removed{"already_removed"}else{"published"}});
    }
    tx.commit()?;
    Ok(Plan{key,preview:Preview{
        operation:"remove_selected_generated_export_blobs",
        scope_boundary:"this_principal_and_account_only",
        removes_source_or_backups:false,secure_erasure:false,
        selected,reclaimable_bytes,
    }})
}

#[derive(Serialize)]
pub(crate) struct Receipt {
    removed:usize,
    already_removed:usize,
    released_quota_bytes:usize,
}

pub(crate) fn apply(store:&EventStore,plugin:&str,scope:&AccountScope,plan:Plan,now:i64)->Result<Receipt> {
    if !valid_now(now){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    if key.owner!=plan.key.owner||key.account!=plan.key.account {return Err(Error::Missing);}
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    if !exists(&tx,PUBLISHED)? {return Err(Error::Missing);}
    let mut result=Receipt{removed:0,already_removed:0,released_quota_bytes:0};
    let has_removed=exists(&tx,REMOVED)?;
    for selection in plan.preview.selected {
        let selected=selection.artifact;
        let slot=Slot{schedule_id:selected.schedule_id.clone(),due_at:selected.due_at};
        let fresh=find(&tx,&key,&slot,has_removed,true)?;
        if fresh.artifact.byte_count!=selected.byte_count||fresh.artifact.format!=selected.format
            ||fresh.artifact.sha256!=selected.sha256 {
            return Err(Error::Confirmation);
        }
        if fresh.removed {
            result.already_removed+=1;
            continue;
        }
        // An original slot may become removed after preview, never the other way around.
        if selection.state=="already_removed" {return Err(Error::Confirmation);}
        tx.execute_batch(REMOVALS)?;
        if tx.execute("DELETE FROM native_export_artifact_v1
            WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND due_at=?4 AND sha256=?5",
            params![key.owner,key.account,slot.schedule_id,slot.due_at,selected.sha256])?!=1 {
            return Err(Error::Confirmation);
        }
        tx.execute("INSERT INTO native_export_artifact_removal_v1
            (owner,account,schedule_id,due_at,format,byte_count,sha256,removed_at) VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
            params![key.owner,key.account,slot.schedule_id,slot.due_at,selected.format,selected.byte_count,selected.sha256,now])?;
        result.removed+=1;
        result.released_quota_bytes+=selected.byte_count;
    }
    tx.commit()?;
    Ok(result)
}
