//! Bounded read-only preview for the pinned legacy media backup format.
use crate::{migration::project_snapshot, store::Store};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{self, Read, Seek, SeekFrom, Write},
    path::Path,
};
use flate2::{Decompress, FlushDecompress, Status};

const MAX_ARCHIVE: u64 = 256 * 1024 * 1024;
const MAX_TOTAL: u64 = 512 * 1024 * 1024;
const MAX_INDEX: u64 = 128 * 1024 * 1024;
const MAX_MEDIA: u64 = 64 * 1024 * 1024;
const MAX_MANIFEST: u64 = 2 * 1024 * 1024;
const MAX_ENTRIES: usize = 4096;
pub(crate) const MAX_REFERENCED_ROWS: usize = 250_000;
const INDEX: &str = "index.sqlite3";
const MANIFEST: &str = "manifest.json";

#[derive(thiserror::Error, Debug)]
pub enum MediaBackupError {
    #[error("unsupported or invalid legacy media backup")]
    Invalid,
    #[error("legacy media backup exceeds preview limits")]
    Limit,
    #[error("a supplied media entry is not referenced by the index")]
    Unreferenced,
    #[error("legacy media backup changed during validation")]
    Changed,
    #[error("a new archive requires a nonexisting directory under a private parent")]
    Target,
    #[error("atomic no-replace media publication is only available on Linux")]
    UnsupportedTarget,
    #[error("could not read the backup or its isolated preview workspace")]
    Io(#[from] io::Error),
}

type Result<T> = std::result::Result<T, MediaBackupError>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: String,
    version: u16,
    created_at: i64,
    media_count: usize,
    missing_media_count: usize,
    media_bytes: u64,
    media: Vec<Member>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Member {
    path: String,
    size: u64,
    sha256: String,
}

#[derive(Serialize)]
pub struct Preview {
    pub archive_sha256: String,
    pub index_sha256: String,
    pub verified_media_count: usize,
    pub verified_media_bytes: u64,
    pub referenced_qq_count: usize,
    pub referenced_wechat_count: usize,
    /// A field recorded by the original generator, not independently proven.
    pub reported_missing_media_count: usize,
}

#[derive(Serialize)]
pub struct Imported {
    #[serde(flatten)]
    pub verified: Preview,
    pub projected_index_sha256: String,
    pub catalog_sha256: String,
    pub unique_object_count: usize,
    pub parent_synced: bool,
}

#[derive(Serialize)]
struct CatalogEntry {
    ref_sha256: String,
    blob_sha256: String,
    size: u64,
    source: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    wechat_leaf: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    wechat_key: Option<String>,
}

struct ImportStage<'a> {
    root: &'a Path,
    objects: std::path::PathBuf,
    catalog: Vec<CatalogEntry>,
    seen: BTreeMap<String, u64>,
}

impl<'a> ImportStage<'a> {
    fn new(root: &'a Path) -> Result<Self> {
        let objects = root.join("objects");
        make_private_dir(&objects)?;
        make_private_dir(&root.join("snapshot"))?;
        Ok(Self { root, objects, catalog: Vec::new(), seen: BTreeMap::new() })
    }

    fn record(&mut self, f: &mut File, member: &ZipMember, item: &Member, source: &'static str) -> Result<String> {
        let sha = if let Some(length) = self.seen.get(&item.sha256) {
            if *length != member.size { return Err(MediaBackupError::Invalid); }
            read_member(f,member,&mut io::sink())?
        } else {
            let blob = self.objects.join(&item.sha256);
            let mut out = open_private_new(&blob)?;
            let actual = read_member(f,member,&mut out)?;
            if actual != item.sha256 { return Err(MediaBackupError::Invalid); }
            out.sync_all()?;
            self.seen.insert(item.sha256.clone(),member.size);
            actual
        };
        self.catalog.push(CatalogEntry {
            ref_sha256: reference_hash(&item.path),blob_sha256:item.sha256.clone(),
            size:member.size,source,
            wechat_key: match kind(&item.path) { Some(Media::Wechat(key)) => Some(key.to_owned()), _ => None },
            wechat_leaf: (source == "wechat").then(|| item.path.rsplit('/').next().unwrap_or_default().to_owned()),
        });
        Ok(sha)
    }

    #[cfg(target_os = "linux")]
    fn finish(&mut self, preview: &Preview, projected_hash: &str) -> Result<String> {
        self.catalog.sort_unstable_by(|left,right| left.ref_sha256.cmp(&right.ref_sha256));
        if self.catalog.windows(2).any(|pair| pair[0].ref_sha256 == pair[1].ref_sha256) {
            return Err(MediaBackupError::Invalid);
        }
        let catalog = serde_json::to_vec_pretty(&serde_json::json!({
            "format":"lmh-private-media-catalog","version":1,
            "source_archive_sha256":preview.archive_sha256,
            "projected_index_sha256":projected_hash,
            "items":self.catalog,
        })).map_err(|_| MediaBackupError::Invalid)?;
        let mut out = open_private_new(&self.root.join("catalog.json"))?;
        out.write_all(&catalog)?;
        out.sync_all()?;
        for dir in [&self.objects,&self.root.join("snapshot"),self.root] {
            File::open(dir)?.sync_all()?;
        }
        Ok(hex::encode(Sha256::digest(&catalog)))
    }
}

fn make_private_dir(path: &Path) -> io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)
}

fn open_private_new(path: &Path) -> io::Result<File> {
    let mut options = fs::OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}

#[derive(Clone, Copy)]
enum Media<'a> {
    Qq(&'a str),
    Wechat(&'a str),
}

fn clean_part(value: &str) -> bool {
    !value.is_empty() && !matches!(value, "." | "..")
        && !value.chars().any(|c| c.is_control() || matches!(c, ':' | '\\'))
}

fn hex_is(value: &str, length: usize, lowercase: bool) -> bool {
    value.len() == length
        && value.bytes().all(|b| b.is_ascii_hexdigit() && (!lowercase || !b.is_ascii_uppercase()))
}

fn kind(name: &str) -> Option<Media<'_>> {
    if name.len() > 512 { return None; }
    let relative = name.strip_prefix("media/")?;
    let seg: Vec<_> = relative.split('/').collect();
    if !seg.iter().all(|part| clean_part(part)) { return None; }
    match seg.as_slice() {
        ["qq-roaming", id, _, ..] if hex_is(id,20,false) => Some(Media::Qq(relative)),
        ["wechat-media", key, _] if hex_is(key,64,true) => Some(Media::Wechat(key)),
        _ => None,
    }
}

pub(crate) fn reference_hash(name: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"lmh-legacy-cache-v1\0");
    hash.update(name.as_bytes());
    hex::encode(hash.finalize())
}

pub(crate) fn qq_reference(relative: &str) -> Option<String> {
    let name = format!("media/{relative}");
    matches!(kind(&name), Some(Media::Qq(_))).then(|| reference_hash(&name))
}

pub(crate) fn wechat_reference(key: &str, leaf: &str) -> Option<String> {
    let name = format!("media/wechat-media/{key}/{leaf}");
    matches!(kind(&name), Some(Media::Wechat(_))).then(|| reference_hash(&name))
}

pub(crate) fn wechat_cache_key(account: &str, conversation: &str, id: &str, raw: Option<&str>, extra: &Value) -> Option<String> {
    let code = extra["local_type"].as_i64().or_else(|| extra["local_type"].as_str().and_then(|s| s.parse().ok()))
        .filter(|v| *v != 0).or_else(|| raw.and_then(|s| s.parse().ok()));
    if !matches!(code,Some(3|34|43|49|34359738417|103079215153|25769803825)) { return None; }
    let session = extra["session_id"].as_str()?;
    let shard = extra["shard"].as_str()?;
    let local = extra["local_id"].as_i64().filter(|i| *i >= 0)?;
    if session != conversation || !clean_part(shard) || shard.contains('/')
        || account.contains('\0') || session.contains('\0')
        || id != format!("{session}:{shard}:{local}")
    { return None; }
    Some(hex::encode(Sha256::digest(format!("{account}\0{session}\0{shard}\0{local}").as_bytes())))
}

fn read_digest(
    source: &mut impl Read,
    output: &mut impl Write,
    exact: u64,
) -> Result<String> {
    let mut total = 0;
    let mut buffer = [0_u8; 64 * 1024];
    let mut digest = Sha256::new();
    loop {
        let bytes = source.read(&mut buffer)?;
        if bytes == 0 { break; }
        total += bytes as u64;
        if total > exact { return Err(MediaBackupError::Invalid); }
        digest.update(&buffer[..bytes]);
        output.write_all(&buffer[..bytes])?;
    }
    if total != exact { return Err(MediaBackupError::Invalid); }
    Ok(hex::encode(digest.finalize()))
}

fn bounded_directory(file: &mut File, size: u64) -> Result<(usize, u64, usize)> {
    // Original small, seekable Python backups have a final non-ZIP64 EOCD
    // with no archive comment. Never search for alternate directory metadata.
    if size < 22 { return Err(MediaBackupError::Invalid); }
    file.seek(SeekFrom::End(-22))?;
    let mut eocd = [0_u8; 22];
    file.read_exact(&mut eocd)?;
    let u16_at = |i| u16::from_le_bytes([eocd[i],eocd[i+1]]);
    let u32_at = |i| u64::from(u32::from_le_bytes([eocd[i],eocd[i+1],eocd[i+2],eocd[i+3]]));
    let count = usize::from(u16_at(10));
    let cd_size = u32_at(12);
    let cd_offset = u32_at(16);
    if &eocd[..4] != b"PK\x05\x06"
        || u16_at(4) != 0 || u16_at(6) != 0 || u16_at(8) != u16_at(10)
        || u16_at(20) != 0 || count < 2 || cd_offset == 0
        || cd_size + cd_offset != size - 22
    { return Err(MediaBackupError::Invalid); }
    if count > MAX_ENTRIES || cd_size > 2 * 1024 * 1024 {
        return Err(MediaBackupError::Limit);
    }
    file.rewind()?;
    Ok((count, cd_offset, cd_size as usize))
}

#[derive(Clone, Copy)]
struct ZipMember {
    flags: u16,
    method: u16,
    crc: u32,
    size: u64,
    compressed: u64,
    header_start: u64,
    data_start: u64,
}

fn word(bytes: &[u8], index: usize) -> u16 {
    u16::from_le_bytes(bytes[index..index+2].try_into().expect("fixed header"))
}

fn dword(bytes: &[u8], index: usize) -> u32 {
    u32::from_le_bytes(bytes[index..index+4].try_into().expect("fixed header"))
}

fn check_local_header(file: &mut File, name: &str, member: &mut ZipMember, cd_offset: u64) -> Result<()> {
    let data_start = member.header_start + 30 + name.len() as u64;
    if data_start + member.compressed > cd_offset { return Err(MediaBackupError::Invalid); }
    file.seek(SeekFrom::Start(member.header_start))?;
    let mut header = [0_u8; 30];
    file.read_exact(&mut header)?;
    if &header[..4] != b"PK\x03\x04" || word(&header,6) != member.flags || word(&header,8) != member.method
        || dword(&header,14) != member.crc || u64::from(dword(&header,18)) != member.compressed
        || u64::from(dword(&header,22)) != member.size || usize::from(word(&header,26)) != name.len()
        || word(&header,28) != 0
    { return Err(MediaBackupError::Invalid); }
    let mut raw_name = vec![0; name.len()];
    file.read_exact(&mut raw_name)?;
    if raw_name != name.as_bytes() { return Err(MediaBackupError::Invalid); }
    member.data_start = data_start;
    Ok(())
}

fn add_declared(total: u64, size: u64) -> Result<u64> {
    total.checked_add(size).filter(|value| *value <= MAX_TOTAL).ok_or(MediaBackupError::Limit)
}

fn parse_members(file: &mut File, file_len: u64) -> Result<BTreeMap<String, ZipMember>> {
    let (count, cd_offset, cd_size) = bounded_directory(file, file_len)?;
    let mut directory = vec![0; cd_size];
    file.seek(SeekFrom::Start(cd_offset))?;
    file.read_exact(&mut directory)?;
    let mut remaining = directory.as_slice();
    let mut entries = BTreeMap::new();
    let mut ranges = Vec::with_capacity(count);
    let mut total = 0_u64;
    for _ in 0..count {
        let header = remaining.get(..46).ok_or(MediaBackupError::Invalid)?;
        let flags = word(header, 8);
        let method = word(header,10);
        let kind_bits = dword(header,38) >> 16 & 0o170000;
        let name_length = usize::from(word(header,28));
        if &header[..4] != b"PK\x01\x02" || flags & !0x0806 != 0 || !matches!(method,0|8)
            || !matches!(kind_bits,0|0o100000) || !(1..=512).contains(&name_length)
            || word(header,30) != 0 || word(header,32) != 0 || word(header,34) != 0
        { return Err(MediaBackupError::Invalid); }
        let cut = 46 + name_length;
        let raw_name = remaining.get(46..cut).ok_or(MediaBackupError::Invalid)?;
        let name = std::str::from_utf8(raw_name).map_err(|_| MediaBackupError::Invalid)?;
        let limit = match name {
            INDEX => MAX_INDEX,
            MANIFEST => MAX_MANIFEST,
            path if kind(path).is_some() => MAX_MEDIA,
            _ => return Err(MediaBackupError::Invalid),
        };
        let size = u64::from(dword(header,24));
        let compressed = u64::from(dword(header,20));
        if size > limit || size > compressed.saturating_mul(1000) {
            return Err(MediaBackupError::Limit);
        }
        if method == 0 && size != compressed { return Err(MediaBackupError::Invalid); }
        total = add_declared(total, size)?;
        let mut member = ZipMember { flags, method, crc:dword(header,16), size, compressed,
            header_start:u64::from(dword(header,42)), data_start:0 };
        check_local_header(file,name,&mut member,cd_offset)?;
        ranges.push((member.header_start,member.data_start+member.compressed));
        if entries.insert(name.to_owned(),member).is_some() { return Err(MediaBackupError::Invalid); }
        remaining = &remaining[cut..];
    }
    ranges.sort_unstable();
    // Refuse preambles, hidden gaps, overlaps, and spare records.
    if !remaining.is_empty() || ranges.first().map(|range| range.0) != Some(0)
        || ranges.last().map(|range| range.1) != Some(cd_offset)
        || ranges.windows(2).any(|pair| pair[0].1 != pair[1].0)
    { return Err(MediaBackupError::Invalid); }
    Ok(entries)
}

fn read_member(file: &mut File, member: &ZipMember, output: &mut impl Write) -> Result<String> {
    file.seek(SeekFrom::Start(member.data_start))?;
    let mut source = file.take(member.compressed);
    let mut plain = [0_u8; 64 * 1024];
    let mut digest = Sha256::new();
    let mut crc = crc32fast::Hasher::new();
    let mut length = 0_u64;
    let mut write = |data: &[u8]| -> Result<()> {
        length += data.len() as u64;
        if length > member.size { return Err(MediaBackupError::Invalid); }
        digest.update(data);
        crc.update(data);
        output.write_all(data)?;
        Ok(())
    };
    if member.method == 0 {
        loop {
            let n = source.read(&mut plain)?;
            if n == 0 { break; }
            write(&plain[..n])?;
        }
    } else {
        let mut decoder = Decompress::new(false);
        let mut compressed = [0_u8; 64 * 1024];
        let mut offset = 0;
        let mut available = 0;
        loop {
            if offset == available {
                available = source.read(&mut compressed)?;
                offset = 0;
            }
            let old_in = decoder.total_in();
            let old_out = decoder.total_out();
            let flush = if available == 0 { FlushDecompress::Finish } else { FlushDecompress::None };
            let status = decoder.decompress(&compressed[offset..available],&mut plain,flush)
                .map_err(|_| MediaBackupError::Invalid)?;
            let used = (decoder.total_in() - old_in) as usize;
            let produced = (decoder.total_out() - old_out) as usize;
            offset += used;
            write(&plain[..produced])?;
            if status == Status::StreamEnd { break; }
            if used == 0 && produced == 0 { return Err(MediaBackupError::Invalid); }
        }
        if decoder.total_in() != member.compressed { return Err(MediaBackupError::Invalid); }
    }
    if length != member.size || crc.finalize() != member.crc {
        return Err(MediaBackupError::Invalid);
    }
    Ok(hex::encode(digest.finalize()))
}

fn validate_refs(projected: &Path, qq: &mut BTreeSet<String>, wechat: &mut BTreeSet<String>) -> Result<()> {
    if qq.is_empty() && wechat.is_empty() { return Ok(()); }
    let conn = Store::open(projected).and_then(|s| s.connection())
        .map_err(|_| MediaBackupError::Invalid)?;
    let mut query = conn.prepare(
        "SELECT platform,account_id,conversation_id,id,raw_type,type,extra FROM messages
         WHERE (platform='qq' AND extra IS NOT NULL)
            OR (platform='wechat' AND type IN ('image','voice','video','file'))
         LIMIT ?",
    ).map_err(|_| MediaBackupError::Invalid)?;
    let mut rows = query.query([MAX_REFERENCED_ROWS+1]).map_err(|_| MediaBackupError::Invalid)?;
    let mut n = 0;
    while let Some(row) = rows.next().map_err(|_| MediaBackupError::Invalid)? {
        n += 1;
        if n > MAX_REFERENCED_ROWS { return Err(MediaBackupError::Limit); }
        let platform: String = row.get(0).map_err(|_| MediaBackupError::Invalid)?;
        let encoded: Option<String> = row.get(6).map_err(|_| MediaBackupError::Invalid)?;
        let extra: Value = encoded.as_deref().and_then(|s| serde_json::from_str(s).ok()).unwrap_or(Value::Null);
        if platform == "qq" {
            let resources = extra["roaming_resources"].as_array().or_else(|| extra["media"].as_array());
            if let Some(list) = resources {
                for item in list {
                    if let Some(relative) = item["path"].as_str() { qq.remove(relative); }
                }
            }
        } else if !wechat.is_empty() {
            let account: String = row.get(1).map_err(|_| MediaBackupError::Invalid)?;
            let conversation: String = row.get(2).map_err(|_| MediaBackupError::Invalid)?;
            let id: String = row.get(3).map_err(|_| MediaBackupError::Invalid)?;
            let raw: Option<String> = row.get(4).map_err(|_| MediaBackupError::Invalid)?;
            if let Some(digest) = wechat_cache_key(&account,&conversation,&id,raw.as_deref(),&extra) {
                wechat.remove(&digest);
            }
        }
        if qq.is_empty() && wechat.is_empty() { return Ok(()); }
    }
    Err(MediaBackupError::Unreferenced)
}

pub fn inspect(path: &Path) -> Result<Preview> {
    verify_archive(path,None).map(|(preview,_)| preview)
}

fn verify_archive(path: &Path, mut stage: Option<&mut ImportStage<'_>>) -> Result<(Preview,String)> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() { return Err(MediaBackupError::Invalid); }
    if meta.len() > MAX_ARCHIVE { return Err(MediaBackupError::Limit); }
    #[cfg(target_os = "linux")]
    let mut file = {
        use rustix::fs::{Mode, OFlags, open};
        File::from(open(path,OFlags::RDONLY|OFlags::NONBLOCK|OFlags::NOFOLLOW|OFlags::CLOEXEC,Mode::empty())
            .map_err(io::Error::from)?)
    };
    #[cfg(not(target_os = "linux"))]
    let mut file = File::open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file() || meta.len() != opened.len() { return Err(MediaBackupError::Changed); }
    let archive_sha256 = read_digest(&mut file,&mut io::sink(),meta.len())?;
    let entries = parse_members(&mut file,meta.len())?;
    let manifest_member = entries.get(MANIFEST).ok_or(MediaBackupError::Invalid)?;
    let index_member = entries.get(INDEX).ok_or(MediaBackupError::Invalid)?;
    let mut encoded = Vec::new();
    read_member(&mut file,manifest_member,&mut encoded)?;
    let manifest: Manifest = serde_json::from_slice(&encoded).map_err(|_| MediaBackupError::Invalid)?;
    // The typed manifest owns its fields. Release the raw member before
    // verification and indexing allocate their own working storage.
    drop(encoded);
    if manifest.format != "local-message-hub-media-backup" || manifest.version != 1 || manifest.created_at < 0
        || manifest.media_count != manifest.media.len() || manifest.media_count != entries.len()-2
    { return Err(MediaBackupError::Invalid); }
    let mut declared = BTreeSet::new();
    let mut qq = BTreeSet::new();
    let mut wechat = BTreeSet::new();
    let mut verified_bytes = 0_u64;
    let mut qq_entries = 0;
    let mut wechat_entries = 0;
    for item in &manifest.media {
        if !declared.insert(item.path.as_str()) || !hex_is(&item.sha256,64,true) {
            return Err(MediaBackupError::Invalid);
        }
        let source = match kind(&item.path).ok_or(MediaBackupError::Invalid)? {
            Media::Qq(relative) => { qq.insert(relative.to_owned()); qq_entries += 1; "qq" }
            Media::Wechat(key) => { wechat.insert(key.to_owned()); wechat_entries += 1; "wechat" }
        };
        let member = entries.get(&item.path).ok_or(MediaBackupError::Invalid)?;
        if item.size != member.size { return Err(MediaBackupError::Invalid); }
        let sha = match stage.as_deref_mut() {
            Some(stage) => stage.record(&mut file,member,item,source)?,
            None => read_member(&mut file,member,&mut io::sink())?,
        };
        if sha != item.sha256 { return Err(MediaBackupError::Invalid); }
        verified_bytes += member.size;
    }
    if manifest.media_bytes != verified_bytes { return Err(MediaBackupError::Invalid); }

    // The input index is materialized under a fixed private scratch name.
    // Import projects into the private stage, preview into scratch only.
    let data = tempfile::tempdir()?;
    let projected_temp = stage.is_none().then(tempfile::tempdir).transpose()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(data.path(),fs::Permissions::from_mode(0o700))?;
        if let Some(dir) = &projected_temp {
            fs::set_permissions(dir.path(),fs::Permissions::from_mode(0o700))?;
        }
    }
    let projected = stage.as_ref().map(|stage| stage.root.join("snapshot"))
        .or_else(|| projected_temp.as_ref().map(|dir| dir.path().to_path_buf()))
        .expect("stage or temporary directory");
    let detached = data.path().join(INDEX);
    let mut output = open_private_new(&detached)?;
    let index_sha256 = read_member(&mut file,index_member,&mut output)?;
    drop(output);
    let projection = project_snapshot(&detached,&projected).map_err(|_| MediaBackupError::Invalid)?;
    if projection.source_sha256 != index_sha256 { return Err(MediaBackupError::Changed); }
    validate_refs(&projected.join(INDEX),&mut qq,&mut wechat)?;
    file.rewind()?;
    if read_digest(&mut file,&mut io::sink(),meta.len())? != archive_sha256 {
        return Err(MediaBackupError::Changed);
    }
    Ok((Preview { archive_sha256,index_sha256,verified_media_count:manifest.media_count,
        verified_media_bytes:verified_bytes,referenced_qq_count:qq_entries,
        referenced_wechat_count:wechat_entries,reported_missing_media_count:manifest.missing_media_count },
        projection.output_sha256))
}

/// Publish a complete private content-addressed archive; never overwrite a target.
#[cfg(target_os = "linux")]
pub fn import(path: &Path, target: &Path) -> Result<Imported> {
    use rustix::fs::{AtFlags, Mode, OFlags, RenameFlags, open, renameat_with, statat};
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let leaf = target.file_name().ok_or(MediaBackupError::Target)?;
    let original_parent = target.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    let info = fs::symlink_metadata(original_parent)?;
    if !info.is_dir() || info.permissions().mode() & 0o077 != 0 {
        return Err(MediaBackupError::Target);
    }
    let parent = original_parent.canonicalize()?;
    let parent_fd = open(&parent,OFlags::RDONLY|OFlags::DIRECTORY|OFlags::NOFOLLOW|OFlags::CLOEXEC,Mode::empty())
        .map_err(io::Error::from)?;
    let dir = File::from(parent_fd);
    let dir_metadata = dir.metadata()?;
    if dir_metadata.permissions().mode() & 0o077 != 0 || info.dev() != dir_metadata.dev()
        || info.ino() != dir_metadata.ino()
    { return Err(MediaBackupError::Target); }
    match fs::symlink_metadata(parent.join(leaf)) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
        Ok(_) => return Err(MediaBackupError::Target),
    }
    let temp = tempfile::Builder::new().prefix(".lmh-media-stage-").tempdir_in(&parent)?;
    fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700))?;
    let stage_identity = fs::symlink_metadata(temp.path())?;
    let mut stage = ImportStage::new(temp.path())?;
    let (verified, projected_index_sha256) = verify_archive(path,Some(&mut stage))?;
    let catalog_sha256 = stage.finish(&verified,&projected_index_sha256)?;
    let unique_object_count = stage.seen.len();
    let old = temp.path().file_name().ok_or(MediaBackupError::Target)?;
    let anchored = statat(&dir,old,AtFlags::SYMLINK_NOFOLLOW).map_err(io::Error::from)?;
    if stage_identity.dev() != anchored.st_dev as u64 || stage_identity.ino() != anchored.st_ino as u64 {
        return Err(MediaBackupError::Target);
    }
    dir.sync_all()?;
    match renameat_with(&dir,old,&dir,leaf,RenameFlags::NOREPLACE) {
        Ok(()) => {}
        Err(rustix::io::Errno::EXIST) => return Err(MediaBackupError::Target),
        Err(error) => return Err(io::Error::from(error).into()),
    }
    // The completed directory is now visible and must not be removed. A failed
    // post-commit directory sync only leaves crash-durability uncertain.
    drop(stage);
    let _published = temp.keep();
    let parent_synced = dir.sync_all().is_ok();
    Ok(Imported { verified, projected_index_sha256, catalog_sha256,unique_object_count,parent_synced })
}

#[cfg(not(target_os = "linux"))]
pub fn import(_path: &Path, _target: &Path) -> Result<Imported> {
    Err(MediaBackupError::UnsupportedTarget)
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::{Compression, write::DeflateEncoder};

    #[test]
    fn deflate_must_end_with_exact_input_range_and_the_expected_logical_output() {
        let data = b"known synthetic member";
        let mut encoded = DeflateEncoder::new(Vec::new(), Compression::default());
        encoded.write_all(data).unwrap();
        let valid = encoded.finish().unwrap();
        let with_extra = [valid.as_slice(), b"extra input"].concat();
        let mut temp = tempfile::tempfile().unwrap();
        let mut check = |bytes: &[u8], accepted: bool| {
            temp.set_len(0).unwrap();
            temp.rewind().unwrap();
            temp.write_all(bytes).unwrap();
            let member = ZipMember { flags:0, method:8, crc:crc32fast::hash(data),
                size:data.len() as u64, compressed:bytes.len() as u64, header_start:0, data_start:0 };
            let mut output = Vec::new();
            let result = read_member(&mut temp,&member,&mut output);
            assert_eq!(result.is_ok(),accepted);
            if accepted { assert_eq!(output,data); }
        };
        check(&valid,true);
        check(&valid[..valid.len()-1],false);
        check(&with_extra,false);
    }

    #[test]
    fn declared_combined_budget_is_bounded_at_the_exact_limit() {
        assert_eq!(add_declared(MAX_TOTAL-1,1).unwrap(),MAX_TOTAL);
        assert!(matches!(add_declared(MAX_TOTAL,1),Err(MediaBackupError::Limit)));
        assert!(matches!(add_declared(u64::MAX,1),Err(MediaBackupError::Limit)));
    }
}
