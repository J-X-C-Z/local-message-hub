//! Plain local drafts. This module depends on storage, never delivery or providers.
use crate::{events::EventStore,plugins::AccountScope};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use serde_json::Value;
use sha2::{Digest,Sha256};

const MAX_BYTES:usize=16*1024;
const MAX_REVISION:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_drafts (
 platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991), body TEXT,
 PRIMARY KEY(platform,account_id,conversation_id),
 FOREIGN KEY(platform,account_id,conversation_id) REFERENCES conversations(platform,account_id,id)
) WITHOUT ROWID;
CREATE TABLE native_draft_receipts (
 platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 grant_id TEXT NOT NULL, request_id TEXT NOT NULL, digest BLOB NOT NULL,
 revision INTEGER NOT NULL, present INTEGER NOT NULL CHECK(present IN (0,1)),
 PRIMARY KEY(platform,account_id,conversation_id,grant_id,request_id),
 FOREIGN KEY(platform,account_id,conversation_id) REFERENCES conversations(platform,account_id,id)
) WITHOUT ROWID;
CREATE TABLE native_draft_audit (
 platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 revision INTEGER NOT NULL, operation TEXT NOT NULL CHECK(operation IN ('create','edit','clear')),
 changed_at INTEGER NOT NULL,
 PRIMARY KEY(platform,account_id,conversation_id,revision),
 FOREIGN KEY(platform,account_id,conversation_id) REFERENCES conversations(platform,account_id,id)
) WITHOUT ROWID;
";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Stale, Duplicate, Storage }
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self { Self::Storage } }
impl From<crate::events::EventError> for Error { fn from(_:crate::events::EventError)->Self { Self::Storage } }
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    pub conversation_id:String,
    pub platform:Option<String>,
    pub account_id:Option<String>,
}
pub(crate) struct Key { scope:AccountScope, conversation_id:String }
impl Target {
    pub fn within(self,scope:AccountScope)->Result<Key> {
        if self.platform.as_ref().is_some_and(|p|p!=&scope.platform)
            || self.account_id.as_ref().is_some_and(|a|a!=&scope.account_id) { return Err(Error::Missing); }
        let id=self.conversation_id;
        if id.trim().is_empty() || id.len()>2_048 || id.chars().count()>512 || id.chars().any(char::is_control) {
            return Err(Error::Input);
        }
        Ok(Key{scope,conversation_id:id})
    }
}
impl Key {
    fn sql(&self)->(&str,&str,&str) { (&self.scope.platform,&self.scope.account_id,&self.conversation_id) }
    fn require_indexed(&self,sql:&Connection)->Result<()> {
        let exists:bool=sql.query_row(
            "SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=? AND account_id=? AND id=?)",
            self.sql(), |r|r.get(0))?;
        if exists { Ok(()) } else { Err(Error::Missing) }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub target:Target,
    pub expected_revision:i64,
    pub request_id:String,
    pub body:Value, // Required; explicit null means clear.
}
pub(crate) struct Validated { expected:i64, request_id:String, body:Option<String> }
impl Change {
    pub fn validate(self,scope:AccountScope)->Result<(Key,Validated)> {
        let target=self.target.within(scope)?;
        if !(0..=MAX_REVISION).contains(&self.expected_revision) || !(1..=128).contains(&self.request_id.len())
            || !self.request_id.bytes().all(|b|b.is_ascii_alphanumeric() || b"-_.".contains(&b)) {
            return Err(Error::Input);
        }
        let body=match self.body {
            Value::Null=>None,
            Value::String(s) if s.len()<=MAX_BYTES && !s.trim().is_empty()
                && !s.chars().any(|c|c.is_control() && !matches!(c,'\r'|'\n'|'\t'))=>Some(s),
            _=>return Err(Error::Input),
        };
        Ok((target,Validated{expected:self.expected_revision,request_id:self.request_id,body}))
    }
}

#[derive(Default,Serialize)]
pub(crate) struct State { revision:i64,body:Option<String> }
#[derive(Debug,Clone,PartialEq,Eq,Serialize)]
pub(crate) struct Receipt { revision:i64,present:bool }

fn ready(sql:&Connection)->Result<bool> {
    let (objects,tables):(i64,i64)=sql.query_row(
        "SELECT count(*),count(CASE WHEN type='table' AND name IN
          ('native_drafts','native_draft_receipts','native_draft_audit') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
          ('native_drafts','native_draft_receipts','native_draft_audit')",
        [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match (objects,tables) { (0,0)=>Ok(false),(3,3)=>Ok(true),_=>Err(Error::Storage) }
}

fn read_slot(sql:&Connection,key:&Key)->Result<State> {
    Ok(sql.query_row(
        "SELECT revision,CASE WHEN length(CAST(body AS BLOB))<=16384 THEN body END,
         (body IS NOT NULL AND length(CAST(body AS BLOB))>16384)
         FROM native_drafts WHERE platform=? AND account_id=? AND conversation_id=?",
        key.sql(), |r| {
            if r.get::<_,bool>(2)? { return Err(rusqlite::Error::InvalidQuery); }
            Ok(State{revision:r.get(0)?,body:r.get(1)?})
        }).optional()?.unwrap_or_default())
}

pub(crate) fn get(store:&EventStore,key:&Key)->Result<State> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    key.require_indexed(&tx)?;
    if ready(&tx)? { read_slot(&tx,key) } else { Ok(State::default()) }
}

pub(crate) fn put(store:&EventStore,key:&Key,grant:&str,change:Validated)->Result<Receipt> {
    let encoded=serde_json::to_vec(&("lmh-local-draft-v1",change.expected,&change.body)).map_err(|_|Error::Input)?;
    let digest=Sha256::digest(encoded);
    let (platform,account,conversation)=key.sql();
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    key.require_indexed(&tx)?;
    if !ready(&tx)? { tx.execute_batch(SCHEMA)?; }
    if let Some((seen,saved))=tx.query_row(
        "SELECT digest,revision,present FROM native_draft_receipts
         WHERE platform=? AND account_id=? AND conversation_id=? AND grant_id=? AND request_id=?",
        (platform,account,conversation,grant,&change.request_id),
        |r|Ok((r.get::<_,[u8;32]>(0)?,Receipt{revision:r.get(1)?,present:r.get(2)?}))).optional()? {
        return if seen==digest[..] { Ok(saved) } else { Err(Error::Duplicate) };
    }
    let current=read_slot(&tx,key)?;
    if change.expected!=current.revision { return Err(Error::Stale); }
    let mut revision=current.revision;
    if change.body!=current.body {
        let operation=match (current.body.is_some(),change.body.is_some()) {
            (false,true)=>"create",(true,true)=>"edit",(true,false)=>"clear",(false,false)=>unreachable!(),
        };
        revision=revision.checked_add(1).filter(|v|*v<=MAX_REVISION).ok_or(Error::Storage)?;
        tx.execute("INSERT INTO native_drafts(platform,account_id,conversation_id,revision,body)
            VALUES (?,?,?,?,?) ON CONFLICT(platform,account_id,conversation_id)
            DO UPDATE SET revision=excluded.revision,body=excluded.body",
            params![platform,account,conversation,revision,change.body])?;
        tx.execute("INSERT INTO native_draft_audit(platform,account_id,conversation_id,revision,operation,changed_at)
            VALUES (?,?,?,?,?,unixepoch())",(platform,account,conversation,revision,operation))?;
    }
    let present=change.body.is_some();
    tx.execute("INSERT INTO native_draft_receipts(platform,account_id,conversation_id,grant_id,request_id,digest,revision,present)
        VALUES (?,?,?,?,?,?,?,?)",params![platform,account,conversation,grant,&change.request_id,digest.as_slice(),revision,present])?;
    tx.commit()?;
    Ok(Receipt{revision,present})
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn key()->Key {
        Target{conversation_id:"room".into(),account_id:None,platform:None}
            .within(AccountScope{platform:"qq".into(),account_id:"Account".into()}).unwrap()
    }
    fn change(expected:i64,request_id:&str,body:Option<&str>)->Validated {
        Validated{expected,request_id:request_id.into(),body:body.map(str::to_owned)}
    }
    fn fixture()->(tempfile::TempDir,EventStore) {
        let dir=tempfile::tempdir().unwrap();
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(dir.path(),std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let store=EventStore::init(dir.path()).unwrap();
        let db=store.write_connection().unwrap();
        db.execute("INSERT INTO conversations(platform,account_id,id) VALUES('qq','Account','room')",[]).unwrap();
        (dir,store)
    }
    #[test]
    fn exact_grant_retry_cas_and_tombstone() {
        let (dir,store)=fixture();
        let key=key();
        assert_eq!(get(&store,&key).unwrap().revision,0);
        let first=put(&store,&key,"grant.a",change(0,"same",Some("private body"))).unwrap();
        assert_eq!(first.revision,1);
        assert!(matches!(put(&store,&key,"grant.b",change(0,"same",Some("private body"))),Err(Error::Stale)));
        let cleared=put(&store,&key,"grant.a",change(1,"clear",None)).unwrap();
        assert_eq!(cleared.revision,2);
        assert!(!cleared.present);
        let reopened=EventStore::open(dir.path()).unwrap();
        assert_eq!(put(&reopened,&key,"grant.a",change(0,"same",Some("private body"))).unwrap(),first);
        assert!(matches!(put(&reopened,&key,"grant.a",change(0,"same",Some("altered"))),Err(Error::Duplicate)));
        assert!(matches!(put(&reopened,&key,"grant.a",change(0,"stale",Some("stale"))),Err(Error::Stale)));
        assert!(get(&reopened,&key).unwrap().body.is_none());
    }

    #[test]
    fn audit_write_failure_rolls_back_slot_and_receipt() {
        let (_dir,store)=fixture();
        let k=key();
        put(&store,&k,"grant.a",change(0,"ok",Some("first"))).unwrap();
        let db=store.write_connection().unwrap();
        db.execute_batch("CREATE TRIGGER fail_audit BEFORE INSERT ON native_draft_audit
             BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
        assert!(matches!(put(&store,&k,"grant.a",change(1,"blocked",Some("second"))),Err(Error::Storage)));
        assert_eq!(get(&store,&k).unwrap().body.as_deref(),Some("first"));
        let rows:i64=db.query_row("SELECT count(*) FROM native_draft_receipts",[],|r|r.get(0)).unwrap();
        assert_eq!(rows,1);
    }

    #[test]
    fn required_body_exact_account_and_limits() {
        let scope=||AccountScope{platform:"qq".into(),account_id:"Account".into()};
        let mut v=json!({"expected_revision":0,"request_id":"simple","target":{"conversation_id":"room"}});
        assert!(serde_json::from_value::<Change>(v.clone()).is_err());
        v["body"]=json!("é".repeat(8193));
        assert!(matches!(serde_json::from_value::<Change>(v.clone()).unwrap().validate(scope()),Err(Error::Input)));
        v["body"]=json!(null);
        assert!(serde_json::from_value::<Change>(v.clone()).unwrap().validate(scope()).is_ok());
        v["target"]["account_id"]=json!("account");
        assert!(matches!(serde_json::from_value::<Change>(v).unwrap().validate(scope()),Err(Error::Missing)));
    }
}
