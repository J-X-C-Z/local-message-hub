//! Private operator result ledger for an already verified detached voice.
//! No provider, model, native-live message or immutable bundle mutations.
use crate::{detached_voice_wav::{self as voice,VerifiedVoice},events::EventStore,
    legacy_import::read_stable_source,media_bundle::MessageKey};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::{error::Error,fs,io::{self,Write},os::unix::fs::MetadataExt,path::Path};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const MAX_SOURCE:usize=128*1024;
const MAX_TEXT:usize=16*1024;
const MAX_ROWS:i64=4096;
const MAX_REV:i64=9_007_199_254_740_991;
const TABLE:&str="native_detached_voice_result_v1";
const SCHEMA:&str="
CREATE TABLE native_detached_voice_result_v1(
 key TEXT PRIMARY KEY NOT NULL CHECK(length(key)=64),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
 text TEXT NOT NULL CHECK(length(CAST(text AS BLOB)) BETWEEN 1 AND 16384),
 language TEXT, confirmation TEXT NOT NULL CHECK(length(confirmation)=64)
) WITHOUT ROWID;";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ResultFile {
    version:u8, platform:String,account_id:String,conversation_id:String,message_id:String,
    attachment_id:String,audio_sha256:String,message_snapshot_witness:String,
    audio_confirmation:String,expected_revision:i64,text:String,language:Option<String>,
}
#[derive(Serialize)]
struct Receipt<'a>{
    mode:&'static str, expected_revision:i64, revision:i64, confirmation:&'a str,
    audio_sha256:&'a str, message_snapshot_witness:&'a str, provider_or_model_verified:bool,
}
fn fail(message:&'static str)->io::Error{io::Error::other(message)}
fn invalid()->io::Error{fail("invalid private detached voice result")}
fn unavailable()->io::Error{fail("detached voice result journal unavailable")}
fn stale()->io::Error{fail("detached voice result revision conflict")}
fn valid_text(value:&str)->bool{
    value.len()<=MAX_TEXT && !value.trim().is_empty()
        && !value.chars().any(|c|c.is_control()&&!matches!(c,'\r'|'\n'|'\t'))
}
fn valid_language(value:&str)->bool{
    let mut parts=value.split('-');
    parts.next().is_some_and(|p|(2..=3).contains(&p.len())&&p.bytes().all(|b|b.is_ascii_lowercase()))
        && parts.next().is_none_or(|p|p.len()==2&&p.bytes().all(|b|b.is_ascii_uppercase()))
        && parts.next().is_none()
}
fn private_file(path:&Path)->io::Result<Zeroizing<Vec<u8>>>{
    let safe=|m:&fs::Metadata| m.is_file() && m.nlink()==1 && m.mode()&0o077==0
        && m.uid()==rustix::process::geteuid().as_raw() && m.len()<=MAX_SOURCE as u64;
    let before=fs::symlink_metadata(path).map_err(|_|invalid())?;
    if !safe(&before){return Err(invalid());}
    let input=Zeroizing::new(read_stable_source(path,MAX_SOURCE).map_err(|_|invalid())?);
    let after=fs::symlink_metadata(path).map_err(|_|invalid())?;
    if !safe(&after) || before.dev()!=after.dev() || before.ino()!=after.ino()
        || before.ctime()!=after.ctime() || before.ctime_nsec()!=after.ctime_nsec()
    {return Err(invalid());}
    Ok(input)
}
fn installed(db:&Connection)->io::Result<bool>{
    let entry:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE=?1",[TABLE],
        |r|Ok((r.get(0)?,r.get(1)?))).optional().map_err(|_|unavailable())?;
    match entry{None=>Ok(false),Some((name,kind)) if name==TABLE && kind=="table"=>Ok(true),_=>Err(unavailable())}
}
fn journal(directory:&Path,bundle:&Path)->io::Result<EventStore>{
    voice::private_owner(directory)?;
    let root=directory.canonicalize().map_err(|_|unavailable())?;
    let bundle=bundle.canonicalize().map_err(|_|unavailable())?;
    if root.starts_with(&bundle)||bundle.starts_with(&root){return Err(unavailable());}
    EventStore::open(directory).map_err(|_|unavailable())
}
fn entry_key(store:&EventStore,verified:&VerifiedVoice,attachment:&str)->String{
    voice::binding(&["detached-voice-result-record",store.archive_id(),&verified.instance(),
        &verified.witness,attachment,&verified.audio_digest])
}
fn valid_result(result:&ResultFile,key:&MessageKey,attachment:&str,verified:&VerifiedVoice)->bool{
    result.version==1 && result.platform==key.platform && result.account_id==key.account_id
        && result.conversation_id==key.conversation_id && result.message_id==key.message_id
        && result.attachment_id==attachment && result.audio_sha256==verified.audio_digest
        && result.message_snapshot_witness==verified.witness && result.audio_confirmation==verified.confirmation
        && (0..MAX_REV).contains(&result.expected_revision) && valid_text(&result.text)
        && result.language.as_deref().is_none_or(valid_language)
}
fn write_json(value:&impl Serialize)->Result<(),Box<dyn Error>>{
    let mut out=io::stdout().lock();
    serde_json::to_writer(&mut out,value)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}
fn store_result(
    store:&EventStore,verified:&VerifiedVoice,key:&str,source:&Path,scope:&MessageKey,attachment:&str,
    confirmation:Option<&str>,
)->Result<(),Box<dyn Error>>{
    let bytes=private_file(source)?;
    let value:ResultFile=serde_json::from_slice(bytes.as_slice()).map_err(|_|invalid())?;
    if !valid_result(&value,scope,attachment,verified){return Err(invalid().into());}
    let source_hash=hex::encode(Sha256::digest(bytes.as_slice()));
    let bound=voice::binding(&["detached-voice-result-approval",key,&source_hash]);
    if confirmation.is_some_and(|token|!voice::hex_digest(token)
        || !bool::from(bound.as_bytes().ct_eq(token.as_bytes())))
    {return Err(fail("confirmation does not bind this result, media, and journal").into());}

    let committing=confirmation.is_some();
    let mut db=if committing {store.write_connection()}else{store.read_connection()}?;
    let tx=db.transaction_with_behavior(if committing{TransactionBehavior::Immediate}else{TransactionBehavior::Deferred})
        .map_err(|_|unavailable())?;
    store.verify_connection(&tx)?;
    verified.reverify()?;
    let exists=installed(&tx)?;
    let current:Option<(i64,String)>=if exists{
        tx.query_row("SELECT revision,confirmation FROM native_detached_voice_result_v1 WHERE key=?1",
            [key],|r|Ok((r.get(0)?,r.get(1)?))).optional().map_err(|_|unavailable())?
    }else{None};
    let revision=value.expected_revision+1;
    let retry=current.as_ref().is_some_and(|(rev,tag)|*rev==revision && tag==&bound);
    if !retry && current.as_ref().map_or(0,|(rev,_)|*rev)!=value.expected_revision {
        return Err(stale().into());
    }
    if current.is_none() && exists {
        let count:i64=tx.query_row("SELECT COUNT(*) FROM native_detached_voice_result_v1",[],|r|r.get(0))
            .map_err(|_|unavailable())?;
        if count>=MAX_ROWS{return Err(fail("detached voice result journal capacity reached").into());}
    }
    if committing && !retry{
        if !exists{tx.execute_batch(SCHEMA).map_err(|_|unavailable())?;}
        tx.execute("INSERT INTO native_detached_voice_result_v1(key,revision,text,language,confirmation)
            VALUES(?1,?2,?3,?4,?5)
            ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,text=excluded.text,
            language=excluded.language,confirmation=excluded.confirmation",
            params![key,revision,value.text,value.language,bound]).map_err(|_|unavailable())?;
    }
    verified.reverify_audio()?;
    store.verify_connection(&tx)?;
    tx.commit().map_err(|_|unavailable())?;
    verified.reverify()?;
    store.verify()?;
    write_json(&Receipt{mode:if committing{"local_voice_result_committed"}else{"local_voice_result_preview"},
        expected_revision:value.expected_revision,revision,confirmation:&bound,audio_sha256:&verified.audio_digest,
        message_snapshot_witness:&verified.witness,provider_or_model_verified:false})
}

#[derive(Serialize)]
struct Annotation<'a>{
    mode:&'static str,revision:i64,text:String,language:Option<String>,
    audio_sha256:&'a str,message_snapshot_witness:&'a str,provider_or_model_verified:bool,
}
fn read(store:&EventStore,verified:&VerifiedVoice,key:&str,pipe:u64)->Result<(),Box<dyn Error>>{
    let mut db=store.read_connection()?;
    let tx=db.transaction().map_err(|_|unavailable())?;
    store.verify_connection(&tx)?;
    if !installed(&tx)?{return Err(fail("no result for current exact detached voice").into());}
    let found:Option<(i64,String,Option<String>)>=tx.query_row(
        "SELECT revision,CASE WHEN length(CAST(text AS BLOB))<=16384 THEN text END,language
         FROM native_detached_voice_result_v1 WHERE key=?1",
        [key],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional().map_err(|_|unavailable())?;
    let (revision,text,language)=found.ok_or_else(||fail("no result for current exact detached voice"))?;
    if !(1..=MAX_REV).contains(&revision)||!valid_text(&text)||!language.as_deref().is_none_or(valid_language){
        return Err(unavailable().into());
    }
    let mut secret=Zeroizing::new(serde_json::to_vec(&Annotation{mode:"local_detached_voice_annotation",
        revision,text,language,audio_sha256:&verified.audio_digest,message_snapshot_witness:&verified.witness,
        provider_or_model_verified:false})?);
    secret.push(b'\n');
    drop(tx);
    verified.reverify_audio()?;
    store.verify()?;
    Ok(voice::output(secret,pipe)?)
}

/// An OS-owner CLI only. Read output requires a private anonymous pipe;
/// preview/commit return metadata only, not transcript text.
pub fn run(bundle:&Path,directory:&Path,platform:&str,account:&str,conversation:&str,message:&str,attachment:&str,
    source:Option<&Path>,confirmation:Option<&str>)->Result<(),Box<dyn Error>>{
    if source.is_none() && confirmation.is_some(){return Err(invalid().into());}
    let pipe=if source.is_none(){Some(voice::pipe()?)}else{None};
    let scope=MessageKey{platform:platform.to_owned(),account_id:account.to_owned(),
        conversation_id:conversation.to_owned(),message_id:message.to_owned()};
    let store=journal(directory,bundle)?;
    let verified=voice::inspect(bundle,&scope,attachment)?;
    let key=entry_key(&store,&verified,attachment);
    if let Some(source)=source {
        store_result(&store,&verified,&key,source,&scope,attachment,confirmation)
    }else{
        read(&store,&verified,&key,pipe.ok_or_else(invalid)?)
    }
}
