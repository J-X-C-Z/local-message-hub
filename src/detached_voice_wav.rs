//! Private operator handoff of one existing verified PCM WAV. No codec, model,
//! subprocess, provider request, HTTP route or annotation mutation.
use crate::{media_bundle::{Bundle,MessageKey},snapshot::Snapshot,store::Store};
use rusqlite::params;
use rustix::fs::{FileType,fstat};
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::{error::Error,fs,io::{self,Write},os::unix::fs::MetadataExt,path::Path,
    sync::mpsc,time::Duration};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const MAX_WAV:usize=8*1024*1024;
const MAX_SECONDS:u32=300;
const WRITE_TIMEOUT:Duration=Duration::from_secs(30);

fn refusal(text:&'static str)->io::Error {io::Error::other(text)}
fn invalid()->io::Error {refusal("exact indexed voice and verified detached attachment unavailable")}
fn unsupported()->io::Error {refusal("attachment is not supported canonical PCM WAV")}
pub(crate) fn hex_digest(s:&str)->bool{
    s.len()==64 && s.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Serialize)]
struct Format {
    encoding:&'static str,
    sample_rate:u32,
    channels:u16,
    sample_frames:u32,
}
fn word(bytes:&[u8],offset:usize)->Option<u16>{
    Some(u16::from_le_bytes(bytes.get(offset..offset+2)?.try_into().ok()?))
}
fn dword(bytes:&[u8],offset:usize)->Option<u32>{
    Some(u32::from_le_bytes(bytes.get(offset..offset+4)?.try_into().ok()?))
}

fn pcm(bytes:&[u8])->Option<Format>{
    if !(44..=MAX_WAV).contains(&bytes.len()) || bytes.get(0..4)?!=b"RIFF"
        || bytes.get(8..16)?!=b"WAVEfmt " || bytes.get(36..40)?!=b"data"
        || dword(bytes,4)? as usize!=bytes.len()-8 || dword(bytes,16)?!=16
        || word(bytes,20)?!=1 || word(bytes,34)?!=16
    {
        return None;
    }
    // No ancillary/vendor chunks, embedded names, compression, external
    // references, extended header, trailing bytes, or transcoding.
    let channels=word(bytes,22)?;
    let sample_rate=dword(bytes,24)?;
    if !matches!(channels,1|2)
        || !matches!(sample_rate,8_000|16_000|22_050|24_000|32_000|44_100|48_000)
    {
        return None;
    }
    let block=channels.checked_mul(2)?;
    if word(bytes,32)?!=block || dword(bytes,28)?!=sample_rate.checked_mul(u32::from(block))? {
        return None;
    }
    let data=dword(bytes,40)?;
    if data==0 || data as usize!=bytes.len()-44 || data%u32::from(block)!=0{return None;}
    let frames=data/u32::from(block);
    if frames>sample_rate.checked_mul(MAX_SECONDS)?{return None;}
    Some(Format{encoding:"pcm_s16le",sample_rate,channels,sample_frames:frames})
}

fn require_voice(store:&Store,key:&MessageKey)->io::Result<()>{
    let db=store.connection().map_err(|_|invalid())?;
    let mut stmt=db.prepare(
        "SELECT CASE WHEN m.type COLLATE BINARY='voice' THEN 1 ELSE 0 END
         FROM messages m JOIN conversations c
           ON c.platform COLLATE BINARY=m.platform AND c.account_id COLLATE BINARY=m.account_id
           AND c.id COLLATE BINARY=m.conversation_id
         WHERE m.platform COLLATE BINARY=?1 AND m.account_id COLLATE BINARY=?2
           AND m.conversation_id COLLATE BINARY=?3 AND m.id COLLATE BINARY=?4 LIMIT 2"
    ).map_err(|_|invalid())?;
    let mut rows=stmt.query(params![key.platform,key.account_id,key.conversation_id,key.message_id])
        .map_err(|_|invalid())?;
    let present=match rows.next().map_err(|_|invalid())? {
        Some(row)=>row.get::<_,bool>(0).map_err(|_|invalid())?,
        None=>false,
    };
    if !present || rows.next().map_err(|_|invalid())?.is_some(){return Err(invalid());}
    Ok(())
}

pub(crate) fn private_owner(directory:&Path)->io::Result<()> {
    let info=fs::symlink_metadata(directory).map_err(|_|invalid())?;
    if !info.is_dir() || info.mode()&0o077!=0 || info.uid()!=rustix::process::geteuid().as_raw(){
        return Err(invalid());
    }
    Ok(())
}
fn verify(bundle:&Bundle,snapshot:&Snapshot)->io::Result<()>{
    bundle.verify().map_err(|_|invalid())?;
    snapshot.verify().map_err(|_|invalid())
}
fn sha(bytes:&[u8])->String {hex::encode(Sha256::digest(bytes))}
pub(crate) fn binding(parts:&[&str])->String{
    let mut hash=Sha256::new();
    hash.update(b"lmh-detached-voice-wav-v1\0");
    for value in parts {
        hash.update((value.len() as u64).to_be_bytes());
        hash.update(value.as_bytes());
    }
    hex::encode(hash.finalize())
}

#[derive(Serialize)]
struct Preview<'a>{
    mode:&'static str,
    platform:&'a str,
    account_id:&'a str,
    conversation_id:&'a str,
    message_id:&'a str,
    attachment_id:&'a str,
    audio_sha256:&'a str,
    message_snapshot_witness:&'a str,
    bytes:usize,
    wav:Format,
    confirmation:&'a str,
    audio_disclosed:bool,
}

pub(crate) fn pipe()->io::Result<u64>{
    let meta=fstat(io::stdout()).map_err(|_|refusal("private audio output unavailable"))?;
    let inode=meta.st_ino as u64;
    if FileType::from_raw_mode(meta.st_mode)!=FileType::Fifo
        || !fs::read_link("/proc/self/fd/1").is_ok_and(|path|
            path.as_os_str()==std::ffi::OsStr::new(&format!("pipe:[{inode}]")))
    {
        return Err(refusal("audio output requires an anonymous operator-owned stdout pipe"));
    }
    Ok(inode)
}

pub(crate) fn output(audio:Zeroizing<Vec<u8>>,expected_pipe:u64)->io::Result<()>{
    let (send,receive)=mpsc::sync_channel(1);
    std::thread::Builder::new().spawn(move||{
        let result=(||{
            if pipe()?!=expected_pipe{return Err(refusal("private audio output changed"));}
            let mut stdout=io::stdout().lock();
            stdout.write_all(audio.as_slice())?;
            stdout.flush()
        })();
        let _=send.send(result);
    })?;
    match receive.recv_timeout(WRITE_TIMEOUT){
        Ok(result)=>result,
        Err(_)=>Err(refusal("private audio output did not complete; discard partial input")),
    }
}

pub(crate) struct VerifiedVoice {
    catalog:Bundle,
    snapshot:Snapshot,
    location:std::path::PathBuf,
    identity:(u64,u64,i64,i64),
    key:MessageKey,
    attachment:String,
    pub audio_digest:String,
    pub witness:String,
    pub confirmation:String,
    audio:Zeroizing<Vec<u8>>,
    wav:Format,
}
fn root_identity(path:&Path)->io::Result<(u64,u64,i64,i64)>{
    private_owner(path)?;
    let info=fs::symlink_metadata(path).map_err(|_|invalid())?;
    if !info.is_dir() || info.uid()!=rustix::process::geteuid().as_raw()
        || info.mode()&0o077!=0 {return Err(invalid());}
    Ok((info.dev(),info.ino(),info.ctime(),info.ctime_nsec()))
}
impl VerifiedVoice {
    pub(crate) fn reverify(&self)->io::Result<()>{
        if root_identity(&self.location)?!=self.identity{return Err(invalid());}
        verify(&self.catalog,&self.snapshot)
    }

    pub(crate) fn reverify_audio(&self)->io::Result<()>{
        self.reverify()?;
        // An in-place object edit may leave the parent directory untouched.
        // Rehash the same bounded complete attachment at the release fence.
        let bytes=Zeroizing::new(self.catalog.read(&self.key,&self.attachment)
            .map_err(|_|invalid())?.ok_or_else(invalid)?);
        if !bool::from(sha(bytes.as_slice()).as_bytes().ct_eq(self.audio_digest.as_bytes())){
            return Err(invalid());
        }
        self.reverify()
    }

    pub(crate) fn instance(&self)->String{
        let (dev,ino,sec,nsec)=self.identity;
        binding(&["voice-bundle-instance",&self.witness,&dev.to_string(),&ino.to_string(),
            &sec.to_string(),&nsec.to_string()])
    }
}
pub(crate) fn inspect(bundle:&Path,key:&MessageKey,attachment:&str)->io::Result<VerifiedVoice>{
    if !key.valid() || !hex_digest(attachment){return Err(invalid());}
    let identity=root_identity(bundle)?;
    let (catalogue,snapshot,index)=Bundle::open(bundle).map_err(|_|invalid())?;
    verify(&catalogue,&snapshot)?;
    require_voice(&index,key)?;
    let audio=Zeroizing::new(catalogue.read(key,attachment).map_err(|_|invalid())?.ok_or_else(invalid)?);
    let wav=pcm(audio.as_slice()).ok_or_else(unsupported)?;
    let audio_digest=sha(audio.as_slice());
    // Retain the original PCM confirmation contract. The result ledger
    // separately binds its entry to the private directory instance.
    let witness=binding(&["voice-snapshot-witness",catalogue.catalog_stamp(),&key.platform,
        &key.account_id,&key.conversation_id,&key.message_id]);
    let confirmation=binding(&["confirmed-audio-pipe",&witness,attachment,&audio_digest]);
    require_voice(&index,key)?;
    let verified=VerifiedVoice{catalog:catalogue,snapshot,location:bundle.to_owned(),identity,
        key:key.clone(),attachment:attachment.to_owned(),audio_digest,witness,confirmation,audio,wav};
    verified.reverify()?;
    Ok(verified)
}

/// Review one already-imported selection, or emit its verified bytes after
/// explicit confirmation. Foreground CLI only, with trusted local descriptors.
pub fn run(
    bundle:&Path,platform:&str,account:&str,conversation:&str,message:&str,attachment:&str,
    confirmation:Option<&str>,
)->Result<(),Box<dyn Error>>{
    let key=MessageKey{platform:platform.to_owned(),account_id:account.to_owned(),
        conversation_id:conversation.to_owned(),message_id:message.to_owned()};
    if confirmation.is_some_and(|id|!hex_digest(id)){return Err(invalid().into());}
    let private_pipe=confirmation.map(|_|pipe()).transpose()?;
    let verified=inspect(bundle,&key,attachment)?;
    if let Some(token)=confirmation{
        if !bool::from(token.as_bytes().ct_eq(verified.confirmation.as_bytes())){
            return Err(refusal("confirmation does not match this exact detached WAV").into());
        }
        return Ok(output(verified.audio,private_pipe.ok_or_else(invalid)?)?);
    }
    let preview=Preview{mode:"private_detached_pcm_wav",platform,account_id:account,conversation_id:conversation,
        message_id:message,attachment_id:attachment,audio_sha256:&verified.audio_digest,
        message_snapshot_witness:&verified.witness,bytes:verified.audio.len(),
        wav:verified.wav,confirmation:&verified.confirmation,audio_disclosed:false};
    let mut out=io::stdout().lock();
    serde_json::to_writer(&mut out,&preview)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}
