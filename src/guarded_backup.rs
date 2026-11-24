//! Bounded one-account native archive backup. The envelope is not a SQLite or ZIP file.
use crate::{events::{EventStore,NATIVE_DATABASE},guarded_snapshot::{self,BackupRows,MAX_PAYLOAD},
    profile_lock::{GuardedExclusive,valid_password}};
use ring::{aead,pbkdf2};
use rustix::fs::{AtFlags, Mode,OFlags,RenameFlags,open,renameat_with,statat};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use std::{ffi::OsString,fs::{self,File},io::{self,Read,Write},num::NonZeroU32,
    os::unix::fs::{MetadataExt,PermissionsExt},path::{Path,PathBuf}};
use zeroize::Zeroizing;

const MAGIC:&[u8;8]=b"LMHENC01";
const HEADER:usize=48;
const TAG:usize=16;
const ROUNDS:u32=600_000;
pub(crate) const MAX_FILE:usize=HEADER+MAX_PAYLOAD+TAG;

#[derive(Debug,thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid backup passphrase parameters")]
    Password,
    #[error("encrypted backup unavailable or invalid")]
    Input,
    #[error("encrypted backup authentication failed")]
    Authentication,
    #[error("encrypted backup does not match the confirmed SHA-256")]
    Confirmation,
    #[error("guarded backup contains invalid or over-budget account data")]
    Data,
    #[error("backup requires an available offline guarded profile")]
    Profile,
    #[error("backup target must be a new leaf within a private local directory")]
    Target,
    #[error("backup operation failed")]
    Io,
}

struct Destination {parent:PathBuf,handle:File,leaf:OsString}
impl Destination {
    fn prepare(target:&Path) -> Result<Self,Error> {
        let leaf=target.file_name().ok_or(Error::Target)?.to_owned();
        let parent=target.parent().filter(|p|!p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        let info=fs::symlink_metadata(parent).map_err(|_|Error::Target)?;
        if !info.is_dir() || info.mode()&0o077!=0 {return Err(Error::Target);}
        let parent=parent.canonicalize().map_err(|_|Error::Target)?;
        let handle:File=open(&parent,OFlags::RDONLY|OFlags::DIRECTORY|OFlags::NOFOLLOW|OFlags::CLOEXEC,Mode::empty())
            .map_err(|_|Error::Target)?.into();
        let actual=handle.metadata().map_err(|_|Error::Target)?;
        if info.dev()!=actual.dev() || info.ino()!=actual.ino() || actual.mode()&0o077!=0 {
            return Err(Error::Target);
        }
        // Never stage inside live/native/legacy profile roots.
        for reserved in [NATIVE_DATABASE,"hub.sqlite3","index.sqlite3","settings.json"] {
            if !matches!(fs::symlink_metadata(parent.join(reserved)),Err(error) if error.kind()==io::ErrorKind::NotFound) {
                return Err(Error::Target);
            }
        }
        if !matches!(fs::symlink_metadata(parent.join(&leaf)),Err(e) if e.kind()==io::ErrorKind::NotFound) {
            return Err(Error::Target);
        }
        Ok(Self{parent,handle,leaf})
    }

    fn publish(&self,stage:&Path,original:&fs::Metadata) -> Result<bool,Error> {
        let from=stage.file_name().ok_or(Error::Target)?;
        let now=statat(&self.handle,from,AtFlags::SYMLINK_NOFOLLOW).map_err(|_|Error::Target)?;
        if original.ino()!=now.st_ino as u64 || original.dev()!=now.st_dev as u64 {return Err(Error::Target);}
        self.handle.sync_all().map_err(|_|Error::Io)?;
        renameat_with(&self.handle,from,&self.handle,&self.leaf,RenameFlags::NOREPLACE)
            .map_err(|_|Error::Target)?;
        Ok(self.handle.sync_all().is_ok())
    }
}

struct Payload(Zeroizing<Vec<u8>>);
impl Write for Payload {
    fn write(&mut self,buf:&[u8]) -> io::Result<usize> {
        if self.0.len()+buf.len()>MAX_PAYLOAD {return Err(io::Error::other("backup too large"));}
        self.0.extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self)->io::Result<()> {Ok(())}
}

fn key(passphrase:&str,salt:&[u8]) -> Result<aead::LessSafeKey,Error> {
    let mut key=Zeroizing::new([0_u8;32]);
    pbkdf2::derive(pbkdf2::PBKDF2_HMAC_SHA256,NonZeroU32::new(ROUNDS).unwrap(),
        salt,passphrase.as_bytes(),&mut key[..]);
    aead::UnboundKey::new(&aead::AES_256_GCM,&key[..]).map(aead::LessSafeKey::new).map_err(|_|Error::Io)
}

fn seal(data:BackupRows,passphrase:&str)->Result<([u8;HEADER],Zeroizing<Vec<u8>>),Error> {
    let mut plain=Payload(Zeroizing::new(Vec::new()));
    data.write_consuming_json(&mut plain).map_err(|_|Error::Data)?;
    let mut header=[0_u8;HEADER];
    header[..8].copy_from_slice(MAGIC);
    header[8..12].copy_from_slice(&[1,1,1,0]);
    header[12..16].copy_from_slice(&ROUNDS.to_be_bytes());
    header[16..20].copy_from_slice(&((plain.0.len()+TAG) as u32).to_be_bytes());
    getrandom::fill(&mut header[20..]).map_err(|_|Error::Io)?;
    let nonce=aead::Nonce::assume_unique_for_key(header[36..48].try_into().map_err(|_|Error::Io)?);
    let tag=key(passphrase,&header[20..36])?.seal_in_place_separate_tag(nonce,aead::Aad::from(header),&mut plain.0)
        .map_err(|_|Error::Io)?;
    plain.0.extend_from_slice(tag.as_ref());
    Ok((header,plain.0))
}

fn decrypt<'a>(input:&'a mut [u8],passphrase:&str)->Result<&'a mut [u8],Error> {
    if !(HEADER+TAG+1..=MAX_FILE).contains(&input.len()) {return Err(Error::Input);}
    let (header,ciphertext)=input.split_at_mut(HEADER);
    if &header[..8]!=MAGIC || header[8..12]!=[1,1,1,0]
        || header[12..16]!=ROUNDS.to_be_bytes()
        || header[16..20]!=(ciphertext.len() as u32).to_be_bytes()
    {return Err(Error::Input);}
    let nonce=aead::Nonce::assume_unique_for_key(header[36..48].try_into().map_err(|_|Error::Input)?);
    key(passphrase,&header[20..36])?.open_in_place(nonce,aead::Aad::from(header),ciphertext)
        .map_err(|_|Error::Authentication)
}

fn passwords(a:&str,b:&str)->Result<(),Error> {
    if !valid_password(a) || !valid_password(b) || a==b {Err(Error::Password)} else {Ok(())}
}

pub(crate) fn create(directory:&Path,target:&Path,platform:&str,account:&str,profile:&str,backup:&str)
    -> Result<Value,Error> {
    passwords(profile,backup)?;
    let out=Destination::prepare(target)?;
    let lease=GuardedExclusive::acquire(directory).map_err(|_|Error::Profile)?;
    let auth=lease.authorize_import(profile).map_err(|_|Error::Profile)?;
    let data=guarded_snapshot::backup_rows(&auth,platform,account).map_err(|_|Error::Data)?;
    let (header,ciphertext)=seal(data,backup)?;
    let mut stage=tempfile::Builder::new().prefix(".lmh-sealed-stage-").tempfile_in(&out.parent).map_err(|_|Error::Io)?;
    let info=stage.as_file().metadata().map_err(|_|Error::Io)?;
    if info.mode()&0o077!=0 {return Err(Error::Io);}
    stage.write_all(&header).and_then(|_|stage.write_all(&ciphertext)).map_err(|_|Error::Io)?;
    stage.as_file().sync_all().map_err(|_|Error::Io)?;
    auth.recheck().map_err(|_|Error::Profile)?;
    let parent_synced=out.publish(stage.path(),&info)?;
    Ok(json!({"event":"backup_created","bytes":HEADER+ciphertext.len(),"parent_synced":parent_synced}))
}

fn read_input(source:&Path)->Result<Zeroizing<Vec<u8>>,Error> {
    let original=fs::symlink_metadata(source).map_err(|_|Error::Input)?;
    if !original.is_file() || original.len()>MAX_FILE as u64 {return Err(Error::Input);}
    let file:File=open(source,OFlags::RDONLY|OFlags::NOFOLLOW|OFlags::NONBLOCK|OFlags::CLOEXEC,Mode::empty())
        .map_err(|_|Error::Input)?.into();
    let info=file.metadata().map_err(|_|Error::Input)?;
    if !info.is_file() || info.dev()!=original.dev() || info.ino()!=original.ino() || info.len()!=original.len() {
        return Err(Error::Input);
    }
    let mut bytes=Zeroizing::new(Vec::new());
    (&file).take(MAX_FILE as u64+1).read_to_end(&mut bytes).map_err(|_|Error::Input)?;
    let after=file.metadata().map_err(|_|Error::Input)?;
    if bytes.len() as u64!=info.len() || info.ctime()!=after.ctime() || info.ctime_nsec()!=after.ctime_nsec() {
        return Err(Error::Input);
    }
    Ok(bytes)
}

/// Authenticate the exact bytes and validate the restore payload without writing it.
pub(crate) fn verify_catalog_scope(ciphertext: &[u8], backup: &str, platform: &str, account: &str)
    -> Result<(), Error> {
    if !valid_password(backup) { return Err(Error::Password); }
    let mut input = Zeroizing::new(ciphertext.to_vec());
    validate_scope(&mut input,backup,platform,account)
}

/// For inventory only: consume the private input buffer so that authenticated
/// plaintext is never retained by the catalog caller after verification.
pub(crate) fn verify_owned_catalog_scope(mut input: Zeroizing<Vec<u8>>, backup: &str,
    platform: &str, account: &str) -> Result<(), Error> {
    if !valid_password(backup) { return Err(Error::Password); }
    validate_scope(&mut input,backup,platform,account)
}

fn validate_scope(input:&mut [u8],backup:&str,platform:&str,account:&str)->Result<(),Error> {
    let decoded = decrypt(input, backup)?;
    let rows: BackupRows = serde_json::from_slice(decoded).map_err(|_|Error::Data)?;
    if !rows.matches_scope(platform, account) { return Err(Error::Data); }
    rows.validate().map_err(|_|Error::Data)?;
    Ok(())
}

pub(crate) fn copy_catalog_ciphertext(ciphertext: &[u8], target: &Path) -> Result<bool, Error> {
    let out = Destination::prepare(target)?;
    let mut stage = tempfile::Builder::new().prefix(".lmh-cipher-copy-")
        .tempfile_in(&out.parent).map_err(|_| Error::Io)?;
    let info = stage.as_file().metadata().map_err(|_| Error::Io)?;
    if info.mode() & 0o077 != 0 { return Err(Error::Io); }
    stage.write_all(ciphertext).map_err(|_| Error::Io)?;
    stage.as_file().sync_all().map_err(|_| Error::Io)?;
    out.publish(stage.path(), &info)
}

/// Verify an operator-selected capsule without creating a profile or a
/// plaintext staging file. Do not expose scope identifiers from the payload.
pub(crate) fn preview(source:&Path,backup:&str,platform:&str,account:&str)->Result<Value,Error> {
    if !valid_password(backup) {return Err(Error::Password);}
    let mut input=read_input(source)?;
    let bytes=input.len();
    let sha256=hex::encode(Sha256::digest(&input[..]));
    validate_scope(&mut input,backup,platform,account)?;
    Ok(json!({"event":"backup_restore_preview","ciphertext_sha256":sha256,
        "bytes":bytes,"target_required_absent":true}))
}

pub(crate) fn restore(source:&Path,target:&Path,backup:&str,profile:&str,
    confirmation:Option<&str>)->Result<Value,Error> {
    passwords(backup,profile)?;
    let out=Destination::prepare(target)?;
    let mut input=read_input(source)?;
    let cipher_identity=hex::encode(Sha256::digest(&input[..]));
    if confirmation.is_some_and(|expected| expected!=cipher_identity.as_str()) {
        return Err(Error::Confirmation);
    }
    let decoded=decrypt(&mut input,backup)?;
    let rows:BackupRows=serde_json::from_slice(decoded).map_err(|_|Error::Data)?;
    let rows=rows.validate().map_err(|_|Error::Data)?;
    drop(input);
    let stage=tempfile::Builder::new().prefix(".lmh-restore-stage-").tempdir_in(&out.parent).map_err(|_|Error::Io)?;
    fs::set_permissions(stage.path(),fs::Permissions::from_mode(0o700)).map_err(|_|Error::Io)?;
    let metadata=fs::symlink_metadata(stage.path()).map_err(|_|Error::Io)?;
    EventStore::init_guarded(stage.path(),profile).map_err(|_|Error::Io)?;
    let receipt={
        let lease=GuardedExclusive::acquire(stage.path()).map_err(|_|Error::Io)?;
        let auth=lease.authorize_import(profile).map_err(|_|Error::Io)?;
        rows.write(&auth,&cipher_identity).map_err(|_|Error::Data)?
    };
    // Writer is closed/checkpointed. Only the completed ordinary SQLite file
    // may be published, never a transient SQLite journal.
    let listed=fs::read_dir(stage.path()).map_err(|_|Error::Io)?
        .collect::<Result<Vec<_>,_>>().map_err(|_|Error::Io)?;
    if listed.len()!=1 || listed[0].file_name()!=NATIVE_DATABASE {return Err(Error::Io);}
    File::open(stage.path().join(NATIVE_DATABASE)).and_then(|db|db.sync_all()).map_err(|_|Error::Io)?;
    File::open(stage.path()).and_then(|dir|dir.sync_all()).map_err(|_|Error::Io)?;
    let parent_synced=out.publish(stage.path(),&metadata)?;
    let _visible=stage.keep();
    let mut result=json!({"event":"backup_restored","rows":receipt,"parent_synced":parent_synced});
    if confirmation.is_some() {
        result["ciphertext_sha256"]=json!(cipher_identity);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_bounds_and_fixed_header_are_enforced_before_authentication() {
        let password="synthetic-encrypted-fixture";
        let mut tiny=vec![0_u8;HEADER+TAG];
        assert!(matches!(decrypt(&mut tiny,password),Err(Error::Input)));
        let mut unknown=vec![0_u8;HEADER+TAG+1];
        unknown[..8].copy_from_slice(MAGIC);
        unknown[8..12].copy_from_slice(&[2,1,1,0]);
        assert!(matches!(decrypt(&mut unknown,password),Err(Error::Input)));
        assert!(passwords(password,password).is_err());
    }
    #[test]
    fn guarded_catalog_owned_listing_preserves_inventory_and_ciphertext_copy() {
        use crate::guarded_backup_catalog::{self as catalog, Error as CatalogError};

        let password="synthetic-catalog-fixture";
        let mut temp=tempfile::Builder::new();
        temp.prefix("lmh-catalog-owned-test-");
        let dir=temp.tempdir().unwrap();
        fs::set_permissions(dir.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let put=|name: &str, bytes: &[u8]| {
            let path=dir.path().join(name);
            fs::write(&path,bytes).unwrap();
            fs::set_permissions(path,fs::Permissions::from_mode(0o600)).unwrap();
        };
        let manifest=|names:&[&str]| {
            put("catalog.json",&serde_json::to_vec(&json!({"version":1,"files":names})).unwrap());
        };
        let tables=|account: &str| -> BackupRows {
            let messages:Vec<_>=(0..512).map(|i|json!([
                format!("m-{i}"),"room",null,"Sender",i,"text","a".repeat(4096),0,null
            ])).collect();
            serde_json::from_value(json!({
                "platform":"wechat","account_id":account,
                "tables":[[["room","Room","group",null,"",null,512]],[],messages,[],[]],
            })).unwrap()
        };
        let envelope=|account| {
            let (header,body)=seal(tables(account),password).unwrap();
            let mut bytes=header.to_vec();
            bytes.extend_from_slice(&body);
            bytes
        };
        let bytes=envelope("allowed");
        let id=hex::encode(Sha256::digest(&bytes));
        put("good.lmhenc",&bytes);
        manifest(&["good.lmhenc"]);
        let inv=catalog::strict_inventory(dir.path(),"wechat","allowed",password).unwrap();
        assert_eq!(inv.backups.len(),1);
        assert_eq!(inv.backups[0].id,id);
        assert_eq!(inv.backups[0].bytes,bytes.len() as u64);
        let expected=json!({"event":"guarded_backup_catalog","format":"LMHENC01",
            "backups":[{"id":id,"bytes":bytes.len()}]});
        let list=|password| catalog::inspect(dir.path(),"wechat","allowed",password,None);
        assert_eq!(list(password).unwrap(),expected);
        assert_eq!(list("wrong-synthetic-passphrase").unwrap()["backups"],json!([]));
        assert!(catalog::strict_inventory(dir.path(),"wechat","allowed","wrong-synthetic-passphrase").is_err());

        put("other.lmhenc",&envelope("different"));
        let target=dir.path().join("output.lmhenc");
        let copy=catalog::inspect(dir.path(),"wechat","allowed",password,Some((&id,&target))).unwrap();
        assert_eq!(copy["id"],json!(id));
        assert_eq!(copy["bytes"],json!(bytes.len()));
        assert_eq!(fs::read(&target).unwrap(),bytes);
        assert!(matches!(catalog::inspect(dir.path(),"wechat","allowed",password,
            Some((&id,&target))),Err(CatalogError::Output)));
        let mut corruption=bytes.clone();
        *corruption.last_mut().unwrap()^=1;
        put("corrupt.lmhenc",&corruption);
        put("dupe.lmhenc",&bytes);
        let foreign=fs::read(dir.path().join("other.lmhenc")).unwrap();
        let foreign_id=hex::encode(Sha256::digest(&foreign));
        manifest(&["corrupt.lmhenc","other.lmhenc","missing.lmhenc","good.lmhenc","dupe.lmhenc"]);
        assert_eq!(list(password).unwrap(),expected);
        assert!(catalog::strict_inventory(dir.path(),"wechat","allowed",password).is_err());
        assert!(matches!(catalog::inspect(dir.path(),"wechat","allowed",password,
            Some((&foreign_id,&dir.path().join("forbidden.lmhenc")))),Err(CatalogError::Missing)));
        assert!(!dir.path().join("forbidden.lmhenc").exists());
    }

}
