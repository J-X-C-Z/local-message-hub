//! Acknowledged pull for one opted-in native Desktop grant. Never delivers or opens a conversation.
use crate::{events::EventStore,notifications::{self,Error,Match},plugins::AccountScope};
use ring::hmac;
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use std::time::Instant;
use zeroize::Zeroizing;

const RECEIPT_SECONDS:u64=120;
const SCHEMA:&str="
CREATE TABLE native_notification_pull (
    platform TEXT NOT NULL,account_id TEXT NOT NULL,plugin_id TEXT NOT NULL,
    through INTEGER NOT NULL CHECK(through>=0),PRIMARY KEY(platform,account_id,plugin_id)
) WITHOUT ROWID;
";
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Poll { limit:Option<usize> }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Acknowledge { receipt:String }

#[derive(Serialize)]
pub(crate) struct Batch {
    enabled:bool,
    preference_revision:i64,
    has_more:bool,
    items:Vec<Match>,
    receipt:Option<String>,
}

#[derive(Serialize)]
pub(crate) struct Ack { acknowledged:bool, duplicate:bool }

pub(crate) struct Authority {
    key:hmac::Key,
    started:Instant,
}
impl Authority {
    pub(crate) fn new()->Option<Self> {
        let mut bytes=Zeroizing::new([0_u8;32]);
        getrandom::fill(bytes.as_mut_slice()).ok()?;
        Some(Self{key:hmac::Key::new(hmac::HMAC_SHA256,bytes.as_slice()),started:Instant::now()})
    }

    fn now(&self)->u64 {self.started.elapsed().as_secs()}

    fn sign(&self,binding:&Binding<'_>, claims:Claims)->Result<String> {
        let payload=claims.payload();
        let bound=binding.encode(&payload)?;
        let signature=hmac::sign(&self.key,&bound);
        Ok(format!("{payload}:{}",hex::encode(signature.as_ref())))
    }

    fn verify(&self,binding:&Binding<'_>,token:&str)->Result<Claims> {
        if token.len()>192 || !token.is_ascii(){return Err(Error::Input);}
        let (payload,tag)=token.rsplit_once(':').ok_or(Error::Input)?;
        if tag.len()!=64 || !tag.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)){
            return Err(Error::Input);
        }
        let mut signature=[0_u8;32];
        hex::decode_to_slice(tag,&mut signature).map_err(|_|Error::Input)?;
        let bound=binding.encode(payload)?;
        hmac::verify(&self.key,&bound,&signature).map_err(|_|Error::Input)?;
        let claims=Claims::parse(payload)?;
        if claims.expires<=self.now(){return Err(Error::Conflict);}
        Ok(claims)
    }
}
struct Binding<'a> { archive:&'a str,scope:&'a AccountScope,plugin:&'a str }
impl Binding<'_> {
    fn encode(&self,payload:&str)->Result<Vec<u8>> {
        serde_json::to_vec(&("lmh-notification-ack-v1",self.archive,&self.scope.platform,
            &self.scope.account_id,self.plugin,payload)).map_err(|_|Error::Storage)
    }
}

#[derive(Clone,Copy)]
struct Claims { after:i64, through:i64, revision:i64, expires:u64 }
impl Claims {
    fn payload(self)->String {
        format!("n1:{}:{}:{}:{}",self.after,self.through,self.revision,self.expires)
    }
    fn parse(value:&str)->Result<Self> {
        let mut p=value.split(':');
        if p.next()!=Some("n1"){return Err(Error::Input);}
        fn number(raw:Option<&str>)->Result<&str>{
            raw.filter(|v| !v.is_empty() && v.len()<=20 && v.bytes().all(|b|b.is_ascii_digit()))
                .ok_or(Error::Input)
        }
        let after=number(p.next())?.parse().map_err(|_|Error::Input)?;
        let through=number(p.next())?.parse().map_err(|_|Error::Input)?;
        let revision=number(p.next())?.parse().map_err(|_|Error::Input)?;
        let expires=number(p.next())?.parse().map_err(|_|Error::Input)?;
        if p.next().is_some() || through<=after {return Err(Error::Input);}
        Ok(Self{after,through,revision,expires})
    }
}

fn installed(db:&Connection)->Result<bool> {
    let kind:Option<String>=db.query_row(
        "SELECT type FROM sqlite_schema WHERE name COLLATE NOCASE='native_notification_pull'",
        [],|r|r.get(0)).optional()?;
    match kind.as_deref(){None=>Ok(false),Some("table")=>Ok(true),_=>Err(Error::Storage)}
}
fn position(db:&Connection,scope:&AccountScope,plugin:&str)->Result<Option<i64>>{
    if !installed(db)?{return Ok(None);}
    let saved:Option<i64>=db.query_row(
        "SELECT through FROM native_notification_pull WHERE platform COLLATE BINARY=?1
           AND account_id COLLATE BINARY=?2 AND plugin_id COLLATE BINARY=?3",
        params![scope.platform,scope.account_id,plugin],|r|r.get(0),
    ).optional()?;
    if saved.is_some_and(|v|v<0){return Err(Error::Storage);}
    Ok(saved)
}

fn initialize(store:&EventStore,scope:&AccountScope,plugin:&str)->Result<()>{
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    // Another local request may have initialized first, or the operator
    // may have disabled preferences between our read and this transaction.
    if !notifications::state(&tx,scope,plugin)?.preferences.enabled
        || position(&tx,scope,plugin)?.is_some(){return Ok(());}
    let through=notifications::checkpoint(&tx,scope)?;
    if !installed(&tx)?{tx.execute_batch(SCHEMA)?;}
    tx.execute(
        "INSERT INTO native_notification_pull(platform,account_id,plugin_id,through) VALUES(?1,?2,?3,?4)",
        params![scope.platform,scope.account_id,plugin,through])?;
    tx.commit()?;
    Ok(())
}

pub(crate) fn pull(
    store:&EventStore,scope:&AccountScope,plugin:&str,authority:&Authority,q:Poll,
)->Result<Batch>{
    store.require_ordinary()?;
    let limit=q.limit.unwrap_or(30);
    if !(1..=notifications::MAX_BATCH).contains(&limit){return Err(Error::Input);}
    if let Some(batch)=poll(store,scope,plugin,authority,limit)?{return Ok(batch);}
    initialize(store,scope,plugin)?;
    poll(store,scope,plugin,authority,limit)?.ok_or(Error::Conflict)
}

fn poll(
    store:&EventStore,scope:&AccountScope,plugin:&str,auth:&Authority,limit:usize,
)->Result<Option<Batch>> {
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let policy=notifications::state(&tx,scope,plugin)?;
    let revision=policy.revision;
    if !policy.preferences.enabled {
        return Ok(Some(Batch{enabled:false,preference_revision:revision,has_more:false,items:vec![],receipt:None}));
    }
    let Some(after)=position(&tx,scope,plugin)? else {return Ok(None);};
    let head=notifications::checkpoint(&tx,scope)?;
    if after>head{return Err(Error::Storage);}
    let page=notifications::scan(&tx,scope,&policy.preferences,after,limit)?;
    let receipt=if page.through>after{
        let claims=Claims{after,through:page.through,revision,
            expires:auth.now().checked_add(RECEIPT_SECONDS).ok_or(Error::Storage)?};
        let binding=Binding{archive:store.archive_id(),scope,plugin};
        Some(auth.sign(&binding,claims)?)
    }else{None};
    Ok(Some(Batch{enabled:true,preference_revision:revision,has_more:page.has_more,items:page.items,receipt}))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Activation { receipt:String, conversation_id:String }

#[derive(Serialize)]
pub(crate) struct Target {
    platform:String,
    account_id:String,
    conversation_id:String,
    conversation_title:Option<String>,
    conversation_kind:Option<String>,
}

pub(crate) fn activation_target(
    store:&EventStore,scope:&AccountScope,plugin:&str,auth:&Authority,q:Activation,
)->Result<Option<Target>> {
    store.require_ordinary()?;
    if !notifications::valid_id(&q.conversation_id){return Err(Error::Input);}
    let binding=Binding{archive:store.archive_id(),scope,plugin};
    let claims=match auth.verify(&binding,&q.receipt) {
        Ok(proof)=>proof,
        Err(Error::Input|Error::Conflict)=>return Ok(None),
        Err(error)=>return Err(error),
    };
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let policy=notifications::state(&tx,scope,plugin)?;
    if !policy.preferences.enabled || policy.revision!=claims.revision
        || !notifications::attests(&tx,scope,&policy.preferences,claims.after,claims.through,&q.conversation_id)? {
        return Ok(None);
    }
    let row:Option<(Option<String>,Option<String>)>=tx.query_row(
        "SELECT CASE WHEN typeof(title)='text' AND length(CAST(title AS BLOB))<=1024 THEN title END,
                CASE WHEN kind IN ('private','group','direct') THEN kind END
         FROM conversations WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
           AND id COLLATE BINARY=?3",
        params![scope.platform,scope.account_id,q.conversation_id],|row|Ok((row.get(0)?,row.get(1)?)),
    ).optional()?;
    if claims.expires<=auth.now(){return Ok(None);}
    Ok(row.map(|(title,kind)|Target{
        platform:scope.platform.clone(),account_id:scope.account_id.clone(),
        conversation_id:q.conversation_id,
        conversation_title:title.filter(|value|!value.chars().any(char::is_control)),
        conversation_kind:kind,
    }))
}

pub(crate) fn ack(
    store:&EventStore,scope:&AccountScope,plugin:&str,auth:&Authority,q:Acknowledge,
)->Result<Ack>{
    store.require_ordinary()?;
    let binding=Binding{archive:store.archive_id(),scope,plugin};
    let claims=auth.verify(&binding,&q.receipt)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    // Recheck expiry after acquiring the write slot. Policy changes, including
    // disable/mute changes, invalidate all outstanding pages atomically.
    let policy=notifications::state(&tx,scope,plugin)?;
    if !policy.preferences.enabled || policy.revision!=claims.revision || claims.expires<=auth.now(){
        return Err(Error::Conflict);
    }
    let current=position(&tx,scope,plugin)?.ok_or(Error::Conflict)?;
    if current==claims.through{
        return Ok(Ack{acknowledged:true,duplicate:true});
    }
    if current!=claims.after || claims.through>notifications::checkpoint(&tx,scope)? {
        return Err(Error::Conflict);
    }
    let n=tx.execute(
        "UPDATE native_notification_pull SET through=?4
         WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
           AND plugin_id COLLATE BINARY=?3 AND through=?5",
        params![scope.platform,scope.account_id,plugin,claims.through,claims.after])?;
    if n!=1{return Err(Error::Conflict);}
    tx.commit()?;
    Ok(Ack{acknowledged:true,duplicate:false})
}
