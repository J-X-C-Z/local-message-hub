//! Archive-wide ordinary message ingress pause; only the local OS owner can
//! change it using a separate operator command. No HTTP or plugin setter.
use crate::events::{EventError,EventStore};
use rusqlite::{Connection,OptionalExtension};
use serde::Serialize;

const MAX_REVISION:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_message_ingress_policy_v1(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    paused INTEGER NOT NULL CHECK(paused IN (0,1))
) WITHOUT ROWID;";
type Result<T>=std::result::Result<T,EventError>;

#[derive(Clone,Copy,Serialize)]
struct Policy {revision:i64,paused:bool}
fn current(db:&Connection)->Result<Policy> {
    let seen:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE='native_message_ingress_policy_v1'",
        [],|row|Ok((row.get(0)?,row.get(1)?))).optional()?;
    match seen.as_ref().map(|(n,t)|(n.as_str(),t.as_str())) {
        None=>Ok(Policy{revision:0,paused:false}),
        Some(("native_message_ingress_policy_v1","table"))=>{
            let (n,revision,paused):(i64,i64,i64)=db.query_row(
                "SELECT count(*),coalesce(max(CASE WHEN singleton=1 THEN revision END),0),
                    coalesce(max(CASE WHEN singleton=1 THEN paused END),-1)
                 FROM native_message_ingress_policy_v1",[],
                |row|Ok((row.get(0)?,row.get(1)?,row.get(2)?)))?;
            if n!=1 || !(1..=MAX_REVISION).contains(&revision) || !matches!(paused,0|1) {
                return Err(EventError::Storage);
            }
            Ok(Policy{revision,paused:paused==1})
        }
        _=>Err(EventError::Storage),
    }
}

/// Use only after opening and verifying an immediate writer transaction.
/// A preflight outside that transaction does not grant write permission.
pub(crate) fn admit(db:&Connection)->Result<()> {
    if current(db)?.paused {Err(EventError::IngressPaused)} else {Ok(())}
}

/// An observation for an already-authorized settings reader. No policy
/// revision or archive identity escapes from this helper.
pub(crate) fn observe_paused(store:&EventStore)->Result<bool> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    Ok(current(&tx)?.paused)
}

#[cfg(target_os="linux")]
fn check_owner(store:&EventStore)->Result<()> {
    use std::os::unix::fs::MetadataExt;
    let uid=rustix::process::geteuid().as_raw();
    let path=store.path();
    let parent=path.parent().ok_or(EventError::UnsafeDirectory)?;
    let root=std::fs::symlink_metadata(parent)?;
    let file=std::fs::symlink_metadata(path)?;
    if !root.is_dir()||!file.is_file()||file.nlink()!=1
        ||root.uid()!=uid||file.uid()!=uid||root.mode()&0o077!=0||file.mode()&0o077!=0 {
        return Err(EventError::UnsafeDirectory);
    }
    Ok(())
}

/// Linux administrative surface: OS archive ownership, durable exact
/// archive ID, and an explicit compare-and-set together identify a change.
#[cfg(target_os="linux")]
pub fn operator(directory:&std::path::Path,archive_id:&str,change:Option<(i64,bool)>)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    if archive_id.len()!=32||!archive_id.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)) {
        return Err(EventError::BadRequest("invalid expected archive instance").into());
    }
    let store=EventStore::open(directory)?;
    if store.archive_id()!=archive_id {
        return Err(EventError::BadRequest("archive instance mismatch").into());
    }
    check_owner(&store)?;
    let (state,changed)=if let Some((expected,paused))=change {
        update(&store,expected,paused)?
    }else{
        let mut db=store.read_connection()?;
        let tx=db.transaction()?;
        store.verify_connection(&tx)?;
        check_owner(&store)?;
        (current(&tx)?,false)
    };
    println!("{}",serde_json::json!({"event":"message_ingress_policy",
        "archive_id":archive_id,"policy":state,"changed":changed}));
    Ok(())
}

#[cfg(target_os="linux")]
fn update(store:&EventStore,expected:i64,paused:bool)->Result<(Policy,bool)> {
    use rusqlite::TransactionBehavior;
    if !(0..=MAX_REVISION).contains(&expected) {
        return Err(EventError::BadRequest("invalid ingress policy revision"));
    }
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    check_owner(store)?;
    let old=current(&tx)?;
    if old.revision!=expected{return Err(EventError::BadRequest("ingress policy revision conflict"));}
    if old.paused==paused{return Ok((old,false));}
    let revision=old.revision.checked_add(1).filter(|r|*r<=MAX_REVISION).ok_or(EventError::Storage)?;
    if old.revision==0 {
        tx.execute_batch(SCHEMA)?;
        tx.execute("INSERT INTO native_message_ingress_policy_v1 VALUES(1,?,?)",(revision,paused))?;
    } else if tx.execute("UPDATE native_message_ingress_policy_v1 SET revision=?,paused=?
        WHERE singleton=1 AND revision=?", (revision,paused,expected))?!=1 {
        return Err(EventError::Storage);
    }
    tx.commit()?;
    Ok((Policy{revision,paused},true))
}
