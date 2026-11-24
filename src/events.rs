//! Explicitly initialized Rust-owned storage. Never upgrades or writes a legacy index.
use crate::profile_lock::{Verifier, ITERATIONS};
use crate::plugins::AccountScope;
use crate::source_progress;
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs, io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub const NATIVE_DATABASE: &str = "native-v1.sqlite3";
pub const APPLICATION_ID: i64 = 0x4c4d4831;
const NATIVE_SCHEMA_VERSION: i64 = 9; // read projection compatibility
pub(crate) const GUARDED_V9_PRODUCER:&str="@offline/guarded-snapshot-v1";
const MAX_BATCH_COUNT: usize = 100;
pub(crate) const MAX_BATCH_BYTES: usize = 512 * 1024;
const MAX_TEXT_BYTES: usize = 64 * 1024;
const MAX_METADATA_BYTES: usize = 1024;
const MAX_ID_BYTES: usize = 256;
const MAX_EVENT_PAGE: usize = 100;
const MAX_DATABASE_PAGES: i64 = 524_288; // 2 GiB with the fixed 4 KiB page size
const DEFAULT_MESSAGE_SCOPE_INDEX_SQL: &str = "CREATE INDEX native_messages_scope
             ON messages(platform, account_id, conversation_id, timestamp DESC)";

const HISTORY_MESSAGE_SCOPE_INDEX_SQL: &str = "CREATE INDEX native_messages_scope_history
             ON messages(platform, account_id, conversation_id, timestamp DESC, id DESC)";

const SCHEMA: &str = "
    CREATE TABLE native_meta (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id BLOB NOT NULL);
    CREATE TABLE conversations (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
        title TEXT, kind TEXT, avatar_url TEXT, member_count INTEGER,
        last_message TEXT, last_timestamp INTEGER, message_count INTEGER NOT NULL DEFAULT 0, extra TEXT,
        PRIMARY KEY (platform, account_id, id));
    CREATE TABLE messages (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
        conversation_id TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
        timestamp INTEGER NOT NULL DEFAULT 0, type TEXT, text TEXT,
        is_self INTEGER NOT NULL DEFAULT 0, media_url TEXT, file_name TEXT,
        raw_type TEXT, preview TEXT, extra TEXT, PRIMARY KEY (platform, account_id, id));
    CREATE TABLE contacts (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
        name TEXT, avatar_url TEXT, kind TEXT, extra TEXT, PRIMARY KEY (platform, account_id, id));
    CREATE TABLE conversation_metadata (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
        archived INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
        PRIMARY KEY (platform, account_id, conversation_id));
    CREATE TABLE contact_identities (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
        identity_id TEXT NOT NULL, updated_at INTEGER NOT NULL,
        PRIMARY KEY (platform, account_id, contact_id));
    CREATE INDEX native_messages_order ON messages(timestamp DESC);
    CREATE INDEX native_conversations_scope ON conversations(platform,last_timestamp DESC);
    CREATE TABLE native_owners (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, message_id TEXT NOT NULL,
        producer TEXT NOT NULL, title TEXT,
        PRIMARY KEY(platform,account_id,message_id),
        FOREIGN KEY(platform,account_id,message_id) REFERENCES messages(platform,account_id,id));
    CREATE TABLE native_event_journal (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK(kind IN ('created','revised')),
        platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        message_id TEXT NOT NULL, timestamp INTEGER NOT NULL);
    CREATE TABLE native_ingest_receipts (
        producer TEXT NOT NULL, idempotency_key TEXT NOT NULL, digest BLOB NOT NULL,
        created INTEGER NOT NULL, revised INTEGER NOT NULL, unchanged INTEGER NOT NULL,
        checkpoint INTEGER NOT NULL, PRIMARY KEY(producer,idempotency_key));
";

// Additive to v9: preserve the original journal IDs, receipts and cursor generation.
const SCOPE_TABLE: &str = "
    CREATE TABLE native_account_event_sequence (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, account_event_id INTEGER NOT NULL,
        event_id INTEGER NOT NULL UNIQUE REFERENCES native_event_journal(event_id),
        PRIMARY KEY(platform,account_id,account_event_id)) WITHOUT ROWID;
    CREATE TRIGGER native_account_event_record AFTER INSERT ON native_event_journal BEGIN
        INSERT INTO native_account_event_sequence(platform,account_id,account_event_id,event_id)
        VALUES (new.platform, new.account_id,
          COALESCE((SELECT MAX(account_event_id) FROM native_account_event_sequence
                    WHERE platform=new.platform AND account_id=new.account_id),0)+1, new.event_id);
    END;
";

// Separate account-local sequence: revisions and foreign events leave no gaps.
const INCOMING_SCHEMA: &str = "
    CREATE TABLE native_account_incoming (
        platform TEXT NOT NULL, account_id TEXT NOT NULL, incoming_id INTEGER NOT NULL,
        event_id INTEGER NOT NULL UNIQUE REFERENCES native_event_journal(event_id),
        PRIMARY KEY(platform,account_id,incoming_id)) WITHOUT ROWID;
    CREATE TRIGGER native_account_incoming_record AFTER INSERT ON native_event_journal
    WHEN new.kind='created' BEGIN
        INSERT INTO native_account_incoming(platform,account_id,incoming_id,event_id)
        VALUES (new.platform,new.account_id,
          COALESCE((SELECT MAX(incoming_id) FROM native_account_incoming
                    WHERE platform=new.platform AND account_id=new.account_id),0)+1,new.event_id);
    END;
";

#[derive(Debug, thiserror::Error)]
pub enum EventError {
    #[error("{0}")]
    BadRequest(&'static str),
    #[error("idempotency key or message identity is already in use")]
    Conflict,
    #[error("source checkpoint no longer matches or cannot advance")]
    SourceCheckpointConflict,
    #[error("synthetic IMAP fixture epoch mismatch")]
    FixtureUidValidity,
    #[error("synthetic IMAP fixture previous UID mismatch")]
    FixtureResume,
    #[error("native storage is not an isolated Rust-owned directory")]
    UnsafeDirectory,
    #[error("native storage is unavailable")]
    Storage,
    #[error("account event journal is not installed; use the explicit offline upgrade")]
    ScopeUnavailable,
    #[error("ordinary message ingress paused by archive owner")]
    IngressPaused,
    #[error("incoming message journal is not installed; use upgrade-mcp-incoming offline")]
    IncomingUnavailable,
    #[error("native guarded profile is locked or unavailable")]
    ProfileLocked,
}

impl From<rusqlite::Error> for EventError {
    fn from(_error: rusqlite::Error) -> Self {
        EventError::Storage
    }
}

impl From<io::Error> for EventError {
    fn from(_error: io::Error) -> Self {
        EventError::Storage
    }
}

type Result<T> = std::result::Result<T, EventError>;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NormalizedMessage {
    pub platform: String,
    pub account_id: String,
    pub conversation_id: String,
    pub message_id: String,
    pub title: Option<String>,
    pub sender_id: Option<String>,
    pub sender_name: Option<String>,
    pub timestamp: i64,
    pub is_self: bool,
    pub text: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct IngestRequest {
    pub idempotency_key: String,
    pub messages: Vec<NormalizedMessage>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IngestReceipt {
    pub created: usize,
    pub revised: usize,
    pub unchanged: usize,
    pub checkpoint: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    Created,
    Revised,
}

impl EventKind {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Revised => "revised",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MessageEvent {
    pub event_id: i64,
    pub kind: EventKind,
    pub platform: String,
    pub account_id: String,
    pub conversation_id: String,
    pub message_id: String,
    pub timestamp: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EventPage {
    pub items: Vec<MessageEvent>,
    pub next_after: i64,
    pub has_more: bool,
    pub checkpoint: i64,
}

#[derive(Clone)]
pub struct EventStore {
    path: PathBuf,
    archive_id: String,
    guard: Option<Verifier>,
}

fn no_redirect(meta: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return false; // FILE_ATTRIBUTE_REPARSE_POINT
        }
    }
    !meta.file_type().is_symlink()
}

fn require_private(meta: &fs::Metadata) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o077 != 0 {
            return Err(EventError::UnsafeDirectory);
        }
    }
    #[cfg(not(unix))]
    let _ = meta; // the directory's private ACL is an explicit Windows operator prerequisite
    Ok(())
}

fn canonical_dir(path: &Path) -> Result<PathBuf> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() || !no_redirect(&meta) {
        return Err(EventError::UnsafeDirectory);
    }
    require_private(&meta)?;
    Ok(path.canonicalize()?)
}

fn verify_files(path: &Path) -> Result<()> {
    let dir = path.parent().ok_or(EventError::UnsafeDirectory)?;
    if canonical_dir(dir)? != dir {
        return Err(EventError::UnsafeDirectory);
    }
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || !no_redirect(&meta) {
        return Err(EventError::UnsafeDirectory);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() != 1 {
            return Err(EventError::UnsafeDirectory);
        }
    }
    require_private(&meta)?;
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let name = entry.file_name();
        match name.to_str() {
            Some(NATIVE_DATABASE) => continue,
            Some(
                "native-v1.sqlite3-wal" | "native-v1.sqlite3-shm" | "native-v1.sqlite3-journal",
            ) => {
                let meta = match fs::symlink_metadata(entry.path()) {
                    Ok(meta) => meta,
                    Err(e) if e.kind() == io::ErrorKind::NotFound => continue,
                    Err(e) => return Err(e.into()),
                };
                if !meta.is_file() || !no_redirect(&meta) {
                    return Err(EventError::UnsafeDirectory);
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::MetadataExt;
                    if meta.nlink() != 1 {
                        return Err(EventError::UnsafeDirectory);
                    }
                }
                require_private(&meta)?;
            }
            _ => return Err(EventError::UnsafeDirectory),
        }
    }
    Ok(())
}

fn connect(path: &Path, read_write: bool) -> Result<Connection> {
    verify_files(path)?;
    let mode = if read_write {
        OpenFlags::SQLITE_OPEN_READ_WRITE
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY
    };
    let sql = Connection::open_with_flags(
        path,
        mode | OpenFlags::SQLITE_OPEN_NOFOLLOW | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    sql.busy_timeout(Duration::from_millis(500))?;
    sql.pragma_update(None, "trusted_schema", false)?;
    sql.pragma_update(None, "foreign_keys", true)?;
    sql.set_limit(
        rusqlite::limits::Limit::SQLITE_LIMIT_LENGTH,
        2 * 1024 * 1024,
    );
    let deadline = Instant::now() + Duration::from_secs(4);
    sql.progress_handler(2048, Some(move || Instant::now() >= deadline));
    if !read_write {
        sql.pragma_update(None, "query_only", true)?;
    }
    Ok(sql)
}

fn instance(sql: &Connection) -> Result<String> {
    let version: i64 = sql.pragma_query_value(None, "user_version", |r| r.get(0))?;
    let application: i64 = sql.pragma_query_value(None, "application_id", |r| r.get(0))?;
    let page_size: i64 = sql.pragma_query_value(None, "page_size", |r| r.get(0))?;
    let journal: String = sql.pragma_query_value(None, "journal_mode", |r| r.get(0))?;
    if version != NATIVE_SCHEMA_VERSION
        || application != APPLICATION_ID
        || page_size != 4096
        || !journal.eq_ignore_ascii_case("wal")
    {
        return Err(EventError::UnsafeDirectory);
    }
    let id: Vec<u8> = sql.query_row(
        "SELECT instance_id FROM native_meta WHERE singleton=1",
        [],
        |r| r.get(0),
    )?;
    if id.len() != 16 {
        return Err(EventError::UnsafeDirectory);
    }
    Ok(hex::encode(&id))
}

const GUARD_OBJECT: &str = "native_profile_lock";

fn has_guard(sql: &Connection) -> Result<bool> {
    Ok(sql.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE name=? COLLATE NOCASE)",
        [GUARD_OBJECT],|row|row.get(0))?)
}

fn verifier(sql: &Connection) -> Result<Verifier> {
    if !has_guard(sql)? { return Err(EventError::ProfileLocked); }
    let kind: String=sql.query_row("SELECT type FROM sqlite_schema WHERE name=?", [GUARD_OBJECT], |r|r.get(0))?;
    if kind != "table" { return Err(EventError::ProfileLocked); }
    let (salt,digest,rounds,count):(Vec<u8>,Vec<u8>,u32,i64)=sql.query_row(
        "SELECT salt,digest,iterations,(SELECT count(*) FROM native_profile_lock)
         FROM native_profile_lock WHERE singleton=1",[],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
    if rounds != ITERATIONS || count != 1 { return Err(EventError::ProfileLocked); }
    Ok(Verifier {salt:salt.try_into().map_err(|_|EventError::ProfileLocked)?,
        digest:digest.try_into().map_err(|_|EventError::ProfileLocked)? })
}

/// Optional index layout for a new, empty ordinary archive.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ArchiveIndexes {
    pub history: bool,
    pub conversations: bool,
}

impl EventStore {
    /// Explicitly initialize an empty, caller-created, private directory.
    pub fn init(dir: &Path) -> Result<Self> {
        Self::init_with_indexes(dir, ArchiveIndexes::default())
    }

    /// Create an ordinary empty archive with the optional full history order
    /// index. Does not open, alter, or reindex an existing archive.
    pub fn init_with_history_index(dir: &Path) -> Result<Self> {
        Self::init_with_indexes(dir, ArchiveIndexes { history: true, ..ArchiveIndexes::default() })
    }

    /// Initialize a fresh ordinary archive with the requested index layout.
    /// Existing directories are not opened, migrated, or reindexed.
    pub fn init_with_indexes(dir: &Path, indexes: ArchiveIndexes) -> Result<Self> {
        Self::init_with_guard(dir, None, indexes)
    }

    pub(crate) fn init_guarded(dir: &Path,password: &str) -> Result<Self> {
        let guard=Verifier::new(password).ok_or(EventError::ProfileLocked)?;
        Self::init_with_guard(dir, Some(guard), ArchiveIndexes::default())
    }

    fn init_with_guard(dir: &Path, guard: Option<Verifier>, indexes: ArchiveIndexes) -> Result<Self> {
        let dir = canonical_dir(dir)?;
        let mut entries = fs::read_dir(&dir)?;
        if entries.next().transpose()?.is_some() {
            return Err(EventError::UnsafeDirectory);
        }
        let path = dir.join(NATIVE_DATABASE);
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        drop(options.open(&path)?);
        let mut instance_id = [0_u8; 16];
        getrandom::fill(&mut instance_id).map_err(|_| EventError::Storage)?;
        let mut sql = connect(&path, true)?;
        sql.pragma_update(None, "page_size", 4096)?;
        let mode: String = sql.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0))?;
        if !mode.eq_ignore_ascii_case("wal") {
            return Err(EventError::Storage);
        }
        sql.pragma_update(None, "synchronous", "FULL")?;
        sql.pragma_update(None, "max_page_count", MAX_DATABASE_PAGES)?;
        let tx = sql.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute_batch(SCHEMA)?;
        tx.execute_batch(if guard.is_none() && indexes.history {
            HISTORY_MESSAGE_SCOPE_INDEX_SQL
        } else {
            DEFAULT_MESSAGE_SCOPE_INDEX_SQL
        })?;
        if guard.is_none() && indexes.conversations {
            tx.execute_batch("CREATE INDEX native_conversations_account_order
                ON conversations(platform, account_id, last_timestamp DESC, id ASC);")?;
        }
        tx.execute_batch(SCOPE_TABLE)?;
        tx.execute_batch(INCOMING_SCHEMA)?;
        if guard.is_none() { tx.execute_batch(source_progress::SCHEMA)?; }
        tx.execute_batch(crate::metadata::SCHEMA)?;
        if let Some(guard) = &guard {
            tx.execute_batch("CREATE TABLE native_profile_lock(
                singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton=1),salt BLOB NOT NULL CHECK(length(salt)=16),
                digest BLOB NOT NULL CHECK(length(digest)=32),iterations INTEGER NOT NULL CHECK(iterations=310000))")?;
            tx.execute("INSERT INTO native_profile_lock VALUES(1,?,?,?)",
                (guard.salt.as_slice(),guard.digest.as_slice(),ITERATIONS))?;
        }
        tx.pragma_update(None, "user_version", NATIVE_SCHEMA_VERSION)?;
        tx.pragma_update(None, "application_id", APPLICATION_ID)?;
        tx.execute(
            "INSERT INTO native_meta(singleton,instance_id) VALUES (1,?)",
            [instance_id.as_slice()],
        )?;
        tx.commit()?;
        Ok(Self {
            path,
            archive_id: hex::encode(instance_id),
            guard,
        })
    }

    pub fn open(dir: &Path) -> Result<Self> {
        let path = canonical_dir(dir)?.join(NATIVE_DATABASE);
        let sql = connect(&path, false)?;
        let archive_id = instance(&sql)?;
        if has_guard(&sql)? { return Err(EventError::ProfileLocked); }
        Ok(Self { path, archive_id, guard:None })
    }

    pub(crate) fn open_guarded(dir: &Path) -> Result<Self> {
        let path = canonical_dir(dir)?.join(NATIVE_DATABASE);
        let sql = connect(&path, false)?;
        let archive_id = instance(&sql)?;
        let guard=verifier(&sql).map_err(|_|EventError::ProfileLocked)?;
        Ok(Self {path,archive_id,guard:Some(guard)})
    }

    pub(crate) fn verify_guarded_password(&self,password: &str) -> Result<bool> {
        let guard=self.guard.as_ref().ok_or(EventError::ProfileLocked)?;
        self.verify()?;
        Ok(guard.verify(password))
    }

    /// Require the canonical BINARY/TEXT contacts layout for optimizations
    /// that depend on identical returned keys inside an exact account.
    pub(crate) fn has_canonical_contacts(&self, db: &Connection) -> bool {
        if self.require_ordinary().is_err() {
            return false;
        }
        // Compare the statement SQLite stores, without its leading indentation
        // or trailing semicolon. If a compatible archive was rebuilt with an
        // altered or differently spelled schema, keep the historical query.
        const START: &str = "CREATE TABLE contacts (";
        let Some((definition, _)) = SCHEMA.find(START).and_then(|start| SCHEMA[start..].split_once(';')) else {
            return false;
        };
        db.query_row("SELECT sql FROM main.sqlite_schema WHERE type='table' AND name='contacts' COLLATE BINARY",
            [], |r| r.get::<_, String>(0)).ok().is_some_and(|actual| actual == definition)
    }

    /// Require a canonical rowid order and a single conversation metadata
    /// match per message. With duplicate join matches, changing the row
    /// visitation plan could change which tied metadata precedes the sentinel.
    pub(crate) fn has_canonical_context_order(&self, db: &Connection) -> bool {
        if self.require_ordinary().is_err() {
            return false;
        }
        fn definition(start: &str) -> Option<&'static str> {
            SCHEMA.find(start).and_then(|i| SCHEMA[i..].split_once(';')).map(|(ddl, _)| ddl)
        }
        let (Some(messages), Some(conversations)) = (
            definition("CREATE TABLE messages ("), definition("CREATE TABLE conversations ("),
        ) else {
            return false;
        };
        db.query_row(
            "SELECT COUNT(*) FROM main.sqlite_schema
             WHERE (type='table' AND name='messages' AND sql=?1)
                OR (type='index' AND name='native_messages_scope' AND sql=?2)
                OR (type='table' AND name='conversations' AND sql=?3)",
            (messages, DEFAULT_MESSAGE_SCOPE_INDEX_SQL, conversations),
            |r| r.get::<_, i64>(0),
        ).is_ok_and(|count| count == 3)
    }

    pub(crate) fn require_ordinary(&self) -> Result<()> {
        if self.guard.is_some() { return Err(EventError::ProfileLocked); }
        Ok(())
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn archive_id(&self) -> &str {
        &self.archive_id
    }

    pub fn verify(&self) -> Result<()> {
        self.read_connection()?;
        Ok(())
    }

    /// Validate a connected reader/transaction against this specific store.
    pub(crate) fn verify_connection(&self, sql: &Connection) -> Result<()> {
        if instance(sql)? != self.archive_id {
            return Err(EventError::UnsafeDirectory);
        }
        match &self.guard {
            Some(saved) if verifier(sql).ok().as_ref() == Some(saved) => Ok(()),
            None if !has_guard(sql)? => Ok(()),
            _ => Err(EventError::ProfileLocked),
        }
    }

    pub(crate) fn read_connection(&self) -> Result<Connection> {
        let sql = connect(&self.path, false)?;
        self.verify_connection(&sql)?;
        Ok(sql)
    }

    /// Add account-local replay to an existing native (never legacy) archive.
    /// The operator must stop the service before running it and retain a backup.
    pub fn upgrade_event_scopes(&self) -> Result<()> {
        self.require_ordinary()?;
        self.verify()?;
        let mut sql = connect(&self.path, true)?;
        self.verify_connection(&sql)?;
        sql.pragma_update(None, "synchronous", "FULL")?;
        sql.pragma_update(None, "max_page_count", MAX_DATABASE_PAGES)?;
        let tx = sql.transaction_with_behavior(TransactionBehavior::Immediate)?;
        self.verify_connection(&tx)?;
        if !scope_ready(&tx)? {
            let existing: i64 = tx.query_row(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name IN
                 ('native_account_event_sequence','native_account_event_record')",
                [], |r| r.get(0),
            )?;
            if existing != 0 { return Err(EventError::ScopeUnavailable); }
            tx.execute_batch(SCOPE_TABLE)?;
            tx.execute(
                "INSERT INTO native_account_event_sequence(platform, account_id, account_event_id, event_id)
                 SELECT platform, account_id, ROW_NUMBER() OVER
                    (PARTITION BY platform,account_id ORDER BY event_id), event_id
                 FROM native_event_journal",
                [],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    /// Explicit offline upgrade; never rewrites existing event IDs or desktop cursors.
    pub fn upgrade_mcp_incoming(&self) -> Result<()> {
        let mut sql = self.write_connection()?;
        let tx = sql.transaction_with_behavior(TransactionBehavior::Immediate)?;
        self.verify_connection(&tx)?;
        if !incoming_ready(&tx)? {
            let existing: i64 = tx.query_row(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name IN
                 ('native_account_incoming','native_account_incoming_record')",
                [], |r| r.get(0),
            )?;
            if existing != 0 { return Err(EventError::IncomingUnavailable); }
            // Backfill native journal only. Historical self rows and offline imports
            // were never journaled and therefore are deliberately not recovered.
            tx.execute_batch(INCOMING_SCHEMA)?;
            tx.execute(
                "INSERT INTO native_account_incoming(platform,account_id,incoming_id,event_id)
                 SELECT platform,account_id,ROW_NUMBER() OVER
                    (PARTITION BY platform,account_id ORDER BY event_id),event_id
                 FROM native_event_journal WHERE kind='created'", [],
            )?;
        }
        tx.commit()?;
        Ok(())
    }

    pub(crate) fn incoming(&self, scope: &AccountScope, after: Option<i64>, limit: usize) -> Result<EventPage> {
        if after.is_some_and(|id| id < 0) || !(1..=50).contains(&limit) {
            return Err(EventError::BadRequest("invalid incoming cursor or page size"));
        }
        let mut sql = self.read_connection()?;
        let tx = sql.transaction()?;
        self.verify_connection(&tx)?;
        if !incoming_ready(&tx)? { return Err(EventError::IncomingUnavailable); }
        let checkpoint: i64 = tx.query_row(
            "SELECT COALESCE(MAX(incoming_id),0) FROM native_account_incoming WHERE platform=? AND account_id=?",
            (&scope.platform,&scope.account_id), |r| r.get(0))?;
        let Some(after) = after else {
            return Ok(EventPage {items:Vec::new(), next_after:checkpoint, has_more:false, checkpoint});
        };
        if after > checkpoint { return Err(EventError::BadRequest("invalid incoming cursor")); }
        let mut query = tx.prepare(
            "SELECT s.incoming_id,j.kind,j.platform,j.account_id,j.conversation_id,j.message_id,j.timestamp
             FROM native_account_incoming s JOIN native_event_journal j ON j.event_id=s.event_id
                 AND j.platform=s.platform AND j.account_id=s.account_id AND j.kind='created'
             WHERE s.platform=? AND s.account_id=? AND s.incoming_id>?
             ORDER BY s.incoming_id LIMIT ?")?;
        let mut items = query.query_map((&scope.platform,&scope.account_id,after,limit+1),event_row)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let has_more = items.len() > limit;
        items.truncate(limit);
        let next_after = items.last().map_or(after, |item| item.event_id);
        Ok(EventPage {items,next_after,has_more,checkpoint})
    }

    /// Initialize only the additive metadata audit schema for older native profiles.
    pub fn upgrade_metadata(&self) -> Result<()> {
        crate::metadata::upgrade(self).map_err(|_| EventError::Storage)
    }

    pub(crate) fn write_connection(&self) -> Result<Connection> {
        self.require_ordinary()?;
        self.verify()?;
        let db = connect(&self.path, true)?;
        self.verify_connection(&db)?;
        db.pragma_update(None, "synchronous", "FULL")?;
        db.pragma_update(None, "max_page_count", MAX_DATABASE_PAGES)?;
        Ok(db)
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn guarded_snapshot_writer(&self, authorization:&crate::profile_lock::AuthorizedGuardedImport<'_>)
        -> Result<Connection> {
        if self.guard.is_none() || !std::ptr::eq(self,authorization.archive()) {
            return Err(EventError::ProfileLocked);
        }
        authorization.recheck().map_err(|_|EventError::ProfileLocked)?;
        let db=connect(&self.path,true)?;
        self.verify_connection(&db)?;
        db.pragma_update(None,"synchronous","FULL")?;
        db.pragma_update(None,"max_page_count",MAX_DATABASE_PAGES)?;
        Ok(db)
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn guarded_has_v9_snapshot(&self) -> Result<bool> {
        if self.guard.is_none() { return Err(EventError::ProfileLocked); }
        has_v9_snapshot(&self.read_connection()?)
    }

    /// Check reserved import provenance on the reader's verified transaction.
    pub(crate) fn guarded_v9_projection_in(&self, db: &Connection) -> Result<bool> {
        if self.guard.is_none() { return Err(EventError::ProfileLocked); }
        self.verify_connection(db)?;
        has_v9_snapshot(db)
    }

    pub fn checkpoint(&self) -> Result<i64> {
        self.checkpoint_for(None)
    }

    pub fn checkpoint_for(&self, scope: Option<&AccountScope>) -> Result<i64> {
        let mut sql = self.read_connection()?;
        let tx = sql.transaction()?;
        self.verify_connection(&tx)?;
        checkpoint_for(&tx, scope)
    }

    pub fn replay(&self, after: i64, limit: usize) -> Result<EventPage> {
        self.replay_for(None, after, limit)
    }

    pub fn replay_for(&self, scope: Option<&AccountScope>, after: i64, limit: usize) -> Result<EventPage> {
        if after < 0 || !(1..=MAX_EVENT_PAGE).contains(&limit) {
            return Err(EventError::BadRequest("invalid replay cursor or page size"));
        }
        let mut sql = self.read_connection()?;
        let tx = sql.transaction()?;
        self.verify_connection(&tx)?;
        let checkpoint = checkpoint_for(&tx, scope)?;
        if after > checkpoint {
            return Err(EventError::BadRequest("replay cursor is ahead of checkpoint"));
        }
        let mut items = if let Some(scope) = scope {
            let mut statement = tx.prepare(
                "SELECT seq.account_event_id,j.kind,j.platform,j.account_id,j.conversation_id,j.message_id,j.timestamp
                 FROM native_account_event_sequence seq JOIN native_event_journal j ON j.event_id=seq.event_id
                    AND j.platform=seq.platform AND j.account_id=seq.account_id
                 WHERE seq.platform=? AND seq.account_id=? AND seq.account_event_id>?
                 ORDER BY seq.account_event_id LIMIT ?",
            )?;
            let rows = statement.query_map((&scope.platform, &scope.account_id, after, limit + 1), event_row)?;
            rows.collect::<rusqlite::Result<Vec<_>>>()?
        } else {
            let mut statement = tx.prepare(
                "SELECT event_id,kind,platform,account_id,conversation_id,message_id,timestamp
                 FROM native_event_journal WHERE event_id>? ORDER BY event_id LIMIT ?",
            )?;
            let rows = statement.query_map((after, limit + 1), event_row)?;
            rows.collect::<rusqlite::Result<Vec<_>>>()?
        };
        let has_more = items.len() > limit;
        items.truncate(limit);
        let next_after = items.last().map_or(after, |event| event.event_id);
        Ok(EventPage {
            items,
            next_after,
            has_more,
            checkpoint,
        })
    }

    /// Submit through an ordinary public producer ID. IDs starting with '@'
    /// belong to internal scoped or offline owners and are never public IDs.
    pub fn ingest(&self, producer: &str, request: IngestRequest) -> Result<IngestReceipt> {
        if producer.starts_with('@') {
            return Err(EventError::BadRequest("reserved producer ID"));
        }
        self.ingest_with_policy(producer, request, false, false, None, None)
    }

    /// Isolate the normalized message owner and receipt identities from the
    /// public producer ID and from other scopes given the same grant ID later.
    pub(crate) fn ingest_scoped(&self, plugin_id: &str, scope: &AccountScope,
        request: IngestRequest) -> Result<IngestReceipt> {
        if !request.matches_account(scope) {
            return Err(EventError::BadRequest("scope mismatch"));
        }
        let producer = scoped_producer(plugin_id, scope)?;
        self.ingest_with_policy(&producer, request, false, false, Some(scope), None)
    }

    pub(crate) fn source_checkpoint(&self, plugin_id: &str, scope: &AccountScope)
        -> Result<source_progress::Checkpoint> {
        self.require_ordinary()?;
        let mut sql = self.read_connection()?;
        let tx = sql.transaction()?;
        self.source_checkpoint_in(&tx, plugin_id, scope)
    }

    // Caller supplies its transaction so job observations and actual source progress
    // share a snapshot. This only reads ingest state; it cannot advance the cursor.
    pub(crate) fn source_checkpoint_in(&self, sql: &Connection, plugin_id: &str,
        scope: &AccountScope) -> Result<source_progress::Checkpoint> {
        self.require_ordinary()?;
        let producer = scoped_producer(plugin_id, scope)?;
        self.verify_connection(sql)?;
        if !scope_ready(sql)? { return Err(EventError::ScopeUnavailable); }
        let sequence = if source_progress::installed(sql)? {
            source_progress::sequence(sql, &producer)?
        } else { 0 };
        Ok(source_progress::checkpoint(&self.archive_id, &producer, sequence))
    }

    pub(crate) fn ingest_source(&self, plugin_id: &str, scope: &AccountScope,
        request: IngestRequest, advance: &source_progress::Advance)
        -> Result<(IngestReceipt,source_progress::Checkpoint)> {
        if !request.matches_account(scope) { return Err(EventError::BadRequest("scope mismatch")); }
        let producer = scoped_producer(plugin_id,scope)?;
        let receipt = self.ingest_with_policy(&producer,request,false,false,Some(scope),Some(Progress::Source(advance)))?;
        Ok((receipt,source_progress::checkpoint(&self.archive_id,&producer,advance.sequence)))
    }

    pub(crate) fn import_legacy(&self, request: IngestRequest) -> Result<IngestReceipt> {
        self.ingest_with_policy(crate::legacy_import::OFFLINE_PRODUCER, request, true, false, None, None)
    }

    /// Explicit local wecom-rendered-v1 commit only; retains the legacy receipt owner.
    pub(crate) fn import_wecom_rendered(&self, request: IngestRequest) -> Result<IngestReceipt> {
        self.ingest_with_policy_and_digest(crate::legacy_import::OFFLINE_PRODUCER,
            request, true, false, None, None, |request, _| bounded_unprogressed_digest(request))
    }

    /// Apply the exact old WeCom text identity plus local snapshot progress.
    /// No provider/journal event is emitted, including for metadata-only batches.
    pub(crate) fn import_wecom_sync(&self,request:IngestRequest,account:&str,
        advance:&crate::wecom_sync::Advance)->Result<IngestReceipt> {
        validate_wecom_sync_scope(&request,account,advance)?;
        self.ingest_with_policy(crate::legacy_import::OFFLINE_PRODUCER,request,true,false,None,
            Some(Progress::WecomSync(advance)))
    }

    #[cfg(target_os="linux")]
    pub(crate) fn read_claimed_wecom_receipt(&self,request:&IngestRequest,account:&str,
        advance:&crate::wecom_sync::Advance)->Result<Option<(IngestReceipt,crate::wecom_sync::HeadRelation)>> {
        self.require_ordinary()?;
        validate_wecom_sync_scope(request,account,advance)?;
        let producer=crate::legacy_import::OFFLINE_PRODUCER;
        request.validate_for_policy(producer,true)?;
        let digest=ingest_digest(request,None)?;
        let mut conn=self.read_connection()?;
        let tx=conn.transaction()?;
        self.verify_connection(&tx)?;
        let Some((saved,receipt))=lookup_ingest_receipt(&tx,producer,&request.idempotency_key)?
            else {return Ok(None);};
        if saved!=digest.as_slice() {return Err(EventError::Conflict);}
        let relation=advance.claimed_head_relation(&tx)?;
        Ok(Some((receipt,relation)))
    }

    /// Dedicated append-only owner; never merge external raw IDs with these.
    pub(crate) fn import_wechat_detached(&self, request: IngestRequest, account: &str)
        -> Result<IngestReceipt> {
        if !request.messages.iter().all(|row| crate::wechat_detached::is_canonical(row, account)) {
            return Err(EventError::BadRequest("invalid detached WeChat account or identity"));
        }
        self.ingest_with_policy(crate::wechat_detached::PRODUCER, request, true, false, None, None)
    }

    #[cfg(target_os="linux")]
    pub(crate) fn import_claimed_wechat_detached(&self, request: IngestRequest, account: &str,
        claim: &crate::wechat_detached::ClaimedBatch) -> Result<IngestReceipt> {
        validate_claimed_wechat_scope(&request,account,claim)?;
        self.ingest_with_policy(claim.producer(), request, true, false, None,
            Some(Progress::WechatClaim(claim)))
    }

    #[cfg(target_os="linux")]
    pub(crate) fn read_claimed_wechat_receipt(&self,request:&IngestRequest,account:&str,
        claim:&crate::wechat_detached::ClaimedBatch)->Result<Option<IngestReceipt>> {
        self.require_ordinary()?;
        validate_claimed_wechat_scope(request,account,claim)?;
        request.validate_for_policy(claim.producer(),false)?;
        let digest=ingest_digest(request,None)?;
        let mut conn=self.read_connection()?;
        let tx=conn.transaction()?;
        self.verify_connection(&tx)?;
        let Some((saved,receipt))=lookup_ingest_receipt(&tx,claim.producer(),&request.idempotency_key)?
            else {return Ok(None);};
        if saved!=digest.as_slice() {return Err(EventError::Conflict);}
        claim.verify_retry(&tx)?;
        Ok(Some(receipt))
    }

    pub(crate) fn import_telegram(&self, request: IngestRequest) -> Result<IngestReceipt> {
        self.ingest_with_policy(crate::telegram_import::PRODUCER,request,true,false,None,None)
    }

    pub(crate) fn import_eml(&self, request: IngestRequest) -> Result<IngestReceipt> {
        self.ingest_with_policy(crate::eml_import::PRODUCER,request,true,false,None,None)
    }

    pub(crate) fn import_imap_fixture(&self, request: IngestRequest, advance:&crate::imap_fixture::Advance)
        -> Result<IngestReceipt> {
        self.ingest_with_policy(crate::eml_import::PRODUCER,request,true,false,None,
            Some(Progress::Imap(advance)))
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    pub(crate) fn import_imap_readonly(&self, request: IngestRequest, scope: &AccountScope,
        advance: &crate::imap_readonly_import::Advance) -> Result<IngestReceipt> {
        if scope.platform != "email" || request.messages.len() != 1
            || !request.matches_account(scope) || request.messages[0].is_self
        {
            return Err(EventError::BadRequest("IMAP mailbox scope mismatch"));
        }
        // Retain the ordinary EML owner and append-only behavior; do not
        // advertise the import as new provider-sourced event/SSE activity.
        self.ingest_with_policy(crate::eml_import::PRODUCER,request,true,false,None,
            Some(Progress::ReadonlyImap(advance)))
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    pub(crate) fn import_imap_readonly_batch(&self, request:IngestRequest,scope:&AccountScope,
        advance:&crate::imap_readonly_import::Advance)->Result<IngestReceipt> {
        if scope.platform!="email" || !(2..=crate::imap_readonly::MAX_EXPLICIT_UIDS).contains(&request.messages.len())
            || !request.matches_account(scope) || request.messages.iter().any(|row|row.is_self) {
            return Err(EventError::BadRequest("IMAP batch mailbox scope mismatch"));
        }
        self.ingest_with_policy(crate::eml_import::PRODUCER,request,true,false,None,
            Some(Progress::ReadonlyImap(advance)))
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn import_legacy_guarded(&self, request: IngestRequest,
        authorization: &crate::profile_lock::AuthorizedGuardedImport<'_>) -> Result<IngestReceipt> {
        if !std::ptr::eq(self,authorization.archive()) { return Err(EventError::ProfileLocked); }
        authorization.recheck().map_err(|_|EventError::ProfileLocked)?;
        self.ingest_with_policy(crate::legacy_import::OFFLINE_PRODUCER, request, true, true, None, None)
    }

    fn ingest_with_policy(&self, producer: &str, request: IngestRequest, offline: bool,
        guarded: bool, scope: Option<&AccountScope>, progress: Option<Progress<'_>>) -> Result<IngestReceipt> {
        self.ingest_with_policy_and_digest(producer, request, offline, guarded, scope, progress, ingest_digest)
    }

    fn ingest_with_policy_and_digest(&self, producer: &str, request: IngestRequest, offline: bool,
        guarded: bool, scope: Option<&AccountScope>, progress: Option<Progress<'_>>,
        encode_digest: fn(&IngestRequest, Option<&source_progress::Advance>) -> Result<[u8;32]>)
        -> Result<IngestReceipt> {
        let advance = match progress { Some(Progress::Source(advance))=>Some(advance),_=>None };
        if let Some(advance) = advance { advance.validate()?; }
        if guarded != self.guard.is_some() || (guarded && !offline) { return Err(EventError::ProfileLocked); }
        request.validate_for_policy(producer, matches!(progress, Some(Progress::WecomSync(_))))?;
        let digest=encode_digest(&request,advance)?;
        // Refuse unrelated/replaced DBs before opening read-write at all; recheck
        // on the actual writer transaction so aliases cannot select another archive.
        self.verify()?;
        let mut sql = connect(&self.path, true)?;
        // Read and verify markers before setting write-capable pragmas.
        self.verify_connection(&sql)?;
        sql.pragma_update(None, "synchronous", "FULL")?;
        sql.pragma_update(None, "max_page_count", MAX_DATABASE_PAGES)?;
        let tx = sql.transaction_with_behavior(TransactionBehavior::Immediate)?;
        self.verify_connection(&tx)?;
        if guarded && has_v9_snapshot(&tx)? { return Err(EventError::Conflict); }
        if scope.is_some() && !scope_ready(&tx)? { return Err(EventError::ScopeUnavailable); }

        let key = &request.idempotency_key;
        if let Some((previous,receipt))=lookup_ingest_receipt(&tx,producer,key)? {
            return if previous == digest.as_slice() {
                if let Some(advance) = advance {
                    let sequence: i64 = tx.query_row(
                        "SELECT sequence FROM native_source_progress_receipts WHERE producer=? AND idempotency_key=?",
                        (producer,key), |r|r.get(0))?;
                    if sequence != advance.sequence { return Err(EventError::Storage); }
                }
                #[cfg(any(target_os = "linux", target_os = "macos"))]
                if let Some(Progress::ReadonlyImap(advance))=progress { advance.verify_receipt(&tx)?; }
                if let Some(Progress::WecomSync(advance))=progress { advance.verify_retry(&tx)?; }
                #[cfg(target_os="linux")]
                if let Some(Progress::WechatClaim(claim))=progress { claim.verify_retry(&tx)?; }
                Ok(receipt)
            } else {
                Err(EventError::Conflict)
            };
        }

        // Prior exact receipts are read-only replays. Every new ordinary
        // batch (including detached and claimed imports) passes the pause
        // check while holding this same SQLite writer lock.
        if !guarded {crate::ingress_policy::admit(&tx)?;}
        if advance.is_some() && !source_progress::installed(&tx)? {
            tx.execute_batch(source_progress::SCHEMA)?;
        }
        if let Some(advance) = advance {
            let sequence = source_progress::sequence(&tx,producer)?;
            let expected = source_progress::checkpoint(&self.archive_id,producer,sequence);
            if advance.expected_revision != expected.revision || advance.sequence <= sequence {
                return Err(EventError::SourceCheckpointConflict);
            }
        }
        if let Some(Progress::Imap(advance))=progress { advance.check(&tx,true)?; }
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        if let Some(Progress::ReadonlyImap(advance))=progress { advance.check(&tx,true)?; }
        if let Some(Progress::WecomSync(advance))=progress { advance.check(&tx,true)?; }
        #[cfg(target_os="linux")]
        if let Some(Progress::WechatClaim(claim))=progress { claim.check(&tx,true)?; }
        let mut receipt = IngestReceipt {
            created: 0,
            revised: 0,
            unchanged: 0,
            checkpoint: 0,
        };
        let mut touched = BTreeMap::new();
        let projection=if offline && matches!(progress,
            Some(Progress::WecomSync(advance)) if advance.is_claimed_frozen()) {
            FitProjection::FrozenWecom
        } else {FitProjection::Exact};
        // Per-transaction reuse only on the repeated ordinary producer path.
        // The writer is newly opened for this batch; no statement is reusable across requests.
        let reuse = !offline && !guarded && request.messages.len() >= 32;
        for message in &request.messages {
            let event = upsert(&tx, producer, message, offline,projection,reuse)?;
            #[cfg(target_os="linux")]
            if let Some(Progress::WechatClaim(claim))=progress {
                claim.record_reply(&tx,message,event==Some(EventKind::Created))?;
            }
            match event {
                Some(EventKind::Created) => receipt.created += 1,
                Some(EventKind::Revised) => receipt.revised += 1,
                None => receipt.unchanged += 1,
            }
            let Some(event) = event else { continue };
            *touched
                .entry((
                    &message.platform,
                    &message.account_id,
                    &message.conversation_id,
                ))
                .or_insert(0) += i64::from(event == EventKind::Created);
            if !offline && !message.is_self {
                write_repeated(&tx, reuse,
                    "INSERT INTO native_event_journal(kind,platform,account_id,conversation_id,message_id,timestamp)
                     VALUES (?,?,?,?,?,?)",
                    (event.as_str(), &message.platform, &message.account_id,
                     &message.conversation_id, &message.message_id, message.timestamp),
                )?;
            }
        }
        let tip_query = conversation_tip_query(&tx, offline, guarded, !touched.is_empty());
        for ((platform, account, conversation), added) in touched {
            update_conversation(&tx, platform, account, conversation, added, tip_query, reuse)?;
            if matches!(producer,crate::telegram_import::PRODUCER|crate::eml_import::PRODUCER) {
                let kind = if platform == "email" || conversation.starts_with("tg:user:") { "private" } else { "group" };
                let changed = tx.execute(
                    "UPDATE conversations SET kind=? WHERE platform=? AND account_id=? AND id=?
                     AND (kind IS NULL OR kind=?)", (kind,platform,account,conversation,kind))?;
                if changed != 1 { return Err(EventError::Conflict); }
            }
        }
        receipt.checkpoint = checkpoint_for(&tx, scope)?;
        tx.execute(
            "INSERT INTO native_ingest_receipts(producer,idempotency_key,digest,created,revised,unchanged,checkpoint)
             VALUES (?,?,?,?,?,?,?)",
            (producer,key,digest.as_slice(),receipt.created,receipt.revised,receipt.unchanged,receipt.checkpoint),
        )?;
        if let Some(advance) = advance {
            tx.execute("INSERT INTO native_source_progress(producer,sequence) VALUES (?,?)
                        ON CONFLICT(producer) DO UPDATE SET sequence=excluded.sequence",
                (producer,advance.sequence))?;
            tx.execute("INSERT INTO native_source_progress_receipts(producer,idempotency_key,sequence)
                        VALUES (?,?,?)", (producer,key,advance.sequence))?;
        }
        if let Some(Progress::Imap(advance))=progress { advance.record(&tx)?; }
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        if let Some(Progress::ReadonlyImap(advance))=progress { advance.record(&tx)?; }
        if let Some(Progress::WecomSync(advance))=progress { advance.record(&tx)?; }
        #[cfg(target_os="linux")]
        if let Some(Progress::WechatClaim(claim))=progress { claim.record(&tx)?; }
        tx.commit()?;
        Ok(receipt)
    }
}


fn validate_wecom_sync_scope(request:&IngestRequest,account:&str,advance:&crate::wecom_sync::Advance)->Result<()> {
    if !advance.matches_account(account)
        || !request.messages.iter().all(|row|row.platform=="wecom" && row.account_id==account) {
        return Err(EventError::BadRequest("invalid WeCom snapshot scope"));
    }
    Ok(())
}

#[cfg(target_os="linux")]
fn validate_claimed_wechat_scope(request:&IngestRequest,account:&str,
    claim:&crate::wechat_detached::ClaimedBatch)->Result<()> {
    if !claim.matches(account,&request.idempotency_key)
        || !request.messages.iter().all(|row|claim.allows_row(row,account)) {
        return Err(EventError::BadRequest("invalid claimed WeChat scope or receipt"));
    }
    Ok(())
}

fn ingest_digest(request:&IngestRequest,advance:Option<&source_progress::Advance>)->Result<[u8;32]> {
    let encoded = serde_json::to_vec(&request.messages).map_err(|_| EventError::Storage)?;
    if encoded.len() > MAX_BATCH_BYTES {
        return Err(EventError::BadRequest("message batch exceeds byte limit"));
    }
    let digest = if let Some(advance) = advance {
        let progress = serde_json::to_vec(advance).map_err(|_| EventError::Storage)?;
        let mut hash = Sha256::new();
        // Domain separation from legacy receipts whose digest is JSON array only.
        hash.update(b"source-progress-v1\0");
        hash.update(progress);
        hash.update(b"\0");
        hash.update(&encoded);
        hash.finalize()
    } else { Sha256::digest(&encoded) };
    Ok(digest.into())
}

/// Hash the exact compact normalized array used by existing unprogressed receipts,
/// without holding its complete encoding. Batch serializer fragments on the stack.
fn bounded_unprogressed_digest(request: &IngestRequest) -> Result<[u8; 32]> {
    const BLOCK: usize = 8192;
    struct Sink {
        hash: Sha256,
        pending: [u8; BLOCK],
        used: usize,
        len: usize,
    }
    impl io::Write for Sink {
        fn write(&mut self, mut bytes: &[u8]) -> io::Result<usize> {
            let len = bytes.len();
            self.len = self.len.checked_add(len)
                .ok_or_else(|| io::Error::other("normalized JSON size overflow"))?;
            while !bytes.is_empty() {
                if self.used == 0 && bytes.len() >= BLOCK {
                    let full = bytes.len() / BLOCK * BLOCK;
                    self.hash.update(&bytes[..full]);
                    bytes = &bytes[full..];
                } else {
                    let n = bytes.len().min(BLOCK - self.used);
                    self.pending[self.used..self.used + n].copy_from_slice(&bytes[..n]);
                    self.used += n;
                    bytes = &bytes[n..];
                    if self.used == BLOCK {
                        self.hash.update(&self.pending);
                        self.used = 0;
                    }
                }
            }
            Ok(len)
        }

        fn flush(&mut self) -> io::Result<()> {
            if self.used != 0 {
                self.hash.update(&self.pending[..self.used]);
                self.used = 0;
            }
            Ok(())
        }
    }
    let mut sink = Sink { hash: Sha256::new(), pending: [0; BLOCK], used: 0, len: 0 };
    serde_json::to_writer(&mut sink, &request.messages).map_err(|_| EventError::Storage)?;
    if sink.len > MAX_BATCH_BYTES {
        return Err(EventError::BadRequest("message batch exceeds byte limit"));
    }
    io::Write::flush(&mut sink).map_err(|_| EventError::Storage)?;
    Ok(sink.hash.finalize().into())
}

fn lookup_ingest_receipt(conn:&Connection,producer:&str,key:&str)
    ->Result<Option<(Vec<u8>,IngestReceipt)>> {
    Ok(conn.query_row(
        "SELECT digest,created,revised,unchanged,checkpoint FROM native_ingest_receipts
         WHERE producer=? AND idempotency_key=?",
        (producer,key),|row| Ok((row.get(0)?,IngestReceipt {
            created:row.get(1)?,revised:row.get(2)?,unchanged:row.get(3)?,checkpoint:row.get(4)?,
        })),
    ).optional()?)
}

/// Read-only evidence tied to the ordinary legacy import producer. Call only
/// inside an already verified archive read transaction.
pub(crate) struct LegacyImportProof { digest:Vec<u8>, receipt:IngestReceipt }
impl LegacyImportProof {
    pub(crate) fn verify(self,request:&IngestRequest)->Result<IngestReceipt> {
        if self.digest.as_slice()!=bounded_unprogressed_digest(request)?.as_slice() {
            return Err(EventError::Storage);
        }
        Ok(self.receipt)
    }
}
pub(crate) fn legacy_import_proof(conn:&Connection,confirmation:&str)->Result<Option<LegacyImportProof>> {
    Ok(lookup_ingest_receipt(conn,crate::legacy_import::OFFLINE_PRODUCER,confirmation)?
        .map(|(digest,receipt)|LegacyImportProof{digest,receipt}))
}

enum Progress<'a> {
    Source(&'a source_progress::Advance),
    Imap(&'a crate::imap_fixture::Advance),
    WecomSync(&'a crate::wecom_sync::Advance),
    #[cfg(target_os="linux")]
    WechatClaim(&'a crate::wechat_detached::ClaimedBatch),
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    ReadonlyImap(&'a crate::imap_readonly_import::Advance),
}

fn scoped_producer(plugin_id: &str, scope: &AccountScope) -> Result<String> {
    let identity = serde_json::to_vec(&["source-v1", plugin_id, &scope.platform, &scope.account_id])
        .map_err(|_| EventError::Storage)?;
    // '@' is not valid in a configured grant ID.
    Ok(format!("@source-v1:{}", hex::encode(Sha256::digest(identity))))
}

fn id(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_ID_BYTES && !value.chars().any(char::is_control)
}

fn valid_optional(value: Option<&str>, limit: usize) -> bool {
    value.is_none_or(|v| v.len() <= limit && !v.contains('\0'))
}

impl IngestRequest {
    pub(crate) fn matches_account(&self, scope: &AccountScope) -> bool {
        self.messages.iter().all(|m| m.platform == scope.platform && m.account_id == scope.account_id)
    }

    pub(crate) fn validate(&self, producer: &str) -> Result<()> {
        self.validate_for_policy(producer, false)
    }

    fn validate_for_policy(&self, producer: &str, allow_empty: bool) -> Result<()> {
        if !id(producer)
            || !id(&self.idempotency_key)
            || (!allow_empty && self.messages.is_empty())
            || self.messages.len() > MAX_BATCH_COUNT
        {
            return Err(EventError::BadRequest(
                "invalid producer, idempotency key, or batch size",
            ));
        }
        let mut keys = BTreeSet::new();
        let mut bytes = 0;
        for m in &self.messages {
            if !matches!(
                m.platform.as_str(),
                "wechat" | "wecom" | "qq" | "telegram" | "email"
            ) || !id(&m.account_id)
                || !id(&m.conversation_id)
                || !id(&m.message_id)
                || !valid_optional(m.title.as_deref(), MAX_METADATA_BYTES)
                || !valid_optional(m.sender_id.as_deref(), MAX_ID_BYTES)
                || !valid_optional(m.sender_name.as_deref(), MAX_METADATA_BYTES)
                || !valid_optional(m.text.as_deref(), MAX_TEXT_BYTES)
                || m.timestamp < 0
                || !keys.insert((&m.platform, &m.account_id, &m.message_id))
            {
                return Err(EventError::BadRequest(
                    "invalid or repeated normalized message",
                ));
            }
            bytes += m.text.as_ref().map_or(0, String::len)
                + m.title.as_ref().map_or(0, String::len)
                + m.sender_name.as_ref().map_or(0, String::len);
            if bytes > MAX_BATCH_BYTES {
                return Err(EventError::BadRequest("message batch exceeds byte limit"));
            }
        }
        Ok(())
    }
}

#[derive(Clone,Copy)]
pub(crate) enum MessageFit {New, Identical, WouldRevise, Conflict}

#[derive(Clone,Copy)]
enum FitProjection {Exact,FrozenWecom}

fn message_fit(tx:&Connection,producer:&str,m:&NormalizedMessage,projection:FitProjection)
    ->Result<MessageFit>{
    if matches!(projection,FitProjection::FrozenWecom)
        && (producer!=crate::legacy_import::OFFLINE_PRODUCER || m.platform!="wecom") {
        return Err(EventError::BadRequest("invalid frozen WeCom owner or platform"));
    }
    let key = (&m.platform, &m.account_id, &m.message_id);
    let mut lookup=tx.prepare_cached(
        "SELECT o.producer,o.title,m.conversation_id,m.sender_id,m.sender_name,m.timestamp,m.is_self,m.text
         FROM messages m LEFT JOIN native_owners o
         ON m.platform=o.platform AND m.account_id=o.account_id AND m.id=o.message_id
         WHERE m.platform=? AND m.account_id=? AND m.id=?")?;
    let prior=lookup.query_row(key, |r| {
            Ok((r.get::<_, Option<String>>(0)?, NormalizedMessage {
                platform: m.platform.clone(), account_id: m.account_id.clone(), message_id: m.message_id.clone(),
                title: r.get(1)?, conversation_id: r.get(2)?, sender_id: r.get(3)?, sender_name: r.get(4)?,
                timestamp:r.get(5)?, is_self:r.get(6)?, text:r.get(7)?,
            }))
        },
    ).optional()?;
    match prior {
        None=>Ok(MessageFit::New),
        Some((owner,mut old))=>{
            if owner.as_deref()!=Some(producer) || old.conversation_id!=m.conversation_id || old.is_self!=m.is_self {
                return Ok(MessageFit::Conflict);
            }
            // The pinned renderer copies the current conversation display
            // name into every emitted row, but the stock Hub message write
            // does not persist that field. A frozen roster owns the latest view.
            // Ignore only the verified mutable display hints;
            // do not write it back to the stored message or owner.
            if matches!(projection,FitProjection::FrozenWecom){
                old.title.clone_from(&m.title);
                // The renderer separately joins the current sender directory.
                // Only a byte-identical, canonical positive source VID is an
                // unambiguous witness; never infer from a matching label or
                // from the renderer's spelling of a missing sender (None).
                let same_sender=m.sender_id.as_deref().is_some_and(|sender|
                    !sender.starts_with('0')
                    && !sender.is_empty() && sender.len()<=20
                    && sender.bytes().all(|byte|byte.is_ascii_digit())
                    && sender.parse::<u64>().is_ok()
                    && old.sender_id.as_deref()==Some(sender));
                if same_sender{old.sender_name.clone_from(&m.sender_name);}
            }
            if &old==m {Ok(MessageFit::Identical)} else {Ok(MessageFit::WouldRevise)}
        }
    }
}

/// Read-only comparison against exactly the same owner and values used by
/// the frozen WeCom append-only writer. No stored fields escape the lookup.
#[cfg(target_os="linux")]
pub(crate) fn frozen_wecom_message_fit(tx:&Connection,account:&str,message:&NormalizedMessage)
    ->Result<MessageFit>{
    if message.platform!="wecom"||message.account_id!=account {
        return Err(EventError::BadRequest("invalid frozen WeCom account"));
    }
    message_fit(tx,crate::legacy_import::OFFLINE_PRODUCER,message,FitProjection::FrozenWecom)
}

fn write_repeated(
    tx: &Connection, reuse: bool, sql: &str, args: impl rusqlite::Params,
) -> rusqlite::Result<usize> {
    if reuse { tx.prepare_cached(sql)?.execute(args) } else { tx.execute(sql,args) }
}

fn upsert(tx: &Connection, producer: &str, m: &NormalizedMessage, append_only: bool,
    projection:FitProjection, reuse:bool) -> Result<Option<EventKind>> {
    match message_fit(tx,producer,m,projection)? {
        MessageFit::Identical=>Ok(None),
        MessageFit::Conflict=>Err(EventError::Conflict),
        MessageFit::WouldRevise if append_only=>Err(EventError::Conflict),
        MessageFit::WouldRevise=>{
            write_repeated(tx, reuse,
                "UPDATE messages SET sender_id=?,sender_name=?,timestamp=?,text=?
                 WHERE platform=? AND account_id=? AND id=?",
                params![
                    m.sender_id,
                    m.sender_name,
                    m.timestamp,
                    m.text,
                    m.platform,
                    m.account_id,
                    m.message_id
                ],
            )?;
            write_repeated(tx, reuse,
                "UPDATE native_owners SET title=? WHERE platform=? AND account_id=? AND message_id=?",
                params![m.title, m.platform, m.account_id, m.message_id],
            )?;
            Ok(Some(EventKind::Revised))
        }
        MessageFit::New=>{
            write_repeated(tx, reuse,
                "INSERT INTO messages(platform,account_id,id,conversation_id,sender_id,sender_name,timestamp,type,text,is_self)
                 VALUES (?,?,?,?,?,?,?,'text',?,?)",
                params![m.platform,m.account_id,m.message_id,m.conversation_id,m.sender_id,m.sender_name,m.timestamp,m.text,m.is_self],
            )?;
            write_repeated(tx, reuse,
                "INSERT INTO native_owners(platform,account_id,message_id,producer,title) VALUES (?,?,?,?,?)",
                params![m.platform,m.account_id,m.message_id,producer,m.title],
            )?;
            Ok(Some(EventKind::Created))
        }
    }
}

fn conversation_tip_query(
    db: &Connection, offline: bool, guarded: bool, changed: bool,
) -> &'static str {
    if offline || guarded || !changed {
        return CONVERSATION_TIP_SCAN;
    }
    fn definition(prefix: &str) -> Option<&'static str> {
        SCHEMA.find(prefix).and_then(|start| SCHEMA[start..].split_once(';')).map(|(ddl, _)| ddl)
    }
    let (Some(messages), Some(owners)) = (
        definition("CREATE TABLE messages ("), definition("CREATE TABLE native_owners (")
    ) else { return CONVERSATION_TIP_SCAN; };
    let canonical_default = db.query_row(
        "SELECT COUNT(*) FROM main.sqlite_schema
         WHERE (type='table' AND name='messages' AND sql=?1)
            OR (type='table' AND name='native_owners' AND sql=?2)
            OR (type='index' AND name='native_messages_scope' AND sql=?3)",
        (messages, owners, DEFAULT_MESSAGE_SCOPE_INDEX_SQL),
        |row| row.get::<_, i64>(0),
    );
    match canonical_default {
        Ok(3) => return CONVERSATION_TIP_SEEK,
        Ok(2) => {}
        _ => return CONVERSATION_TIP_SCAN,
    }
    let canonical_history = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM main.sqlite_schema
                 WHERE type='index' AND name='native_messages_scope_history' AND sql=?1)
         AND NOT EXISTS(SELECT 1 FROM main.sqlite_schema WHERE name='native_messages_scope')",
        [HISTORY_MESSAGE_SCOPE_INDEX_SQL],
        |row| row.get::<_, bool>(0),
    );
    if matches!(canonical_history, Ok(true)) {
        CONVERSATION_TIP_HISTORY_MAX
    } else {
        CONVERSATION_TIP_SCAN
    }
}

const CONVERSATION_TIP_SCAN: &str =
    "SELECT o.title,m.timestamp,m.text FROM messages m
     JOIN native_owners o ON m.platform=o.platform AND m.account_id=o.account_id AND m.id=o.message_id
     WHERE m.platform=?1 AND m.account_id=?2 AND m.conversation_id=?3
     ORDER BY m.timestamp DESC,m.rowid DESC LIMIT 1";

// The canonical default index ends in timestamp then implicit rowid.
// Equality on the newest owned timestamp permits a scoped reverse rowid
// seek without sorting a whole tied bucket. Both probes must join owners:
// an orphan above/in that bucket cannot supply the conversation tip.
const CONVERSATION_TIP_SEEK: &str =
    "SELECT o.title,m.timestamp,m.text FROM messages m INDEXED BY native_messages_scope
     CROSS JOIN native_owners o ON m.platform=o.platform AND m.account_id=o.account_id AND m.id=o.message_id
     WHERE m.platform=?1 AND m.account_id=?2 AND m.conversation_id=?3 AND m.timestamp=(
       SELECT newest.timestamp FROM messages newest INDEXED BY native_messages_scope
       CROSS JOIN native_owners owner ON newest.platform=owner.platform AND newest.account_id=owner.account_id
         AND newest.id=owner.message_id
       WHERE newest.platform=?1 AND newest.account_id=?2 AND newest.conversation_id=?3
       ORDER BY newest.timestamp DESC LIMIT 1
     )
     ORDER BY m.rowid DESC LIMIT 1";

// Explicit ID precedes rowid in the expanded history index. Read the
// covered owned timestamp bucket to preserve the original rowid winner
// without sorting or fetching any losing payload. The scalar probes each
// require an owner; the final lookup runs in the same writer transaction.
const CONVERSATION_TIP_HISTORY_MAX: &str =
    "SELECT o.title,m.timestamp,m.text FROM messages m
     CROSS JOIN native_owners o ON m.platform=o.platform AND m.account_id=o.account_id AND m.id=o.message_id
     WHERE m.rowid=(
       SELECT MAX(tied.rowid) FROM messages tied INDEXED BY native_messages_scope_history
       CROSS JOIN native_owners tied_owner ON tied.platform=tied_owner.platform
         AND tied.account_id=tied_owner.account_id AND tied.id=tied_owner.message_id
       WHERE tied.platform=?1 AND tied.account_id=?2 AND tied.conversation_id=?3 AND tied.timestamp=(
         SELECT newest.timestamp FROM messages newest INDEXED BY native_messages_scope_history
         CROSS JOIN native_owners owner ON newest.platform=owner.platform
           AND newest.account_id=owner.account_id AND newest.id=owner.message_id
         WHERE newest.platform=?1 AND newest.account_id=?2 AND newest.conversation_id=?3
         ORDER BY newest.timestamp DESC LIMIT 1
       )
     )";

fn update_conversation(
    tx: &Connection,
    platform: &str,
    account: &str,
    conversation: &str,
    added: i64,
    tip_query: &str,
    reuse: bool,
) -> Result<()> {
    let scope = (platform, account, conversation);
    let tip = |row: &rusqlite::Row<'_>| Ok((row.get(0)?,row.get(1)?,row.get(2)?));
    let (title, timestamp, text): (Option<String>, i64, Option<String>) = if reuse {
        tx.prepare_cached(tip_query)?.query_row(scope, tip)?
    } else {
        tx.query_row(tip_query, scope, tip)?
    };
    write_repeated(tx, reuse,
        "INSERT INTO conversations(platform,account_id,id,title,last_timestamp,last_message,message_count)
         VALUES (?,?,?,?,?,?,?)
         ON CONFLICT(platform,account_id,id) DO UPDATE SET title=COALESCE(excluded.title,conversations.title),
         last_timestamp=excluded.last_timestamp,last_message=excluded.last_message,message_count=conversations.message_count+excluded.message_count",
        params![platform,account,conversation,title,timestamp,text,added],
    )?;
    Ok(())
}

fn event_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<MessageEvent> {
    let kind = match row.get::<_, String>(1)?.as_str() {
        "created" => EventKind::Created,
        "revised" => EventKind::Revised,
        _ => return Err(rusqlite::Error::InvalidQuery),
    };
    Ok(MessageEvent {
        event_id: row.get(0)?,
        kind,
        platform: row.get(2)?,
        account_id: row.get(3)?,
        conversation_id: row.get(4)?,
        message_id: row.get(5)?,
        timestamp: row.get(6)?,
    })
}

fn has_v9_snapshot(db:&Connection) -> Result<bool> {
    Ok(db.query_row("SELECT EXISTS(SELECT 1 FROM native_ingest_receipts WHERE producer=?)",
        [GUARDED_V9_PRODUCER],|r|r.get(0))?)
}

fn incoming_ready(db: &Connection) -> Result<bool> {
    let found: i64 = db.query_row(
        "SELECT COUNT(*) FROM sqlite_schema
         WHERE (type='table' AND name='native_account_incoming')
            OR (type='trigger' AND name='native_account_incoming_record')", [], |r| r.get(0))?;
    Ok(found == 2)
}

fn scope_ready(db: &Connection) -> Result<bool> {
    let found: i64 = db.query_row(
        "SELECT COUNT(*) FROM sqlite_schema
         WHERE (type='table' AND name='native_account_event_sequence')
            OR (type='trigger' AND name='native_account_event_record')",
        [], |r| r.get(0),
    )?;
    Ok(found == 2)
}

fn checkpoint_for(db: &Connection, scope: Option<&AccountScope>) -> Result<i64> {
    if let Some(scope) = scope {
        if !scope_ready(db)? { return Err(EventError::ScopeUnavailable); }
        Ok(db.query_row(
            "SELECT COALESCE(MAX(account_event_id),0) FROM native_account_event_sequence
             WHERE platform=? AND account_id=?",
             (&scope.platform,&scope.account_id), |r| r.get(0),
        )?)
    } else {
        checkpoint(db)
    }
}

fn checkpoint(sql: &Connection) -> Result<i64> {
    Ok(sql.query_row(
        "SELECT COALESCE(MAX(event_id),0) FROM native_event_journal",
        [],
        |r| r.get(0),
    )?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn private_dir() -> TempDir {
        let dir = tempfile::tempdir().unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(dir.path(), fs::Permissions::from_mode(0o700)).unwrap();
        }
        dir
    }

    fn initialized() -> (TempDir, EventStore) {
        let dir = private_dir();
        let store = EventStore::init(dir.path()).unwrap();
        (dir, store)
    }

    fn row(id: &str, timestamp: i64, content: &str) -> NormalizedMessage {
        NormalizedMessage {
            platform: "wechat".into(),
            account_id: "fixture-account".into(),
            conversation_id: "fixture-conversation".into(),
            message_id: id.into(),
            title: Some("private-title".into()),
            sender_id: Some("private-sender-id".into()),
            sender_name: Some("private-sender-name".into()),
            timestamp,
            is_self: false,
            text: Some(content.into()),
        }
    }

    fn request(key: &str, messages: Vec<NormalizedMessage>) -> IngestRequest {
        IngestRequest {
            idempotency_key: key.into(),
            messages,
        }
    }

    #[test]
    fn ordinary_cached_batch_matches_single_writes_and_all_rollbacks() {
        let (_batch_dir, batch) = initialized();
        let (_single_dir, single) = initialized();
        let older = row("older", 1, "first");
        let newest = row("newest", 3, "next");
        let mut own = row("own", 9, "self");
        own.is_self = true;
        own.conversation_id = "other-room".into();
        let mut foreign = row("older", 10, "foreign");
        foreign.account_id = "other-account".into();
        let filler: Vec<_> = (0..28).map(|i| {
            let mut message = row(&format!("filler-{i}"), 0, "stable");
            message.is_self = true;
            message.conversation_id = "filler-room".into();
            message
        }).collect();
        let mut seed = vec![older.clone(), newest.clone(), own.clone(), foreign.clone()];
        seed.extend(filler.iter().cloned());
        let receipt = batch.ingest("fixture", request("seed", seed.clone())).unwrap();
        assert_eq!(receipt, IngestReceipt { created:32, revised:0, unchanged:0, checkpoint:3 });
        for m in seed {
            let key = format!("seed-{}-{}",m.account_id,m.message_id);
            single.ingest("fixture", request(&key, vec![m])).unwrap();
        }
        let mut revised = older;
        revised.text = Some("revised".into());
        revised.title = Some("later-title".into());
        revised.sender_name = None;
        revised.timestamp = 3;
        let mut revised_own = own;
        revised_own.text = None;
        revised_own.sender_id = None;
        let mut updates = vec![revised, newest.clone(), revised_own, foreign];
        updates.extend(filler);
        let equal = request("updates", updates.clone());
        let receipt = batch.ingest("fixture", equal.clone()).unwrap();
        assert_eq!(receipt, IngestReceipt { created:0, revised:2, unchanged:30, checkpoint:4 });
        assert_eq!(batch.ingest("fixture", equal).unwrap(), receipt);
        for m in updates {
            let key = format!("update-{}-{}",m.account_id,m.message_id);
            single.ingest("fixture", request(&key, vec![m])).unwrap();
        }
        let mut conflict = newest;
        conflict.conversation_id = "not-movable".into();
        let mut rollback: Vec<_> = (0..31).map(|i| row(&format!("partial-{i}"),99,"no")).collect();
        rollback.push(conflict);
        assert!(matches!(batch.ingest("fixture", request("abort", rollback)),
            Err(EventError::Conflict)));
        assert_eq!(batch.replay(0,100).unwrap(), single.replay(0,100).unwrap());
        let projection = |archive: &EventStore, sql: &str| {
            let db = archive.read_connection().unwrap();
            let mut stmt = db.prepare(sql).unwrap();
            stmt.query_map([], |row| row.get::<_,String>(0)).unwrap()
                .collect::<rusqlite::Result<Vec<_>>>().unwrap()
        };
        for sql in [
            "SELECT json_array(platform,account_id,id,conversation_id,sender_id,sender_name,timestamp,text,is_self)
               FROM messages ORDER BY rowid",
            "SELECT json_array(platform,account_id,id,title,last_timestamp,last_message,message_count)
               FROM conversations ORDER BY platform,account_id,id",
            "SELECT json_array(platform,account_id,message_id,producer,title)
               FROM native_owners ORDER BY platform,account_id,message_id",
            "SELECT json_array(platform,account_id,account_event_id,event_id)
               FROM native_account_event_sequence ORDER BY platform,account_id,account_event_id",
        ] {
            assert_eq!(projection(&batch,sql),projection(&single,sql),"{sql}");
        }
    }

    #[test]
    fn history_index_is_opt_in_for_fresh_ordinary_archives_only() {
        fn schema_index(store: &EventStore) -> (String, String) {
            store.read_connection().unwrap().query_row(
                "SELECT name,sql FROM sqlite_schema
                 WHERE type='index' AND name IN ('native_messages_scope','native_messages_scope_history')",
                [], |r| Ok((r.get(0)?, r.get(1)?)),
            ).unwrap()
        }
        let (old_dir, old) = initialized();
        let old_index = schema_index(&old);
        assert_eq!(old_index.0, "native_messages_scope");
        assert!(matches!(EventStore::init_with_history_index(old_dir.path()), Err(EventError::UnsafeDirectory)));
        assert_eq!(schema_index(&EventStore::open(old_dir.path()).unwrap()), old_index);

        let fresh_dir = private_dir();
        let fresh = EventStore::init_with_history_index(fresh_dir.path()).unwrap();
        let full_index = schema_index(&fresh);
        assert_eq!(full_index.0, "native_messages_scope_history");
        assert!(full_index.1.ends_with("timestamp DESC, id DESC)"));
        assert_eq!(fresh.read_connection().unwrap().query_row(
            "SELECT count(*) FROM sqlite_schema WHERE name='native_messages_scope'", [], |r| r.get::<_, i64>(0)
        ).unwrap(), 0);
        assert_eq!(schema_index(&EventStore::open(fresh_dir.path()).unwrap()), full_index);

        let gated_dir = private_dir();
        let guarded = EventStore::init_guarded(gated_dir.path(), "synthetic history fixture only").unwrap();
        assert_eq!(schema_index(&guarded), old_index);
    }

    #[test]
    fn context_seek_falls_back_when_conversation_metadata_can_multiply_rows() {
        let (_dir, store) = initialized();
        let mut db = connect(store.path(), true).unwrap();
        let tx = db.transaction().unwrap();
        assert!(store.has_canonical_context_order(&tx));
        tx.execute_batch("DROP TABLE conversations;
            CREATE TABLE conversations(
                platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                title TEXT, kind TEXT, avatar_url TEXT);
            INSERT INTO conversations VALUES('qq','account','room','first','group',NULL),
                                          ('qq','account','room','second','group',NULL);
            INSERT INTO messages(platform,account_id,id,conversation_id,timestamp)
              VALUES ('qq','account','mid','room',100);").unwrap();
        let matches: i64 = tx.query_row(
            "SELECT COUNT(*) FROM messages m LEFT JOIN conversations c
             ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id",
            [], |r| r.get(0),
        ).unwrap();
        assert_eq!(matches, 2);
        assert!(!store.has_canonical_context_order(&tx));
    }

    #[test]
    fn conversation_tip_optimized_layouts_keep_owned_rowid_order_and_fail_closed() {
        for (expanded, expected) in [(false, CONVERSATION_TIP_SEEK), (true, CONVERSATION_TIP_HISTORY_MAX)] {
            let dir = private_dir();
            let store = if expanded { EventStore::init_with_history_index(dir.path()).unwrap() }
                        else { EventStore::init(dir.path()).unwrap() };
            let mut db = connect(store.path(), true).unwrap();
            let tx = db.transaction().unwrap();
            assert_eq!(conversation_tip_query(&tx, false, false, true), expected);
            for (offline, guarded, changed) in [(true,false,true),(false,true,true),(false,false,false)] {
                assert_eq!(conversation_tip_query(&tx, offline, guarded, changed), CONVERSATION_TIP_SCAN);
            }
            tx.execute_batch("INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,text)
                VALUES ('wechat','fixture-account','zzz','fixture-conversation',100,'earlier row'),
                       ('wechat','fixture-account','aaa','fixture-conversation',100,NULL),
                       ('wechat','fixture-account','orphan','fixture-conversation',200,'orphan above'),
                       ('wechat','elsewhere','foreign','fixture-conversation',100,'foreign'),
                       ('wechat','fixture-account','orphan-tie','fixture-conversation',100,'orphan within');
                INSERT INTO native_owners VALUES ('wechat','fixture-account','zzz','owner','earlier title'),
                     ('wechat','fixture-account','aaa','owner',NULL),('wechat','elsewhere','foreign','owner','foreign');").unwrap();
            let scope = ("wechat","fixture-account","fixture-conversation");
            type Tip = (Option<String>, i64, Option<String>);
            let tip = |sql| -> Tip {
                tx.query_row(sql, scope, |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?))).unwrap()
            };
            assert_eq!(tip(CONVERSATION_TIP_SCAN), (None, 100, None));
            assert_eq!(tip(CONVERSATION_TIP_SCAN), tip(expected));
            tx.execute("UPDATE messages SET timestamp=99 WHERE id='aaa'", []).unwrap();
            assert_eq!(tip(expected), (Some("earlier title".into()), 100, Some("earlier row".into())));
            assert_eq!(tip(CONVERSATION_TIP_SCAN), tip(expected));
            assert!(matches!(tx.query_row(expected, ("wechat","fixture-account","unknown"),
                |r| r.get::<_, Option<String>>(0)), Err(rusqlite::Error::QueryReturnedNoRows)));
            let name = if expanded {"native_messages_scope_history"} else {"native_messages_scope"};
            tx.execute_batch(&format!("DROP INDEX {name};
                CREATE INDEX {name} ON messages(timestamp);")).unwrap();
            assert_eq!(conversation_tip_query(&tx, false, false, true), CONVERSATION_TIP_SCAN);
            tx.rollback().unwrap();
        }
    }

    #[test]
    fn contact_scope_fast_path_requires_canonical_ordinary_layout() {
        let (_dir, ordinary) = initialized();
        let db = ordinary.read_connection().unwrap();
        assert!(ordinary.has_canonical_contacts(&db));
        for collation in ["COLLATE NOCASE", "COLLATE RTRIM", ""] {
            let alternate = Connection::open_in_memory().unwrap();
            alternate.execute_batch(&format!(
                "CREATE TABLE contacts (platform TEXT NOT NULL, account_id BLOB {collation} NOT NULL,
                 id TEXT NOT NULL, name TEXT, added INTEGER, PRIMARY KEY(platform, account_id, id));"
            )).unwrap();
            assert!(!ordinary.has_canonical_contacts(&alternate));
        }
        let guarded_dir = private_dir();
        let guarded = EventStore::init_guarded(guarded_dir.path(), "synthetic contacts gate").unwrap();
        assert!(!guarded.has_canonical_contacts(&guarded.read_connection().unwrap()));
    }

    #[test]
    fn conversation_index_is_additive_and_independent_of_history_index() {
        fn index_names(store: &EventStore) -> BTreeSet<String> {
            let db = store.read_connection().unwrap();
            let mut stmt = db.prepare("SELECT name FROM sqlite_schema WHERE type='index'").unwrap();
            stmt.query_map([], |r| r.get(0)).unwrap().collect::<rusqlite::Result<_>>().unwrap()
        }
        for history in [false, true] {
            for conversations in [false, true] {
                let dir = private_dir();
                let layout = ArchiveIndexes { history, conversations };
                let store = EventStore::init_with_indexes(dir.path(), layout).unwrap();
                let names = index_names(&store);
                assert!(names.contains("native_conversations_scope"));
                assert_eq!(names.contains("native_conversations_account_order"), conversations);
                assert_eq!(names.contains("native_messages_scope_history"), history);
                assert_eq!(names.contains("native_messages_scope"), !history);
                if conversations {
                    let db = store.read_connection().unwrap();
                    let mut stmt = db.prepare("SELECT name,desc FROM pragma_index_xinfo(
                        'native_conversations_account_order') WHERE key=1 ORDER BY seqno").unwrap();
                    let columns: Vec<(String, bool)> = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
                        .unwrap().collect::<rusqlite::Result<_>>().unwrap();
                    assert_eq!(columns, vec![
                        ("platform".into(), false), ("account_id".into(), false),
                        ("last_timestamp".into(), true), ("id".into(), false),
                    ]);
                }
                let opposite = ArchiveIndexes { history: !history, conversations: !conversations };
                assert!(matches!(EventStore::init_with_indexes(dir.path(), opposite), Err(EventError::UnsafeDirectory)));
                assert_eq!(index_names(&EventStore::open(dir.path()).unwrap()), names);
            }
        }
        let guarded_dir = private_dir();
        let guarded = EventStore::init_guarded(guarded_dir.path(), "synthetic conversation fixture only").unwrap();
        let names = index_names(&guarded);
        assert!(names.contains("native_conversations_scope"));
        assert!(names.contains("native_messages_scope"));
        assert!(!names.contains("native_conversations_account_order"));
        assert!(!names.contains("native_messages_scope_history"));
    }

    #[test]
    fn scoped_receipts_distinguish_trusted_identity_scope_and_reused_key() {
        let (_dir, store) = initialized();
        let a = row("same-id", 100, "a");
        let mut b = a.clone();
        b.account_id = "B".into();
        let scope = |m: &NormalizedMessage| AccountScope {
            platform: m.platform.clone(), account_id: m.account_id.clone()
        };
        let sa = scope(&a);
        let sb = scope(&b);
        let first = store.ingest("source.plugin", request("same-key", vec![a.clone()])).unwrap();
        assert_eq!(first.created, 1);
        assert!(matches!(store.ingest_scoped("source.plugin", &sa, request("same-key", vec![a.clone()])),
            Err(EventError::Conflict)));
        assert!(matches!(store.ingest_scoped("source.plugin", &sa, request("mixed", vec![a,b.clone()])),
            Err(EventError::BadRequest("scope mismatch"))));
        let local = store.ingest_scoped("source.plugin", &sb, request("same-key", vec![b.clone()])).unwrap();
        assert_eq!(local.checkpoint, 1);
        assert_eq!(store.checkpoint().unwrap(),2);
        assert_eq!(store.ingest_scoped("source.plugin", &sb, request("same-key", vec![b])).unwrap(),local);
    }

    #[test]
    fn reconnect_at_empty_checkpoint_yields_committed_revision_after_restart() {
        let (dir, store) = initialized();
        let first = row("m1", 100, "private-content");
        let batch = request("first", vec![first.clone()]);
        assert_eq!(
            store.replay(0, 100).unwrap(),
            EventPage {
                items: vec![],
                next_after: 0,
                has_more: false,
                checkpoint: 0,
            }
        );
        assert_eq!(
            store.ingest("alpha", batch.clone()).unwrap(),
            IngestReceipt {
                created: 1,
                revised: 0,
                unchanged: 0,
                checkpoint: 1,
            }
        );
        let initial = store.replay(0, 1).unwrap();
        assert_eq!(initial.items[0].kind, EventKind::Created);
        assert_eq!(initial.items[0].event_id, 1);
        assert_eq!(initial.next_after, 1);
        assert_eq!(
            store.replay(initial.next_after, 1).unwrap(),
            EventPage {
                items: vec![],
                next_after: 1,
                has_more: false,
                checkpoint: 1,
            }
        );
        let archive_id = store.archive_id().to_owned();
        drop(store);
        let store = EventStore::open(dir.path()).unwrap();
        assert_eq!(store.archive_id(), archive_id);
        let mut revised = first;
        revised.text = Some("revised-private-content".into());
        revised.timestamp = 110;
        assert_eq!(
            store
                .ingest("alpha", request("second", vec![revised.clone()]))
                .unwrap(),
            IngestReceipt {
                created: 0,
                revised: 1,
                unchanged: 0,
                checkpoint: 2,
            }
        );
        let replay = store.replay(initial.next_after, 100).unwrap();
        assert_eq!(replay.items[0].kind, EventKind::Revised);
        assert_eq!(replay.items[0].message_id, "m1");
        assert_eq!(replay.items[0].timestamp, 110);
        assert_eq!(replay.next_after, 2);
        let serialized = serde_json::to_string(&replay).unwrap();
        for private in [
            "private-content",
            "revised-private-content",
            "private-title",
            "private-sender",
            "\"text\"",
        ] {
            assert!(!serialized.contains(private));
        }
        // Replaying a previously committed request cannot revert a newer revision.
        assert_eq!(store.ingest("alpha", batch).unwrap().checkpoint, 1);
        let unchanged = store
            .ingest("alpha", request("no-op", vec![revised]))
            .unwrap();
        assert_eq!(
            unchanged,
            IngestReceipt {
                created: 0,
                revised: 0,
                unchanged: 1,
                checkpoint: 2
            }
        );
        assert_eq!(store.checkpoint().unwrap(), 2);
        let reader = store.read_connection().unwrap();
        let projected: (String, i64, i64) = reader
            .query_row(
                "SELECT last_message,last_timestamp,message_count FROM conversations",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(projected, ("revised-private-content".into(), 110, 1));
        assert!(matches!(store.replay(3, 1), Err(EventError::BadRequest(_))));
        assert!(matches!(
            store.replay(-1, 1),
            Err(EventError::BadRequest(_))
        ));
        assert!(matches!(
            store.replay(0, 101),
            Err(EventError::BadRequest(_))
        ));
    }

    #[test]
    fn producer_scope_and_batch_conflict_do_not_leave_partial_index_or_journal() {
        let (_dir, store) = initialized();
        let a = row("existing", 100, "first");
        store
            .ingest("A", request("equal-key", vec![a.clone()]))
            .unwrap();
        let mut changed = a.clone();
        changed.text = Some("attempted change".into());
        assert!(matches!(
            store.ingest("A", request("equal-key", vec![changed.clone()])),
            Err(EventError::Conflict)
        ));
        let b = row("new", 101, "would be written first");
        assert!(matches!(
            store.ingest("B", request("equal-key", vec![b.clone(), changed.clone()])),
            Err(EventError::Conflict)
        ));
        assert_eq!(store.checkpoint().unwrap(), 1);
        assert_eq!(
            store
                .read_connection()
                .unwrap()
                .query_row("SELECT COUNT(*) FROM messages", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        assert_eq!(
            store
                .ingest("B", request("equal-key", vec![b]))
                .unwrap()
                .checkpoint,
            2
        );
        let mut moved = changed.clone();
        moved.conversation_id = "different".into();
        assert!(matches!(
            store.ingest("A", request("move", vec![moved])),
            Err(EventError::Conflict)
        ));
        changed.is_self = true;
        assert!(matches!(
            store.ingest("A", request("flip", vec![changed])),
            Err(EventError::Conflict)
        ));
        let mut separate_account = a;
        separate_account.account_id = "different".into();
        assert_eq!(
            store
                .ingest("B", request("independent-scope", vec![separate_account]))
                .unwrap()
                .checkpoint,
            3
        );
        let p1 = store.replay(0, 1).unwrap();
        let p2 = store.replay(p1.next_after, 1).unwrap();
        let p3 = store.replay(p2.next_after, 1).unwrap();
        assert_eq!([p1.next_after, p2.next_after, p3.next_after], [1, 2, 3]);
        assert!(!p3.has_more && p2.has_more && p1.has_more);
    }

    #[test]
    fn oversized_and_own_messages_follow_contract() {
        let (_dir, store) = initialized();
        let mut own = row("self", 1, "message");
        own.is_self = true;
        let result = store.ingest("alpha", request("self", vec![own])).unwrap();
        assert_eq!(result.created, 1);
        assert_eq!(result.checkpoint, 0);
        let oversize = row("huge", 1, &"x".repeat(MAX_TEXT_BYTES + 1));
        assert!(matches!(
            store.ingest("alpha", request("oversize", vec![oversize])),
            Err(EventError::BadRequest(_))
        ));
        let records = (0..10)
            .map(|i| row(&format!("row{i}"), 1, &"x".repeat(MAX_TEXT_BYTES)))
            .collect();
        assert!(matches!(
            store.ingest("alpha", request("too many bytes", records)),
            Err(EventError::BadRequest(_))
        ));
        let duplicate = row("duplicate", 1, "message");
        assert!(matches!(
            store.ingest(
                "alpha",
                request("duplicates", vec![duplicate.clone(), duplicate])
            ),
            Err(EventError::BadRequest(_))
        ));
        assert_eq!(store.checkpoint().unwrap(), 0);
    }

    #[test]
    fn never_writes_legacy_or_replaced_directory() {
        let dir = private_dir();
        fs::write(dir.path().join("settings.json"), b"{}").unwrap();
        assert!(matches!(
            EventStore::init(dir.path()),
            Err(EventError::UnsafeDirectory)
        ));
        assert!(!dir.path().join(NATIVE_DATABASE).exists());
        let (native, original) = initialized();
        let (other, different_instance) = initialized();
        assert_ne!(original.archive_id(), different_instance.archive_id());
        fs::copy(different_instance.path(), original.path()).unwrap();
        assert!(matches!(
            original.verify(),
            Err(EventError::UnsafeDirectory)
        ));
        assert!(matches!(
            original.ingest("alpha", request("blocked", vec![row("new", 1, "blocked")])),
            Err(EventError::UnsafeDirectory)
        ));
        assert_eq!(
            EventStore::open(native.path())
                .unwrap()
                .checkpoint()
                .unwrap(),
            0
        );
        assert_eq!(
            EventStore::open(other.path())
                .unwrap()
                .checkpoint()
                .unwrap(),
            0
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_directories_and_databases_are_refused() {
        use std::os::unix::fs::symlink;
        let (_original, store) = initialized();
        let dir = private_dir();
        let redirect_dir = dir.path().join("redirect");
        symlink(store.path().parent().unwrap(), &redirect_dir).unwrap();
        assert!(matches!(
            EventStore::open(&redirect_dir),
            Err(EventError::UnsafeDirectory)
        ));
        fs::remove_file(redirect_dir).unwrap();
        symlink(store.path(), dir.path().join(NATIVE_DATABASE)).unwrap();
        assert!(matches!(
            EventStore::open(dir.path()),
            Err(EventError::UnsafeDirectory)
        ));
    }
}
