//! Explicit, read-only projection of a detached legacy index into a fresh file.
use crate::snapshot::{Snapshot, SnapshotError};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read},
    path::Path,
    time::{Duration, Instant},
};

const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const SOURCE_TABLES: &[&str] = &[
    "contacts", "conversations", "messages", "conversation_metadata", "contact_identities",
    "sync_checkpoints", "import_jobs", "mcp_operations", "operation_log",
    "moments_catalog", "moments_posts", "moments_media", "messages_fts",
    "messages_fts_config", "messages_fts_content", "messages_fts_data",
    "messages_fts_docsize", "messages_fts_idx", "sqlite_sequence", "sqlite_stat1",
    "sqlite_stat4",
];
const PROJECTIONS: &[(&str, &str)] = &[
    ("contacts", "platform,account_id,id,name,avatar_url,kind,extra"),
    (
        "conversations",
        "platform,account_id,id,title,kind,avatar_url,member_count,last_message,last_timestamp,message_count,extra",
    ),
    (
        "messages",
        "platform,account_id,id,conversation_id,sender_id,sender_name,timestamp,type,text,is_self,media_url,file_name,raw_type,preview,extra",
    ),
    ("conversation_metadata", "platform,account_id,conversation_id,note,tags,archived,updated_at"),
    ("contact_identities", "platform,account_id,contact_id,identity_id,updated_at"),
];

#[derive(Debug, thiserror::Error)]
pub enum ProjectError {
    #[error("{0}")]
    Snapshot(#[from] SnapshotError),
    #[error("source is not a supported detached Hub v9 index")]
    Unsupported,
    #[error("source changed during the projection")]
    Changed,
    #[error("target must be an existing empty private directory")]
    Target,
    #[error("could not read or write the isolated index")]
    Io(#[from] io::Error),
    #[error("could not validate or project the isolated SQLite index")]
    Sql(#[from] rusqlite::Error),
}

#[derive(Serialize)]
pub struct Projection {
    pub source_sha256: String,
    pub output_sha256: String,
    pub rows: BTreeMap<&'static str, usize>,
}

pub(crate) fn fingerprint(path: &Path) -> Result<String, ProjectError> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || !(100..=MAX_FILE_BYTES).contains(&meta.len()) {
        return Err(ProjectError::Unsupported);
    }
    let mut file = fs::File::open(path)?;
    let mut header = [0_u8; 16];
    file.read_exact(&mut header)?;
    if &header != b"SQLite format 3\0" {
        return Err(ProjectError::Unsupported);
    }
    let mut digest = Sha256::new();
    digest.update(header);
    let mut chunk = [0_u8; 65_536];
    let mut bytes = 16;
    loop {
        let n = file.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        bytes += n as u64;
        if bytes > MAX_FILE_BYTES {
            return Err(ProjectError::Unsupported);
        }
        digest.update(&chunk[..n]);
    }
    if bytes != meta.len() {
        return Err(ProjectError::Changed);
    }
    Ok(hex::encode(digest.finalize()))
}

pub(crate) fn validate_source(db: &Connection) -> Result<(), ProjectError> {
    let version: i64 = db.query_row("PRAGMA source.user_version", [], |row| row.get(0))?;
    let application: i64 = db.query_row("PRAGMA source.application_id", [], |row| row.get(0))?;
    if version != 9 || application != 0 {
        return Err(ProjectError::Unsupported);
    }
    let mut statement = db.prepare(
        "SELECT name,type FROM pragma_table_list WHERE schema='source' AND name!='sqlite_schema'",
    )?;
    let objects = statement.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
    let mut ordinary = std::collections::BTreeSet::new();
    for row in objects {
        let (name, kind) = row?;
        if !SOURCE_TABLES.contains(&name.as_str())
            || !(kind == "table" || (kind == "virtual" && name == "messages_fts")
                || (kind == "shadow" && name.starts_with("messages_fts_")))
        {
            return Err(ProjectError::Unsupported);
        }
        if kind == "table" {
            ordinary.insert(name);
        }
    }
    for &(name, selected) in PROJECTIONS {
        if !ordinary.contains(name) {
            return Err(ProjectError::Unsupported);
        }
        let sql = format!("PRAGMA source.table_xinfo({name})");
        let mut schema = db.prepare(&sql)?;
        let columns = schema.query_map([], |r| {
            Ok((r.get::<_, String>(1)?, (r.get::<_, String>(2)?, r.get::<_, bool>(3)?,
                r.get::<_, u8>(5)?, r.get::<_, u8>(6)?)))
        })?.collect::<rusqlite::Result<BTreeMap<_, _>>>()?;
        let primary = match name {
            "conversation_metadata" => "conversation_id",
            "contact_identities" => "contact_id",
            _ => "id",
        };
        if columns.len() != selected.split(',').count() {
            return Err(ProjectError::Unsupported);
        }
        for c in selected.split(',') {
            let (affinity, required, pk, hidden) = columns.get(c).ok_or(ProjectError::Unsupported)?;
            let expected = if ["timestamp","member_count","last_timestamp","message_count","is_self",
                "archived","updated_at"].contains(&c) {"INTEGER"} else {"TEXT"};
            let position = match c { "platform" => 1, "account_id" => 2, _ if c == primary => 3, _ => 0 };
            if affinity != expected || *hidden != 0 || *pk != position || (position != 0 && !required) {
                return Err(ProjectError::Unsupported);
            }
        }
    }
    check(db, "PRAGMA source.quick_check(1)")?;
    Ok(())
}

fn check(db: &Connection, pragma: &str) -> Result<(), ProjectError> {
    let result: String = db.query_row(pragma, [], |row| row.get(0))?;
    if result == "ok" { Ok(()) } else { Err(ProjectError::Unsupported) }
}

fn private_empty(directory: &Path) -> Result<(), ProjectError> {
    let info = fs::symlink_metadata(directory)?;
    if !info.is_dir() {
        return Err(ProjectError::Target);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if info.permissions().mode() & 0o077 != 0 {
            return Err(ProjectError::Target);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if info.file_attributes() & 0x400 != 0 {
            return Err(ProjectError::Target);
        }
    }
    if fs::read_dir(directory)?.next().transpose()?.is_some() {
        return Err(ProjectError::Target);
    }
    Ok(())
}

pub fn project_snapshot(source_path: &Path, directory: &Path) -> Result<Projection, ProjectError> {
    let source = Snapshot::open(source_path)?;
    private_empty(directory)?;
    let directory = directory.canonicalize()?;
    let output = directory.join("index.sqlite3");
    let before = fingerprint(source.path())?;

    let stage = tempfile::Builder::new().prefix(".hub-project-").tempfile_in(&directory)?;
    let mut db = Connection::open_with_flags(
        stage.path(),
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI,
    )?;
    db.pragma_update(None, "trusted_schema", false)?;
    db.pragma_update(None, "synchronous", "FULL")?;
    db.pragma_update(None, "max_page_count", 524_288)?;
    let deadline = Instant::now() + Duration::from_secs(120);
    db.progress_handler(2048, Some(move || Instant::now() > deadline));
    let mut input_uri = url::Url::from_file_path(source.path()).map_err(|()| ProjectError::Unsupported)?;
    if input_uri.has_host() {
        return Err(ProjectError::Unsupported);
    }
    input_uri.set_query(Some("mode=ro&immutable=1"));
    db.execute("ATTACH DATABASE ? AS source", [input_uri.as_str()])?;
    validate_source(&db)?;
    let mut rows = BTreeMap::new();
    {
        let tx = db.transaction()?;
        tx.execute_batch(include_str!("projected_snapshot.sql"))?;
        for (table, columns) in PROJECTIONS {
            // Raw SQLite rowids are local to this authorized detached archive.
            // Preserve exact pagination ties, including gaps left by deletes.
            let copied = tx.execute(
                &format!("INSERT INTO main.{table} (rowid,{columns}) SELECT rowid,{columns} FROM source.{table}"),
                [],
            )?;
            rows.insert(*table, copied);
        }
        tx.execute_batch("
            CREATE INDEX snapshot_messages_scope ON messages(platform,account_id,conversation_id,timestamp DESC);
            CREATE INDEX snapshot_messages_order ON messages(timestamp DESC);
            CREATE INDEX snapshot_conversations_scope ON conversations(platform,account_id,last_timestamp DESC);
        ")?;
        tx.commit()?;
    }
    check(&db, "PRAGMA main.integrity_check(1)")?;
    drop(db);
    stage.as_file().sync_all()?;
    source.verify()?;
    if before != fingerprint(source.path())? {
        return Err(ProjectError::Changed);
    }
    let output_sha256 = fingerprint(stage.path())?;

    // Another writer or an existing destination must never be overwritten.
    for existing in fs::read_dir(&directory)? {
        if existing?.path() != stage.path() {
            return Err(ProjectError::Target);
        }
    }
    fs::hard_link(stage.path(), &output)?;
    #[cfg(unix)]
    if let Err(error) = fs::File::open(directory).and_then(|dir| dir.sync_all()) {
        let _ = fs::remove_file(&output);
        return Err(error.into());
    }
    Ok(Projection { source_sha256: before, output_sha256, rows })
}
