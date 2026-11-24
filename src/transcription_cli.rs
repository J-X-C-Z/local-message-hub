//! Operator-approved text-only handoff. No transcriber executable or audio path.
use crate::{
    events::EventStore,legacy_import::read_stable_source,plugins::AccountScope,
    store::PLATFORMS,transcription::{self,Change},
};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::{error::Error,io::{self,Write},path::Path};
use zeroize::Zeroizing;

const MAX_BYTES:usize=128*1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Detached {
    version:u8,
    platform:String,
    account_id:String,
    message_id:String,
    conversation_id:String,
    expected_revision:i64,
    text:String,
    language:Option<String>,
}

#[derive(Serialize)]
struct Preview {
    confirmation:String,
    prospective_annotation:transcription::Receipt,
    automatic:bool,
}

fn problem(error:&'static str)->io::Error {io::Error::other(error)}

#[cfg(unix)]
fn read_private(source:&Path)->io::Result<Zeroizing<Vec<u8>>>{
    use std::os::unix::fs::MetadataExt;
    let unavailable=||problem("private detached voice annotation unavailable");
    let before=std::fs::symlink_metadata(source).map_err(|_|unavailable())?;
    if !before.is_file() || before.mode()&0o077!=0 || before.nlink()!=1 || before.len()>MAX_BYTES as u64 {
        return Err(unavailable());
    }
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|unavailable())?);
    let after=std::fs::symlink_metadata(source).map_err(|_|unavailable())?;
    if !after.is_file() || after.mode()&0o077!=0 || after.nlink()!=1
        || before.dev()!=after.dev() || before.ino()!=after.ino()
        || before.ctime()!=after.ctime() || before.ctime_nsec()!=after.ctime_nsec()
    {
        return Err(unavailable());
    }
    Ok(bytes)
}

#[cfg(not(unix))]
fn read_private(_source:&Path)->io::Result<Zeroizing<Vec<u8>>>{
    Err(problem("private detached voice annotation requires a supported Unix file mode"))
}

fn result_error(error:transcription::Error)->io::Error{
    use transcription::Error::*;
    problem(match error {
        Input=>"invalid detached annotation",
        Missing=>"exact indexed voice message unavailable",
        Conflict=>"detached annotation revision conflict",
        Quota=>"manual annotation quota reached",
        Locked|Storage=>"local annotation store unavailable",
    })
}

fn output(record:impl Serialize)->Result<(),Box<dyn Error>> {
    let mut stdout=io::stdout().lock();
    serde_json::to_writer(&mut stdout,&record)?;
    writeln!(stdout)?;
    stdout.flush()?;
    Ok(())
}

/// Bind a preview to exact local bytes, ordinary archive instance, and account;
/// the operator must explicitly pass the same confirmation at apply time.
pub fn run(source:&Path,archive:&Path,platform:&str,account:&str,confirmation:Option<&str>)
    ->Result<(),Box<dyn Error>> {
    if !PLATFORMS.contains(&platform) || platform=="all" || !(1..=256).contains(&account.len())
        || account.trim()!=account || account.chars().any(char::is_control)
    {
        return Err(problem("invalid exact account selection").into());
    }
    let archive=EventStore::open(archive).map_err(|_|problem("ordinary native archive unavailable"))?;
    let bytes=read_private(source)?;
    let document:Detached=serde_json::from_slice(&bytes).map_err(|_|problem("invalid detached voice annotation"))?;
    if document.version!=1 || document.platform!=platform || document.account_id!=account {
        return Err(problem("detached annotation scope mismatch").into());
    }
    let content=hex::encode(Sha256::digest(bytes.as_slice()));
    let encoded=serde_json::to_vec(&("lmh-voice-annotation-v1",archive.archive_id(),platform,account,content))?;
    let expected=hex::encode(Sha256::digest(encoded));
    if confirmation.is_some_and(|token|token!=expected) {
        return Err(problem("confirmation does not match exact detached voice annotation").into());
    }
    let scope=AccountScope{platform:platform.to_owned(),account_id:account.to_owned()};
    let change=Change::Set{
        conversation_id:document.conversation_id,message_id:document.message_id,
        expected_revision:document.expected_revision,text:document.text,language:document.language,
    };
    let record=transcription::operator_handoff(&archive,&scope,change,confirmation.is_some())
        .map_err(result_error)?;
    if confirmation.is_some(){
        output(record)
    }else{
        output(Preview{confirmation:expected,prospective_annotation:record,automatic:false})
    }
}
