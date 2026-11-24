//! Manual text annotations on existing voice message identities. No audio is opened.
use crate::{events::{EventError,EventStore},plugins::AccountScope};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};

const MAX_TEXT:usize=16*1024;
pub(crate) const MAX_REVISION:i64=9_007_199_254_740_991;
const MAX_ACCOUNT_ROWS:i64=4096;
const SCHEMA:&str="
CREATE TABLE native_manual_transcriptions (
    platform TEXT NOT NULL, account_id TEXT NOT NULL, message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL, revision INTEGER NOT NULL
        CHECK(revision BETWEEN 1 AND 9007199254740991),
    text TEXT, language TEXT,
    PRIMARY KEY(platform,account_id,message_id,conversation_id),
    FOREIGN KEY(platform,account_id,message_id) REFERENCES messages(platform,account_id,id),
    CHECK(text IS NOT NULL OR language IS NULL),
    CHECK(text IS NULL OR length(CAST(text AS BLOB)) BETWEEN 1 AND 16384),
    CHECK(language IS NULL OR length(CAST(language AS BLOB)) BETWEEN 2 AND 12)
) WITHOUT ROWID;
";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Conflict, Quota, Locked, Storage }
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self {Self::Storage} }
impl From<EventError> for Error {
    fn from(value:EventError)->Self {
        if matches!(value,EventError::ProfileLocked) {Self::Locked} else {Self::Storage}
    }
}
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    pub(crate) conversation_id:String,
    pub(crate) message_id:String,
}

#[derive(Deserialize)]
#[serde(tag="operation",rename_all="snake_case",deny_unknown_fields)]
pub(crate) enum Change {
    Set {
        conversation_id:String,
        message_id:String,
        expected_revision:i64,
        text:String,
        language:Option<String>,
    },
    Clear {
        conversation_id:String,
        message_id:String,
        expected_revision:i64,
    },
}

#[derive(Serialize)]
#[serde(rename_all="snake_case")]
enum Status { NotRecorded, Manual, Cleared }

#[derive(Serialize)]
pub(crate) struct Record {
    revision:i64,
    status:Status,
    #[serde(skip_serializing_if="Option::is_none")]
    text:Option<String>,
    #[serde(skip_serializing_if="Option::is_none")]
    language:Option<String>,
}

#[derive(Serialize)]
pub(crate) struct Receipt { revision:i64,status:Status }

impl Record {
    fn observed(revision:i64,text:Option<String>,language:Option<String>)->Self {
        let status=if text.is_some() {Status::Manual}
            else if revision==0 {Status::NotRecorded} else {Status::Cleared};
        Self{revision,status,text,language}
    }

    fn receipt(self)->Receipt {Receipt{revision:self.revision,status:self.status}}

    pub(crate) fn into_annotation(self)->Option<(i64,String,Option<String>)> {
        self.text.map(|text|(self.revision,text,self.language))
    }
}

pub(crate) fn valid_id(id:&str)->bool {
    !id.trim().is_empty() && id.len()<=2048 && id.chars().count()<=512
       && !id.chars().any(char::is_control)
}

pub(crate) fn valid_text(text:&str)->bool {
    text.len()<=MAX_TEXT && !text.trim().is_empty()
       && !text.chars().any(|c|c.is_control() && !matches!(c,'\r'|'\n'|'\t'))
}

// A limited canonical language-tag syntax, intentionally not a locale guesser.
pub(crate) fn language(value:&str)->bool {
    let mut parts=value.split('-');
    let Some(primary)=parts.next() else {return false;};
    (2..=3).contains(&primary.len()) && primary.bytes().all(|b|b.is_ascii_lowercase())
        && parts.next().is_none_or(|region|region.len()==2 && region.bytes().all(|b|b.is_ascii_uppercase()))
        && parts.next().is_none()
}

fn installed(db:&Connection)->Result<bool> {
    let table:Option<String>=db.query_row(
        "SELECT type FROM sqlite_schema WHERE name COLLATE NOCASE='native_manual_transcriptions'",
        [],|r|r.get(0)).optional()?;
    match table.as_deref() {None=>Ok(false),Some("table")=>Ok(true),_=>Err(Error::Storage)}
}

fn require_voice(db:&Connection,scope:&AccountScope,key:&Target)->Result<()> {
    let exists:bool=db.query_row(
        "SELECT EXISTS(
          SELECT 1 FROM messages m JOIN conversations c
             ON c.platform COLLATE BINARY=m.platform AND c.account_id COLLATE BINARY=m.account_id
             AND c.id COLLATE BINARY=m.conversation_id
          WHERE m.platform COLLATE BINARY=?1 AND m.account_id COLLATE BINARY=?2
             AND m.id COLLATE BINARY=?3 AND m.conversation_id COLLATE BINARY=?4
             AND m.type COLLATE BINARY='voice')",
        params![scope.platform,scope.account_id,key.message_id,key.conversation_id],|r|r.get(0))?;
    if exists {Ok(())} else {Err(Error::Missing)}
}

fn read(db:&Connection,scope:&AccountScope,key:&Target)->Result<Record> {
    let value=db.query_row(
        "SELECT revision,CASE WHEN length(CAST(text AS BLOB))<=16384 THEN text END,
                CASE WHEN length(CAST(language AS BLOB))<=12 THEN language END,
                (text IS NOT NULL AND length(CAST(text AS BLOB))>16384)
                  OR (language IS NOT NULL AND length(CAST(language AS BLOB))>12)
         FROM native_manual_transcriptions WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
           AND message_id COLLATE BINARY=?3 AND conversation_id COLLATE BINARY=?4",
        params![scope.platform,scope.account_id,key.message_id,key.conversation_id],
        |r|Ok((r.get::<_,i64>(0)?,r.get::<_,Option<String>>(1)?,
            r.get::<_,Option<String>>(2)?,r.get::<_,bool>(3)?))).optional()?;
    match value {
        None=>Ok(Record::observed(0,None,None)),
        Some((revision,text,tag,false)) if (1..=MAX_REVISION).contains(&revision)
            && text.as_deref().is_none_or(valid_text)
            && tag.as_deref().is_none_or(|tag|text.is_some() && language(tag))
            =>Ok(Record::observed(revision,text,tag)),
        _=>Err(Error::Storage),
    }
}

pub(crate) fn get(store:&EventStore,scope:&AccountScope,key:Target)->Result<Record> {
    if !valid_id(&key.conversation_id) || !valid_id(&key.message_id) {return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    require_voice(&tx,scope,&key)?;
    if installed(&tx)? {read(&tx,scope,&key)} else {Ok(Record::observed(0,None,None))}
}

#[derive(Clone,Copy,PartialEq,Eq)]
enum Apply { Api, Preview, Confirmed }

pub(crate) fn put(store:&EventStore,scope:&AccountScope,change:Change)->Result<Receipt> {
    apply(store,scope,change,Apply::Api)
}

pub(crate) fn operator_handoff(store:&EventStore,scope:&AccountScope,change:Change,commit:bool)->Result<Receipt> {
    if !matches!(&change,Change::Set{..}) {return Err(Error::Input);}
    apply(store,scope,change,if commit {Apply::Confirmed}else{Apply::Preview})
}

fn apply(store:&EventStore,scope:&AccountScope,change:Change,mode:Apply)->Result<Receipt> {
    let (key,expected,text,tag)=match change {
        Change::Set{conversation_id,message_id,expected_revision,text,language}
            =>(Target{conversation_id,message_id},expected_revision,Some(text),language),
        Change::Clear{conversation_id,message_id,expected_revision}
            =>(Target{conversation_id,message_id},expected_revision,None,None),
    };
    if !valid_id(&key.conversation_id) || !valid_id(&key.message_id) || !(0..=MAX_REVISION).contains(&expected)
        || !text.as_deref().is_none_or(valid_text) || !tag.as_deref().is_none_or(language)
    {return Err(Error::Input);}
    store.require_ordinary()?;
    let preview=mode==Apply::Preview;
    let mut db=if preview{store.read_connection()?}else{store.write_connection()?};
    let tx=db.transaction_with_behavior(if preview {
        TransactionBehavior::Deferred
    } else {TransactionBehavior::Immediate})?;
    store.verify_connection(&tx)?;
    require_voice(&tx,scope,&key)?;
    let table=installed(&tx)?;
    let existing=if table {read(&tx,scope,&key)?} else {Record::observed(0,None,None)};
    let unchanged=existing.text==text && existing.language==tag;
    if existing.revision!=expected {
        // Operator retries can reconcile a single lost response, but cannot
        // advance or restore an intervening workflow. The HTTP CAS is unchanged.
        if mode!=Apply::Api && unchanged && expected.checked_add(1)==Some(existing.revision) {
            return Ok(existing.receipt());
        }
        return Err(Error::Conflict);
    }
    if unchanged {return Ok(existing.receipt());}
    let revision=expected.checked_add(1).filter(|value|*value<=MAX_REVISION).ok_or(Error::Conflict)?;
    if table && expected==0 {
        let count:i64=tx.query_row(
            "SELECT COUNT(*) FROM native_manual_transcriptions WHERE
             platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2",
            params![scope.platform,scope.account_id],|r|r.get(0))?;
        if count>=MAX_ACCOUNT_ROWS {return Err(Error::Quota);}
    }
    if preview {return Ok(Record::observed(revision,text,tag).receipt());}
    if !table {tx.execute_batch(SCHEMA)?;}
    tx.execute(
        "INSERT INTO native_manual_transcriptions(platform,account_id,message_id,conversation_id,revision,text,language)
         VALUES (?1,?2,?3,?4,?5,?6,?7) ON CONFLICT(platform,account_id,message_id,conversation_id)
         DO UPDATE SET revision=excluded.revision,text=excluded.text,language=excluded.language",
        params![scope.platform,scope.account_id,key.message_id,key.conversation_id,revision,text,tag])?;
    tx.commit()?;
    Ok(Record::observed(revision,text,tag).receipt())
}
