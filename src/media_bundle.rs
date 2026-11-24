#![cfg(target_os = "linux")]

use crate::{media_backup::{MAX_REFERENCED_ROWS, qq_reference, wechat_cache_key, wechat_reference}, snapshot::Snapshot, store::Store};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs::{self, File}, io::{self, Read}, os::unix::fs::MetadataExt, path::{Path, PathBuf}, sync::Arc};

const MAX_CATALOG: u64 = 4 * 1024 * 1024;
const MAX_INDEX: u64 = 128 * 1024 * 1024;
const MAX_REFERENCE_EXTRA: usize = 256 * 1024;
const MAX_DOWNLOAD: u64 = 8 * 1024 * 1024;

fn invalid() -> io::Error { io::Error::other("media bundle unavailable") }
fn sha(bytes: &[u8]) -> String { hex::encode(Sha256::digest(bytes)) }
fn hex_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Clone, PartialEq, Eq)]
struct Stamp {
    device: u64, inode: u64, size: u64, mode: u32, ctime: i64, ctime_ns: i64,
}
impl Stamp {
    fn checked(m: fs::Metadata, directory: bool) -> io::Result<Self> {
        if (if directory { !m.is_dir() } else { !m.is_file() || m.nlink() != 1 })
            || m.mode() & 0o077 != 0
        { return Err(invalid()); }
        Ok(Self { device:m.dev(), inode:m.ino(), size:m.len(), mode:m.mode(), ctime:m.ctime(), ctime_ns:m.ctime_nsec() })
    }
    fn path(path: &Path, directory: bool) -> io::Result<Self> {
        Self::checked(fs::symlink_metadata(path)?, directory)
    }
}
fn open_file(path: &Path) -> io::Result<File> {
    use rustix::fs::{open, OFlags, Mode};
    Ok(open(path, OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC, Mode::empty())?.into())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Catalog {
    format: String,
    version: u8,
    source_archive_sha256: String,
    projected_index_sha256: String,
    items: Vec<Item>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    ref_sha256: String,
    blob_sha256: String,
    source: String,
    size: u64,
    wechat_key: Option<String>,
    wechat_leaf: Option<String>,
}

#[derive(Clone, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub(crate) struct MessageKey {
    pub platform: String,
    pub account_id: String,
    pub conversation_id: String,
    pub message_id: String,
}
impl MessageKey {
    pub(crate) fn valid(&self) -> bool {
        [&self.account_id, &self.conversation_id, &self.message_id].iter().all(|s|
            (1..=512).contains(&s.len()) && !s.chars().any(char::is_control))
            && matches!(self.platform.as_str(), "qq" | "wechat")
    }
}
#[derive(Serialize)]
pub(crate) struct Attachment {
    pub id: String,
    pub size: u64,
    #[serde(skip)]
    item: Item,
}
#[derive(Clone)]
pub struct Bundle(Arc<Inner>);
struct Inner {
    marks: Vec<(PathBuf, bool, Stamp)>,
    objects: PathBuf,
    catalog_stamp: String,
    messages: BTreeMap<MessageKey, Vec<Attachment>>,
}

#[derive(Default)]
enum Owner { #[default] Unclaimed, One(MessageKey), Ambiguous }

fn claim(owners: &mut BTreeMap<String, Owner>, key: &str, message: MessageKey) {
    match owners.get_mut(key) {
        Some(owner @ Owner::Unclaimed) => *owner = Owner::One(message),
        Some(owner @ Owner::One(_)) => {
            if !matches!(owner, Owner::One(prev) if prev == &message) { *owner = Owner::Ambiguous; }
        }
        _ => {}
    }
}
fn key(platform: &str, account: &str, conversation: &str, id: &str) -> MessageKey {
    MessageKey { platform:platform.into(), account_id:account.into(), conversation_id:conversation.into(), message_id:id.into() }
}

impl Bundle {
    pub fn open(root: &Path) -> io::Result<(Self, Snapshot, Store)> {
        Stamp::path(root, true)?;
        let root = root.canonicalize()?;
        let snapshot_dir = root.join("snapshot");
        let index = snapshot_dir.join("index.sqlite3");
        let objects = root.join("objects");
        let catalog_path = root.join("catalog.json");
        let marks = [(root,true), (snapshot_dir,true), (objects.clone(),true), (index.clone(),false), (catalog_path.clone(),false)]
            .into_iter().map(|(path,dir)| Ok((path.clone(),dir,Stamp::path(&path,dir)?))).collect::<io::Result<Vec<_>>>()?;
        let mut index_file = open_file(&index)?;
        let index_mark = &marks[3].2;
        if &Stamp::checked(index_file.metadata()?,false)? != index_mark || index_mark.size > MAX_INDEX { return Err(invalid()); }
        let mut hash = Sha256::new();
        if io::copy(&mut (&mut index_file).take(index_mark.size + 1), &mut hash)? != index_mark.size
            || &Stamp::checked(index_file.metadata()?,false)? != index_mark
        { return Err(invalid()); }
        let catalog_file = open_file(&catalog_path)?;
        let catalog_mark = &marks[4].2;
        if catalog_mark.size > MAX_CATALOG || &Stamp::checked(catalog_file.metadata()?,false)? != catalog_mark { return Err(invalid()); }
        let mut bytes = Vec::new();
        (&catalog_file).take(catalog_mark.size + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 != catalog_mark.size || &Stamp::checked(catalog_file.metadata()?,false)? != catalog_mark { return Err(invalid()); }
        let catalog_stamp = sha(&bytes);
        let catalog: Catalog = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
        // The typed catalog owns its fields; do not keep the raw catalog
        // resident while allocating the indexes used to bind its items.
        drop(bytes);
        if catalog.format != "lmh-private-media-catalog" || catalog.version != 1
            || catalog.projected_index_sha256 != hex::encode(hash.finalize())
            || !hex_digest(&catalog.source_archive_sha256) || catalog.items.len() > 4094
        { return Err(invalid()); }

        let mut qq = BTreeMap::new();
        let mut wechat = BTreeMap::new();
        let mut refs = std::collections::BTreeSet::new();
        for item in &catalog.items {
            if !hex_digest(&item.ref_sha256) || !hex_digest(&item.blob_sha256)
                || !refs.insert(&item.ref_sha256) || item.size > 64 * 1024 * 1024
            { return Err(invalid()); }
            match (item.source.as_str(), &item.wechat_key, &item.wechat_leaf) {
                ("qq", None, None) => { qq.insert(item.ref_sha256.clone(), Owner::Unclaimed); }
                ("wechat", None, None) => {} // v1.006: no usable ownership signals.
                ("wechat", Some(cache), Some(leaf)) if hex_digest(cache)
                    && wechat_reference(cache,leaf).as_deref() == Some(item.ref_sha256.as_str()) => {
                    wechat.insert(cache.clone(), Owner::Unclaimed);
                }
                _ => return Err(invalid()),
            }
        }
        let snapshot = Snapshot::open(&index).map_err(|_| invalid())?;
        let store = Store::open(snapshot.path()).map_err(|_| invalid())?;
        bind(&store, &mut qq, &mut wechat)?;
        let mut messages = BTreeMap::<MessageKey, Vec<Attachment>>::new();
        for item in catalog.items {
            let owner = if item.source == "qq" { qq.get(&item.ref_sha256) }
                else { item.wechat_key.as_ref().and_then(|id| wechat.get(id)) };
            let owner = match owner {
                Some(Owner::One(owner)) => owner,
                Some(Owner::Ambiguous) => continue,
                None if item.source == "wechat" && item.wechat_key.is_none() => continue,
                _ => return Err(invalid()),
            };
            if !owner.valid() || item.size > MAX_DOWNLOAD { continue; }
            let token = serde_json::to_vec(&("lmh-desktop-attachment-v1", &catalog_stamp,
                &owner.platform, &owner.account_id, &owner.conversation_id, &owner.message_id, &item.ref_sha256))
                .map_err(|_| invalid())?;
            let id = sha(&token);
            messages.entry(key(&owner.platform,&owner.account_id,&owner.conversation_id,&owner.message_id))
                .or_default().push(Attachment { id, size:item.size, item });
        }
        // Keep legacy per-message listing order unchanged. The new catalog
        // sorts only the single matched message in its bounded projection.
        let bundle = Self(Arc::new(Inner { marks, objects, catalog_stamp, messages }));
        bundle.verify()?;
        Ok((bundle,snapshot,store))
    }

    pub(crate) fn verify(&self) -> io::Result<()> {
        for (path,directory,stamp) in &self.0.marks {
            if Stamp::path(path,*directory)? != *stamp { return Err(invalid()); }
        }
        Ok(())
    }

    pub(crate) fn list(&self, key: &MessageKey) -> &[Attachment] {
        self.0.messages.get(key).map_or(&[], Vec::as_slice)
    }

    pub(crate) fn catalog_stamp(&self) -> &str { &self.0.catalog_stamp }

    // Start from a real ordered key rather than scanning unrelated accounts.
    pub(crate) fn conversation<'a>(&'a self, platform:&'a str, account:&'a str,
        conversation:&'a str, first_message:&str)
        -> impl Iterator<Item=(&'a MessageKey,&'a [Attachment])>+'a {
        self.0.messages.range(key(platform,account,conversation,first_message)..)
            .take_while(move |(k,_)|k.platform==platform && k.account_id==account
                && k.conversation_id==conversation)
            .map(|(k,v)|(k,v.as_slice()))
    }

    /// Materialize one member in private owned scratch. On success, return
    /// the lowercase SHA-256 just verified against the catalog. A caller
    /// must not publish the destination unless this returns Some(digest).
    pub(crate) fn read_into(&self,key:&MessageKey,id:&str,destination:&mut [u8])
        ->io::Result<Option<String>> {
        let Some(attachment)=self.list(key).iter().find(|a|a.id==id) else {return Ok(None);};
        let file=open_file(&self.0.objects.join(&attachment.item.blob_sha256))?;
        let mark=Stamp::checked(file.metadata()?,false)?;
        if mark.size!=attachment.size || mark.size!=destination.len() as u64 {
            return Err(invalid());
        }
        let mut reader=(&file).take(mark.size+1);
        let mut filled=0;
        while filled<destination.len() {
            let n=match reader.read(&mut destination[filled..]) {
                Err(err) if err.kind()==io::ErrorKind::Interrupted=>continue,
                n=>n?,
            };
            if n==0 {break;}
            filled+=n;
        }
        // Match the incumbent's size+one bounded read without ever growing
        // the ZIP capacity on a changed file. A non-short read must see EOF.
        let extra=if filled==destination.len() {
            let mut witness=[0u8;1];
            loop {
                match reader.read(&mut witness) {
                    Err(err) if err.kind()==io::ErrorKind::Interrupted=>continue,
                    n=>break n?!=0,
                }
            }
        } else {false};
        if Stamp::checked(file.metadata()?,false)?!=mark
            || filled!=destination.len() || extra {
            return Err(invalid());
        }
        let digest=sha(destination);
        if digest!=attachment.item.blob_sha256 {return Err(invalid());}
        Ok(Some(digest))
    }

    pub(crate) fn read(&self, key: &MessageKey, id: &str) -> io::Result<Option<Vec<u8>>> {
        self.read_with_verified_digest(key,id).map(|pair|pair.map(|(bytes,_)|bytes))
    }

    /// Return the digest computed from these same owned bytes and verified
    /// against the catalog after the file and length checks.
    pub(crate) fn read_with_verified_digest(&self,key:&MessageKey,id:&str)
        ->io::Result<Option<(Vec<u8>,String)>> {
        let Some(attachment) = self.list(key).iter().find(|a| a.id == id) else { return Ok(None); };
        let file = open_file(&self.0.objects.join(&attachment.item.blob_sha256))?;
        let mark = Stamp::checked(file.metadata()?,false)?;
        if mark.size != attachment.size { return Err(invalid()); }
        let mut bytes = Vec::with_capacity(mark.size as usize);
        (&file).take(mark.size + 1).read_to_end(&mut bytes)?;
        if Stamp::checked(file.metadata()?,false)? != mark
            || bytes.len() as u64 != mark.size
        { return Err(invalid()); }
        let digest=sha(&bytes);
        if digest!=attachment.item.blob_sha256 {return Err(invalid());}
        Ok(Some((bytes,digest)))
    }
}

/// One bounded byte range on an already authenticated and fully verified blob.
/// Multipart responses are intentionally unsupported.
pub(crate) fn byte_range(header: &str, size: usize) -> Option<std::ops::Range<usize>> {
    if size == 0 || header.len() > 128 {return None;}
    let (unit, span)=header.trim().split_once('=')?;
    if !unit.eq_ignore_ascii_case("bytes") {return None;}
    let (first,last)=span.split_once('-')?;
    let number=|s:&str| {
        if s.is_empty() || !s.bytes().all(|b|b.is_ascii_digit()) {return None;}
        s.parse::<u64>().ok()
    };
    let size=size as u64;
    let (from,until)=if first.is_empty() {
        let suffix=number(last)?;
        if suffix==0 {return None;}
        (size.saturating_sub(suffix),size)
    } else {
        let from=number(first)?;
        if from>=size {return None;}
        let until=if last.is_empty() {size} else {
            let end=number(last)?;
            if end<from {return None;}
            end.saturating_add(1).min(size)
        };
        (from,until)
    };
    Some(from as usize..until as usize)
}

fn bind(store: &Store, qq: &mut BTreeMap<String, Owner>,
        wechat: &mut BTreeMap<String, Owner>) -> io::Result<()> {
    let db = store.connection().map_err(|_| invalid())?;
    db.set_limit(rusqlite::limits::Limit::SQLITE_LIMIT_LENGTH, 512 * 1024);
    let mut q = db.prepare("SELECT platform, account_id, conversation_id, id, raw_type, extra FROM messages
        WHERE (platform='qq' AND extra IS NOT NULL) OR (platform='wechat' AND type IN ('image','voice','video','file'))
        LIMIT ?").map_err(|_| invalid())?;
    let mut rows = q.query([MAX_REFERENCED_ROWS + 1]).map_err(|_| invalid())?;
    let mut count = 0;
    while let Some(row) = rows.next().map_err(|_| invalid())? {
        count += 1;
        if count > MAX_REFERENCED_ROWS { return Err(invalid()); }
        use rusqlite::types::ValueRef;
        for (column, max) in [(0, 8), (1, 512), (2, 512), (3, 512), (4, 64), (5, MAX_REFERENCE_EXTRA)] {
            match row.get_ref(column).map_err(|_| invalid())? {
                ValueRef::Text(value) if value.len() <= max => {}
                ValueRef::Null if column >= 4 => {}
                // Do not skip an unreadable reference: it could hide a competing owner.
                _ => return Err(invalid()),
            }
        }
        let platform: String = row.get(0).map_err(|_| invalid())?;
        let account: String = row.get(1).map_err(|_| invalid())?;
        let conversation: String = row.get(2).map_err(|_| invalid())?;
        let id: String = row.get(3).map_err(|_| invalid())?;
        let raw: Option<String> = row.get(4).map_err(|_| invalid())?;
        let extra = row.get_ref(5).map_err(|_| invalid())?
            .as_str_or_null().map_err(|_| invalid())?;
        let Some(encoded) = extra else { continue };
        let extra: Value = serde_json::from_str(encoded).map_err(|_| invalid())?;
        if platform == "qq" {
            if let Some(resources) = extra["roaming_resources"].as_array().or_else(|| extra["media"].as_array()) {
                for entry in resources {
                    if let Some(reference) = entry["path"].as_str().and_then(qq_reference) {
                        claim(qq,&reference,key(&platform,&account,&conversation,&id));
                    }
                }
            }
        } else if let Some(cache) = wechat_cache_key(&account,&conversation,&id,raw.as_deref(),&extra) {
            claim(wechat,&cache,key(&platform,&account,&conversation,&id));
        }
    }
    Ok(())
}

#[cfg(test)]
mod direct_read_tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn private_direct_read_matches_legacy_and_rejects_nonmatching_members() {
        let root=tempfile::tempdir().unwrap();
        let key=key("qq","owner","room","message");
        let payload=b"binary\0fixture";
        let digest=sha(payload);
        let id="synthetic-id";
        let item=Item{ref_sha256:"unused".into(),blob_sha256:digest.clone(),
            source:"qq".into(),size:payload.len() as u64,wechat_key:None,wechat_leaf:None};
        let bundle=Bundle(Arc::new(Inner{
            marks:Vec::new(),objects:root.path().to_path_buf(),catalog_stamp:String::new(),
            messages:BTreeMap::from([(key.clone(),vec![Attachment{id:id.into(),
                size:payload.len() as u64,item}])]),
        }));
        let path=root.path().join(digest);
        let put=|body:&[u8]|{
            fs::write(&path,body).unwrap();
            fs::set_permissions(&path,fs::Permissions::from_mode(0o600)).unwrap();
        };
        put(payload);
        let mut output=[0;14];
        assert_eq!(bundle.read_into(&key,id,&mut output).unwrap().as_deref(),
            Some("0b5d5476c110fdccc90b9dfe392f432f523b9cee7aa440c899886bf5e79fb775"));
        assert_eq!(bundle.read(&key,id).unwrap().unwrap().as_slice(),&output[..]);
        assert_eq!(bundle.read_into(&key,"missing",&mut output).unwrap(),None);
        assert!(bundle.read_into(&key,id,&mut output[..1]).is_err());
        put(b"short");
        assert!(bundle.read_into(&key,id,&mut output).is_err());
        assert!(bundle.read(&key,id).is_err());
        put(b"binary\0forgery");
        assert!(bundle.read_into(&key,id,&mut output).is_err());
        assert!(bundle.read(&key,id).is_err());
    }
}

#[cfg(test)]
mod borrowed_inventory_tests {
    use super::*;
    use rusqlite::{params, Connection};

    fn store_with_rows(setup:impl FnOnce(&mut Connection))->(tempfile::TempDir,Store) {
        let temp=tempfile::tempdir().unwrap();
        let path=temp.path().join("index.sqlite3");
        let mut db=Connection::open(&path).unwrap();
        db.execute_batch(include_str!("../tests/fixtures/index-v9.sql")).unwrap();
        setup(&mut db);
        drop(db);
        let store=Store::open(path).unwrap();
        (temp,store)
    }

    #[test]
    fn borrowed_inventory_claims_ordinary_rows_and_preserves_null() {
        const ROWS:usize=768;
        let ref_for=|i|format!("qq-roaming/0123456789abcdefabcd/photo-{i:04}.bin");
        let mut qq:BTreeMap<_,_>=(0..ROWS).map(|i|(qq_reference(&ref_for(i)).unwrap(),Owner::Unclaimed)).collect();
        let mut bytes=0;
        let (_temp,store)=store_with_rows(|db|{
            let tx=db.transaction().unwrap();
            for i in 0..ROWS {
                let extra=serde_json::json!({
                    "roaming_resources":[{"path":ref_for(i),"filename":format!("photo-{i:04}.bin"),
                        "size":140352,"mime":"image/png"}],
                    "profile":{"title":"fixture attachment","annotation":"a".repeat(420)}
                }).to_string();
                assert!((512..=1024).contains(&extra.len()));
                bytes+=extra.len();
                tx.execute("INSERT INTO messages(platform,account_id,conversation_id,id,type,extra)
                    VALUES('qq','fixture','scope',?1,'image',?2)",params![format!("m-{i}"),extra]).unwrap();
            }
            tx.execute("INSERT INTO messages(platform,account_id,conversation_id,id,type,extra)
                VALUES('wechat','fixture','scope','no-reference','image',NULL)",[]).unwrap();
            tx.commit().unwrap();
        });
        assert!(bytes>=393216);
        bind(&store,&mut qq,&mut BTreeMap::new()).unwrap();
        for i in 0..ROWS {
            let reference=qq_reference(&ref_for(i)).unwrap();
            match qq.get(&reference) {
                Some(Owner::One(row))=>assert_eq!((&*row.account_id,&*row.conversation_id,&*row.message_id),
                    ("fixture","scope",format!("m-{i}").as_str())),
                _=>panic!("ordinary exact reference was not resolved"),
            }
        }
    }

    #[test]
    fn borrowed_inventory_still_fails_closed_for_invalid_types_and_text() {
        for value in ["X'ff'","CAST(X'ff' AS TEXT)","'{broken'"] {
            let (_temp,store)=store_with_rows(|db|{
                db.execute_batch(&format!(
                    "INSERT INTO messages(platform,account_id,conversation_id,id,type,extra)
                     VALUES('qq','fixture','scope','bad-reference','image',{value})")).unwrap();
            });
            assert!(bind(&store,&mut BTreeMap::new(),&mut BTreeMap::new()).is_err());
        }
    }
}
