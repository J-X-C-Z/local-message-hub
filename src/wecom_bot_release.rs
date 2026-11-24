//! Private durable release kernel for one official WeCom bot text frame.
//! No CLI, HTTP route, credentials store, socket, or executable is defined here.
use crate::{draft_review,events::EventStore,plugins::AccountScope,
    wecom_bot_text as wire};
use rusqlite::{Connection,OptionalExtension,Transaction,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::{fs::File,os::unix::fs::MetadataExt,path::Path};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const WINDOW:i64=120;
const MAX_ROWS:i64=4096;
const MAX_REVISION:i64=9_007_199_254_740_991;
const TABLE:&str="native_wecom_bot_text_release_v1";
const SCHEMA:&str="
CREATE TABLE native_wecom_bot_text_release_v1(
 target_key BLOB NOT NULL PRIMARY KEY CHECK(length(target_key)=32),
 intent BLOB NOT NULL CHECK(length(intent)=32),
 body_hash BLOB NOT NULL CHECK(length(body_hash)=32),
 challenge BLOB CHECK(challenge IS NULL OR length(challenge)=32),
 issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at>issued_at),
 attempt_id TEXT CHECK(attempt_id IS NULL OR length(attempt_id)=48),
 state TEXT NOT NULL CHECK(state IN ('pending','reserved','attempted','closed_before_attempt',
    'delivery_unknown','provider_accepted','provider_rejected')),
 receipt_hash TEXT CHECK(receipt_hash IS NULL OR length(receipt_hash)=64),
 provider_code INTEGER,
 CHECK((state='pending')=(challenge IS NOT NULL)),
 CHECK((state='pending')=(attempt_id IS NULL)),
 CHECK((state='provider_accepted')=(receipt_hash IS NOT NULL)),
 CHECK((state='provider_rejected')=(provider_code IS NOT NULL))
) WITHOUT ROWID;";

#[derive(Debug,thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid official bot release input")] Input,
    #[error("private operator authorization or accepted exact draft unavailable")] Unauthorized,
    #[error("release approval is stale, expired, or already consumed")] Stale,
    #[error("local release journal capacity reached")] Capacity,
    #[error("local release journal locked or unavailable")] Storage,
    #[error("official bot protocol refused this phase")] Protocol,
}
impl From<rusqlite::Error> for Error {fn from(_:rusqlite::Error)->Self{Self::Storage}}
impl From<crate::events::EventError> for Error {
    fn from(_:crate::events::EventError)->Self{Self::Storage}
}
type Result<T>=std::result::Result<T,Error>;

#[derive(Clone,Copy,Deserialize,Serialize)]
#[serde(rename_all="snake_case")]
pub(crate) enum Recipient { Private, Group }
impl From<Recipient> for wire::RecipientKind {
    fn from(value:Recipient)->Self{
        match value{Recipient::Private=>Self::Private,Recipient::Group=>Self::Group}
    }
}

#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target{
    pub scope:AccountScope,
    pub conversation_id:String,
    pub review_id:String,
    pub revision:i64,
    pub bot:String,
    pub recipient:String,
    pub recipient_kind:Recipient,
}
impl Target{
    fn key(&self)->Result<[u8;32]> {
        let valid=|s:&str,max| (1..=max).contains(&s.len()) && s.trim()==s
            && !s.chars().any(char::is_control);
        let wire_id=|s:&str|valid(s,256) && !s.chars().any(char::is_whitespace);
        if self.scope.platform!="wecom" || !valid(&self.scope.account_id,256)
            || !valid(&self.conversation_id,2048) || self.conversation_id.chars().count()>512
            || !hex_token(&self.review_id,48) || !(1..=MAX_REVISION).contains(&self.revision)
            || !wire_id(&self.bot) || !wire_id(&self.recipient){return Err(Error::Input);}
        // Deliberately excludes review, bot and recipient to prevent an
        // already-attempted draft revision being rerouted through a second bot.
        hash(&("lmh-wecom-release-target-v1",&self.scope.platform,&self.scope.account_id,
            &self.conversation_id,self.revision))
    }

    fn intent(&self,archive:&str)->Result<[u8;32]> {
        hash(&("lmh-wecom-release-intent-v1",archive,self))
    }
}

fn hex_token(value:&str,len:usize)->bool {
    value.len()==len && value.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn hash(value:&impl Serialize)->Result<[u8;32]> {
    let bytes=Zeroizing::new(serde_json::to_vec(value).map_err(|_|Error::Input)?);
    Ok(Sha256::digest(bytes.as_slice()).into())
}
fn random<const N:usize>()->Result<String>{
    let mut bytes=Zeroizing::new([0_u8;N]);
    getrandom::fill(bytes.as_mut_slice()).map_err(|_|Error::Storage)?;
    Ok(hex::encode(bytes.as_slice()))
}
fn now(db:&Connection)->Result<i64>{
    let time:i64=db.query_row("SELECT unixepoch()",[],|r|r.get(0))?;
    if !(1..=MAX_REVISION-WINDOW).contains(&time){return Err(Error::Storage);}
    Ok(time)
}
fn installed(db:&Connection)->Result<bool>{
    let entry:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE=?1",
        [TABLE],|r|Ok((r.get(0)?,r.get(1)?)),
    ).optional()?;
    match entry {
        None=>Ok(false),
        Some((name,kind)) if name==TABLE && kind=="table"=>Ok(true),
        _=>Err(Error::Storage),
    }
}
fn body(db:&Connection, target:&Target)->Result<(Zeroizing<String>,[u8;32])>{
    let (text,digest)=draft_review::checked_handoff_body(db,&target.scope,&target.review_id,
        &target.conversation_id,target.revision).map_err(|_|Error::Unauthorized)?;
    if text.len()>wire::MAX_TEXT_BYTES || text.trim().is_empty()
        || text.chars().any(|c|c.is_control()&&!matches!(c,'\r'|'\n'|'\t'))
    {return Err(Error::Input);}
    Ok((Zeroizing::new(text),digest))
}
fn commitment(archive:&str,intent:&[u8;32],body:&[u8;32],issued:i64,expires:i64,challenge:&str)
    ->Result<[u8;32]>{
    hash(&("lmh-wecom-release-approval-v1",archive,intent,body,issued,expires,challenge))
}

pub(crate) struct Operator {
    store:EventStore,
    lock:File,
}

#[derive(Serialize)]
pub(crate) struct Preview<'a>{
    pub target:&'a Target,
    #[serde(serialize_with="serialize_private")]
    pub text:Zeroizing<String>,
    pub body_sha256:String,
    #[serde(serialize_with="serialize_private")]
    pub challenge:Zeroizing<String>,
    pub expires_at:i64,
    pub external_action_performed:bool,
}

fn serialize_private<S:serde::Serializer>(secret:&Zeroizing<String>,serializer:S)
    ->std::result::Result<S::Ok,S::Error>{
    serializer.serialize_str(secret.as_str())
}

#[derive(Deserialize)]
pub(crate) enum Intent { #[serde(rename="approve_one_official_wecom_bot_text")] Approve }

pub(crate) struct Confirmation {
    pub target:Target,
    pub action:Intent,
    pub passphrase:Zeroizing<String>,
    pub challenge:Zeroizing<String>,
    pub body_sha256:String,
}

#[derive(Serialize)]
pub(crate) struct Receipt {
    pub state:String,
    pub attempt_id:Option<String>,
    pub provider_receipt_sha256:Option<String>,
    pub provider_code:Option<i64>,
}

struct Record {
    intent:[u8;32],body:[u8;32],challenge:Option<[u8;32]>,
    issued:i64,expires:i64,state:String,attempt:Option<String>,
    receipt:Option<String>,code:Option<i64>,
}
fn find(db:&Connection,key:&[u8;32])->Result<Record>{
    Ok(db.query_row("SELECT intent,body_hash,challenge,issued_at,expires_at,state,
        attempt_id,receipt_hash,provider_code FROM native_wecom_bot_text_release_v1 WHERE target_key=?1",
        [key.as_slice()],|r|Ok(Record{intent:r.get(0)?,body:r.get(1)?,challenge:r.get(2)?,
            issued:r.get(3)?,expires:r.get(4)?,state:r.get(5)?,attempt:r.get(6)?,
            receipt:r.get(7)?,code:r.get(8)?})).optional()?.ok_or(Error::Stale)?)
}
fn current_time(record:&Record,time:i64)->bool {
    record.expires.checked_sub(record.issued)==Some(WINDOW) && record.issued<=time && time<record.expires
}
fn equal(a:&[u8;32],b:&[u8;32])->bool{bool::from(a.as_slice().ct_eq(b.as_slice()))}

impl Operator {
    /// This is a trusted local operator path only. The archive OS owner and
    /// private authenticated controller are the boundary, not same-UID programs.
    pub(crate) fn open(location:&Path,passphrase:&str)->Result<Self>{
        use rustix::fs::{flock,open,FlockOperation,Mode,OFlags};
        let store=EventStore::open(location)?;
        let lock:File=open(store.path(),OFlags::RDONLY|OFlags::NOFOLLOW|OFlags::NONBLOCK|OFlags::CLOEXEC,
            Mode::empty()).map_err(|_|Error::Storage)?.into();
        flock(&lock,FlockOperation::NonBlockingLockExclusive).map_err(|_|Error::Storage)?;
        let current=Self{store,lock};
        current.authorize(passphrase)?;
        current.change(|tx|{
            if installed(tx)? {
                tx.execute("UPDATE native_wecom_bot_text_release_v1 SET state='closed_before_attempt'
                    WHERE state='reserved'",[])?;
                tx.execute("UPDATE native_wecom_bot_text_release_v1 SET state='delivery_unknown'
                    WHERE state='attempted'",[])?;
            }
            Ok(())
        })?;
        Ok(current)
    }

    fn verify_path(&self)->Result<()>{
        let opened=self.lock.metadata().map_err(|_|Error::Storage)?;
        let current=std::fs::symlink_metadata(self.store.path()).map_err(|_|Error::Storage)?;
        let private=|m:&std::fs::Metadata|m.is_file() && m.mode()&0o077==0 && m.nlink()==1;
        if !private(&opened) || !private(&current) || opened.dev()!=current.dev()
            || opened.ino()!=current.ino(){return Err(Error::Storage);}
        Ok(())
    }

    fn verify(&self)->Result<()>{
        self.verify_path()?;
        self.store.require_ordinary()?;
        self.store.verify()?;
        Ok(())
    }

    fn authorize(&self,passphrase:&str)->Result<()>{
        self.verify()?;
        draft_review::authenticate(&self.store,passphrase).map_err(|_|Error::Unauthorized)?;
        self.verify()
    }

    fn change<T>(&self,operation:impl FnOnce(&Transaction<'_>)->Result<T>)->Result<T>{
        self.verify()?;
        let mut db=self.store.write_connection()?;
        let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
        self.store.verify_connection(&tx)?;
        let value=operation(&tx)?;
        self.verify_path()?;
        self.store.verify_connection(&tx)?;
        tx.commit()?;
        self.verify()?;
        Ok(value)
    }

    /// The exact body and challenge in this result belong on private operator
    /// output only. An accepted review alone does not issue a send frame.
    pub(crate) fn preview<'a>(&self,passphrase:&str,target:&'a Target)->Result<Preview<'a>>{
        let key=target.key()?;
        let archive=self.store.archive_id();
        let intent=target.intent(archive)?;
        self.authorize(passphrase)?;
        let challenge=Zeroizing::new(random::<32>()?);
        let (text,digest,expires)=self.change(|tx|{
            let (text,digest)=body(tx,target)?;
            let issued=now(tx)?;
            if !installed(tx)?{tx.execute_batch(SCHEMA)?;}
            tx.execute("DELETE FROM native_wecom_bot_text_release_v1
                WHERE state='pending' AND expires_at<=?1",[issued])?;
            let existing:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM native_wecom_bot_text_release_v1
                WHERE target_key=?1)",[key.as_slice()],|r|r.get(0))?;
            if !existing {
                let count:i64=tx.query_row("SELECT COUNT(*) FROM native_wecom_bot_text_release_v1",[],|r|r.get(0))?;
                if count>=MAX_ROWS{return Err(Error::Capacity);}
            }
            let expires=issued+WINDOW;
            let tag=commitment(archive,&intent,&digest,issued,expires,&challenge)?;
            let n=tx.execute(
                "INSERT INTO native_wecom_bot_text_release_v1(target_key,intent,body_hash,challenge,
                   issued_at,expires_at,state) VALUES(?1,?2,?3,?4,?5,?6,'pending')
                 ON CONFLICT(target_key) DO UPDATE SET intent=excluded.intent,body_hash=excluded.body_hash,
                   challenge=excluded.challenge,issued_at=excluded.issued_at,expires_at=excluded.expires_at,
                   attempt_id=NULL,state='pending'
                 WHERE state IN ('pending','closed_before_attempt')",
                params![key.as_slice(),intent.as_slice(),digest.as_slice(),tag.as_slice(),issued,expires])?;
            if n!=1{return Err(Error::Stale);}
            Ok((text,digest,expires))
        })?;
        Ok(Preview{target,text,body_sha256:hex::encode(digest),challenge,expires_at:expires,
            external_action_performed:false})
    }

    pub(crate) fn status(&self,passphrase:&str,target:&Target)->Result<Receipt>{
        let key=target.key()?;
        self.authorize(passphrase)?;
        let mut db=self.store.read_connection()?;
        let tx=db.transaction()?;
        self.store.verify_connection(&tx)?;
        if !installed(&tx)?{return Err(Error::Stale);}
        let record=find(&tx,&key)?;
        if !equal(&record.intent,&target.intent(self.store.archive_id())?){return Err(Error::Stale);}
        let state=if record.state=="pending" && !current_time(&record,now(&tx)?) {
            "expired".to_owned()
        }else{record.state};
        self.verify()?;
        Ok(Receipt{state,attempt_id:record.attempt,provider_receipt_sha256:record.receipt,provider_code:record.code})
    }

    /// Returns only the authentication frame. The protocol is kept private
    /// until a reservation can durably enter attempted just before text transport.
    pub(crate) fn begin(&self,input:Confirmation,credential_bot:&str,secret:Zeroizing<String>)
        ->Result<(Session<'_>,wire::Frame)>{
        let Confirmation{target,action:Intent::Approve,passphrase,challenge,body_sha256}=input;
        let key=target.key()?;
        let archive=self.store.archive_id();
        let intent=target.intent(archive)?;
        if !hex_token(&challenge,64) || !hex_token(&body_sha256,64){return Err(Error::Input);}
        self.authorize(&passphrase)?;
        let attempt=random::<24>()?;
        let (protocol,auth_frame)=self.change(|tx|{
            if !installed(tx)?{return Err(Error::Stale);}
            let record=find(tx,&key)?;
            let time=now(tx)?;
            if record.state!="pending" || !equal(&record.intent,&intent)
                || !current_time(&record,time){return Err(Error::Stale);}
            let (text,actual)=body(tx,&target)?;
            if !equal(&actual,&record.body) || hex::encode(actual)!=body_sha256{return Err(Error::Stale);}
            let tag=commitment(archive,&intent,&actual,record.issued,record.expires,&challenge)?;
            if !record.challenge.as_ref().is_some_and(|saved|equal(saved,&tag)){return Err(Error::Stale);}
            // The protocol challenge is subordinate to the independently
            // authenticated exact manual operator confirmation above.
            let proposal=wire::Proposal::new(&target.bot,&target.recipient,target.recipient_kind.into(),&text)
                .map_err(|_|Error::Protocol)?;
            let exact=proposal.challenge();
            let approved=proposal.approve(&exact).map_err(|_|Error::Protocol)?;
            let send=wire::Exchange::begin(approved,credential_bot,secret).map_err(|_|Error::Protocol)?;
            let n=tx.execute("UPDATE native_wecom_bot_text_release_v1
                SET state='reserved',challenge=NULL,attempt_id=?2
                WHERE target_key=?1 AND state='pending'",params![key.as_slice(),attempt])?;
            if n!=1{return Err(Error::Stale);}
            Ok(send)
        })?;
        Ok((Session{operator:self,target,key,intent,attempt,protocol,
            passphrase:Some(passphrase),active:Phase::Reserved},auth_frame))
    }
}

#[derive(PartialEq,Eq)]
enum Phase { Reserved, Attempted, Finished }

/// Non-cloneable one-connection exchange. Only a future trusted fixed-endpoint
/// TLS adapter may feed authenticated frame messages; there is no caller receipt JSON.
pub(crate) struct Session<'a> {
    operator:&'a Operator,
    target:Target,
    key:[u8;32],
    intent:[u8;32],
    attempt:String,
    protocol:wire::Exchange,
    passphrase:Option<Zeroizing<String>>,
    active:Phase,
}

impl Session<'_>{
    pub(crate) fn deadline(&self)->std::time::Instant{self.protocol.deadline()}
    pub(crate) fn status(&self)->&wire::Status{self.protocol.status()}

    /// Must be called after provider subscription and immediately before any
    /// text write. Once committed, failure means never replay this revision.
    pub(crate) fn begin_send(&mut self)->Result<wire::Frame>{
        if self.active!=Phase::Reserved || self.status()!=&wire::Status::ReadyToSend
            || std::time::Instant::now()>=self.deadline()
        {return Err(Error::Protocol);}
        let passphrase=self.passphrase.take().ok_or(Error::Stale)?;
        self.operator.authorize(&passphrase)?;
        drop(passphrase);
        self.operator.change(|tx|{
            let record=find(tx,&self.key)?;
            if record.state!="reserved" || record.attempt.as_deref()!=Some(self.attempt.as_str())
                || !equal(&record.intent,&self.intent) || !current_time(&record,now(tx)?)
                || std::time::Instant::now()>=self.deadline()
            {return Err(Error::Stale);}
            let (_,actual)=body(tx,&self.target)?;
            if !equal(&actual,&record.body){return Err(Error::Stale);}
            let n=tx.execute("UPDATE native_wecom_bot_text_release_v1 SET state='attempted'
                WHERE target_key=?1 AND state='reserved' AND attempt_id=?2",
                params![self.key.as_slice(),self.attempt])?;
            if n!=1{return Err(Error::Stale);}
            Ok(())
        })?;
        self.active=Phase::Attempted;
        match self.protocol.begin_send(){
            Ok(frame)=>Ok(frame),
            Err(_)=>{
                self.protocol.close();
                self.persist_terminal()?;
                Err(Error::Protocol)
            }
        }
    }

    /// Internal trusted driver only. No exposed method accepts an external
    /// reported success, receipt string, or provider outcome.
    pub(crate) fn receive_from_official(&mut self,frame:&[u8])->Result<&wire::Status>{
        self.protocol.receive(frame);
        self.persist_terminal()?;
        Ok(self.protocol.status())
    }

    pub(crate) fn close(&mut self)->Result<&wire::Status>{
        self.protocol.close();
        self.persist_terminal()?;
        Ok(self.protocol.status())
    }

    fn persist_terminal(&mut self)->Result<()>{
        let wire::Status::Finished(outcome)=self.protocol.status() else{return Ok(());};
        let (before,after,hash,code)=match (&self.active,outcome){
            (Phase::Finished,_)=>return Ok(()),
            (Phase::Reserved,_)=>("reserved","closed_before_attempt",None,None),
            (Phase::Attempted,wire::Outcome::ProviderAccepted{message_id})=>{
                let digest=hash(&("lmh-official-wecom-receipt-v1",&self.attempt,message_id))?;
                ("attempted","provider_accepted",Some(hex::encode(digest)),None)
            }
            (Phase::Attempted,wire::Outcome::ProviderRejected{code})=>
                ("attempted","provider_rejected",None,Some(*code)),
            (Phase::Attempted,_)=>("attempted","delivery_unknown",None,None),
        };
        self.operator.change(|tx|{
            let n=tx.execute("UPDATE native_wecom_bot_text_release_v1 SET state=?3,
                receipt_hash=?4,provider_code=?5 WHERE target_key=?1 AND attempt_id=?2 AND state=?6",
                params![self.key.as_slice(),self.attempt,after,hash,code,before])?;
            if n!=1{return Err(Error::Stale);}
            Ok(())
        })?;
        self.passphrase=None;
        self.active=Phase::Finished;
        Ok(())
    }
}
