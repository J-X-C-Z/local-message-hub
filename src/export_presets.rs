//! Exact-account, foreground-only saved selections for existing text export.
use crate::{
    events::{EventError,EventStore},
    grouped_exports::{self,Selection},
    plugins::AccountScope,
    store::{Store,StoreError},
};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::collections::BTreeSet;

const MAX_TOTAL: i64=1024;
const MAX_OWNER: i64=16;
const MAX_IDS: usize=20;
const MAX_ID_JSON: usize=7*1024; // shared batch manifest limit
const MAX_SAVED: usize=MAX_ID_JSON+512;
const MAX_VERSION: i64=9_007_199_254_740_991;
const TABLE:&str="
CREATE TABLE native_export_presets(
    owner TEXT NOT NULL,preset TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    spec TEXT NOT NULL CHECK(length(CAST(spec AS BLOB))<=7680),
    PRIMARY KEY(owner,preset)
) WITHOUT ROWID;";

#[derive(Debug)]
pub(crate) enum Error { Invalid, Missing, Stale, Quota, Limit, Storage }
impl From<EventError> for Error {fn from(_:EventError)->Self{Self::Storage}}
impl From<rusqlite::Error> for Error {fn from(_:rusqlite::Error)->Self{Self::Storage}}
impl From<StoreError> for Error {
    fn from(error:StoreError)->Self {
        match error {StoreError::BadQuery(_)=>Self::Invalid,StoreError::TooLarge=>Self::Limit,_=>Self::Storage}
    }
}

#[derive(Clone,Copy,Serialize,Deserialize)]
#[serde(rename_all="lowercase")]
enum Format { Json,Csv,Txt,Md }

#[derive(Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Spec {
    label:String,
    format:Format,
    conversation_ids:Vec<String>,
    start:Option<i64>,
    end:Option<i64>,
}
impl Spec {
    fn selection_json(&self)->Result<String,Error> {
        let text=serde_json::to_string(&self.conversation_ids).map_err(|_|Error::Invalid)?;
        if text.len()>MAX_ID_JSON {return Err(Error::Limit);}
        Ok(text)
    }

    fn validate(&self)->Result<(),Error> {
        let mut seen=BTreeSet::new();
        let date=|v:i64|(-62_135_596_800..=253_402_300_799).contains(&v);
        if !(1..=80).contains(&self.label.len()) || self.label.trim()!=self.label
            || self.label.chars().any(|c|!c.is_alphanumeric() && !matches!(c,' '|'-'|'_'))
            || !(1..=MAX_IDS).contains(&self.conversation_ids.len())
            || self.conversation_ids.iter().any(|id| !(1..=256).contains(&id.len())
                || id.trim()!=id || id.chars().any(char::is_control) || !seen.insert(id))
            || self.start.is_some_and(|v|!date(v)) || self.end.is_some_and(|v|!date(v))
            || matches!((self.start,self.end),(Some(a),Some(b)) if a>b) {return Err(Error::Invalid);}
        self.selection_json()?;
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Save {preset_id:Option<String>,expected_revision:i64,spec:Spec}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {pub preset_id:String}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {pub preset_id:String,expected_revision:i64}
#[derive(Serialize)]
pub(crate) struct Details {preset_id:String,revision:i64,spec:Spec}
#[derive(Serialize)]
pub(crate) struct Summary {preset_id:String,revision:i64,label:String,format:Format,selections:usize}
impl Details {
    pub(crate) fn summary(&self)->Summary {
        Summary {preset_id:self.preset_id.clone(),revision:self.revision,label:self.spec.label.clone(),
            format:self.spec.format,selections:self.spec.conversation_ids.len()}
    }
}
#[derive(Serialize)]
struct Estimate {ordinal:usize,rows_at_most_limit:u64,over_row_limit:bool}
#[derive(Serialize)]
pub(crate) struct Plan {
    preset_id:String,revision:i64,format:Format,selections:Vec<Estimate>,
    download_route:&'static str,background:bool,snapshot_bound:bool,
}

fn owner(store:&EventStore,plugin:&str,scope:&AccountScope)->Result<String,Error> {
    let bytes=serde_json::to_vec(&("lmh-export-preset-v1",store.archive_id(),plugin,
        &scope.platform,&scope.account_id)).map_err(|_|Error::Storage)?;
    Ok(hex::encode(Sha256::digest(bytes)))
}
fn preset_id(id:&str)->bool {
    id.len()==64 && id.bytes().all(|x|x.is_ascii_digit()||(b'a'..=b'f').contains(&x))
}
fn installed(db:&Connection)->Result<bool,Error> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name='native_export_presets' COLLATE NOCASE",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object.as_ref().map(|(name,kind)|(name.as_str(),kind.as_str())) {
        None=>Ok(false),Some(("native_export_presets","table"))=>Ok(true),_=>Err(Error::Storage),
    }
}
fn decode(preset_id:String,revision:i64,text:&str)->Result<Details,Error> {
    if !self::preset_id(&preset_id)||!(1..=MAX_VERSION).contains(&revision)||text.len()>MAX_SAVED {
        return Err(Error::Storage);
    }
    let spec:Spec=serde_json::from_str(text).map_err(|_|Error::Storage)?;
    spec.validate().map_err(|_|Error::Storage)?;
    Ok(Details{preset_id,revision,spec})
}
fn get(db:&Connection,owner:&str,id:&str)->Result<Details,Error> {
    let found:Option<(i64,String)>=db.query_row("SELECT revision,spec FROM native_export_presets
        WHERE owner=? AND preset=?", (owner,id),|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    let (revision,text)=found.ok_or(Error::Missing)?;
    decode(id.to_owned(),revision,&text)
}
/// Check the exact preset inside a caller-owned transaction on this store.
pub(crate) fn require_revision_in(
    db:&Connection, store:&EventStore, plugin:&str, scope:&AccountScope,
    id:&str, revision:i64,
)->Result<(),Error> {
    if !preset_id(id)||!(1..=MAX_VERSION).contains(&revision) {return Err(Error::Invalid);}
    let owner=owner(store,plugin,scope)?;
    if !installed(db)? {return Err(Error::Missing);}
    if get(db,&owner,id)?.revision!=revision {return Err(Error::Stale);}
    Ok(())
}
/// Resolve a validated owned preset inside the already-pinned read snapshot.
pub(crate) struct RenderSpec {
    pub format:crate::export::Format,
    pub conversation_ids:Vec<String>,
    pub start:Option<i64>,
    pub end:Option<i64>,
}
pub(crate) fn render_spec_in(
    db:&Connection, store:&EventStore, plugin:&str, scope:&AccountScope,
    id:&str, revision:i64,
)->Result<RenderSpec,Error> {
    if !preset_id(id)||!(1..=MAX_VERSION).contains(&revision) {return Err(Error::Invalid);}
    let owner=owner(store,plugin,scope)?;
    if !installed(db)? {return Err(Error::Missing);}
    let saved=get(db,&owner,id)?;
    if saved.revision!=revision {return Err(Error::Stale);}
    let Spec{format,conversation_ids,start,end,..}=saved.spec;
    let format=match format {
        Format::Json=>crate::export::Format::Json,
        Format::Csv=>crate::export::Format::Csv,
        Format::Txt=>crate::export::Format::Txt,
        Format::Md=>crate::export::Format::Md,
    };
    Ok(RenderSpec{format,conversation_ids,start,end})
}
pub(crate) fn details(store:&EventStore,plugin:&str,scope:&AccountScope,target:&str)->Result<Details,Error> {
    if !preset_id(target){return Err(Error::Invalid);}
    store.require_ordinary()?;
    let owner=owner(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {return Err(Error::Missing);}
    get(&tx,&owner,target)
}
pub(crate) fn list(store:&EventStore,plugin:&str,scope:&AccountScope)->Result<Vec<Summary>,Error> {
    store.require_ordinary()?;
    let owner=owner(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {return Ok(Vec::new());}
    let mut stmt=tx.prepare("SELECT preset,revision,spec FROM native_export_presets
        WHERE owner=? ORDER BY preset LIMIT ?")?;
    let mut rows=stmt.query(params![owner,MAX_OWNER+1])?;
    let mut out=Vec::new();
    while let Some(row)=rows.next()? {
        if out.len()>=MAX_OWNER as usize {return Err(Error::Storage);}
        let value = row.get_ref(2)?;
        let spec = value.as_str().map_err(|error|
            rusqlite::Error::FromSqlConversionFailure(2, value.data_type(), Box::new(error)))?;
        out.push(decode(row.get(0)?,row.get(1)?,spec)?.summary());
    }
    Ok(out)
}
pub(crate) fn save(store:&EventStore,plugin:&str,scope:&AccountScope,request:Save)->Result<Summary,Error> {
    let Save{preset_id:id,expected_revision,spec}=request;
    spec.validate()?;
    if match id.as_deref() {
        None=>expected_revision!=0,
        Some(id)=>!preset_id(id)||!(1..MAX_VERSION).contains(&expected_revision),
    } {return Err(Error::Invalid);}
    let text=serde_json::to_string(&spec).map_err(|_|Error::Invalid)?;
    if text.len()>MAX_SAVED {return Err(Error::Limit);}
    store.require_ordinary()?;
    let owner=owner(store,plugin,scope)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {
        if id.is_some(){return Err(Error::Missing);}
        tx.execute_batch(TABLE)?;
    }
    if let Some(id)=id.as_deref() {
        if get(&tx,&owner,id)?.revision!=expected_revision {return Err(Error::Stale);}
    }
    // Validate all IDs against the authorized account under the same write transaction.
    {
        let mut lookup=tx.prepare(
            "SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=? AND account_id=? AND id=?)")?;
        for id in &spec.conversation_ids {
            if !lookup.query_row(params![scope.platform,scope.account_id,id],|row|row.get::<_,bool>(0))? {
                return Err(Error::Missing);
            }
        }
    }
    let revision=expected_revision+1;
    let preset_id=if let Some(id)=id {
        if tx.execute("UPDATE native_export_presets SET revision=?,spec=?
            WHERE owner=? AND preset=? AND revision=?",params![revision,text,owner,id,expected_revision])?!=1 {
            return Err(Error::Stale);
        }
        id
    } else {
        let counts:(i64,i64)=tx.query_row("SELECT count(*),count(CASE WHEN owner=? THEN 1 END)
            FROM native_export_presets",[&owner],|r|Ok((r.get(0)?,r.get(1)?)))?;
        if counts.0>=MAX_TOTAL||counts.1>=MAX_OWNER {return Err(Error::Quota);}
        let mut random=[0_u8;32];
        getrandom::fill(&mut random).map_err(|_|Error::Storage)?;
        let id=hex::encode(random);
        tx.execute("INSERT INTO native_export_presets(owner,preset,revision,spec) VALUES(?,?,?,?)",
            params![owner,id,revision,text])?;
        id
    };
    tx.commit()?;
    Ok(Details{preset_id,revision,spec}.summary())
}
pub(crate) fn remove(store:&EventStore,plugin:&str,scope:&AccountScope,request:Change)->Result<(),Error> {
    if !preset_id(&request.preset_id)||!(1..=MAX_VERSION).contains(&request.expected_revision) {
        return Err(Error::Invalid);
    }
    store.require_ordinary()?;
    let owner=owner(store,plugin,scope)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {return Err(Error::Missing);}
    let saved=get(&tx,&owner,&request.preset_id)?;
    if saved.revision!=request.expected_revision {return Err(Error::Stale);}
    if tx.execute("DELETE FROM native_export_presets WHERE owner=? AND preset=? AND revision=?",
        params![owner,request.preset_id,request.expected_revision])?!=1 {return Err(Error::Stale);}
    tx.commit()?;
    Ok(())
}
pub(crate) fn plan(store:&EventStore,plugin:&str,scope:&AccountScope,request:Change)->Result<Plan,Error> {
    if !(1..=MAX_VERSION).contains(&request.expected_revision){return Err(Error::Invalid);}
    let saved=details(store,plugin,scope,&request.preset_id)?;
    if saved.revision!=request.expected_revision {return Err(Error::Stale);}
    let db=Store::open_live(store.clone())?;
    let described=grouped_exports::describe(db,scope.clone(),Selection{
        platform:scope.platform.clone(),account_id:scope.account_id.clone(),
        conversation_ids:saved.spec.selection_json()?,start:saved.spec.start,end:saved.spec.end,
    })?.ok_or(Error::Missing)?;
    let selections=described["conversations"].as_array().ok_or(Error::Storage)?
        .iter().enumerate().map(|(i,value)| Ok(Estimate{
            ordinal:i+1,rows_at_most_limit:value["rows_at_most_limit"].as_u64().ok_or(Error::Storage)?,
            over_row_limit:value["over_row_limit"].as_bool().ok_or(Error::Storage)?,
        })).collect::<Result<Vec<_>,Error>>()?;
    Ok(Plan{preset_id:saved.preset_id,revision:saved.revision,format:saved.spec.format,selections,
        download_route:"/api/v1/messages/export",background:false,snapshot_bound:false})
}
