//! Redacted views of indexed operations. No local paths, payloads, or provider state.
use crate::{
    events::{EventError, EventStore},
    import_jobs,
    plugins::{AccountScope, Permission, Plugin, PluginKind},
};
use rusqlite::{Connection, params};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const DEFAULT_PAGE: u32 = 32;
const MAX_PAGE: u32 = 64;
const MAX_SEQUENCE: i64 = 9_007_199_254_740_991;

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Metadata,
    Draft,
    ImportJob,
    SourceCheckpoint,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    kind: Kind,
    platform: Option<String>,
    account_id: Option<String>,
    cursor: Option<String>,
    limit: Option<u32>,
    conversation_id: Option<String>,
    job_id: Option<String>,
}

#[derive(Debug)]
pub(crate) enum Error { Invalid, Permission, Missing, Upgrade, Storage }

impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}

impl From<EventError> for Error {
    fn from(error: EventError) -> Self {
        match error {
            EventError::ScopeUnavailable => Self::Upgrade,
            EventError::ProfileLocked => Self::Permission,
            _ => Self::Storage,
        }
    }
}

impl From<import_jobs::Error> for Error {
    fn from(error: import_jobs::Error) -> Self {
        match error {
            import_jobs::Error::Input => Self::Invalid,
            import_jobs::Error::Missing => Self::Missing,
            _ => Self::Storage,
        }
    }
}

pub(crate) fn has_view(plugin: &Plugin) -> bool {
    let p = &plugin.permissions;
    p.contains(&Permission::OperationsRead) && plugin.account_scope.is_some()
        && (plugin.kind == PluginKind::Desktop && (p.contains(&Permission::MetadataAudit)
            || p.contains(&Permission::DraftRead) || p.contains(&Permission::ImportsManage))
            || plugin.kind == PluginKind::ScopedProducer && p.contains(&Permission::Ingest))
}

pub(crate) fn status(store: &EventStore, plugin: &Plugin, q: Query) -> Result<Value, Error> {
    if !has_view(plugin) { return Err(Error::Permission); }
    let scope = plugin.account_scope.as_ref().ok_or(Error::Permission)?;
    if q.platform.as_ref().is_some_and(|requested| requested != &scope.platform)
        || q.account_id.as_ref().is_some_and(|requested| requested != &scope.account_id)
    {
        return Err(Error::Permission);
    }

    let (role, permission) = match q.kind {
        Kind::Metadata => (PluginKind::Desktop, Permission::MetadataAudit),
        Kind::Draft => (PluginKind::Desktop, Permission::DraftRead),
        Kind::ImportJob => (PluginKind::Desktop, Permission::ImportsManage),
        Kind::SourceCheckpoint => (PluginKind::ScopedProducer, Permission::Ingest),
    };
    if plugin.kind != role || !plugin.permissions.contains(&permission) {
        return Err(Error::Permission);
    }
    store.require_ordinary()?;
    match q.kind {
        Kind::Metadata => {
            if q.conversation_id.is_some() || q.job_id.is_some() { return Err(Error::Invalid); }
            let page = Page::new(store, plugin, scope, "metadata", None, q.cursor.as_deref(), q.limit)?;
            let mut db = store.read_connection()?;
            let tx = db.transaction()?;
            store.verify_connection(&tx)?;
            metadata(&tx, scope, page)
        }
        Kind::Draft => {
            if q.job_id.is_some() { return Err(Error::Invalid); }
            let id = q.conversation_id.as_deref().filter(|id|
                !id.trim().is_empty() && id.len()<=2048 && id.chars().count()<=512
                && !id.chars().any(char::is_control)).ok_or(Error::Invalid)?;
            let page = Page::new(store, plugin, scope, "draft", Some(id), q.cursor.as_deref(), q.limit)?;
            let mut db = store.read_connection()?;
            let tx = db.transaction()?;
            store.verify_connection(&tx)?;
            draft(&tx, scope, &plugin.id, id, page)
        }
        Kind::ImportJob => {
            if q.conversation_id.is_some() || q.cursor.is_some() || q.limit.is_some() {
                return Err(Error::Invalid);
            }
            let phase = import_jobs::redacted_phase(store, &plugin.id, scope,
                q.job_id.as_deref().ok_or(Error::Invalid)?)?;
            Ok(json!({"kind":"import_job","status":phase}))
        }
        Kind::SourceCheckpoint => {
            if q.conversation_id.is_some() || q.job_id.is_some() || q.cursor.is_some() || q.limit.is_some() {
                return Err(Error::Invalid);
            }
            let checkpoint = store.source_checkpoint(&plugin.id, scope)?;
            Ok(json!({"kind":"source_checkpoint","status":if checkpoint.sequence==0 {"initial"} else {"indexed"},
                "revision":checkpoint.revision,"sequence":checkpoint.sequence}))
        }
    }
}

struct Page { tag: String, after: i64, limit: u32 }

impl Page {
    fn new(
        store: &EventStore, plugin: &Plugin, scope: &AccountScope, kind: &str, target: Option<&str>,
        cursor: Option<&str>, limit: Option<u32>,
    ) -> Result<Self, Error> {
        let limit = limit.unwrap_or(DEFAULT_PAGE);
        if !(1..=MAX_PAGE).contains(&limit) { return Err(Error::Invalid); }

        let encoded = serde_json::to_vec(&("lmh-operation-read-v1",store.archive_id(),&plugin.id,
            &scope.platform,&scope.account_id,kind,target)).map_err(|_| Error::Storage)?;
        let tag = format!("o1-{}:",hex::encode(Sha256::digest(encoded)));
        let after = if let Some(cursor) = cursor {
            let position = cursor.strip_prefix(tag.as_str()).ok_or(Error::Invalid)?;
            if !(1..=16).contains(&position.len()) || !position.bytes().all(|p|p.is_ascii_digit()) {
                return Err(Error::Invalid);
            }
            let position: i64 = position.parse().map_err(|_|Error::Invalid)?;
            if position > MAX_SEQUENCE { return Err(Error::Invalid); }
            position
        } else { 0 };
        Ok(Self { tag, after, limit })
    }

    fn response(&self, kind: &str, items: Vec<Value>, last: i64, more: bool) -> Value {
        json!({"kind":kind,"items":items,"next_cursor":format!("{}{last}",self.tag),"has_more":more})
    }
}

fn metadata(db: &Connection, scope: &AccountScope, page: Page) -> Result<Value,Error> {
    let objects: i64 = db.query_row(
        "SELECT COUNT(*) FROM sqlite_schema WHERE
        (name='native_metadata_audit' AND type='table') OR
        (name='native_meta_entity_revision' AND type='index')", [], |r| r.get(0),
    )?;
    if objects != 2 { return Err(Error::Upgrade); }
    let mut stmt = db.prepare(
        "SELECT audit_id,revision,operation FROM native_metadata_audit
         WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2 AND audit_id>?3
         ORDER BY audit_id LIMIT ?4"
    )?;
    let mut rows = stmt.query(params![scope.platform,scope.account_id,page.after,page.limit+1])?;
    let mut items = Vec::with_capacity(page.limit as usize);
    let (mut last,mut more) = (page.after,false);
    while let Some(row) = rows.next()? {
        if items.len() == page.limit as usize { more = true; break; }
        last = row.get(0)?;
        let revision: i64 = row.get(1)?;
        let value = row.get_ref(2)?;
        let operation = value.as_str().map_err(|error|
            rusqlite::Error::FromSqlConversionFailure(2, value.data_type(), Box::new(error)))?;
        if !(1..=MAX_SEQUENCE).contains(&last) || !(1..=MAX_SEQUENCE).contains(&revision)
            || !matches!(operation,"conversation.update"|"contact.link"|"contact.clear")
        {
            return Err(Error::Storage);
        }
        items.push(json!({"type":operation,"revision":revision}));
    }
    Ok(page.response("metadata",items,last,more))
}

fn draft(db: &Connection, scope: &AccountScope, grant: &str, id: &str, page: Page) -> Result<Value,Error> {
    let (objects,tables):(i64,i64) = db.query_row(
        "SELECT COUNT(*), COUNT(CASE WHEN type='table' AND name IN
          ('native_drafts','native_draft_receipts','native_draft_audit') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
          ('native_drafts','native_draft_receipts','native_draft_audit')", [],
        |r| Ok((r.get(0)?,r.get(1)?)),
    )?;
    match (objects,tables) {
        (0,0) => return Ok(page.response("draft",Vec::new(),page.after,false)),
        (3,3) => (),
        _ => return Err(Error::Storage),
    }
    // Receipt revisions can be observed after no-op writes, so do not join them
    // to shared draft audit records and claim this grant authored those edits.
    // A revision-0 no-op is not included in the timeline.
    let mut stmt = db.prepare(
        "SELECT revision,MIN(present),MAX(present) FROM native_draft_receipts
         WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
           AND conversation_id COLLATE BINARY=?3 AND grant_id COLLATE BINARY=?4 AND revision>?5
         GROUP BY revision ORDER BY revision LIMIT ?6"
    )?;
    let mut rows = stmt.query(params![scope.platform,scope.account_id,id,grant,page.after,page.limit+1])?;
    let mut items = Vec::with_capacity(page.limit as usize);
    let (mut last,mut more) = (page.after,false);
    while let Some(row) = rows.next()? {
        if items.len() == page.limit as usize { more = true; break; }
        last = row.get(0)?;
        let (low,high):(i64,i64) = (row.get(1)?,row.get(2)?);
        if !(1..=MAX_SEQUENCE).contains(&last) || low != high || !matches!(low,0|1) {
            return Err(Error::Storage);
        }
        items.push(json!({"type":"own_receipt","revision":last,"status":if low==1 {"present"} else {"empty"}}));
    }
    Ok(page.response("draft",items,last,more))
}
