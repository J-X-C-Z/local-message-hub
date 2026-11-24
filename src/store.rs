use crate::events::EventStore;
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime};
use regex::Regex;
use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Row, params_from_iter, types::Value as Sql,
};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    io::{self, Write},
    path::Path,
    sync::LazyLock,
    time::{Duration, Instant},
};

const SCHEMA_VERSION: i64 = 9;
pub(crate) const PLATFORMS: &[&str] = &["all", "wechat", "wecom", "qq", "telegram", "email"];
const SCOPED_MESSAGE_ORDER: &str = "m.id";
const GUARDED_V9_SEARCH: &str = "guarded-v9-search-v1";
const GUARDED_V9_MESSAGES: &str = "guarded-v9-messages-v1";

#[derive(Clone, Copy)]
enum GuardedV9Page {
    Search,
    Messages,
}

impl GuardedV9Page {
    fn tag(self) -> &'static str {
        match self {
            Self::Search => GUARDED_V9_SEARCH,
            Self::Messages => GUARDED_V9_MESSAGES,
        }
    }
}
#[derive(Clone, Copy)]
enum RowPageLane {
    Guarded(GuardedV9Page),
    LiveLegacy { messages: bool },
}

impl RowPageLane {
    fn tag(self) -> &'static str {
        match self {
            Self::Guarded(purpose) => purpose.tag(),
            Self::LiveLegacy { messages: true } => "live-legacy-messages-v1",
            Self::LiveLegacy { messages: false } => "live-legacy-search-v1",
        }
    }
}

const MSG_SELECT: &str = "m.rowid, m.id, m.platform, m.account_id, m.conversation_id, m.sender_id,
    m.sender_name, m.timestamp, m.type, m.is_self, m.media_url, m.file_name, m.raw_type,
    m.extra, (SELECT p.avatar_url FROM contacts p WHERE p.platform=m.platform
        AND p.account_id=m.account_id AND p.id=m.sender_id) AS sender_avatar_url";
const MSG_SEARCH_SELECT: &str = "c.title, c.kind, COALESCE(NULLIF(c.avatar_url,''), (
    SELECT p.avatar_url FROM contacts p WHERE p.platform=m.platform AND p.account_id=m.account_id
    AND p.id=m.conversation_id))";
const MSG_JOIN: &str = "LEFT JOIN conversations c ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id";
const MAX_OFFSET: i64 = 100_000;
const MAX_SQL_ROW_BYTES: i32 = 2 * 1024 * 1024;
const MAX_JSON_ROW_BYTES: usize = 4 * 1024 * 1024 - 512; // reserve envelope/cursor overhead

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("{0}")]
    BadQuery(&'static str),
    #[error("not a supported Local Message Hub schema (expected v9)")]
    Schema,
    #[error("page is too large; use a smaller limit")]
    TooLarge,
    #[error("local index is not readable")]
    Sql(#[source] rusqlite::Error),
}

impl From<rusqlite::Error> for StoreError {
    fn from(error: rusqlite::Error) -> Self {
        if error.sqlite_error_code() == Some(rusqlite::ErrorCode::TooBig) {
            Self::TooLarge
        } else {
            Self::Sql(error)
        }
    }
}

pub type Result<T> = std::result::Result<T, StoreError>;

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Query {
    pub platform: String,
    pub account_id: String,
    pub conversation_id: String,
    pub q: String,
    #[serde(rename = "type")]
    pub kind_type: String,
    pub kind: String,
    pub start: String,
    pub end: String,
    pub cursor: String,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
    /// Server-owned permission flag; never set by callers of a wire protocol.
    #[serde(skip)]
    pub account_scoped: bool,
}

/// Message targeting uses only public normalized identifiers, scoped to one conversation.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ContextQuery {
    pub platform: String,
    pub account_id: String,
    pub conversation_id: String,
    pub message_id: String,
    pub reference_message_id: String,
    pub before: Option<i64>,
    pub after: Option<i64>,
}

fn valid_context_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 1024 && !id.contains('\0')
}

impl ContextQuery {
    fn validate(&self, quoted: bool) -> Result<(usize, usize)> {
        if !valid_context_id(&self.conversation_id) {
            return Err(bad("invalid message context scope"));
        }
        self.validate_target(quoted)
    }

    fn validate_target(&self, quoted: bool) -> Result<(usize, usize)> {
        let platform_matches = if quoted {
            self.platform == "wechat"
        } else {
            self.platform != "all" && PLATFORMS.contains(&self.platform.as_str())
        };
        let (target, unused) = if quoted {
            (&self.reference_message_id, &self.message_id)
        } else {
            (&self.message_id, &self.reference_message_id)
        };
        if !platform_matches
            || !unused.is_empty()
            || [&self.account_id, target]
                .iter()
                .any(|id| !valid_context_id(id))
        {
            return Err(bad("invalid message context scope"));
        }
        Ok((
            self.before.unwrap_or(5).clamp(0, 50) as usize,
            self.after.unwrap_or(5).clamp(0, 50) as usize,
        ))
    }

    fn scope(&self) -> [&str; 3] {
        [&self.platform, &self.account_id, &self.conversation_id]
    }
}

fn bad(message: &'static str) -> StoreError {
    StoreError::BadQuery(message)
}

impl Query {
    fn platform(&self) -> &str {
        if self.platform.is_empty() {
            "all"
        } else {
            &self.platform
        }
    }

    fn pages(&self) -> Result<(i64, i64)> {
        if !PLATFORMS.contains(&self.platform()) {
            return Err(bad("unknown platform"));
        }
        if self.q.len() > 4096
            || self.account_id.len() > 1024
            || self.conversation_id.len() > 1024
            || self.kind.len() > 128
            || self.kind_type.len() > 128
            || self.start.len() > 128
            || self.end.len() > 128
            || self.cursor.len() > if self.account_scoped { 2048 } else { 128 }
        {
            return Err(bad("query parameter exceeds size limit"));
        }
        let offset = self.offset.unwrap_or(0).max(0);
        if offset > MAX_OFFSET {
            return Err(bad(
                "offset exceeds limit; use cursor pagination when available",
            ));
        }
        Ok((self.limit.unwrap_or(100).clamp(1, 1000), offset))
    }

    fn scope(&self, scoped: bool) -> String {
        let fields = if scoped {
            vec![
                self.platform(),
                &self.account_id,
                &self.conversation_id,
                &self.q,
                &self.kind_type,
                &self.start,
                &self.end,
            ]
        } else {
            let mut fields = vec![
                &*self.q,
                self.platform(),
                &self.kind_type,
                &self.start,
                &self.end,
            ];
            if !self.account_id.is_empty() || !self.conversation_id.is_empty() {
                fields.extend([&*self.account_id, &self.conversation_id]);
            }
            fields
        };
        let digest = Sha256::digest(serde_json::to_vec(&fields).expect("strings serialize"));
        digest[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }
}

#[derive(Clone)]
/// Read the normalized archive without mutation. Stored bodies/metadata are private and
/// may contain URLs or path strings. This layer never opens or fetches those references.
pub struct Store {
    location: StoreLocation,
}

#[derive(Clone)]
enum StoreLocation {
    Snapshot(String),
    Live(EventStore),
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let canonical = path
            .as_ref()
            .canonicalize()
            .map_err(|_| StoreError::Schema)?;
        let mut uri = url::Url::from_file_path(canonical).map_err(|()| StoreError::Schema)?;
        if uri.has_host() {
            return Err(StoreError::Schema);
        }
        uri.set_query(Some("mode=ro&immutable=1"));
        let store = Self {
            location: StoreLocation::Snapshot(uri.into()),
        };
        store.status()?;
        Ok(store)
    }

    pub fn open_live(events: EventStore) -> Result<Self> {
        let store = Self {
            location: StoreLocation::Live(events),
        };
        store.status()?;
        Ok(store)
    }

    pub(crate) fn connection(&self) -> Result<Connection> {
        let db = match &self.location {
            StoreLocation::Snapshot(uri) => Connection::open_with_flags(
                uri,
                OpenFlags::SQLITE_OPEN_READ_ONLY
                    | OpenFlags::SQLITE_OPEN_NO_MUTEX
                    | OpenFlags::SQLITE_OPEN_URI,
            )?,
            StoreLocation::Live(events) => {
                events.read_connection().map_err(|_| StoreError::Schema)?
            }
        };
        if matches!(self.location,StoreLocation::Snapshot(_)) {
            let app_id: i64=db.pragma_query_value(None,"application_id",|row| row.get(0))?;
            if app_id != 0 { return Err(StoreError::Schema); }
        }
        Self::configure_reader(db)
    }

    fn configure_reader(db: Connection) -> Result<Connection> {
        db.busy_timeout(Duration::from_millis(500))?;
        db.pragma_update(None, "query_only", true)?;
        db.pragma_update(None, "trusted_schema", false)?;
        db.set_limit(
            rusqlite::limits::Limit::SQLITE_LIMIT_LENGTH,
            MAX_SQL_ROW_BYTES,
        );
        // This deadline interrupts an expensive SELECT, including work to find matching
        // rows. The callback lives only for this connection/request.
        let deadline = Instant::now() + Duration::from_secs(3);
        db.progress_handler(2048, Some(move || Instant::now() >= deadline));
        Ok(db)
    }

    pub fn status(&self) -> Result<Value> {
        let db = self.connection()?;
        let schema_version: i64 = db.pragma_query_value(None, "user_version", |r| r.get(0))?;
        if schema_version != SCHEMA_VERSION {
            return Err(StoreError::Schema);
        }
        let ready: i64 = db.query_row("SELECT COUNT(*) FROM sqlite_schema
            WHERE type='table' AND name IN ('conversations','messages','contacts','conversation_metadata','contact_identities')",
            [], |r| r.get(0))?;
        if ready != 5 {
            return Err(StoreError::Schema);
        }
        Ok(json!({"status":"ok", "schema_version":schema_version,
            "fts_enabled": has_fts(&db)?, "read_only":true}))
    }

    pub fn conversations(&self, q: &Query) -> Result<Value> {
        self.conversations_page(q, None)
    }

    /// Only the ordinary versioned route enables an exact lookup. Existing
    /// list/query users must not acquire a new filter from an ignored field.
    pub(crate) fn exact_conversation(&self, q: &Query) -> Result<Value> {
        if !q.account_scoped || q.platform() == "all" || q.account_id.is_empty()
            || q.conversation_id.is_empty() || q.conversation_id.len() > 256
            || q.conversation_id.trim() != q.conversation_id
            || q.conversation_id.chars().any(char::is_control)
        {
            return Err(bad("exact conversation scope and normalized ID required"));
        }
        self.conversations_page(q, Some(&q.conversation_id))
    }

    fn conversations_page(&self, q: &Query, exact_id: Option<&str>) -> Result<Value> {
        let (limit, offset) = q.pages()?;
        let mut conditions = Conditions::default();
        if let Some(id) = exact_id {
            conditions.push("c.platform COLLATE BINARY = ?", q.platform().to_owned());
            conditions.push("c.account_id COLLATE BINARY = ?", q.account_id.clone());
            conditions.push("c.id COLLATE BINARY = ?", id.to_owned());
        } else {
            conditions.scope("c.platform", q.platform());
            if q.account_scoped {
                conditions.push("c.account_id = ?", q.account_id.clone());
            }
        }
        if !q.q.is_empty() {
            conditions.push("c.title LIKE ? ESCAPE '\\'", like(&q.q));
        }
        let mut db = self.connection()?;
        let tx = db.transaction()?;
        let where_sql = conditions.sql();
        let total = count(&tx, "conversations c", &where_sql, &conditions.values)?;
        if exact_id.is_some() && total > 1 {
            return Err(StoreError::Schema);
        }
        let eq = if exact_id.is_some() { " COLLATE BINARY=" } else { "=" };
        conditions
            .values
            .extend([Sql::Integer(limit), Sql::Integer(offset)]);
        let sql = format!("SELECT c.id,c.platform,c.account_id,c.title,c.kind,
            COALESCE(NULLIF((SELECT p.avatar_url FROM contacts p WHERE p.platform{eq}c.platform
                AND p.account_id{eq}c.account_id AND p.id{eq}c.id),''), c.avatar_url),
            c.member_count,c.last_message,c.last_timestamp,c.message_count,c.extra,
            COALESCE(cm.note,''),COALESCE(cm.tags,'[]'),COALESCE(cm.archived,0)
            FROM conversations c
            LEFT JOIN conversation_metadata cm ON cm.platform{eq}c.platform AND cm.account_id{eq}c.account_id
                AND cm.conversation_id{eq}c.id
            {where_sql} ORDER BY c.last_timestamp DESC,c.id,c.platform,c.account_id LIMIT ? OFFSET ?");
        let (items, _) = collect(
            &tx,
            &sql,
            &conditions.values,
            &mut Budget::new(),
            usize::MAX,
            |r| {
                let last = r.get::<_, Option<String>>(7)?;
                Ok(json!({
                    "id":r.get::<_,String>(0)?, "platform":r.get::<_,String>(1)?, "account_id":r.get::<_,String>(2)?,
                    "title":string(r,3)?, "kind":string(r,4)?, "avatar_url":string(r,5)?,
                    "member_count":r.get::<_,Option<i64>>(6)?, "last_message": preview(last.as_deref().unwrap_or("")),
                    "last_timestamp":r.get::<_,Option<i64>>(8)?, "message_count":r.get::<_,i64>(9)?,
                    "extra": extra(r,10)?, "note":r.get::<_,String>(11)?,
                    "tags": r.get::<_,String>(12)?.parse::<Value>().unwrap_or_else(|_| json!([])),
                    "archived":r.get::<_,bool>(13)?
                }))
            },
        )?;
        Ok(json!({"items":items,"total":total}))
    }

    pub fn messages(&self, q: &Query) -> Result<Value> {
        self.message_page(q, true)
    }

    pub fn search(&self, q: &Query) -> Result<Value> {
        self.message_page(q, false)
    }

    /// Stock-style relative tie ordering for ordinary legacy reads. Keep
    /// candidates and row identities within one verified archive snapshot.
    pub(crate) fn live_legacy_message_page(&self, q: &Query, grant_id: &str, messages: bool)
        -> Result<Value> {
        let StoreLocation::Live(archive) = &self.location else { return Err(StoreError::Schema); };
        archive.require_ordinary().map_err(|_| StoreError::Schema)?;
        if !q.account_scoped || q.platform() == "all" || q.account_id.is_empty()
            || (messages && q.conversation_id.is_empty())
        {
            return Err(bad("exact legacy account required"));
        }
        Self::message_page_in(self.connection()?, q, messages, false,
            Some((archive, grant_id, RowPageLane::LiveLegacy { messages })), false, None)
    }

    /// Only the guarded legacy search handler selects this policy. The
    /// reserved v9 import receipt is checked in the same read as the page.
    pub(crate) fn search_guarded_legacy(&self, q: &Query, grant_id: &str) -> Result<Value> {
        let StoreLocation::Live(archive) = &self.location else { return Err(StoreError::Schema); };
        if !q.account_scoped || q.platform() == "all" || q.account_id.is_empty() {
            return Err(bad("exact legacy search account required"));
        }
        q.pages()?;
        Self::message_page_in(self.connection()?, q, false, false,
            Some((archive, grant_id, RowPageLane::Guarded(GuardedV9Page::Search))), false, None)
    }

    /// Only the guarded legacy conversation endpoint passes its verified
    /// reader and grant. The caller still owns the independent postflight.
    pub(crate) fn legacy_messages_on(db: Connection, archive: &EventStore,
        q: &Query, grant_id: &str) -> Result<Value> {
        if !q.account_scoped || q.platform() == "all" || q.account_id.is_empty()
            || q.conversation_id.is_empty()
        {
            return Err(bad("exact legacy message conversation required"));
        }
        Self::message_page_in(Self::configure_reader(db)?, q, true, false,
            Some((archive, grant_id, RowPageLane::Guarded(GuardedV9Page::Messages))), false, None)
    }

    /// Fixed existing reader policy for the guarded cookie-browser route.
    pub(crate) fn messages_on(db: Connection, q: &Query) -> Result<Value> {
        Self::message_page_in(Self::configure_reader(db)?, q, true, false, None, false, None)
    }

    fn message_page(&self, q: &Query, scoped: bool) -> Result<Value> {
        q.pages()?;
        let ordinary_search = match &self.location {
            StoreLocation::Live(archive) if !scoped && q.account_scoped
                && q.platform() != "all" && !q.account_id.is_empty() && !q.q.is_empty() => Some(archive),
            _ => None,
        };
        Self::message_page_in(self.connection()?, q, scoped,
            matches!(self.location, StoreLocation::Snapshot(_)), None, false, ordinary_search)
    }

    #[cfg(target_os="linux")]
    pub(crate) fn messages_with_local_wechat_replies(&self,q:&Query)->Result<Value> {
        let StoreLocation::Live(archive)=&self.location else {return Err(StoreError::Schema);};
        if !q.account_scoped || q.platform()!="wechat" || q.account_id.is_empty()
            || q.conversation_id.is_empty() {
            return Err(bad("exact WeChat conversation required"));
        }
        archive.require_ordinary().map_err(|_|StoreError::Schema)?;
        Self::message_page_in(self.connection()?,q,true,false,None,true,None)
    }

    fn message_page_in(mut db: Connection, q: &Query, scoped: bool, snapshot: bool,
        row_order: Option<(&EventStore, &str, RowPageLane)>, _with_local_replies:bool,
        ordinary_search: Option<&EventStore>) -> Result<Value> {
        let (limit, offset) = q.pages()?;
        let scope = q.scope(scoped);
        let tx = db.transaction()?;
        let row_page = match row_order {
            Some((archive, grant_id, lane @ RowPageLane::Guarded(_))) => {
                archive.guarded_v9_projection_in(&tx).map_err(|_| StoreError::Schema)?
                    .then(|| (lane, row_page_scope(lane, q, archive.archive_id(), grant_id)))
            }
            Some((archive, grant_id, lane @ RowPageLane::LiveLegacy { .. })) => {
                archive.verify_connection(&tx).map_err(|_| StoreError::Schema)?;
                Some((lane, row_page_scope(lane, q, archive.archive_id(), grant_id)))
            }
            None => None,
        };
        let guarded_projection = matches!(&row_page, Some((RowPageLane::Guarded(_), _)));
        let anchor = match &row_page {
            Some((RowPageLane::Guarded(purpose), projected_scope)) =>
                guarded_page_anchor(&tx, *purpose, q, projected_scope)?,
            Some((RowPageLane::LiveLegacy { .. }, _)) => None, // verified against native filters below
            None if q.account_scoped => decode_scoped_cursor(&q.cursor, &scope)?,
            None => decode_cursor(&q.cursor, &scope)?.map(|(stamp, rowid)| (stamp, Sql::Integer(rowid))),
        };
        // Legacy wire order uses the local row position as a tie breaker,
        // but tokens expose only scoped normalized IDs, not row positions.
        let use_id_order = q.account_scoped && row_page.is_none();
        let order = if use_id_order { SCOPED_MESSAGE_ORDER } else { "m.rowid" };
        let rank = if use_id_order { "page.id" } else { "page.rowid" };
        let mut c = message_scope(q, scoped);
        let fts_clause_index = c.clauses.len();
        let fts = if guarded_projection {
            // Guarded v9 imports only scoped rows, not the stock FTS table.
            append_literal_filters(q, &mut c)?;
            None
        } else {
            append_message_filters(&tx, q, &mut c)?
        };
        let anchor = if let Some((lane @ RowPageLane::LiveLegacy { .. }, projected_scope)) = &row_page {
            // Match the identical literals/type/date/FTS filters before locating
            // an anchor. A revised/deleted anchor invalidates this token.
            let mut filtered = c.clone();
            if let Some(phrase) = &fts {
                filtered.insert(fts_clause_index, FTS_ROWID_MEMBERSHIP, phrase.clone());
            }
            live_legacy_page_anchor(&tx, *lane, q, projected_scope, filtered)?
        } else {
            anchor
        };
        let literal_only = fts.is_none();
        let total = match fts {
            Some(phrase) if snapshot && !scoped && q.account_scoped && stock_trigram_fts(&tx)? => {
                let total = count_fts_rowids(&tx, &c, &phrase)?;
                c.insert(fts_clause_index, FTS_ROWID_MEMBERSHIP, phrase);
                total
            }
            phrase => {
                if let Some(phrase) = phrase {
                    c.insert(fts_clause_index, FTS_ROWID_MEMBERSHIP, phrase);
                }
                count(&tx, "messages m", &c.sql(), &c.values)?
            }
        };
        // COUNT used the identical filters in this transaction. For a
        // canonical single-match first page, finding the only matching ID
        // needs no inner sort. Preserve the original outer joined projection.
        let empty = total == 0 || anchor.is_none() && offset >= total;
        let singleton = anchor.is_none() && offset == 0 && total == 1;
        let canonical_literal = literal_only && (empty || singleton)
            && ordinary_search.is_some_and(|archive| archive.has_canonical_context_order(&tx)
                && archive.has_canonical_contacts(&tx));
        let singleton = canonical_literal && singleton;
        if canonical_literal && empty {
            c.clauses.push("0".into());
        }
        if let Some((timestamp, value)) = anchor.as_ref() {
            c.clauses.push(format!("(m.timestamp,{order}) < (?,?)"));
            c.values.extend([Sql::Integer(*timestamp), value.clone()]);
        }
        let where_sql = c.sql();
        c.values.extend([
            Sql::Integer(if singleton { 1 } else { limit + 1 }),
            Sql::Integer(if anchor.is_some() { 0 } else { offset }),
        ]);
        let inner_order = match (singleton, use_id_order) {
            (true, _) => "",
            (false, true) => "ORDER BY m.timestamp DESC,m.id DESC",
            (false, false) => "ORDER BY m.timestamp DESC,m.rowid DESC",
        };
        let sql = if scoped {
            format!(
                "SELECT {MSG_SELECT},m.text FROM messages m {where_sql}
                ORDER BY m.timestamp DESC,{order} DESC LIMIT ? OFFSET ?"
            )
        } else {
            format!(
                "SELECT {MSG_SELECT},substr(m.text,1,1200),{MSG_SEARCH_SELECT}
                FROM (SELECT m.rowid,m.id FROM messages m {where_sql}
                    {inner_order} LIMIT ? OFFSET ?) page
                JOIN messages m ON m.rowid=page.rowid {MSG_JOIN}
                ORDER BY m.timestamp DESC,{rank} DESC"
            )
        };
        // Reserve extra envelope bytes only for the longer versioned token.
        let mut budget = if row_page.is_some() {
            Budget(MAX_JSON_ROW_BYTES - 512)
        } else {
            Budget::new()
        };
        #[cfg(target_os="linux")]
        let with_local_replies=_with_local_replies && crate::wechat_source::reply_export::installed(&tx)?;
        let (rows, has_more) = collect(
            &tx, &sql, &c.values, &mut budget, limit as usize, |r| {
                let out=message(r,!scoped)?;
                #[cfg(target_os="linux")]
                if with_local_replies {
                    let (_stamp,_rowid,mut data)=out;
                    let id:String=r.get(1)?;
                    if let Some(display)=crate::wechat_source::reply_export::get_for_page(
                        &tx,&q.account_id,&q.conversation_id,&id)? {
                        data["extra"]["wechat_quote"]=json!({
                            "sender":display.sender,"content":display.content
                        });
                    }
                    return Ok((_stamp,_rowid,data));
                }
                Ok(out)
            },
        )?;
        let next_cursor = rows
            .last()
            .filter(|_| has_more)
            .map(|(timestamp, rowid, row)| {
                let cursor = if let Some((lane, projected_scope)) = row_page.as_ref() {
                    json!([lane.tag(), timestamp,
                        URL_SAFE_NO_PAD.encode(row["id"].as_str().expect("projected ID")), projected_scope])
                } else if q.account_scoped {
                    json!([timestamp, URL_SAFE_NO_PAD.encode(row["id"].as_str().expect("normalized ID")), scope])
                } else {
                    json!([timestamp, rowid, scope])
                };
                URL_SAFE_NO_PAD.encode(cursor.to_string())
            });
        let items: Vec<_> = rows.into_iter().map(|(_, _, msg)| msg).collect();
        Ok(json!({"items":items,"total":total,"has_more":has_more,"next_cursor":next_cursor}))
    }

    /// Bounded typed projection for the one-conversation verified media export.
    /// Use the same filters and row ordering as the scoped desktop read, but
    /// never read raw extras, materialize a JSON page, or count unbounded rows.
    #[cfg(target_os = "linux")]
    pub(crate) fn filtered_bundle_ids(&self, q: &Query, max_rows: usize) -> Result<Vec<String>> {
        if !matches!(self.location, StoreLocation::Snapshot(_)) || !(1..=16).contains(&max_rows) {
            return Err(bad("filtered bundle requires one explicit conversation"));
        }
        let mut db=self.connection()?;
        let tx=db.transaction()?;
        let (filters, mut values)=exact_message_filter_sql(&tx,q)?;
        values.push(Sql::Integer(max_rows as i64+1));
        let mut stmt=tx.prepare(&format!(
            "SELECT m.id FROM messages m {filters}
             ORDER BY m.timestamp DESC,{SCOPED_MESSAGE_ORDER} DESC LIMIT ?"))?;
        let mut rows=stmt.query(params_from_iter(values.iter()))?;
        let mut ids=Vec::with_capacity(max_rows);
        while let Some(row)=rows.next()? {
            if ids.len()==max_rows {return Err(StoreError::TooLarge);}
            ids.push(row.get(0)?);
        }
        Ok(ids)
    }

    pub fn message_context(&self, q: &ContextQuery) -> Result<Option<Value>> {
        self.message_context_with_native_order(q, false)
    }

    pub(crate) fn native_desktop_message_context(&self, q: &ContextQuery) -> Result<Option<Value>> {
        self.message_context_with_native_order(q, true)
    }

    fn message_context_with_native_order(&self, q: &ContextQuery, native: bool) -> Result<Option<Value>> {
        let window = q.validate(false)?;
        let mut db = self.connection()?;
        let tx = db.transaction()?;
        let archive = match &self.location {
            StoreLocation::Live(archive) if native => Some(archive),
            _ => None,
        };
        Self::context_in(&tx, q, &q.message_id, window, archive)
    }

    /// A legacy bearer handler with an exact Desktop grant may resolve an
    /// omitted conversation from a normalized ID inside the exact account PK.
    /// Live and guarded callers must independently verify the archive mode.
    pub(crate) fn live_legacy_context(&self, q: ContextQuery) -> Result<Option<Value>> {
        Self::legacy_context_in(self.connection()?, q)
    }

    /// Guarded caller supplies a freshly verified reader for this request.
    pub(crate) fn legacy_context_on(db: Connection, q: ContextQuery) -> Result<Option<Value>> {
        Self::legacy_context_in(Self::configure_reader(db)?, q)
    }

    fn legacy_context_in(mut db: Connection, mut q: ContextQuery) -> Result<Option<Value>> {
        if !q.conversation_id.is_empty() {
            return Err(bad("invalid message context scope"));
        }
        let window = q.validate_target(false)?;
        let tx = db.transaction()?;
        let conversation = {
            let mut stmt = tx.prepare(
                "SELECT conversation_id FROM messages
                 WHERE platform COLLATE BINARY=? AND account_id COLLATE BINARY=? AND id COLLATE BINARY=?
                 LIMIT 2",
            )?;
            let mut rows = stmt.query((&q.platform, &q.account_id, &q.message_id))?;
            let Some(row) = rows.next()? else { return Ok(None) };
            let conversation: Option<String> = row.get(0)?;
            if rows.next()?.is_some() { return Ok(None) }
            conversation
        };
        let Some(conversation) = conversation.filter(|id| valid_context_id(id)) else {
            return Ok(None);
        };
        q.conversation_id = conversation;
        Self::context_in(&tx, &q, &q.message_id, window, None)
    }

    /// Resolve a reference with exact archive scope. Never accept a source-server
    /// identifier from the client or return it in a result.
    pub fn quoted_message_context(&self, q: &ContextQuery) -> Result<Option<Value>> {
        let window = q.validate(true)?;
        let mut db = self.connection()?;
        let tx = db.transaction()?;
        let [platform, account, conversation] = q.scope();
        let Some(raw) = tx.query_row(
            "SELECT extra FROM messages WHERE platform=? AND account_id=? AND conversation_id=? AND id=?",
            (platform, account, conversation, &q.reference_message_id),
            |r| extra(r, 0),
        ).optional()? else { return Ok(None) };
        let Some(source) = raw
            .get("wechat_quote")
            .and_then(|value| value.get("target_server_id"))
            .and_then(Value::as_str)
            .filter(|source| {
                let bytes = source.as_bytes();
                (1..=20).contains(&bytes.len())
                    && bytes.iter().all(u8::is_ascii_digit)
                    && bytes[0] != b'0'
            })
        else {
            return Ok(None);
        };
        let mut stmt = tx.prepare(
            "SELECT id FROM messages WHERE platform=? AND account_id=? AND conversation_id=?
                AND CASE WHEN json_valid(extra) THEN json_extract(extra, '$._hub_wechat_server_id') = ?
                    ELSE 0 END
                LIMIT 2"
        )?;
        let targets = stmt
            .query_map((platform, account, conversation, source), |r| {
                r.get::<_, String>(0)
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let [target] = targets.as_slice() else {
            return Ok(None);
        };
        if target == &q.reference_message_id || !valid_context_id(target) {
            return Ok(None);
        }
        let mut result = Self::context_in(&tx, q, target, window, None)?;
        if let Some(context) = &mut result {
            context["target_message_id"] = json!(target);
        }
        Ok(result)
    }

    fn context_in(
        db: &Connection,
        q: &ContextQuery,
        target: &str,
        (before, after): (usize, usize),
        ordinary_archive: Option<&EventStore>,
    ) -> Result<Option<Value>> {
        let [platform, account, conversation] = q.scope();
        let Some((timestamp, rowid)) = db.query_row(
            "SELECT timestamp,rowid FROM messages WHERE platform=? AND account_id=? AND conversation_id=? AND id=?",
            (platform, account, conversation, target),
            |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)),
        ).optional()? else { return Ok(None) };
        let scope = [
            Sql::Text(platform.to_owned()),
            Sql::Text(account.to_owned()),
            Sql::Text(conversation.to_owned()),
        ];
        let where_sql = "WHERE m.platform=? AND m.account_id=? AND m.conversation_id=?";
        let total = count(db, "messages m", where_sql, &scope)?;
        // Named conversation fields follow the legacy context call, which unlike search
        // does not synthesize a missing conversation avatar from a contact.
        let base = format!(
            "SELECT {MSG_SELECT},m.text,c.title,c.kind,c.avatar_url
            FROM messages m {MSG_JOIN} {where_sql}"
        );
        let mut budget = Budget::new();
        // Reserve room for the canonical quoted target's JSON field even when
        // control characters in the provided index identifiers require escaping.
        budget
            .consume(1024 * 6 + 32)
            .map_err(|_| StoreError::TooLarge)?;
        let page = |budget: &mut Budget, limit, older| {
            let sql = if older {
                format!(
                    "{base} AND (m.timestamp,m.rowid) < (?,?) ORDER BY m.timestamp DESC,m.rowid DESC LIMIT ?"
                )
            } else {
                format!(
                    "{base} AND (m.timestamp,m.rowid) > (?,?) ORDER BY m.timestamp,m.rowid LIMIT ?"
                )
            };
            let params = [
                &scope[..],
                &[
                    Sql::Integer(timestamp),
                    Sql::Integer(rowid),
                    Sql::Integer(limit as i64 + 1),
                ],
            ]
            .concat();
            collect(db, &sql, &params, budget, limit, |r| message(r, true))
        };
        let center_sql = format!("{base} AND m.rowid=?");
        let center_params = [&scope[..], &[Sql::Integer(rowid)]].concat();
        let (center, _) = collect(db, &center_sql, &center_params, &mut budget, 1, |r| {
            message(r, true)
        })?;
        let split_timestamp = ordinary_archive.is_some_and(|archive| archive.has_canonical_context_order(db))
            && db.query_row(
                "SELECT EXISTS(SELECT 1 FROM messages INDEXED BY native_messages_scope
                 WHERE platform=? AND account_id=? AND conversation_id=?
                   AND timestamp=? AND rowid<>?)",
                (platform, account, conversation, timestamp, rowid), |r| r.get::<_, bool>(0),
            ).unwrap_or(false);
        let split_page = |budget: &mut Budget, limit, older| {
            let (op, dir) = if older { ("<", "DESC") } else { (">", "ASC") };
            let equal = format!(
                "SELECT {MSG_SELECT},m.text,c.title,c.kind,c.avatar_url
                 FROM messages m INDEXED BY native_messages_scope {MSG_JOIN} {where_sql}
                 AND m.timestamp=? AND m.rowid {op} ? ORDER BY m.rowid {dir} LIMIT ?"
            );
            let mut params = scope.to_vec();
            params.extend([Sql::Integer(timestamp), Sql::Integer(rowid), Sql::Integer(limit as i64 + 1)]);
            let (mut items, more) = collect(db, &equal, &params, budget, limit, |r| message(r, true))?;
            if more {
                return Ok((items, true));
            }
            // The timestamp ranges are disjoint and the equal part is closer to
            // the pivot. Keep a single budget and allocate exactly one sentinel
            // across the whole side, including when the equal part filled it.
            let remaining = limit - items.len();
            let other = format!(
                "{base} AND m.timestamp {op} ? ORDER BY m.timestamp {dir},m.rowid {dir} LIMIT ?"
            );
            params.truncate(scope.len() + 1);
            params.push(Sql::Integer(remaining as i64 + 1));
            let (rest, more) = collect(db, &other, &params, budget, remaining, |r| message(r, true))?;
            items.extend(rest);
            Ok::<_, StoreError>((items, more))
        };
        let (older, has_more_before, newer, has_more_after) = if split_timestamp {
            let (older, more_before) = split_page(&mut budget, before, true)?;
            let (newer, more_after) = split_page(&mut budget, after, false)?;
            (older, more_before, newer, more_after)
        } else {
            let (older, more_before) = page(&mut budget, before, true)?;
            let (newer, more_after) = page(&mut budget, after, false)?;
            (older, more_before, newer, more_after)
        };
        let target_index = older.len();
        let items = older
            .into_iter()
            .rev()
            .chain(center)
            .chain(newer)
            .map(|(_, _, value)| value)
            .collect::<Vec<_>>();
        Ok(Some(
            json!({"items":items,"total":total,"target_index":target_index,
                       "has_more_before":has_more_before,"has_more_after":has_more_after}),
        ))
    }

    pub fn contacts(&self, q: &Query) -> Result<Value> {
        self.contacts_inner(q,true)
    }

    /// Guarded legacy view cannot use redacted metadata as a search oracle.
    pub(crate) fn projected_contacts(&self, q: &Query) -> Result<Value> {
        self.contacts_inner(q,false)
    }

    fn contacts_inner(&self, q: &Query, private_aliases: bool) -> Result<Value> {
        let (limit, offset) = q.pages()?;
        let mut c = Conditions::default();
        c.scope("c.platform", q.platform());
        if !q.account_id.is_empty() {
            c.push("c.account_id = ?", q.account_id.clone());
        }
        if !q.kind.is_empty() {
            c.push("c.kind = ?", q.kind.clone());
        }
        if !q.q.is_empty() {
            let pattern = like(&q.q);
            if private_aliases {
                c.clauses.push(
                    "(c.name LIKE ? ESCAPE '\\' OR c.id LIKE ? ESCAPE '\\' OR
                    (CASE WHEN json_valid(c.extra) THEN (
                    json_extract(c.extra,'$.nickname') LIKE ? ESCAPE '\\' OR
                    json_extract(c.extra,'$.nick_name') LIKE ? ESCAPE '\\' OR
                    json_extract(c.extra,'$.alias') LIKE ? ESCAPE '\\' OR
                    json_extract(c.extra,'$.remark') LIKE ? ESCAPE '\\') ELSE 0 END))".into(),
                );
                c.values.extend((0..6).map(|_| Sql::Text(pattern.clone())));
            } else {
                c.clauses.push("(c.name LIKE ? ESCAPE '\\' OR c.id LIKE ? ESCAPE '\\')".into());
                c.values.extend([Sql::Text(pattern.clone()), Sql::Text(pattern)]);
            }
        }
        let mut db = self.connection()?;
        let tx = db.transaction()?;
        let where_sql = c.sql();
        let total = count(&tx, "contacts c", &where_sql, &c.values)?;
        c.values.extend([Sql::Integer(limit), Sql::Integer(offset)]);
        let (extra_fields, link) = if private_aliases {
            ("c.avatar_url,c.kind,c.extra,ci.identity_id",
             "LEFT JOIN contact_identities ci ON ci.platform=c.platform AND ci.account_id=c.account_id AND ci.contact_id=c.id")
        } else {
            ("NULL,c.kind,NULL,NULL", "")
        };
        let sql = format!("SELECT c.id,c.platform,c.account_id,c.name,{extra_fields}
            FROM contacts c {link}
            {where_sql} ORDER BY c.name COLLATE NOCASE,c.id,c.platform,c.account_id LIMIT ? OFFSET ?");
        let mut budget = Budget::new();
        let (items, _) = collect(&tx, &sql, &c.values, &mut budget, usize::MAX, |r| {
            Ok(json!({
                "id":r.get::<_,String>(0)?, "platform":r.get::<_,String>(1)?, "account_id":r.get::<_,String>(2)?,
                "name":string(r,3)?, "avatar_url":string(r,4)?, "kind":string(r,5)?,
                "extra":extra(r,6)?, "identity_id":string(r,7)?
            }))
        })?;
        let (accounts_query, account_args) = if q.account_scoped {
            let exact_native = private_aliases && q.platform() != "all" && !q.account_id.is_empty()
                && matches!(&self.location, StoreLocation::Live(archive) if archive.has_canonical_contacts(&tx));
            (
                if exact_native {
                    "SELECT platform,account_id FROM contacts WHERE platform=? AND account_id=? LIMIT 1"
                } else {
                    "SELECT DISTINCT platform,account_id FROM contacts WHERE platform=? AND account_id=? ORDER BY platform,account_id"
                },
                vec![Sql::Text(q.platform.clone()), Sql::Text(q.account_id.clone())],
            )
        } else {
            ("SELECT DISTINCT platform,account_id FROM contacts ORDER BY platform,account_id", vec![])
        };
        let (accounts, _) = collect(
            &tx,
            accounts_query,
            &account_args,
            &mut budget,
            usize::MAX,
            |r| Ok(json!({"platform":r.get::<_,String>(0)?,"account_id":r.get::<_,String>(1)?})),
        )?;
        Ok(json!({"items":items,"total":total,"accounts":accounts}))
    }
}

#[derive(Default, Clone)]
struct Conditions {
    clauses: Vec<String>,
    values: Vec<Sql>,
}

impl Conditions {
    fn push(&mut self, clause: impl Into<String>, value: impl Into<Sql>) {
        self.clauses.push(clause.into());
        self.values.push(value.into());
    }
    fn insert(&mut self, at: usize, clause: &str, value: impl Into<Sql>) {
        self.clauses.insert(at, clause.to_owned());
        self.values.insert(at, value.into());
    }
    fn and_sql(&self) -> String {
        if self.clauses.is_empty() { String::new() }
        else { format!("AND {}", self.clauses.join(" AND ")) }
    }
    fn scope(&mut self, column: &str, platform: &str) {
        if platform != "all" {
            self.push(format!("{column} = ?"), platform.to_owned());
        }
    }
    fn sql(&self) -> String {
        if self.clauses.is_empty() {
            String::new()
        } else {
            format!("WHERE {}", self.clauses.join(" AND "))
        }
    }
}

fn message_scope(q:&Query,scoped:bool)->Conditions {
    let mut conditions=Conditions::default();
    if scoped {
        // Python /api/messages requires the full triple; omitted values return no rows.
        conditions.push("m.platform = ?",q.platform().to_owned());
        conditions.push("m.account_id = ?",q.account_id.clone());
        conditions.push("m.conversation_id = ?",q.conversation_id.clone());
    } else {
        conditions.scope("m.platform",q.platform());
        if !q.account_id.is_empty(){conditions.push("m.account_id = ?",q.account_id.clone());}
        if !q.conversation_id.is_empty(){conditions.push("m.conversation_id = ?",q.conversation_id.clone());}
    }
    conditions
}

/// Shared exact-conversation predicates for bounded exports, evaluated on the
/// caller's archive transaction. Authorization and mode checks are upstream.
pub(crate) fn exact_message_filter_sql(db:&Connection,q:&Query)->Result<(String,Vec<Sql>)> {
    q.pages()?;
    if !q.account_scoped || q.platform.is_empty() || q.platform=="all"
        || q.account_id.is_empty() || q.conversation_id.is_empty()
        || !q.cursor.is_empty() || q.offset.is_some() || q.limit.is_some() {
        return Err(bad("filtered export requires one explicit conversation"));
    }
    if matches!((parse_bound(&q.start,false)?,parse_bound(&q.end,true)?),
        (Some(start),Some(end)) if start>end) {
        return Err(bad("invalid ordered date bounds"));
    }
    // Detached schemas may declare NOCASE/custom column collation.
    let mut conditions=Conditions::default();
    conditions.push("m.platform COLLATE BINARY = ?",q.platform.clone());
    conditions.push("m.account_id COLLATE BINARY = ?",q.account_id.clone());
    conditions.push("m.conversation_id COLLATE BINARY = ?",q.conversation_id.clone());
    let fts_index=conditions.clauses.len();
    if let Some(phrase)=append_message_filters(db,q,&mut conditions)? {
        conditions.insert(fts_index,FTS_ROWID_MEMBERSHIP,phrase);
    }
    Ok((conditions.sql(),conditions.values))
}

pub(crate) fn valid_search_export_keyword(keyword: &str) -> bool {
    !keyword.trim().is_empty() && keyword.len() <= 512 && !keyword.chars().any(char::is_control)
}

/// Exact-account search export predicates. Reuse the native search's literal
/// matching and quoted FTS candidate narrowing, not its preview or cursor.
pub(crate) fn exact_search_export_filter_sql(
    db: &Connection, scope: &crate::plugins::AccountScope, keyword: &str,
) -> Result<(String, Vec<Sql>)> {
    if scope.platform == "all" || !PLATFORMS.contains(&scope.platform.as_str())
        || scope.account_id.is_empty() || !valid_search_export_keyword(keyword)
    {
        return Err(bad("invalid exact-account search export selection"));
    }
    let mut conditions = Conditions::default();
    conditions.push("m.platform COLLATE BINARY = ?", scope.platform.clone());
    conditions.push("m.account_id COLLATE BINARY = ?", scope.account_id.clone());
    let fts_index = conditions.clauses.len();
    let query = Query { q: keyword.to_owned(), ..Query::default() };
    if let Some(phrase) = append_message_filters(db, &query, &mut conditions)? {
        conditions.insert(fts_index, FTS_ROWID_MEMBERSHIP, phrase);
    }
    Ok((conditions.sql(), conditions.values))
}

fn append_message_filters(db:&Connection,q:&Query,conditions:&mut Conditions)->Result<Option<String>> {
    let fts=if q.q.chars().take(3).count()==3 && has_fts(db)? {
        Some(format!("\"{}\"",q.q.replace('"',"\"\"")))
    } else { None };
    append_literal_filters(q, conditions)?;
    Ok(fts)
}

fn append_literal_filters(q:&Query,conditions:&mut Conditions)->Result<()> {
    if !q.q.is_empty(){conditions.push("m.text LIKE ? ESCAPE '\\'",like(&q.q));}
    if !q.kind_type.is_empty(){conditions.push("m.type = ?",q.kind_type.clone());}
    if let Some(start)=parse_bound(&q.start,false)?{conditions.push("m.timestamp >= ?",start);}
    if let Some(end)=parse_bound(&q.end,true)?{conditions.push("m.timestamp <= ?",end);}
    Ok(())
}

fn has_fts(db: &Connection) -> rusqlite::Result<bool> {
    db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='messages_fts')",
        [],
        |r| r.get(0),
    )
}

const FTS_ROWID_MEMBERSHIP: &str = "m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)";
const STOCK_TRIGRAM_FTS: &str =
    "CREATE VIRTUAL TABLE messages_fts USING fts5( text, content='messages', content_rowid='rowid', tokenize='trigram' )";

/// Do not change the plan for a nonstandard schema, fallback-only archive,
/// live/guarded profile, context lookup or list. FTS5 enumerates rowids once.
fn stock_trigram_fts(db: &Connection) -> rusqlite::Result<bool> {
    let schema = db.query_row(
        "SELECT sql FROM main.sqlite_schema WHERE type='table' AND name='messages_fts'",
        [], |r| r.get::<_, Option<String>>(0),
    ).optional()?.flatten();
    Ok(schema.is_some_and(|sql| sql.split_ascii_whitespace().eq(STOCK_TRIGRAM_FTS.split_ascii_whitespace())))
}

fn count_fts_rowids(db: &Connection, conditions: &Conditions, phrase: &str) -> rusqlite::Result<i64> {
    let sql = format!("SELECT COUNT(*) FROM main.messages_fts
        CROSS JOIN main.messages m ON m.rowid = messages_fts.rowid
        WHERE messages_fts MATCH ? {}", conditions.and_sql());
    db.query_row(&sql, params_from_iter([Sql::Text(phrase.to_owned())].iter().chain(&conditions.values)), |r| r.get(0))
}

fn count(db: &Connection, table: &str, where_sql: &str, values: &[Sql]) -> rusqlite::Result<i64> {
    db.query_row(
        &format!("SELECT COUNT(*) FROM {table} {where_sql}"),
        params_from_iter(values.iter()),
        |r| r.get(0),
    )
}

struct Budget(usize);

impl Budget {
    fn new() -> Self {
        Self(MAX_JSON_ROW_BYTES)
    }

    fn consume(&mut self, bytes: usize) -> io::Result<()> {
        self.0 = self
            .0
            .checked_sub(bytes)
            .ok_or_else(|| io::Error::other("response budget"))?;
        Ok(())
    }
}

impl Write for Budget {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.consume(bytes.len())?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

trait JsonRow {
    fn value(&self) -> &Value;
}

impl JsonRow for Value {
    fn value(&self) -> &Value {
        self
    }
}

impl JsonRow for (i64, i64, Value) {
    fn value(&self) -> &Value {
        &self.2
    }
}

fn collect<T: JsonRow>(
    db: &Connection,
    sql: &str,
    values: &[Sql],
    budget: &mut Budget,
    max_rows: usize,
    mut map: impl FnMut(&Row<'_>) -> rusqlite::Result<T>,
) -> Result<(Vec<T>, bool)> {
    let mut stmt = db.prepare(sql)?;
    let mut rows = stmt.query(params_from_iter(values.iter()))?;
    let mut out = Vec::new();
    while let Some(row) = rows.next()? {
        if out.len() == max_rows {
            return Ok((out, true));
        }
        let mapped = map(row)?;
        budget.consume(1).map_err(|_| StoreError::TooLarge)?; // comma or delimiter
        serde_json::to_writer(&mut *budget, mapped.value()).map_err(|_| StoreError::TooLarge)?;
        out.push(mapped);
    }
    Ok((out, false))
}

fn string(r: &Row<'_>, index: usize) -> rusqlite::Result<Option<String>> {
    r.get(index)
}

fn extra(r: &Row<'_>, index: usize) -> rusqlite::Result<Value> {
    Ok(string(r, index)?
        .and_then(|raw| raw.parse::<Value>().ok())
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({})))
}

fn message(r: &Row<'_>, with_conversation: bool) -> rusqlite::Result<(i64, i64, Value)> {
    let mut attributes = extra(r, 13)?;
    let object = attributes.as_object_mut().expect("extra always object");
    object.remove("_hub_wechat_server_id");
    if let Some(quote) = object
        .get_mut("wechat_quote")
        .and_then(Value::as_object_mut)
    {
        quote.remove("target_server_id");
    }
    let timestamp = r.get::<_, i64>(7)?;
    let mut data = json!({"id":r.get::<_,String>(1)?, "platform":r.get::<_,String>(2)?,
        "account_id":r.get::<_,String>(3)?, "conversation_id":r.get::<_,String>(4)?,
        "sender_id":string(r,5)?, "sender_name":string(r,6)?, "timestamp":timestamp,
        "type":string(r,8)?, "is_self":r.get::<_,bool>(9)?, "media_url":string(r,10)?,
        "file_name":string(r,11)?, "raw_type":string(r,12)?, "extra":attributes,
        "sender_avatar_url":string(r,14)?, "text":string(r,15)?});
    if with_conversation {
        data["conversation_title"] = json!(string(r, 16)?);
        data["conversation_kind"] = json!(string(r, 17)?);
        data["conversation_avatar_url"] = json!(string(r, 18)?);
    }
    Ok((timestamp, r.get(0)?, data))
}

fn like(q: &str) -> String {
    format!(
        "%{}%",
        q.replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_")
    )
}

fn decode_cursor(cursor: &str, scope: &str) -> Result<Option<(i64, i64)>> {
    if cursor.is_empty() {
        return Ok(None);
    }
    let error = || bad("invalid page cursor");
    let payload = URL_SAFE_NO_PAD.decode(cursor).map_err(|_| error())?;
    let (stamp, rowid, encoded): (i64, i64, String) =
        serde_json::from_slice(&payload).map_err(|_| error())?;
    if rowid < 1 || scope != encoded {
        return Err(error());
    }
    Ok(Some((stamp, rowid)))
}

fn decode_scoped_cursor(cursor: &str, scope: &str) -> Result<Option<(i64, Sql)>> {
    if cursor.is_empty() {
        return Ok(None);
    }
    let error = || bad("invalid page cursor");
    let payload = URL_SAFE_NO_PAD.decode(cursor).map_err(|_| error())?;
    let (stamp, encoded_id, encoded): (i64, String, String) =
        serde_json::from_slice(&payload).map_err(|_| error())?;
    let id = String::from_utf8(URL_SAFE_NO_PAD.decode(encoded_id).map_err(|_| error())?)
        .map_err(|_| error())?;
    if !valid_context_id(&id) || scope != encoded {
        return Err(error());
    }
    Ok(Some((stamp, Sql::Text(id))))
}

fn row_page_scope(lane: RowPageLane, q: &Query, archive_id: &str, grant_id: &str) -> String {
    let fields = (lane.tag(), archive_id, grant_id, q.platform(), &q.account_id,
        &q.conversation_id, &q.q, &q.kind_type, &q.start, &q.end);
    hex::encode(Sha256::digest(serde_json::to_vec(&fields).expect("strings serialize")))
}

fn guarded_page_anchor(db: &Connection, purpose: GuardedV9Page, q: &Query, scope: &str)
    -> Result<Option<(i64, Sql)>> {
    if q.cursor.is_empty() { return Ok(None); }
    let invalid = || bad(match purpose {
        GuardedV9Page::Search => "invalid legacy search cursor; restart at the first page",
        GuardedV9Page::Messages => "invalid legacy message cursor; restart at the first page",
    });
    let payload = URL_SAFE_NO_PAD.decode(&q.cursor).map_err(|_| invalid())?;
    let (version, timestamp, encoded_id, cursor_scope): (String, i64, String, String) =
        serde_json::from_slice(&payload).map_err(|_| invalid())?;
    if version != purpose.tag() || cursor_scope != scope { return Err(invalid()); }
    let id = String::from_utf8(URL_SAFE_NO_PAD.decode(encoded_id).map_err(|_| invalid())?)
        .map_err(|_| invalid())?;
    if id.is_empty() || id.len() > 256 || id.chars().any(char::is_control) {
        return Err(invalid());
    }
    // The primary key finds at most one exact account row; the remaining
    // predicates verify that the anchor belongs to these narrower filters.
    // All rowids stay inside this transaction, including failure paths.
    let mut predicates = Conditions::default();
    predicates.push("m.platform = ?", q.platform().to_owned());
    predicates.push("m.account_id = ?", q.account_id.clone());
    predicates.push("m.id = ?", id);
    predicates.push("m.timestamp = ?", timestamp);
    if !q.conversation_id.is_empty() {
        predicates.push("m.conversation_id = ?", q.conversation_id.clone());
    }
    // Guarded v9 stores the projected messages but no stock FTS table.
    // Check the same literal/type/bounds on this one PK candidate directly.
    append_literal_filters(q, &mut predicates)?;
    let sql = format!("SELECT m.rowid FROM messages m {}", predicates.sql());
    let rowid: Option<i64> = db.query_row(&sql,
        params_from_iter(predicates.values.iter()), |row| row.get(0)).optional()?;
    let rowid = rowid.ok_or_else(invalid)?;
    Ok(Some((timestamp, Sql::Integer(rowid))))
}

fn live_legacy_page_anchor(db: &Connection, lane: RowPageLane, q: &Query, scope: &str,
    mut filtered: Conditions) -> Result<Option<(i64, Sql)>> {
    if q.cursor.is_empty() { return Ok(None); }
    let invalid = || bad("invalid native legacy cursor; restart at the first page");
    let payload = URL_SAFE_NO_PAD.decode(&q.cursor).map_err(|_| invalid())?;
    let (version, timestamp, encoded_id, token_scope): (String, i64, String, String) =
        serde_json::from_slice(&payload).map_err(|_| invalid())?;
    if version != lane.tag() || token_scope != scope { return Err(invalid()); }
    let id = String::from_utf8(URL_SAFE_NO_PAD.decode(encoded_id).map_err(|_| invalid())?)
        .map_err(|_| invalid())?;
    if !valid_context_id(&id) { return Err(invalid()); }
    filtered.push("m.id COLLATE BINARY = ?", id);
    filtered.push("m.timestamp = ?", timestamp);
    let sql = format!("SELECT m.rowid FROM messages m {}", filtered.sql());
    let rowid: Option<i64> = db.query_row(&sql,
        params_from_iter(filtered.values.iter()), |row| row.get(0)).optional()?;
    Ok(Some((timestamp, Sql::Integer(rowid.ok_or_else(invalid)?))))
}

pub(crate) fn parse_bound(value: &str, end: bool) -> Result<Option<i64>> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }
    if let Ok(n) = value.parse::<i64>() {
        return Ok(Some(n));
    }
    let local = FixedOffset::east_opt(28_800).expect("valid UTC offset");
    if let Ok(date) = NaiveDate::parse_from_str(value, "%Y-%m-%d") {
        let (h, m, s) = if end { (23, 59, 59) } else { (0, 0, 0) };
        return Ok(date
            .and_hms_opt(h, m, s)
            .and_then(|dt| dt.and_local_timezone(local).single())
            .map(|dt| dt.timestamp()));
    }
    let parsed = DateTime::parse_from_rfc3339(value)
        .or_else(|_| DateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f%:z"));
    if let Ok(dt) = parsed {
        return Ok(Some(
            dt.timestamp() + i64::from(dt.timestamp() < 0 && dt.timestamp_subsec_nanos() > 0),
        ));
    }
    for layout in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(dt) = NaiveDateTime::parse_from_str(value, layout)
            && let Some(dt) = dt.and_local_timezone(local).single()
        {
            return Ok(Some(
                dt.timestamp() + i64::from(dt.timestamp() < 0 && dt.timestamp_subsec_nanos() > 0),
            ));
        }
    }
    Err(bad("invalid date bound"))
}

static URL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bhttps?://[^\s<>]+").unwrap());
static STRUCTURED: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)<[^>]{1,512}>|\b(?:",
        r"access[_-]?token|refresh[_-]?token|authorization|password|passwd|",
        r"client[_-]?secret|session[_-]?(?:key|token)|api[_-]?key|token|secret|cookie)\b\s*[:=]"
    ))
    .unwrap()
});

fn preview(value: &str) -> String {
    let redacted = URL.replace_all(value.trim(), "[链接]");
    if STRUCTURED.is_match(&redacted) {
        return "[结构化消息]".into();
    }
    let mut chars = redacted.chars();
    let mut result: String = chars.by_ref().take(240).collect();
    if chars.next().is_some() {
        result.push('…');
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::PathBuf};
    use tempfile::TempDir;


    #[test]
    fn stock_fts_count_preserves_exact_account_filters_literal_and_row_membership() {
        let (_temp,path,store)=fixture(false);
        {
            let db = Connection::open(&path).unwrap();
            db.execute_batch(&format!("{}; INSERT INTO messages_fts(messages_fts) VALUES('rebuild');",STOCK_TRIGRAM_FTS)).unwrap();
        }
        let db = store.connection().unwrap();
        assert!(stock_trigram_fts(&db).unwrap());
        let mut filters=Conditions::default();
        filters.push("m.platform = ?","wechat".to_owned());
        filters.push("m.account_id = ?","a".to_owned());
        filters.push("m.text LIKE ? ESCAPE '\\'",like("100%"));
        let needle="\"100%\"";
        let direct=count_fts_rowids(&db,&filters,needle).unwrap();
        filters.push(FTS_ROWID_MEMBERSHIP,needle.to_owned());
        assert_eq!(direct,1);
        assert_eq!(direct,count(&db,"messages m",&filters.sql(),&filters.values).unwrap());
        let page=store.search(&Query{platform:"wechat".into(),account_id:"a".into(),
            account_scoped:true,q:"100%".into(),..Default::default()}).unwrap();
        assert_eq!(page["total"],1);
        assert_eq!(page["items"][0]["id"],"z");
        let (_t,_p,nonstock)=fixture(true);
        assert!(!stock_trigram_fts(&nonstock.connection().unwrap()).unwrap(),"noncanonical shape uses original SQL");
    }
    fn fixture(fts: bool) -> (TempDir, PathBuf, Store) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("normalized.db");
        let db = Connection::open(&path).unwrap();
        db.execute_batch(r#"
            PRAGMA user_version = 9;
            CREATE TABLE conversations (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                title TEXT, kind TEXT, avatar_url TEXT, member_count INTEGER,
                last_message TEXT, last_timestamp INTEGER, message_count INTEGER NOT NULL DEFAULT 0,
                extra TEXT, PRIMARY KEY (platform,account_id,id));
            CREATE TABLE messages (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                conversation_id TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
                timestamp INTEGER NOT NULL DEFAULT 0, type TEXT, text TEXT,
                is_self INTEGER NOT NULL DEFAULT 0, media_url TEXT, file_name TEXT,
                raw_type TEXT, preview TEXT, extra TEXT, PRIMARY KEY (platform,account_id,id));
            CREATE TABLE contacts (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                name TEXT,avatar_url TEXT,kind TEXT,extra TEXT, PRIMARY KEY (platform,account_id,id));
            CREATE TABLE conversation_metadata (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                note TEXT, tags TEXT, archived INTEGER,
                PRIMARY KEY(platform,account_id,conversation_id));
            CREATE TABLE contact_identities (
                platform TEXT NOT NULL, account_id TEXT NOT NULL, contact_id TEXT NOT NULL,
                identity_id TEXT,PRIMARY KEY(platform,account_id,contact_id));
            INSERT INTO conversations VALUES ('wechat','a','room','Room','group','other.png',2,'access_token=fixture',100,2,'{}');
            INSERT INTO conversations VALUES ('wechat','b','room','Different account','group',NULL,2,'https://example.test/',100,1,'{}');
            INSERT INTO contacts VALUES ('wechat','a','room','Room','room.png','group','{"alias":"Team 课程"}');
            INSERT INTO contacts VALUES ('wechat','a','sender','Name','sender.png','private','malformed');
            INSERT INTO conversation_metadata VALUES ('wechat','a','room','Note','["tag"]',1);
            INSERT INTO contact_identities VALUES ('wechat','a','room','unified-id');
            INSERT INTO messages (platform,account_id,id,conversation_id,sender_id,timestamp,type,text,extra)
                VALUES ('wechat','a','z','room','sender',100,'text','课程 100% pure','{"_hub_wechat_server_id":"39","wechat_quote":{"target_server_id":"38","title":"fixture"}}'),
                       ('wechat','a','a','room','sender',100,'text','课程 100x pure','{}'),
                       ('wechat','b','a','room','sender',100,'text','different account','{}');
        "#).unwrap();
        if fts {
            db.execute_batch("CREATE VIRTUAL TABLE messages_fts USING fts5(text,content='messages',content_rowid='rowid',tokenize='trigram'); INSERT INTO messages_fts(messages_fts) VALUES('rebuild');").unwrap();
        }
        drop(db);
        let store = Store::open(&path).unwrap();
        (temp, path, store)
    }

    #[test]
    fn populated_archive_can_be_read_without_mutation() {
        for enabled in [false, true] {
            let (_tmp, path, store) = fixture(enabled);
            let original = fs::read(&path).unwrap();
            assert_eq!(store.status().unwrap()["fts_enabled"], enabled);
            let found = store
                .search(&Query {
                    q: "课程 100%".into(),
                    ..Default::default()
                })
                .unwrap();
            assert_eq!(found["total"], 1);
            assert_eq!(found["items"][0]["id"], "z");
            assert_eq!(found["items"][0]["sender_avatar_url"], "sender.png");
            assert_eq!(found["items"][0]["conversation_avatar_url"], "other.png");
            assert!(
                found["items"][0]["extra"]
                    .get("_hub_wechat_server_id")
                    .is_none()
            );
            assert!(
                found["items"][0]["extra"]["wechat_quote"]
                    .get("target_server_id")
                    .is_none()
            );
            let list = store.conversations(&Query::default()).unwrap();
            assert_eq!(list["items"][0]["last_message"], "[结构化消息]");
            assert_eq!(list["items"][0]["tags"], json!(["tag"]));
            assert_eq!(list["items"][0]["avatar_url"], "room.png");
            let contacts = store
                .contacts(&Query {
                    q: "Team 课程".into(),
                    ..Default::default()
                })
                .unwrap();
            assert_eq!(contacts["items"][0]["identity_id"], "unified-id");
            assert_eq!(contacts["total"], 1);
            assert_eq!(fs::read(&path).unwrap(), original);
        }
    }

    #[test]
    fn tied_messages_page_without_replay_and_reject_cross_scope_cursor() {
        let (_tmp, _path, store) = fixture(false);
        let mut q = Query {
            platform: "wechat".into(),
            account_id: "a".into(),
            conversation_id: "room".into(),
            limit: Some(1),
            ..Default::default()
        };
        let first = store.messages(&q).unwrap();
        assert_eq!(first["items"][0]["id"], "a");
        assert_eq!(first["total"], 2);
        q.cursor = first["next_cursor"].as_str().unwrap().to_owned();
        assert_eq!(q.cursor, "WzEwMCwyLCIxODBmYmRkZDY4MmI3ZmYyIl0"); // frozen cross-language fixture
        let next = store.messages(&q).unwrap();
        assert_eq!(next["items"][0]["id"], "z");
        assert_eq!(next["total"], 2);
        assert_eq!(next["next_cursor"], Value::Null);
        q.account_id = "b".into();
        assert!(matches!(
            store.messages(&q),
            Err(StoreError::BadQuery("invalid page cursor"))
        ));
    }

    #[test]
    fn date_bounds_follow_cst_and_invalid_values_fail_closed() {
        assert_eq!(parse_bound("1970-01-01", false).unwrap(), Some(-28_800));
        assert_eq!(parse_bound("1970-01-01", true).unwrap(), Some(57_599));
        assert_eq!(parse_bound("1970-01-01T08:00:00", false).unwrap(), Some(0));
        assert_eq!(
            parse_bound("1970-01-01T08:00:00+08:00", false).unwrap(),
            Some(0)
        );
        assert!(parse_bound("1970-02-30", false).is_err());
    }

    #[test]
    fn rejects_non_archive_after_readonly_open() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bad.db");
        let db = Connection::open(&path).unwrap();
        db.pragma_update(None, "user_version", 9).unwrap();
        drop(db);
        assert!(matches!(Store::open(path), Err(StoreError::Schema)));
        let missing = dir.path().join("missing.db");
        assert!(Store::open(&missing).is_err());
        assert!(!missing.exists());
    }
    #[test]
    fn response_budget_rejects_oversized_page_but_not_a_smaller_page() {
        let (_tmp, path, store) = fixture(false);
        let writer = Connection::open(path).unwrap();
        let body = "x".repeat(800_000);
        for i in 0..7 {
            writer
                .execute(
                    "INSERT INTO messages
                (platform,account_id,id,conversation_id,timestamp,type,text)
                VALUES ('wechat','a',?1,'room',500,'text',?2)",
                    rusqlite::params![format!("large{i}"), body],
                )
                .unwrap();
        }
        drop(writer);
        let mut query = Query {
            platform: "wechat".into(),
            account_id: "a".into(),
            conversation_id: "room".into(),
            limit: Some(7),
            ..Default::default()
        };
        assert!(matches!(store.messages(&query), Err(StoreError::TooLarge)));
        query.limit = Some(2);
        let page = store.messages(&query).unwrap();
        assert_eq!(page["total"], 9);
        assert_eq!(page["items"].as_array().unwrap().len(), 2);
        assert!(page["next_cursor"].is_string());
    }
    #[test]
    fn message_context_returns_chronological_window_in_exact_scope() {
        let (_tmp, path, store) = fixture(false);
        let db = Connection::open(&path).unwrap();
        db.execute_batch(
            "INSERT INTO messages
            (platform,account_id,id,conversation_id,timestamp,text)
            VALUES ('wechat','a','early','room',30,'first'),
                   ('wechat','a','foreign-room','other-room',100,'wrong scope'),
                   ('wechat','a','late','room',150,'last');",
        )
        .unwrap();
        drop(db);
        let mut q = ContextQuery {
            platform: "wechat".into(),
            account_id: "a".into(),
            conversation_id: "room".into(),
            message_id: "a".into(),
            before: Some(1),
            after: Some(0),
            ..Default::default()
        };
        let window = store.message_context(&q).unwrap().unwrap();
        assert_eq!(window["items"][0]["id"], "z");
        assert_eq!(window["items"][1]["id"], "a");
        assert_eq!(window["items"][1]["conversation_title"], "Room");
        assert_eq!(window["total"], 4);
        assert_eq!(window["target_index"], 1);
        assert_eq!(window["has_more_before"], true);
        assert_eq!(window["has_more_after"], true);
        q.conversation_id = "other-room".into();
        assert!(store.message_context(&q).unwrap().is_none());
        q.conversation_id.clear();
        assert!(matches!(
            store.message_context(&q),
            Err(StoreError::BadQuery(_))
        ));
        assert!(
            serde_json::from_value::<ContextQuery>(json!({"source_message_id":"123"})).is_err()
        );
    }

    #[test]
    fn quote_resolves_only_a_unique_same_conversation_target_and_redacts_server_identifier() {
        let (_tmp, path, store) = fixture(false);
        let db = Connection::open(&path).unwrap();
        db.execute_batch(r#"INSERT INTO messages
            (platform,account_id,id,conversation_id,timestamp,text,extra)
            VALUES ('wechat','a','foreign-room','other-room',30,'wrong','{"_hub_wechat_server_id":"38"}'),
                   ('wechat','b','foreign-account','room',30,'wrong','{"_hub_wechat_server_id":"38"}'),
                   ('qq','a','foreign-platform','room',30,'wrong','{"_hub_wechat_server_id":"38"}'),
                   ('wechat','a','invalid-json','room',30,'malformed','{'),
                   ('wechat','a','wrong-type','room',30,'not a string id','{"_hub_wechat_server_id":38}');"#).unwrap();
        let mut q = ContextQuery {
            platform: "wechat".into(),
            account_id: "a".into(),
            conversation_id: "room".into(),
            reference_message_id: "z".into(),
            before: Some(0),
            after: Some(1),
            ..Default::default()
        };
        assert!(store.quoted_message_context(&q).unwrap().is_none());
        let put = |id: &str| {
            db.execute(
                "INSERT INTO messages (platform,account_id,id,conversation_id,timestamp,text,extra)
                VALUES ('wechat','a',?,'room',95,'target',?)",
                (id, r#"{"_hub_wechat_server_id":"38"}"#),
            )
            .unwrap();
        };
        put("canonical-target");
        let result = store.quoted_message_context(&q).unwrap().unwrap();
        assert_eq!(result["target_message_id"], "canonical-target");
        assert_eq!(result["items"][0]["id"], "canonical-target");
        assert_eq!(result["items"][1]["id"], "z");
        let result_json = result.to_string();
        assert!(!result_json.contains("server_id"));
        for wrong in ["other-room", "missing"] {
            q.conversation_id = wrong.into();
            assert!(store.quoted_message_context(&q).unwrap().is_none());
        }
        q.conversation_id = "room".into();
        put("duplicate");
        assert!(store.quoted_message_context(&q).unwrap().is_none());
        q.platform = "qq".into();
        assert!(matches!(
            store.quoted_message_context(&q),
            Err(StoreError::BadQuery(_))
        ));
    }
}
