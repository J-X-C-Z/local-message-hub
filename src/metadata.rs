//! Edit explicitly authorized user metadata in a Rust-owned native archive.
use crate::{events::EventStore, plugins::AccountScope};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub(crate) const SCHEMA: &str = "
CREATE TABLE native_metadata_audit (
  platform TEXT NOT NULL,
  account_id TEXT NOT NULL,
  audit_id INTEGER NOT NULL,
  entity_type TEXT NOT NULL CHECK(entity_type IN ('conversation','contact')),
  entity_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  operation TEXT NOT NULL CHECK(operation IN ('conversation.update','contact.link','contact.clear')),
  fields TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  PRIMARY KEY(platform,account_id,audit_id)
) WITHOUT ROWID;
CREATE UNIQUE INDEX native_meta_entity_revision ON native_metadata_audit
  (platform,account_id,entity_type,entity_id,revision DESC);
";

#[derive(Debug, thiserror::Error)]
pub(crate) enum MetaError {
    #[error("invalid metadata input")]
    Input,
    #[error("metadata record not found")]
    Missing,
    #[error("metadata revision conflict")]
    Conflict,
    #[error("metadata audit is missing; use explicit upgrade")]
    Upgrade,
    #[error("metadata store is unavailable")]
    Storage,
}

impl From<crate::events::EventError> for MetaError {
    fn from(_: crate::events::EventError) -> Self { Self::Storage }
}
impl From<rusqlite::Error> for MetaError {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}
impl From<serde_json::Error> for MetaError {
    fn from(_: serde_json::Error) -> Self { Self::Storage }
}

type Result<T> = std::result::Result<T, MetaError>;

pub(crate) fn upgrade(store: &EventStore) -> Result<()> {
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let found: i64 = tx.query_row(
        "SELECT COUNT(*) FROM sqlite_schema
         WHERE name IN ('native_metadata_audit','native_meta_entity_revision')",
        [], |r| r.get(0),
    )?;
    match found {
        0 => tx.execute_batch(SCHEMA)?,
        2 => ready(&tx)?,
        _ => return Err(MetaError::Storage),
    }
    tx.commit()?;
    Ok(())
}

fn ready(db: &Connection) -> Result<()> {
    let found: i64 = db.query_row(
        "SELECT COUNT(*) FROM sqlite_schema
         WHERE (name='native_metadata_audit' AND type='table')
            OR (name='native_meta_entity_revision' AND type='index')",
        [], |r| r.get(0),
    )?;
    if found == 2 { Ok(()) } else { Err(MetaError::Upgrade) }
}

#[derive(Clone, Copy)]
pub(crate) enum Kind { Conversation, Contact }

impl Kind {
    fn name(self) -> &'static str {
        match self { Self::Conversation => "conversation", Self::Contact => "contact" }
    }

    fn verify_entity(self, db: &Connection, key: (&str, &str, &str)) -> Result<()> {
        let sql = match self {
            Self::Conversation => "SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=? AND account_id=? AND id=?)",
            Self::Contact => "SELECT EXISTS(SELECT 1 FROM contacts WHERE platform=? AND account_id=? AND id=?)",
        };
        let found: bool = db.query_row(sql, key, |r| r.get(0))?;
        if found { Ok(()) } else { Err(MetaError::Missing) }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    pub id: String,
    #[serde(default)]
    pub platform: String,
    #[serde(default)]
    pub account_id: String,
}

impl Target {
    fn key<'a>(&'a self, scope: &'a AccountScope) -> Result<(&'a str, &'a str, &'a str)> {
        if self.id.trim().is_empty() || self.id.len() > 2_048 || self.id.chars().count() > 512
            || self.id.chars().any(char::is_control)
        {
            return Err(MetaError::Input);
        }
        if (!self.platform.is_empty() && self.platform != scope.platform)
            || (!self.account_id.is_empty() && self.account_id != scope.account_id)
        {
            return Err(MetaError::Missing);
        }
        Ok((&scope.platform, &scope.account_id, &self.id))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ConversationWrite {
    pub target: Target,
    pub(crate) expected_revision: i64,
    pub(crate) note: Option<String>,
    pub(crate) tags: Option<Vec<String>>,
    pub(crate) archived: Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ContactWrite {
    pub target: Target,
    pub(crate) expected_revision: i64,
    pub(crate) identity_id: Value, // required; explicit null clears
}

#[derive(Default, Serialize, PartialEq, Eq)]
struct ConversationState {
    note: String,
    tags: Vec<String>,
    archived: bool,
}

fn conversation(db: &Connection, key: (&str, &str, &str)) -> Result<ConversationState> {
    let row = db.query_row(
        "SELECT note,tags,archived FROM conversation_metadata
         WHERE platform=? AND account_id=? AND conversation_id=?",
        key, |r| Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,bool>(2)?)),
    ).optional()?;
    match row {
        Some((note,tags,archived)) => Ok(ConversationState { note, tags: serde_json::from_str(&tags)?, archived }),
        None => Ok(ConversationState::default()),
    }
}

fn contact(db: &Connection, key: (&str, &str, &str)) -> Result<Option<String>> {
    Ok(db.query_row(
        "SELECT identity_id FROM contact_identities WHERE platform=? AND account_id=? AND contact_id=?",
        key, |r| r.get(0),
    ).optional()?)
}

fn revision(db: &Connection, kind: Kind, (platform,account,id): (&str,&str,&str)) -> Result<i64> {
    Ok(db.query_row(
        "SELECT revision FROM native_metadata_audit
         WHERE platform=? AND account_id=? AND entity_type=? AND entity_id=?
         ORDER BY revision DESC LIMIT 1", (platform,account,kind.name(),id), |r| r.get(0),
    ).optional()?.unwrap_or(0))
}

pub(crate) fn state(store: &EventStore, scope: &AccountScope, target: Target, kind: Kind) -> Result<Value> {
    let key = target.key(scope)?;
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.verify_connection(&tx)?;
    ready(&tx)?;
    kind.verify_entity(&tx,key)?;
    let current = revision(&tx,kind,key)?;
    match kind {
        Kind::Conversation => Ok(json!({"revision":current,"metadata":conversation(&tx,key)?})),
        Kind::Contact => Ok(json!({"revision":current,"identity_id":contact(&tx,key)?})),
    }
}

fn audit_write(
    db: &Connection, kind: Kind, (platform,account,id): (&str,&str,&str),
    current: i64, operation: &'static str, fields: &[&str],
) -> Result<i64> {
    let next = current.checked_add(1).ok_or(MetaError::Storage)?;
    db.execute(
        "INSERT INTO native_metadata_audit(platform,account_id,audit_id,entity_type,entity_id,revision,operation,fields,timestamp)
         VALUES(?1,?2,COALESCE((SELECT MAX(audit_id) FROM native_metadata_audit WHERE platform=?1 AND account_id=?2),0)+1,
                ?3,?4,?5,?6,?7,unixepoch())",
        params![platform,account,kind.name(),id,next,operation,serde_json::to_string(fields)?],
    )?;
    Ok(next)
}

pub(crate) fn limited_text(value: &str, max: usize, multiline: bool) -> bool {
    value.chars().count() <= max && !value.chars().any(|c| {
        c.is_control() && !(multiline && matches!(c, '\t' | '\r' | '\n'))
    })
}

pub(crate) fn normalize_tags(tags: Vec<String>) -> Result<Vec<String>> {
    if tags.len() > 50 { return Err(MetaError::Input); }
    let mut normalized: Vec<String> = Vec::new();
    for tag in tags {
        let trimmed = tag.trim();
        if trimmed.is_empty() || !limited_text(trimmed,64,false) { return Err(MetaError::Input); }
        if !normalized.iter().any(|existing| existing == trimmed) { normalized.push(trimmed.to_owned()); }
    }
    Ok(normalized)
}

pub(crate) fn write_conversation(store: &EventStore, scope: &AccountScope, req: ConversationWrite) -> Result<Value> {
    let key = req.target.key(scope)?;
    if req.expected_revision < 0 || (req.note.is_none() && req.tags.is_none() && req.archived.is_none())
        || req.note.as_ref().is_some_and(|note| !limited_text(note,4096,true))
    {
        return Err(MetaError::Input);
    }
    let normalized = req.tags.map(normalize_tags).transpose()?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    ready(&tx)?;
    let kind = Kind::Conversation;
    kind.verify_entity(&tx,key)?;
    let current = revision(&tx,kind,key)?;
    let existing = conversation(&tx,key)?;
    let next = ConversationState {
        note: req.note.unwrap_or_else(|| existing.note.clone()),
        tags: normalized.unwrap_or_else(|| existing.tags.clone()),
        archived: req.archived.unwrap_or(existing.archived),
    };
    let mut fields = Vec::new();
    if existing.note != next.note { fields.push("note"); }
    if existing.tags != next.tags { fields.push("tags"); }
    if existing.archived != next.archived { fields.push("archived"); }
    if fields.is_empty() {
        return Ok(json!({"changed":false,"revision":current,"metadata":next}));
    }
    if current != req.expected_revision { return Err(MetaError::Conflict); }
    tx.execute(
        "INSERT INTO conversation_metadata(platform,account_id,conversation_id,note,tags,archived,updated_at)
         VALUES(?,?,?,?,?,?,unixepoch()) ON CONFLICT(platform,account_id,conversation_id)
         DO UPDATE SET note=excluded.note,tags=excluded.tags,archived=excluded.archived,updated_at=excluded.updated_at",
        params![key.0,key.1,key.2,&next.note,serde_json::to_string(&next.tags)?,next.archived],
    )?;
    let version = audit_write(&tx,kind,key,current,"conversation.update",&fields)?;
    tx.commit()?;
    Ok(json!({"changed":true,"revision":version,"metadata":next}))
}

pub(crate) fn write_contact(store: &EventStore, scope: &AccountScope, req: ContactWrite) -> Result<Value> {
    let key = req.target.key(scope)?;
    if req.expected_revision < 0 { return Err(MetaError::Input); }
    let desired = match &req.identity_id {
        Value::Null => None,
        Value::String(value) if !value.trim().is_empty() && limited_text(value.trim(),256,false) => Some(value.trim()),
        _ => return Err(MetaError::Input),
    };
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    ready(&tx)?;
    let kind = Kind::Contact;
    kind.verify_entity(&tx,key)?;
    let current = revision(&tx,kind,key)?;
    if contact(&tx,key)?.as_deref() == desired {
        return Ok(json!({"changed":false,"revision":current,"identity_id":desired}));
    }
    if current != req.expected_revision { return Err(MetaError::Conflict); }
    let operation = if let Some(identity) = desired {
        tx.execute(
            "INSERT INTO contact_identities(platform,account_id,contact_id,identity_id,updated_at)
             VALUES(?,?,?,?,unixepoch()) ON CONFLICT(platform,account_id,contact_id)
             DO UPDATE SET identity_id=excluded.identity_id,updated_at=excluded.updated_at",
            (key.0,key.1,key.2,identity),
        )?;
        "contact.link"
    } else {
        tx.execute("DELETE FROM contact_identities WHERE platform=? AND account_id=? AND contact_id=?",key)?;
        "contact.clear"
    };
    let version = audit_write(&tx,kind,key,current,operation,&["identity_id"])?;
    tx.commit()?;
    Ok(json!({"changed":true,"revision":version,"identity_id":desired}))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AuditQuery {
    #[serde(default)]
    after: i64,
    limit: Option<u16>,
}

// Stable byte order matches serde_json's existing sorted object keys.
#[derive(Serialize)]
pub(crate) struct AuditPage {
    has_more: bool,
    items: Vec<AuditItem>,
    next_after: i64,
}

#[derive(Serialize)]
struct AuditItem {
    audit_id: i64,
    entity_id: String,
    entity_type: String,
    fields: Vec<String>,
    operation: String,
    revision: i64,
    timestamp: i64,
}

pub(crate) fn audit(store: &EventStore, scope: &AccountScope, q: AuditQuery) -> Result<AuditPage> {
    let limit = usize::from(q.limit.unwrap_or(50));
    if q.after < 0 || !(1..=100).contains(&limit) { return Err(MetaError::Input); }
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.verify_connection(&tx)?;
    ready(&tx)?;
    let mut stmt = tx.prepare(
        "SELECT audit_id,entity_type,entity_id,revision,operation,fields,timestamp
         FROM native_metadata_audit WHERE platform=? AND account_id=? AND audit_id>?
         ORDER BY audit_id LIMIT ?",
    )?;
    let mut rows = stmt.query(params![scope.platform, scope.account_id, q.after, limit+1])?;
    let mut items = Vec::with_capacity(limit);
    let mut next = q.after;
    let mut more = false;
    while let Some(row) = rows.next()? {
        if items.len() == limit { more = true; break; }
        let fields = row.get_ref(5)?.as_str().map_err(|_| MetaError::Storage)?;
        let fields: Vec<String> = serde_json::from_str(fields)?;
        next = row.get(0)?;
        let entity_type = row.get(1)?;
        let entity_id = row.get(2)?;
        let revision = row.get(3)?;
        let operation = row.get(4)?;
        let timestamp = row.get(6)?;
        items.push(AuditItem { audit_id: next, entity_id, entity_type, fields, operation, revision, timestamp });
    }
    Ok(AuditPage { has_more: more, items, next_after: next })
}

#[cfg(test)]
#[test]
fn audit_page_typed_json_matches_value_bytes() {
    let row = AuditItem { audit_id: i64::MAX, entity_id: "引用 \"\n\t café 😀".into(),
        entity_type: "contact".into(), fields: vec!["identity_id".into(), "混合".into()],
        operation: "contact.clear".into(), revision: 260, timestamp: -1 };
    let old = json!({"items":[{"audit_id":row.audit_id,"entity_type":row.entity_type,
        "entity_id":row.entity_id,"revision":row.revision,"operation":row.operation,
        "fields":row.fields,"timestamp":row.timestamp}],"next_after":i64::MAX,"has_more":true});
    let page = AuditPage {has_more:true,items:vec![row],next_after:i64::MAX};
    assert_eq!(serde_json::to_vec(&page).unwrap(),serde_json::to_vec(&old).unwrap());
    let empty = AuditPage {has_more:false,items:vec![],next_after:42};
    assert_eq!(serde_json::to_vec(&empty).unwrap(),
        serde_json::to_vec(&json!({"items":[],"next_after":42,"has_more":false})).unwrap());
}
