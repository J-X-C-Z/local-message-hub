//! Offline operator-only catalog. The manifest is not an account authority.
use crate::{guarded_backup, guarded_snapshot, profile_lock::valid_password};
use rustix::{fs::{AtFlags,FlockOperation,Mode,OFlags,RenameFlags,flock,open,openat,renameat,
    renameat_with,statat,unlinkat},process::geteuid};
use serde::{Deserialize,Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fs::File, io::{Read,Write}, os::unix::fs::MetadataExt, path::Path};
use zeroize::Zeroizing;

pub(crate) const MAX_ENTRIES: usize = 16;
const MAX_MANIFEST: usize = 4096;
const MANIFEST: &str = "catalog.json";
const QUARANTINE:&str=".lmh-quarantine-v1.json";
const MAX_STATE:usize=2048;

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid backup catalog or scope")]
    Catalog,
    #[error("verified backup not found for this scope")]
    Missing,
    #[error("verified ciphertext could not be copied")]
    Output,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest { version: u8, files: Vec<String> }

fn leaf(value: &str) -> bool {
    (1..=80).contains(&value.len())
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
        && value.ends_with(".lmhenc")
}

pub(crate) fn id_valid(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn read(dir: &File, name: &str, limit: usize) -> Result<Zeroizing<Vec<u8>>, Error> {
    let file: File = openat(dir, name, OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK
        | OFlags::CLOEXEC, Mode::empty()).map_err(|_|Error::Catalog)?.into();
    let before = file.metadata().map_err(|_|Error::Catalog)?;
    if !before.is_file() || before.nlink() != 1 || before.uid() != geteuid().as_raw()
        || before.mode() & 0o077 != 0 || before.len() > limit as u64 {
        return Err(Error::Catalog);
    }
    let mut bytes = Zeroizing::new(Vec::new());
    (&file).take(limit as u64 + 1).read_to_end(&mut bytes).map_err(|_| Error::Catalog)?;
    let after = file.metadata().map_err(|_|Error::Catalog)?;
    if bytes.len() as u64 != before.len() || after.len() != before.len()
        || after.ctime() != before.ctime() || after.ctime_nsec() != before.ctime_nsec()
        || after.mtime() != before.mtime() || after.mtime_nsec() != before.mtime_nsec()
        || after.uid() != before.uid() || after.mode() != before.mode() || after.nlink() != 1 {
        return Err(Error::Catalog);
    }
    Ok(bytes)
}

#[derive(Clone,PartialEq,Eq,Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Quarantined {
    pub ordinal:usize,
    pub id:String,
    pub bytes:u64,
}
#[derive(PartialEq,Eq,Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct State {
    pub version:u8,
    pub generation:String,
    pub manifest_sha256:String,
    #[serde(deserialize_with="explicit_member")]
    pub member:Option<Quarantined>,
}

fn explicit_member<'de,D:serde::Deserializer<'de>>(value:D)->Result<Option<Quarantined>,D::Error> {
    Option::<Quarantined>::deserialize(value)
}

pub(crate) struct Catalog {
    directory:File,
    manifest:Manifest,
    manifest_bytes:Zeroizing<Vec<u8>>,
    pub state:Option<State>,
    pub manifest_sha256:String,
    private_root:(u64,u64),
    exclusive:bool,
}

impl Catalog {
    pub(crate) fn open(directory:&Path,exclusive:bool)->Result<Self,Error> {
        let dir:File=open(directory,OFlags::RDONLY|OFlags::DIRECTORY|OFlags::NOFOLLOW|OFlags::CLOEXEC,
            Mode::empty()).map_err(|_|Error::Catalog)?.into();
        let lock=if exclusive {FlockOperation::NonBlockingLockExclusive}
            else {FlockOperation::NonBlockingLockShared};
        flock(&dir,lock).map_err(|_|Error::Catalog)?;
        let meta=dir.metadata().map_err(|_|Error::Catalog)?;
        if !meta.is_dir()||meta.mode()&0o077!=0||meta.uid()!=geteuid().as_raw(){return Err(Error::Catalog);}
        let data=read(&dir,MANIFEST,MAX_MANIFEST)?;
        let manifest:Manifest=serde_json::from_slice(&data).map_err(|_|Error::Catalog)?;
        let manifest_sha256=hex::encode(Sha256::digest(&data[..]));
        let mut names=BTreeSet::new();
        if manifest.version!=1||manifest.files.len()>MAX_ENTRIES
            ||!manifest.files.iter().all(|name|leaf(name)&&names.insert(name.as_str())) {
            return Err(Error::Catalog);
        }
        let state=Self::read_state(&dir)?;
        if state.as_ref().is_some_and(|s|s.member.as_ref().is_some_and(|m|
            s.manifest_sha256!=manifest_sha256 || m.ordinal>=manifest.files.len())) {
            return Err(Error::Catalog);
        }
        Ok(Self{directory:dir,manifest,manifest_bytes:data,state,manifest_sha256,
            private_root:(meta.dev(),meta.ino()),exclusive})
    }
    fn read_state(dir:&File)->Result<Option<State>,Error> {
        match statat(dir,QUARANTINE,AtFlags::SYMLINK_NOFOLLOW) {
            Err(rustix::io::Errno::NOENT)=>return Ok(None),
            Err(_)=>return Err(Error::Catalog),
            Ok(_)=>{},
        }
        let bytes=read(dir,QUARANTINE,MAX_STATE)?;
        let state:State=serde_json::from_slice(&bytes).map_err(|_|Error::Catalog)?;
        if state.version!=1 || !id_valid(&state.generation) || !id_valid(&state.manifest_sha256)
            || state.member.as_ref().is_some_and(|m|m.ordinal>=MAX_ENTRIES || !id_valid(&m.id)
                || m.bytes==0 || m.bytes>guarded_backup::MAX_FILE as u64) {
            return Err(Error::Catalog);
        }
        Ok(Some(state))
    }
    pub(crate) fn root_identity(&self)->(u64,u64) {self.private_root}

    pub(crate) fn verify_unchanged(&self)->Result<(),Error> {
        let meta=self.directory.metadata().map_err(|_|Error::Catalog)?;
        let bytes=read(&self.directory,MANIFEST,MAX_MANIFEST)?;
        if !meta.is_dir()||meta.mode()&0o077!=0||meta.uid()!=geteuid().as_raw()
            || (meta.dev(),meta.ino())!=self.private_root || bytes[..]!=self.manifest_bytes[..]
            || Self::read_state(&self.directory)?!=self.state {return Err(Error::Catalog);}
        Ok(())
    }

    /// One atomic, replaceable marker; never modifies manifest or ciphertext.
    /// Degraded parent fsync is reported as post-publication durability status.
    pub(crate) fn write_state(&self,new:&State)->Result<bool,Error> {
        if !self.exclusive{return Err(Error::Catalog);}
        self.verify_unchanged()?;
        let data=serde_json::to_vec(new).map_err(|_|Error::Output)?;
        if data.len()>MAX_STATE{return Err(Error::Output);}
        let stage=format!(".lmh-quarantine-stage-{}",new.generation);
        let fd=openat(&self.directory,stage.as_str(),OFlags::WRONLY|OFlags::CREATE|OFlags::EXCL
            |OFlags::NOFOLLOW|OFlags::CLOEXEC,Mode::RUSR|Mode::WUSR).map_err(|_|Error::Output)?;
        let mut file:File=fd.into();
        let result=(|| {
            file.write_all(&data).and_then(|_|file.sync_all()).map_err(|_|Error::Output)?;
            self.verify_unchanged()?;
            if self.state.is_none() {
                renameat_with(&self.directory,stage.as_str(),&self.directory,QUARANTINE,
                    RenameFlags::NOREPLACE).map_err(|_|Error::Output)?;
            } else {
                renameat(&self.directory,stage.as_str(),&self.directory,QUARANTINE)
                    .map_err(|_|Error::Output)?;
            }
            Ok(self.directory.sync_all().is_ok())
        })();
        if result.is_err(){
            // This name is exclusively allocated for this operation. Never
            // remove an archive, marker, manifest or caller-supplied path.
            let _=unlinkat(&self.directory,stage.as_str(),AtFlags::empty());
        }
        result
    }

    fn hidden(&self,ordinal:usize,id:&str)->bool {
        self.state.as_ref().and_then(|s|s.member.as_ref())
            .is_some_and(|m|m.ordinal==ordinal || m.id==id)
    }
}

pub(crate) fn inspect(directory: &Path, platform: &str, account: &str, passphrase: &str,
    copy: Option<(&str, &Path)>) -> Result<Value, Error> {
    if !guarded_snapshot::valid_scope(platform, account) || !valid_password(passphrase)
        || copy.is_some_and(|(id,_)| !id_valid(id)) {
        return Err(Error::Catalog);
    }
    let catalog=Catalog::open(directory,false)?;
    let mut entries = Vec::new();
    let mut ids = BTreeSet::new();
    for (ordinal,name) in catalog.manifest.files.iter().enumerate() {
        let Ok(ciphertext) = read(&catalog.directory, name, guarded_backup::MAX_FILE) else { continue };
        let id = hex::encode(Sha256::digest(&ciphertext[..]));
        if catalog.hidden(ordinal,&id) || copy.is_some_and(|(wanted,_)| wanted != id.as_str()) || !ids.insert(id.clone()) {
            continue;
        }
        if let Some((_,target)) = copy {
            if guarded_backup::verify_catalog_scope(&ciphertext, passphrase, platform, account).is_err() {
                continue;
            }
            catalog.verify_unchanged()?;
            let parent_synced = guarded_backup::copy_catalog_ciphertext(&ciphertext, target)
                .map_err(|_|Error::Output)?;
            return Ok(json!({"event":"backup_ciphertext_copied","id":id,"bytes":ciphertext.len(),
                "parent_synced":parent_synced}));
        }
        let bytes = ciphertext.len();
        if guarded_backup::verify_owned_catalog_scope(ciphertext, passphrase, platform, account).is_err() {
            continue;
        }
        entries.push((id,bytes));
    }
    catalog.verify_unchanged()?;
    if copy.is_some() { return Err(Error::Missing); }
    entries.sort_unstable();
    let backups: Vec<_> = entries.into_iter().map(|(id,bytes)|json!({"id":id,"bytes":bytes})).collect();
    Ok(json!({"event":"guarded_backup_catalog","format":"LMHENC01","backups":backups}))
}


#[derive(Debug, thiserror::Error)]
#[error("cannot plan from a fully verified single-account backup catalog")]
pub(crate) struct StrictInventoryError;

#[derive(serde::Serialize)]
pub(crate) struct VerifiedBackup {
    pub id:String,
    pub bytes:u64,
}

pub(crate) struct VerifiedInventory {
    pub manifest_sha256:String,
    pub backups:Vec<VerifiedBackup>,
}

/// Unlike listing, retention cannot silently skip a single corrupt, foreign,
/// missing or duplicate ciphertext: that could obscure a backup the operator
/// intended to protect. The resulting inventory preserves manifest order.
pub(crate) fn strict_inventory(directory:&Path,platform:&str,account:&str,passphrase:&str)
    -> Result<VerifiedInventory,StrictInventoryError> {
    let catalog=Catalog::open(directory,false).map_err(|_|StrictInventoryError)?;
    let mut inventory=catalog.strict_all(platform,account,passphrase)?;
    let mut ordinal=0;
    inventory.backups.retain(|backup|{
        let visible=!catalog.hidden(ordinal,&backup.id);
        ordinal+=1;
        visible
    });
    Ok(inventory)
}
impl Catalog {
    pub(crate) fn strict_all(&self,platform:&str,account:&str,passphrase:&str)
        ->Result<VerifiedInventory,StrictInventoryError> {
        if !guarded_snapshot::valid_scope(platform,account) || !valid_password(passphrase)
            || self.manifest.files.is_empty() {return Err(StrictInventoryError);}
        let mut ids=BTreeSet::new();
        let mut backups=Vec::with_capacity(self.manifest.files.len());
        for (ordinal,name) in self.manifest.files.iter().enumerate() {
            let ciphertext=read(&self.directory,name,guarded_backup::MAX_FILE)
                .map_err(|_|StrictInventoryError)?;
            let id=hex::encode(Sha256::digest(&ciphertext[..]));
            if !ids.insert(id.clone()){return Err(StrictInventoryError);}
            let bytes=ciphertext.len() as u64;
            guarded_backup::verify_owned_catalog_scope(ciphertext,passphrase,platform,account)
                .map_err(|_|StrictInventoryError)?;
            if let Some(hidden)=self.state.as_ref().and_then(|s|s.member.as_ref()) {
                if (hidden.ordinal==ordinal || hidden.id==id)
                    && !(hidden.ordinal==ordinal && hidden.id==id && hidden.bytes==bytes) {
                    return Err(StrictInventoryError);
                }
            }
            backups.push(VerifiedBackup{id,bytes});
        }
        self.verify_unchanged().map_err(|_|StrictInventoryError)?;
        Ok(VerifiedInventory{manifest_sha256:self.manifest_sha256.clone(),backups})
    }
}
