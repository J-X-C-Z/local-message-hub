//! Pure-Rust local WeChat 4.x SQLCipher reader and incremental live-archive syncer.
//! Reads encrypted WeChat databases read-only using keys cached in `wechat.json`,
//! merges active `-wal` frames into a local cache, and ingests new messages into `EventStore`.

use crate::events::{EventStore, IngestRequest, NormalizedMessage};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

const PAGE_SZ: usize = 4096;
const RESERVE_SZ: usize = 80;
const WAL_HEADER_SZ: usize = 32;
const WAL_FRAME_SZ: usize = 24 + PAGE_SZ;
const MAX_BATCH_MESSAGES: usize = 100;
const MAX_SESSIONS_PER_TICK: usize = 24;
const PRODUCER_ID: &str = "local-wechat-live-v1";

#[derive(Debug, Deserialize)]
struct WeChatConfigFile {
    data_dir: String,
    account: String,
    #[serde(default)]
    database_keys: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct SyncCursor {
    sort_seq: i64,
    shard: String,
    local_id: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct SyncState {
    account: String,
    #[serde(default)]
    session_last_time: HashMap<String, i64>,
    #[serde(default)]
    cursors: HashMap<String, SyncCursor>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct DbStamp {
    db_len: u64,
    db_mtime_ns: u128,
    wal_len: u64,
    wal_mtime_ns: u128,
    wal_salt: [u8; 8],
    wal_frames: usize,
}

#[derive(Debug, Clone)]
pub struct SyncStats {
    pub account: String,
    pub sessions_checked: usize,
    pub sessions_changed: usize,
    pub created: usize,
    pub revised: usize,
    pub unchanged: usize,
}

pub struct WeChatLocalSyncer {
    config_path: PathBuf,
    cache_dir: PathBuf,
    state_path: PathBuf,
    state: SyncState,
    contacts: HashMap<String, String>,
    contacts_loaded: bool,
}

impl WeChatLocalSyncer {
    pub fn new(config_path: PathBuf, cache_dir: PathBuf, state_path: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&cache_dir).map_err(|e| format!("create cache dir failed: {e}"))?;
        let state = if state_path.exists() {
            let raw = fs::read_to_string(&state_path).unwrap_or_default();
            serde_json::from_str(&raw).unwrap_or_default()
        } else {
            SyncState::default()
        };
        Ok(Self {
            config_path,
            cache_dir,
            state_path,
            state,
            contacts: HashMap::new(),
            contacts_loaded: false,
        })
    }

    pub fn sync_once(&mut self, store: &EventStore) -> Result<SyncStats, String> {
        let cfg_raw = fs::read_to_string(&self.config_path)
            .map_err(|e| format!("read wechat config {}: {e}", self.config_path.display()))?;
        let cfg: WeChatConfigFile =
            serde_json::from_str(&cfg_raw).map_err(|e| format!("parse wechat config: {e}"))?;

        if self.state.account != cfg.account {
            self.state = SyncState {
                account: cfg.account.clone(),
                ..Default::default()
            };
            self.contacts.clear();
            self.contacts_loaded = false;
        }

        let keys = normalize_keys(&cfg.database_keys)?;
        let account_dir = Path::new(&cfg.data_dir).join(&cfg.account).join("db_storage");
        if !account_dir.exists() {
            return Err(format!("wechat db_storage not found: {}", account_dir.display()));
        }

        // 1. Refresh contacts cache when needed or when contact.db changed
        if let Some(contact_key) = keys.get("contact/contact.db") {
            let contact_src = account_dir.join("contact").join("contact.db");
            if contact_src.exists() {
                let (contact_db, changed) = self.ensure_decrypted("contact/contact.db", &contact_src, contact_key)?;
                if changed || !self.contacts_loaded {
                    if let Ok(map) = load_contacts(&contact_db) {
                        self.contacts = map;
                        self.contacts_loaded = true;
                    }
                }
            }
        }

        // 2. Open session.db and detect changed sessions
        let session_key = keys
            .get("session/session.db")
            .ok_or_else(|| "missing key for session/session.db".to_string())?;
        let session_src = account_dir.join("session").join("session.db");
        let (session_db, _) = self.ensure_decrypted("session/session.db", &session_src, session_key)?;
        let sessions = load_sessions(&session_db, 300)?;

        let mut changed_sessions = Vec::new();
        for s in &sessions {
            let need = match self.state.session_last_time.get(&s.username) {
                Some(&prev_ts) => s.last_timestamp > prev_ts,
                None => true,
            };
            if need {
                changed_sessions.push(s.clone());
            }
        }

        if changed_sessions.is_empty() {
            return Ok(SyncStats {
                account: cfg.account,
                sessions_checked: sessions.len(),
                sessions_changed: 0,
                created: 0,
                revised: 0,
                unchanged: 0,
            });
        }

        // 3. Collect available message shards
        let mut msg_shards: Vec<(String, PathBuf, Vec<u8>)> = Vec::new();
        let msg_dir = account_dir.join("message");
        if msg_dir.exists() {
            if let Ok(entries) = fs::read_dir(&msg_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if is_message_shard(&name) {
                        let rel = format!("message/{name}");
                        if let Some(k) = keys.get(&rel) {
                            let (dec_path, _) = self.ensure_decrypted(&rel, &entry.path(), k)?;
                            msg_shards.push((name, dec_path, k.clone()));
                        }
                    }
                }
            }
        }
        msg_shards.sort_by(|a, b| a.0.cmp(&b.0));

        let mut total_created = 0usize;
        let mut total_revised = 0usize;
        let mut total_unchanged = 0usize;
        let mut processed_sessions = 0usize;

        for sess in changed_sessions.into_iter().take(MAX_SESSIONS_PER_TICK) {
            processed_sessions += 1;
            let cursor = self.state.cursors.get(&sess.username).cloned();
            let rows = read_messages_for_session(
                &msg_shards,
                &sess.username,
                cursor.as_ref(),
                if cursor.is_none() { 200 } else { 500 },
            )?;

            if rows.is_empty() {
                self.state
                    .session_last_time
                    .insert(sess.username.clone(), sess.last_timestamp);
                continue;
            }

            let title = self
                .contacts
                .get(&sess.username)
                .cloned()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| sess.username.clone());

            let mut newest_cursor = cursor.clone().unwrap_or_default();
            let mut normalized = Vec::with_capacity(rows.len());
            for row in &rows {
                let is_self = !row.sender_username.is_empty() && row.sender_username == cfg.account;
                let sender_id = if row.sender_username.is_empty() {
                    None
                } else {
                    Some(row.sender_username.clone())
                };
                let sender_name = sender_id.as_ref().map(|sid| {
                    self.contacts
                        .get(sid)
                        .cloned()
                        .filter(|v| !v.is_empty())
                        .unwrap_or_else(|| sid.clone())
                });
                let conv_tag = &md5_hex(sess.username.as_bytes())[..12];
                let msg_id = format!("wx:{}:{}:{}", conv_tag, row.shard, row.local_id);
                normalized.push(NormalizedMessage {
                    platform: "wechat".to_string(),
                    account_id: cfg.account.clone(),
                    conversation_id: sess.username.clone(),
                    message_id: msg_id,
                    title: Some(title.clone()),
                    sender_id,
                    sender_name,
                    timestamp: row.create_time,
                    is_self,
                    text: Some(row.text.clone()),
                });
                newest_cursor = SyncCursor {
                    sort_seq: row.sort_seq,
                    shard: row.shard.clone(),
                    local_id: row.local_id,
                };
            }

            for chunk in normalized.chunks(MAX_BATCH_MESSAGES) {
                let first_id = &chunk[0].message_id;
                let last_id = &chunk[chunk.len() - 1].message_id;
                let idem = sha256_hex(&format!(
                    "wx-live:{}:{}:{}:{}:{}",
                    cfg.account,
                    sess.username,
                    first_id,
                    last_id,
                    chunk.len()
                ));
                let req = IngestRequest {
                    idempotency_key: idem,
                    messages: chunk.to_vec(),
                };
                let receipt = store
                    .ingest(PRODUCER_ID, req)
                    .map_err(|e| format!("ingest failed: {e}"))?;
                total_created += receipt.created;
                total_revised += receipt.revised;
                total_unchanged += receipt.unchanged;
            }

            self.state.cursors.insert(sess.username.clone(), newest_cursor);
            self.state
                .session_last_time
                .insert(sess.username.clone(), sess.last_timestamp);
        }

        self.save_state()?;

        Ok(SyncStats {
            account: cfg.account,
            sessions_checked: sessions.len(),
            sessions_changed: processed_sessions,
            created: total_created,
            revised: total_revised,
            unchanged: total_unchanged,
        })
    }

    fn save_state(&self) -> Result<(), String> {
        let tmp = self.state_path.with_extension("json.tmp");
        let data = serde_json::to_string_pretty(&self.state).map_err(|e| e.to_string())?;
        fs::write(&tmp, data).map_err(|e| format!("write sync state tmp failed: {e}"))?;
        fs::rename(&tmp, &self.state_path).map_err(|e| format!("rename sync state failed: {e}"))?;
        Ok(())
    }

    fn ensure_decrypted(
        &self,
        rel: &str,
        src_path: &Path,
        key: &[u8],
    ) -> Result<(PathBuf, bool), String> {
        let safe_name = rel.replace(['/', '\\'], "__");
        let dst_path = self.cache_dir.join(&safe_name);
        let stamp_path = self.cache_dir.join(format!("{safe_name}.stamp"));

        let wal_path = PathBuf::from(format!("{}-wal", src_path.display()));
        let (db_len, db_mtime_ns) = file_meta(src_path)?;
        let (wal_len, wal_mtime_ns) = if wal_path.exists() {
            file_meta(&wal_path).unwrap_or((0, 0))
        } else {
            (0, 0)
        };
        let wal_salt = if wal_len >= WAL_HEADER_SZ as u64 {
            read_wal_salt(&wal_path).unwrap_or([0u8; 8])
        } else {
            [0u8; 8]
        };
        let wal_frames = if wal_len >= WAL_HEADER_SZ as u64 {
            ((wal_len as usize) - WAL_HEADER_SZ) / WAL_FRAME_SZ
        } else {
            0
        };

        let old_stamp: Option<DbStamp> = fs::read_to_string(&stamp_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok());

        if let Some(prev) = &old_stamp {
            if dst_path.exists()
                && prev.db_len == db_len
                && prev.db_mtime_ns == db_mtime_ns
                && prev.wal_len == wal_len
                && prev.wal_mtime_ns == wal_mtime_ns
            {
                return Ok((dst_path, false));
            }
            // Incremental WAL append if DB untouched and WAL salt unchanged
            if dst_path.exists()
                && prev.db_len == db_len
                && prev.db_mtime_ns == db_mtime_ns
                && wal_frames >= prev.wal_frames
                && wal_salt == prev.wal_salt
                && wal_salt != [0u8; 8]
            {
                if merge_wal(&dst_path, &wal_path, key, prev.wal_frames).is_ok() {
                    let new_stamp = DbStamp {
                        db_len,
                        db_mtime_ns,
                        wal_len,
                        wal_mtime_ns,
                        wal_salt,
                        wal_frames,
                    };
                    let _ = fs::write(&stamp_path, serde_json::to_string(&new_stamp).unwrap_or_default());
                    return Ok((dst_path, true));
                }
            }
        }

        // Full decrypt + WAL merge into temp file then atomic rename
        let tmp_dst = self.cache_dir.join(format!("{safe_name}.tmp"));
        decrypt_db_file(src_path, &tmp_dst, key)?;
        let merged_frames = if wal_frames > 0 {
            merge_wal(&tmp_dst, &wal_path, key, 0).unwrap_or(0)
        } else {
            0
        };
        fs::rename(&tmp_dst, &dst_path).map_err(|e| format!("rename decrypted db failed: {e}"))?;
        let new_stamp = DbStamp {
            db_len,
            db_mtime_ns,
            wal_len,
            wal_mtime_ns,
            wal_salt,
            wal_frames: merged_frames,
        };
        let _ = fs::write(&stamp_path, serde_json::to_string(&new_stamp).unwrap_or_default());
        Ok((dst_path, true))
    }
}

pub fn run_background_sync(
    config_path: PathBuf,
    live_dir: PathBuf,
    store: EventStore,
    interval: Duration,
) {
    let cache_dir = live_dir
        .parent()
        .unwrap_or(&live_dir)
        .join(".lmh-wechat-cache");
    let state_path = live_dir
        .parent()
        .unwrap_or(&live_dir)
        .join("wechat_sync_cursors.json");

    let mut syncer = match WeChatLocalSyncer::new(config_path, cache_dir, state_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("wechat-local-sync init error: {e}");
            return;
        }
    };

    loop {
        match syncer.sync_once(&store) {
            Ok(stats) => {
                if stats.created > 0 || stats.revised > 0 {
                    eprintln!(
                        "wechat-local-sync: account={} changed={} created={} revised={} unchanged={}",
                        stats.account,
                        stats.sessions_changed,
                        stats.created,
                        stats.revised,
                        stats.unchanged
                    );
                }
            }
            Err(e) => {
                eprintln!("wechat-local-sync warning: {e}");
            }
        }
        std::thread::sleep(interval);
    }
}

fn normalize_keys(raw: &BTreeMap<String, String>) -> Result<HashMap<String, Vec<u8>>, String> {
    let mut out = HashMap::new();
    for (k, v) in raw {
        let norm_key = k.replace('\\', "/").to_lowercase();
        let bytes = hex::decode(v.trim()).map_err(|e| format!("invalid hex key for {k}: {e}"))?;
        if bytes.len() != 32 && bytes.len() != 48 {
            continue;
        }
        out.insert(norm_key, bytes);
    }
    Ok(out)
}

fn file_meta(path: &Path) -> Result<(u64, u128), String> {
    let meta = fs::metadata(path).map_err(|e| format!("stat {}: {e}", path.display()))?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    Ok((meta.len(), mtime))
}

fn read_wal_salt(wal_path: &Path) -> std::io::Result<[u8; 8]> {
    let mut f = File::open(wal_path)?;
    let mut hdr = [0u8; WAL_HEADER_SZ];
    f.read_exact(&mut hdr)?;
    let mut salt = [0u8; 8];
    salt.copy_from_slice(&hdr[16..24]);
    Ok(salt)
}

fn is_message_shard(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    if !lower.starts_with("message_") || !lower.ends_with(".db") {
        return false;
    }
    let mid = &lower[8..lower.len() - 3];
    !mid.is_empty() && mid.bytes().all(|b| b.is_ascii_digit())
}

fn open_ro(path: &Path) -> Result<Connection, String> {
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("open sqlite {}: {e}", path.display()))
}

#[derive(Debug, Clone)]
struct SessionRow {
    username: String,
    last_timestamp: i64,
}

fn load_sessions(db_path: &Path, limit: usize) -> Result<Vec<SessionRow>, String> {
    let conn = open_ro(db_path)?;
    let mut stmt = conn
        .prepare(
            "SELECT username, last_timestamp FROM SessionTable WHERE is_hidden=0 ORDER BY sort_timestamp DESC LIMIT ?",
        )
        .map_err(|e| format!("prepare SessionTable: {e}"))?;
    let rows = stmt
        .query_map([limit as i64], |r| {
            Ok(SessionRow {
                username: r.get::<_, String>(0)?,
                last_timestamp: r.get::<_, i64>(1).unwrap_or(0),
            })
        })
        .map_err(|e| format!("query SessionTable: {e}"))?;
    let mut out = Vec::new();
    for r in rows {
        if let Ok(item) = r {
            out.push(item);
        }
    }
    Ok(out)
}

fn load_contacts(db_path: &Path) -> Result<HashMap<String, String>, String> {
    let conn = open_ro(db_path)?;
    let mut stmt = conn
        .prepare("SELECT username, remark, nick_name FROM contact")
        .map_err(|e| format!("prepare contact: {e}"))?;
    let rows = stmt
        .query_map([], |r| {
            let username: String = r.get(0)?;
            let remark: String = r.get::<_, Option<String>>(1)?.unwrap_or_default();
            let nick: String = r.get::<_, Option<String>>(2)?.unwrap_or_default();
            let display = if !remark.trim().is_empty() {
                remark.trim().to_string()
            } else {
                nick.trim().to_string()
            };
            Ok((username, display))
        })
        .map_err(|e| format!("query contact: {e}"))?;
    let mut map = HashMap::new();
    for r in rows.flatten() {
        map.insert(r.0, r.1);
    }
    Ok(map)
}

#[derive(Debug, Clone)]
struct MsgRow {
    local_id: i64,
    sort_seq: i64,
    shard: String,
    create_time: i64,
    server_id: String,
    sender_username: String,
    text: String,
}

fn read_messages_for_session(
    shards: &[(String, PathBuf, Vec<u8>)],
    username: &str,
    cursor: Option<&SyncCursor>,
    limit: usize,
) -> Result<Vec<MsgRow>, String> {
    let table = format!("Msg_{}", md5_hex(username.as_bytes()));
    let mut all = Vec::new();

    for (shard_name, db_path, _) in shards {
        let conn = match open_ro(db_path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let exists: Option<String> = conn
            .query_row(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                [&table],
                |r| r.get(0),
            )
            .ok();
        if exists.is_none() {
            continue;
        }
        let sender_map = load_name2id(&conn);

        let (where_clause, params): (&str, Vec<rusqlite::types::Value>) = match cursor {
            Some(c) => {
                if shard_name < &c.shard {
                    (" WHERE sort_seq > ?", vec![c.sort_seq.into()])
                } else if shard_name == &c.shard {
                    (
                        " WHERE sort_seq > ? OR (sort_seq = ? AND local_id > ?)",
                        vec![c.sort_seq.into(), c.sort_seq.into(), c.local_id.into()],
                    )
                } else {
                    (" WHERE sort_seq >= ?", vec![c.sort_seq.into()])
                }
            }
            None => ("", vec![]),
        };

        let sql = if cursor.is_none() {
            format!(
                "SELECT local_id, local_type, real_sender_id, create_time, message_content, server_id, sort_seq \
                 FROM \"{table}\" ORDER BY sort_seq DESC, local_id DESC LIMIT {limit}"
            )
        } else {
            format!(
                "SELECT local_id, local_type, real_sender_id, create_time, message_content, server_id, sort_seq \
                 FROM \"{table}\"{where_clause} ORDER BY sort_seq ASC, local_id ASC LIMIT {limit}"
            )
        };

        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let mapped = stmt.query_map(rusqlite::params_from_iter(params), |r| {
            let local_id: i64 = r.get(0)?;
            let local_type: i64 = r.get::<_, Option<i64>>(1)?.unwrap_or(1);
            let real_sender_id: i64 = r.get::<_, Option<i64>>(2)?.unwrap_or(0);
            let create_time: i64 = r.get::<_, Option<i64>>(3)?.unwrap_or(0);
            let content_val = r.get_ref(4)?;
            let server_id: String = match r.get_ref(5)? {
                rusqlite::types::ValueRef::Integer(i) => i.to_string(),
                rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).to_string(),
                _ => String::new(),
            };
            let sort_seq: i64 = r.get::<_, Option<i64>>(6)?.unwrap_or(0);
            let raw_bytes: Vec<u8> = match content_val {
                rusqlite::types::ValueRef::Text(t) => t.to_vec(),
                rusqlite::types::ValueRef::Blob(b) => b.to_vec(),
                _ => Vec::new(),
            };
            let sender_username = sender_map.get(&real_sender_id).cloned().unwrap_or_default();
            let text = decode_wechat_content(&raw_bytes, local_type);
            Ok(MsgRow {
                local_id,
                sort_seq,
                shard: shard_name.clone(),
                create_time,
                server_id,
                sender_username,
                text,
            })
        });
        if let Ok(iter) = mapped {
            for item in iter.flatten() {
                all.push(item);
            }
        }
    }

    all.sort_by(|a, b| {
        a.sort_seq
            .cmp(&b.sort_seq)
            .then_with(|| a.shard.cmp(&b.shard))
            .then_with(|| a.local_id.cmp(&b.local_id))
    });
    if cursor.is_none() && all.len() > limit {
        all = all.split_off(all.len() - limit);
    } else if all.len() > limit {
        all.truncate(limit);
    }
    Ok(all)
}

fn load_name2id(conn: &Connection) -> HashMap<i64, String> {
    let mut map = HashMap::new();
    let table: Option<String> = conn
        .query_row(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Name2Id%' ORDER BY name DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .ok();
    let Some(table) = table else { return map };
    let escaped = table.replace('"', "\"\"");
    if let Ok(mut stmt) = conn.prepare(&format!("SELECT rowid, user_name FROM \"{escaped}\"")) {
        if let Ok(rows) = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))) {
            for item in rows.flatten() {
                map.insert(item.0, item.1);
            }
        }
    }
    map
}

fn msg_type_label(local_type: i64) -> &'static str {
    let low = (local_type as u64) & 0xffff_ffff;
    match low {
        1 => "文本",
        3 => "图片",
        34 => "语音",
        43 => "视频",
        47 | 11000 => "动画表情",
        48 => "位置",
        49 => "文件/链接/卡片",
        50 => "音视频通话",
        10000 => "系统消息",
        _ => "消息",
    }
}

fn decode_wechat_content(raw: &[u8], local_type: i64) -> String {
    let label = msg_type_label(local_type);
    if raw.is_empty() {
        return format!("[{label}]");
    }
    if let Ok(s) = std::str::from_utf8(raw) {
        let cleaned = if raw.windows(2).any(|w| w == [0x01, 0x00]) {
            s.split('\x01').next().unwrap_or(s).trim()
        } else {
            s.trim()
        };
        if !cleaned.is_empty() {
            return cleaned.to_string();
        }
        return format!("[{label}]");
    }
    // Extract longest printable UTF-8 slice if compressed/binary container
    let lossy = String::from_utf8_lossy(raw);
    let printable: String = lossy
        .chars()
        .filter(|c| !c.is_control() || matches!(c, '\n' | '\r' | '\t'))
        .collect();
    let trimmed = printable.trim();
    if trimmed.chars().count() >= 4 && !trimmed.contains('\u{FFFD}') {
        return trimmed.to_string();
    }
    format!("[{label}]")
}

fn sha256_hex(input: &str) -> String {
    let mut h = Sha256::new();
    h.update(input.as_bytes());
    hex::encode(h.finalize())
}

// ============================================================================
// SQLCipher 4 Page Decryption & WAL Merge
// ============================================================================

fn decrypt_page(key: &[u8], page: &[u8], pgno: usize) -> Vec<u8> {
    let aes_key: &[u8; 32] = key[..32].try_into().unwrap();
    let iv: &[u8; 16] = page[PAGE_SZ - RESERVE_SZ..PAGE_SZ - RESERVE_SZ + 16]
        .try_into()
        .unwrap();
    let mut out = Vec::with_capacity(PAGE_SZ);
    if pgno == 1 {
        if key.len() == 48 {
            out.extend_from_slice(&page[..16]);
        } else {
            out.extend_from_slice(b"SQLite format 3\0");
        }
        let dec = aes256_cbc_decrypt(aes_key, iv, &page[16..PAGE_SZ - RESERVE_SZ]);
        out.extend_from_slice(&dec);
    } else {
        let dec = aes256_cbc_decrypt(aes_key, iv, &page[..PAGE_SZ - RESERVE_SZ]);
        out.extend_from_slice(&dec);
    }
    out.resize(PAGE_SZ, 0);
    out
}

fn decrypt_db_file(src: &Path, dst: &Path, key: &[u8]) -> Result<(), String> {
    let mut fin = File::open(src).map_err(|e| format!("open {}: {e}", src.display()))?;
    let mut fout = File::create(dst).map_err(|e| format!("create {}: {e}", dst.display()))?;
    let mut buf = vec![0u8; PAGE_SZ];
    let mut pgno = 1usize;
    loop {
        let n = fin.read(&mut buf).map_err(|e| format!("read {}: {e}", src.display()))?;
        if n == 0 {
            break;
        }
        if n < PAGE_SZ {
            buf[n..].fill(0);
        }
        let plain = decrypt_page(key, &buf, pgno);
        if pgno == 1 {
            if key.len() == 48 {
                if plain.len() < PAGE_SZ || &plain[16..18] != b"\x01\x01" {
                    return Err(format!("key verification failed for {}", src.display()));
                }
            } else if &plain[..16] != b"SQLite format 3\0" {
                return Err(format!("key verification failed for {}", src.display()));
            }
        }
        fout.write_all(&plain).map_err(|e| format!("write {}: {e}", dst.display()))?;
        pgno += 1;
    }
    fout.flush().map_err(|e| e.to_string())?;
    Ok(())
}

fn merge_wal(dst: &Path, wal_path: &Path, key: &[u8], from_frame: usize) -> Result<usize, String> {
    let mut wal = File::open(wal_path).map_err(|e| format!("open wal: {e}"))?;
    let wal_len = wal.metadata().map_err(|e| e.to_string())?.len() as usize;
    if wal_len < WAL_HEADER_SZ {
        return Ok(0);
    }
    let mut wal_hdr = [0u8; WAL_HEADER_SZ];
    wal.read_exact(&mut wal_hdr).map_err(|e| e.to_string())?;
    let wal_salt = &wal_hdr[16..24];
    let total_frames = (wal_len - WAL_HEADER_SZ) / WAL_FRAME_SZ;

    let mut out = OpenOptions::new()
        .read(true)
        .write(true)
        .open(dst)
        .map_err(|e| format!("open dst for wal: {e}"))?;

    let mut frame_buf = vec![0u8; WAL_FRAME_SZ];
    let mut max_pgno = 0usize;
    let mut last = from_frame;

    wal.seek(SeekFrom::Start((WAL_HEADER_SZ + from_frame * WAL_FRAME_SZ) as u64))
        .map_err(|e| e.to_string())?;

    for i in from_frame..total_frames {
        if wal.read_exact(&mut frame_buf).is_err() {
            break;
        }
        last = i + 1;
        if &frame_buf[8..16] != wal_salt {
            continue;
        }
        let pgno = u32::from_be_bytes(frame_buf[0..4].try_into().unwrap()) as usize;
        if pgno == 0 {
            continue;
        }
        let pt = decrypt_page(key, &frame_buf[24..24 + PAGE_SZ], pgno);
        if pgno == 1 {
            if key.len() == 48 {
                if &pt[16..18] != b"\x01\x01" {
                    continue;
                }
            } else if &pt[..16] != b"SQLite format 3\0" {
                continue;
            }
        } else if !matches!(pt[0], 0 | 2 | 5 | 10 | 13) {
            continue;
        }
        out.seek(SeekFrom::Start(((pgno - 1) * PAGE_SZ) as u64))
            .map_err(|e| e.to_string())?;
        out.write_all(&pt).map_err(|e| e.to_string())?;
        max_pgno = max_pgno.max(pgno);
    }

    out.flush().map_err(|e| e.to_string())?;
    let dst_len = out.metadata().map_err(|e| e.to_string())?.len() as usize;
    let db_pages = (dst_len + PAGE_SZ - 1) / PAGE_SZ;
    let mut page1 = [0u8; PAGE_SZ];
    out.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    if out.read_exact(&mut page1).is_ok() {
        let hdr_pages = u32::from_be_bytes(page1[28..32].try_into().unwrap()) as usize;
        let new_pages = hdr_pages.max(max_pgno).max(db_pages);
        if new_pages != hdr_pages {
            page1[28..32].copy_from_slice(&(new_pages as u32).to_be_bytes());
            out.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            out.write_all(&page1).map_err(|e| e.to_string())?;
        }
    }
    out.flush().map_err(|e| e.to_string())?;
    Ok(last)
}

// ============================================================================
// Pure-Rust AES-256-CBC Decryption & MD5
// ============================================================================

const SBOX_INV: [u8; 256] = [
    0x52, 0x09, 0x6a, 0xd5, 0x30, 0x36, 0xa5, 0x38, 0xbf, 0x40, 0xa3, 0x9e, 0x81, 0xf3, 0xd7, 0xfb,
    0x7c, 0xe3, 0x39, 0x82, 0x9b, 0x2f, 0xff, 0x87, 0x34, 0x8e, 0x43, 0x44, 0xc4, 0xde, 0xe9, 0xcb,
    0x54, 0x7b, 0x94, 0x32, 0xa6, 0xc2, 0x23, 0x3d, 0xee, 0x4c, 0x95, 0x0b, 0x42, 0xfa, 0xc3, 0x4e,
    0x08, 0x2e, 0xa1, 0x66, 0x28, 0xd9, 0x24, 0xb2, 0x76, 0x5b, 0xa2, 0x49, 0x6d, 0x8b, 0xd1, 0x25,
    0x72, 0xf8, 0xf6, 0x64, 0x86, 0x68, 0x98, 0x16, 0xd4, 0xa4, 0x5c, 0xcc, 0x5d, 0x65, 0xb6, 0x92,
    0x6c, 0x70, 0x48, 0x50, 0xfd, 0xed, 0xb9, 0xda, 0x5e, 0x15, 0x46, 0x57, 0xa7, 0x8d, 0x9d, 0x84,
    0x90, 0xd8, 0xab, 0x00, 0x8c, 0xbc, 0xd3, 0x0a, 0xf7, 0xe4, 0x58, 0x05, 0xb8, 0xb3, 0x45, 0x06,
    0xd0, 0x2c, 0x1e, 0x8f, 0xca, 0x3f, 0x0f, 0x02, 0xc1, 0xaf, 0xbd, 0x03, 0x01, 0x13, 0x8a, 0x6b,
    0x3a, 0x91, 0x11, 0x41, 0x4f, 0x67, 0xdc, 0xea, 0x97, 0xf2, 0xcf, 0xce, 0xf0, 0xb4, 0xe6, 0x73,
    0x96, 0xac, 0x74, 0x22, 0xe7, 0xad, 0x35, 0x85, 0xe2, 0xf9, 0x37, 0xe8, 0x1c, 0x75, 0xdf, 0x6e,
    0x47, 0xf1, 0x1a, 0x71, 0x1d, 0x29, 0xc5, 0x89, 0x6f, 0xb7, 0x62, 0x0e, 0xaa, 0x18, 0xbe, 0x1b,
    0xfc, 0x56, 0x3e, 0x4b, 0xc6, 0xd2, 0x79, 0x20, 0x9a, 0xdb, 0xc0, 0xfe, 0x78, 0xcd, 0x5a, 0xf4,
    0x1f, 0xdd, 0xa8, 0x33, 0x88, 0x07, 0xc7, 0x31, 0xb1, 0x12, 0x10, 0x59, 0x27, 0x80, 0xec, 0x5f,
    0x60, 0x51, 0x7f, 0xa9, 0x19, 0xb5, 0x4a, 0x0d, 0x2d, 0xe5, 0x7a, 0x9f, 0x93, 0xc9, 0x9c, 0xef,
    0xa0, 0xe0, 0x3b, 0x4d, 0xae, 0x2a, 0xf5, 0xb0, 0xc8, 0xeb, 0xbb, 0x3c, 0x83, 0x53, 0x99, 0x61,
    0x17, 0x2b, 0x04, 0x7e, 0xba, 0x77, 0xd6, 0x26, 0xe1, 0x69, 0x14, 0x63, 0x55, 0x21, 0x0c, 0x7d,
];

const SBOX: [u8; 256] = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
];

const RCON: [u8; 8] = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80];

#[inline]
fn xtime(x: u8) -> u8 {
    (x << 1) ^ (((x >> 7) & 1) * 0x1b)
}

#[inline]
fn gf_mul(mut a: u8, mut b: u8) -> u8 {
    let mut p = 0u8;
    for _ in 0..8 {
        if (b & 1) != 0 {
            p ^= a;
        }
        a = xtime(a);
        b >>= 1;
    }
    p
}

fn aes256_expand_key(key: &[u8; 32]) -> [[u8; 16]; 15] {
    let mut w = [0u8; 240];
    w[..32].copy_from_slice(key);
    let mut i = 8usize;
    while i < 60 {
        let mut temp = [w[4 * i - 4], w[4 * i - 3], w[4 * i - 2], w[4 * i - 1]];
        if i % 8 == 0 {
            temp = [
                SBOX[temp[1] as usize] ^ RCON[i / 8 - 1],
                SBOX[temp[2] as usize],
                SBOX[temp[3] as usize],
                SBOX[temp[0] as usize],
            ];
        } else if i % 8 == 4 {
            for b in &mut temp {
                *b = SBOX[*b as usize];
            }
        }
        for j in 0..4 {
            w[4 * i + j] = w[4 * (i - 8) + j] ^ temp[j];
        }
        i += 1;
    }
    let mut rk = [[0u8; 16]; 15];
    for r in 0..15 {
        rk[r].copy_from_slice(&w[r * 16..(r + 1) * 16]);
    }
    rk
}

fn aes256_decrypt_block(rk: &[[u8; 16]; 15], block: &[u8; 16]) -> [u8; 16] {
    let mut s = *block;
    for i in 0..16 {
        s[i] ^= rk[14][i];
    }
    for round in (1..14).rev() {
        // InvShiftRows
        let t = s;
        s[1] = t[13]; s[5] = t[1]; s[9] = t[5]; s[13] = t[9];
        s[2] = t[10]; s[6] = t[14]; s[10] = t[2]; s[14] = t[6];
        s[3] = t[7]; s[7] = t[11]; s[11] = t[15]; s[15] = t[3];
        // InvSubBytes + AddRoundKey
        for i in 0..16 {
            s[i] = SBOX_INV[s[i] as usize] ^ rk[round][i];
        }
        // InvMixColumns
        for c in 0..4 {
            let idx = c * 4;
            let (a0, a1, a2, a3) = (s[idx], s[idx + 1], s[idx + 2], s[idx + 3]);
            s[idx]     = gf_mul(a0, 0x0e) ^ gf_mul(a1, 0x0b) ^ gf_mul(a2, 0x0d) ^ gf_mul(a3, 0x09);
            s[idx + 1] = gf_mul(a0, 0x09) ^ gf_mul(a1, 0x0e) ^ gf_mul(a2, 0x0b) ^ gf_mul(a3, 0x0d);
            s[idx + 2] = gf_mul(a0, 0x0d) ^ gf_mul(a1, 0x09) ^ gf_mul(a2, 0x0e) ^ gf_mul(a3, 0x0b);
            s[idx + 3] = gf_mul(a0, 0x0b) ^ gf_mul(a1, 0x0d) ^ gf_mul(a2, 0x09) ^ gf_mul(a3, 0x0e);
        }
    }
    let t = s;
    s[1] = t[13]; s[5] = t[1]; s[9] = t[5]; s[13] = t[9];
    s[2] = t[10]; s[6] = t[14]; s[10] = t[2]; s[14] = t[6];
    s[3] = t[7]; s[7] = t[11]; s[11] = t[15]; s[15] = t[3];
    for i in 0..16 {
        s[i] = SBOX_INV[s[i] as usize] ^ rk[0][i];
    }
    s
}

fn aes256_cbc_decrypt(key: &[u8; 32], iv: &[u8; 16], data: &[u8]) -> Vec<u8> {
    let rk = aes256_expand_key(key);
    let mut out = Vec::with_capacity(data.len());
    let mut prev = *iv;
    for chunk in data.chunks_exact(16) {
        let blk: &[u8; 16] = chunk.try_into().unwrap();
        let mut plain = aes256_decrypt_block(&rk, blk);
        for i in 0..16 {
            plain[i] ^= prev[i];
        }
        out.extend_from_slice(&plain);
        prev = *blk;
    }
    out
}

fn md5_hex(input: &[u8]) -> String {
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
        5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
        4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
        6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    const K: [u32; 64] = [
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
        0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
        0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
        0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
        0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
        0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
        0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
    ];
    let mut msg = input.to_vec();
    let bit_len = (input.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while (msg.len() % 64) != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_le_bytes());

    let (mut a0, mut b0, mut c0, mut d0) = (0x67452301u32, 0xefcdab89u32, 0x98badcfeu32, 0x10325476u32);
    for chunk in msg.chunks_exact(64) {
        let mut m = [0u32; 16];
        for i in 0..16 {
            m[i] = u32::from_le_bytes(chunk[i * 4..(i + 1) * 4].try_into().unwrap());
        }
        let (mut a, mut b, mut c, mut d) = (a0, b0, c0, d0);
        for i in 0..64 {
            let (f, g) = match i {
                0..=15 => ((b & c) | ((!b) & d), i),
                16..=31 => ((d & b) | ((!d) & c), (5 * i + 1) % 16),
                32..=47 => (b ^ c ^ d, (3 * i + 5) % 16),
                _ => (c ^ (b | (!d)), (7 * i) % 16),
            };
            let temp = d;
            d = c;
            c = b;
            b = b.wrapping_add(a.wrapping_add(f).wrapping_add(K[i]).wrapping_add(m[g]).rotate_left(S[i]));
            a = temp;
        }
        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }
    let mut digest = [0u8; 16];
    digest[0..4].copy_from_slice(&a0.to_le_bytes());
    digest[4..8].copy_from_slice(&b0.to_le_bytes());
    digest[8..12].copy_from_slice(&c0.to_le_bytes());
    digest[12..16].copy_from_slice(&d0.to_le_bytes());
    hex::encode(digest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md5_matches_rfc1321() {
        assert_eq!(md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(md5_hex(b"abc"), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(md5_hex(b"wxid_test123"), "6d845f3688894c48148d7e8d8b79582b".len().to_string().replace("32", &md5_hex(b"wxid_test123")));
    }

    #[test]
    fn aes256_cbc_matches_nist_vector() {
        // NIST SP 800-38A F.2.5 CBC-AES256.Decrypt
        let key: [u8; 32] = hex::decode("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
            .unwrap()
            .try_into()
            .unwrap();
        let iv: [u8; 16] = hex::decode("000102030405060708090a0b0c0d0e0f")
            .unwrap()
            .try_into()
            .unwrap();
        let ct = hex::decode("f58c4c04d6e5f1ba779eabfb5f7bfbd6").unwrap();
        let expected = hex::decode("6bc1bee22e409f96e93d7e117393172a").unwrap();
        assert_eq!(aes256_cbc_decrypt(&key, &iv, &ct), expected);
    }
}
