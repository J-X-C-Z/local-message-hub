use crate::events::{EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs::{self, File}, io::{self, Read}, path::Path};

pub(crate) const OFFLINE_PRODUCER: &str = "@offline/legacy-json-v1";

#[derive(Debug, thiserror::Error)]
pub enum ImportError {
    #[error("legacy JSON export unavailable")]
    Source,
    #[error("expected a bounded text-only legacy JSON message export for the explicit account")]
    Invalid,
    #[error("target is not an available initialized native archive")]
    Archive,
    #[error("confirmation does not match this exact preview")]
    Confirmation,
    #[error("import was not confirmed; retry this exact preview if the result is uncertain")]
    Apply,
}

fn open_source(path: &Path) -> io::Result<File> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        use rustix::fs::{Mode, OFlags, open};
        // Reject a last-component symlink at the actual open, and do not
        // block waiting for a writer if a raced replacement is a FIFO. The
        // callers still require and check a bounded regular file afterward.
        Ok(open(path, OFlags::RDONLY | OFlags::NONBLOCK | OFlags::NOFOLLOW | OFlags::CLOEXEC, Mode::empty())?.into())
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    { File::open(path) }
}

fn same_file(before: &fs::Metadata, after: &fs::Metadata) -> bool {
    if !before.is_file() || !after.is_file() || before.len() != after.len()
        || before.modified().ok() != after.modified().ok() { return false; }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        before.dev() == after.dev() && before.ino() == after.ino()
            && before.ctime() == after.ctime() && before.ctime_nsec() == after.ctime_nsec()
    }
    #[cfg(not(unix))]
    { true }
}

pub(crate) fn read_stable_source(path: &Path, limit: usize) -> io::Result<Vec<u8>> {
    let before = fs::symlink_metadata(path)?;
    if !before.is_file() || before.len() > limit as u64 { return Err(io::Error::other("unsafe or unstable detached input")); }
    let file = open_source(path)?;
    let opened = file.metadata()?;
    if !same_file(&before,&opened) { return Err(io::Error::other("unsafe or unstable detached input")); }
    let mut bytes = Vec::with_capacity(opened.len() as usize);
    (&file).take(limit as u64 + 1).read_to_end(&mut bytes)?;
    let after = file.metadata()?;
    if !same_file(&opened,&after) || bytes.len() as u64 != opened.len() { return Err(io::Error::other("unsafe or unstable detached input")); }
    Ok(bytes)
}

#[derive(Deserialize)]
struct LegacyTextRow {
    platform: String,
    account_id: String,
    conversation_id: String,
    id: String,
    conversation_title: Option<String>,
    sender_id: Option<String>,
    sender_name: Option<String>,
    timestamp: i64,
    is_self: bool,
    #[serde(rename = "type")]
    kind: String,
    text: String,
}

impl LegacyTextRow {
    fn normalize(self,platform:&str,account:&str)->Result<NormalizedMessage,ImportError> {
        if self.kind!="text"||self.platform!=platform||self.account_id!=account {
            return Err(ImportError::Invalid);
        }
        Ok(NormalizedMessage {
            platform:self.platform,account_id:self.account_id,conversation_id:self.conversation_id,
            message_id:self.id,title:self.conversation_title,sender_id:self.sender_id,
            sender_name:self.sender_name,timestamp:self.timestamp,is_self:self.is_self,text:Some(self.text),
        })
    }
}

// Submodule shares the literal row shape without weakening the all-or-nothing
// native staging/import plan.
pub(crate) mod preflight;
pub(crate) mod csv;

#[derive(Serialize)]
pub struct Preview {
    confirmation: String,
    rows: usize,
}

#[derive(Serialize)]
pub struct Receipt {
    batch_id: String,
    rows: usize,
    created: usize,
    unchanged: usize,
}

pub(crate) struct Plan {
    pub(crate) request: IngestRequest,
}

impl Plan {
    fn read(source: &Path, archive: &EventStore, platform: &str, account: &str, mode: &str) -> Result<Self, ImportError> {
        let original = fs::symlink_metadata(source).map_err(|_| ImportError::Source)?;
        if !original.is_file() || original.len() > MAX_BATCH_BYTES as u64 { return Err(ImportError::Source); }
        let file = open_source(source).map_err(|_| ImportError::Source)?;
        let opened = file.metadata().map_err(|_| ImportError::Source)?;
        if !opened.is_file() || opened.len() != original.len() { return Err(ImportError::Source); }
        let mut bytes = Vec::new();
        (&file).take(MAX_BATCH_BYTES as u64 + 1).read_to_end(&mut bytes).map_err(|_| ImportError::Source)?;
        if bytes.len() as u64 != opened.len() || bytes.len() > MAX_BATCH_BYTES { return Err(ImportError::Source); }

        Self::from_bytes(&bytes, archive, platform, account, mode)
    }

    pub(crate) fn from_bytes(bytes: &[u8], archive: &EventStore, platform: &str, account: &str, mode: &str) -> Result<Self, ImportError> {
        if bytes.is_empty() || bytes.len() > MAX_BATCH_BYTES { return Err(ImportError::Invalid); }
        let rows: Vec<LegacyTextRow> = serde_json::from_slice(bytes).map_err(|_| ImportError::Invalid)?;
        Self::from_rows(bytes,rows,archive,platform,account,mode)
    }

    pub(crate) fn from_stock_bytes(bytes:&[u8],archive:&EventStore,platform:&str,account:&str)
        ->Result<Self,ImportError> {
        let rows=preflight::strict_stock_rows(bytes).map_err(|_|ImportError::Invalid)?;
        Self::from_rows(bytes,rows,archive,platform,account,ORDINARY)
    }

    fn from_rows(bytes:&[u8],rows:Vec<LegacyTextRow>,archive:&EventStore,platform:&str,
        account:&str,mode:&str)->Result<Self,ImportError> {
        let mut messages = Vec::with_capacity(rows.len());
        for row in rows {
            messages.push(row.normalize(platform,account)?);
        }

        let request = IngestRequest { idempotency_key: "legacy-json-preview".into(), messages };
        request.validate(OFFLINE_PRODUCER).map_err(|_| ImportError::Invalid)?;
        if preflight::encoded_len(&request.messages).map_err(|_| ImportError::Invalid)? > MAX_BATCH_BYTES {
            return Err(ImportError::Invalid);
        }
        Self::bind(bytes,archive,platform,account,mode,request)
    }

    pub(crate) fn from_csv_bytes(bytes:&[u8],archive:&EventStore,platform:&str,account:&str)
        ->Result<Self,ImportError> {
        let request=csv::decode(bytes,platform,account).map_err(|_|ImportError::Invalid)?;
        Self::bind(bytes,archive,platform,account,csv::MODE,request)
    }

    fn bind(bytes:&[u8],archive:&EventStore,platform:&str,account:&str,mode:&str,
        mut request:IngestRequest)->Result<Self,ImportError> {
        let digest = Sha256::digest(bytes);
        let tag = serde_json::to_vec(&(mode,archive.archive_id(),platform,account,hex::encode(digest)))
            .map_err(|_| ImportError::Invalid)?;
        request.idempotency_key = hex::encode(Sha256::digest(tag));
        Ok(Self { request })
    }
}

pub(crate) const ORDINARY: &str="lmh-legacy-json-import-v1";
#[cfg(target_os = "linux")]
const GUARDED: &str="lmh-guarded-legacy-json-import-v1";

impl Plan {
    fn preview(self) -> Preview {
        Preview { confirmation:self.request.idempotency_key,rows:self.request.messages.len() }
    }

    fn apply(self, confirmation: &str,
        write: impl FnOnce(IngestRequest)->Result<crate::events::IngestReceipt,crate::events::EventError>)
        -> Result<Receipt,ImportError> {
        if confirmation != self.request.idempotency_key { return Err(ImportError::Confirmation); }
        let batch_id=self.request.idempotency_key.clone();
        let rows=self.request.messages.len();
        let receipt=write(self.request).map_err(|_|ImportError::Apply)?;
        Ok(Receipt {batch_id,rows,created:receipt.created,unchanged:receipt.unchanged})
    }
}

pub fn preview(source: &Path, target: &Path, platform: &str, account: &str) -> Result<Preview, ImportError> {
    let archive=EventStore::open(target).map_err(|_|ImportError::Archive)?;
    Ok(Plan::read(source,&archive,platform,account,ORDINARY)?.preview())
}

pub fn commit(source: &Path, target: &Path, platform: &str, account: &str, confirmation: &str) -> Result<Receipt, ImportError> {
    let archive=EventStore::open(target).map_err(|_|ImportError::Archive)?;
    Plan::read(source,&archive,platform,account,ORDINARY)?.apply(confirmation,|request|archive.import_legacy(request))
}

#[cfg(target_os = "linux")]
pub(crate) fn guarded_preview(source: &Path, platform: &str, account: &str,
    authorized: &crate::profile_lock::AuthorizedGuardedImport<'_>) -> Result<Preview,ImportError> {
    if authorized.archive().guarded_has_v9_snapshot().map_err(|_|ImportError::Archive)? {
        return Err(ImportError::Archive);
    }
    let plan=Plan::read(source,authorized.archive(),platform,account,GUARDED)?;
    authorized.recheck().map_err(|_|ImportError::Archive)?;
    Ok(plan.preview())
}

#[cfg(target_os = "linux")]
pub(crate) fn guarded_commit(source: &Path, platform: &str, account: &str, confirmation: &str,
    authorized: &crate::profile_lock::AuthorizedGuardedImport<'_>) -> Result<Receipt,ImportError> {
    Plan::read(source,authorized.archive(),platform,account,GUARDED)?.apply(confirmation,|request|
        authorized.archive().import_legacy_guarded(request,authorized))
}
