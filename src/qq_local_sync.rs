//! Pure-Rust QQNT local SQLCipher reader and incremental live-archive syncer.
//! Unlocks DPAPI-protected keys from `qq-local.json`, decrypts `nt_msg.db` + `-wal`
//! into a local cache, decodes protobuf message payloads, and ingests into `EventStore`.

use crate::events::{EventStore, IngestRequest, NormalizedMessage};
use crate::local_crypto::{aes_cbc_decrypt, dpapi_unprotect, md5_hex, parse_pb_fields, pb_find, PbValue};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use ring::pbkdf2;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::num::NonZeroU32;
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

const HEADER_BYTES: usize = 1024;
const PAGE_SZ: usize = 4096;
const RESERVE_SZ: usize = 48;
const WAL_HEADER_SZ: usize = 32;
const WAL_FRAME_SZ: usize = 24 + PAGE_SZ;
const MAX_BATCH_MESSAGES: usize = 100;
const MAX_SESSIONS_PER_TICK: usize = 24;
const PRODUCER_ID: &str = "local-qq-live-v1";
const DPAPI_PREFIX: &str = "dpapi:v1:";
const DPAPI_ENTROPY: &[u8] = b"Local Message Hub QQ database key v1";

#[derive(Debug, Deserialize)]
struct QQConfigFile {
    key: String,
    #[serde(default)]
    data_dir: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct QQCursor {
    last_time: i64,
    last_rowid: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct QQSyncState {
    account_id: String,
    #[serde(default)]
    cursors: HashMap<String, QQCursor>, // key: "private:<id>" or "group:<id>"
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct DbStamp {
    db_len: u64,
    db_mtime_ns: u128,
    wal_len: u64,
    wal_mtime_ns: u128,
}

#[derive(Debug, Clone)]
pub struct QQSyncStats {
    pub account_id: String,
    pub conversations_checked: usize,
    pub conversations_changed: usize,
    pub created: usize,
    pub revised: usize,
    pub unchanged: usize,
}

pub struct QQLocalSyncer {
    config_path: PathBuf,
    cache_dir: PathBuf,
    state_path: PathBuf,
    state: QQSyncState,
}

impl QQLocalSyncer {
    pub fn new(config_path: PathBuf, cache_dir: PathBuf, state_path: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&cache_dir).map_err(|e| format!("create qq cache dir: {e}"))?;
        let state = if state_path.exists() {
            let raw = fs::read_to_string(&state_path).unwrap_or_default();
            serde_json::from_str(&raw).unwrap_or_default()
        } else {
            QQSyncState::default()
        };
        Ok(Self {
            config_path,
            cache_dir,
            state_path,
            state,
        })
    }

    pub fn sync_once(&mut self, store: &EventStore) -> Result<QQSyncStats, String> {
        let raw = fs::read_to_string(&self.config_path)
            .map_err(|e| format!("read qq config {}: {e}", self.config_path.display()))?;
        let cfg: QQConfigFile = serde_json::from_str(&raw).map_err(|e| format!("parse qq config: {e}"))?;
        let password = unlock_qq_key(&cfg.key)?;
        let (msg_src, account_id) = discover_qq_db(cfg.data_dir.as_deref())?;

        if self.state.account_id != account_id {
            self.state = QQSyncState {
                account_id: account_id.clone(),
                ..Default::default()
            };
        }

        let (msg_db, _) = self.ensure_decrypted("nt_msg.db", &msg_src, &password)?;
        let parent_dir = msg_src.parent().unwrap_or(&msg_src);

        let mut profiles = HashMap::new();
        let mut uins = HashMap::new();
        let mut groups = HashMap::new();

        let profile_src = parent_dir.join("profile_info.db");
        if profile_src.is_file() {
            if let Ok((p_db, _)) = self.ensure_decrypted("profile_info.db", &profile_src, &password) {
                load_qq_profiles(&p_db, &mut profiles, &mut uins);
            }
        }
        let group_src = parent_dir.join("group_info.db");
        if group_src.is_file() {
            if let Ok((g_db, _)) = self.ensure_decrypted("group_info.db", &group_src, &password) {
                load_qq_groups(&g_db, &mut groups);
            }
        }

        let conn = open_ro(&msg_db)?;
        load_uid_mapping(&conn, &mut uins);

        let mut convs = Vec::new();
        for (kind, table, peer_candidates) in [
            ("private", "c2c_msg_table", vec!["40021", "40030"]),
            ("group", "group_msg_table", vec!["40021", "40027", "40030"]),
        ] {
            let cols = table_columns(&conn, table);
            if !cols.contains("40050") || !cols.contains("40800") {
                continue;
            }
            let peers: Vec<&str> = peer_candidates
                .into_iter()
                .filter(|c| cols.contains(*c))
                .collect();
            if peers.is_empty() {
                continue;
            }
            let peer_expr = if peers.len() == 1 {
                format!("NULLIF(NULLIF(CAST(\"{}\" AS TEXT),''),'0')", peers[0])
            } else {
                let parts: Vec<String> = peers
                    .iter()
                    .map(|p| format!("NULLIF(NULLIF(CAST(\"{p}\" AS TEXT),''),'0')"))
                    .collect();
                format!("COALESCE({})", parts.join(","))
            };
            let sql = format!("SELECT {peer_expr} AS peer, MAX(\"40050\") FROM \"{table}\" GROUP BY peer");
            if let Ok(mut stmt) = conn.prepare(&sql) {
                if let Ok(rows) = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                        r.get::<_, Option<i64>>(1)?.unwrap_or(0),
                    ))
                }) {
                    for item in rows.flatten() {
                        if !item.0.is_empty() {
                            convs.push((kind.to_string(), table.to_string(), peer_expr.clone(), item.0, item.1));
                        }
                    }
                }
            }
        }

        let total_convs = convs.len();
        convs.sort_by(|a, b| b.4.cmp(&a.4));

        let mut changed = Vec::new();
        for c in convs {
            let key = format!("{}:{}", c.0, c.3);
            let need = match self.state.cursors.get(&key) {
                Some(cur) => c.4 > cur.last_time,
                None => true,
            };
            if need {
                changed.push(c);
            }
        }

        if changed.is_empty() {
            return Ok(QQSyncStats {
                account_id,
                conversations_checked: total_convs,
                conversations_changed: 0,
                created: 0,
                revised: 0,
                unchanged: 0,
            });
        }

        let mut total_created = 0usize;
        let mut total_revised = 0usize;
        let mut total_unchanged = 0usize;
        let mut processed = 0usize;

        for (kind, table, peer_expr, peer_id, max_ts) in changed.into_iter().take(MAX_SESSIONS_PER_TICK) {
            processed += 1;
            let state_key = format!("{kind}:{peer_id}");
            let cur = self.state.cursors.get(&state_key).cloned();
            let cols = table_columns(&conn, &table);

            let (where_sql, params): (String, Vec<rusqlite::types::Value>) = match &cur {
                Some(c) => (
                    format!("{peer_expr}=? AND (\"40050\" > ? OR (\"40050\" = ? AND rowid > ?))"),
                    vec![peer_id.clone().into(), c.last_time.into(), c.last_time.into(), c.last_rowid.into()],
                ),
                None => (format!("{peer_expr}=?"), vec![peer_id.clone().into()]),
            };

            let limit = if cur.is_none() { 200 } else { 500 };
            let order = if cur.is_none() { "DESC" } else { "ASC" };
            let sel_sender = if cols.contains("40020") { "\"40020\"" } else { "NULL" };
            let sel_self = if cols.contains("40033") { "\"40033\"" } else { "NULL" };
            let sel_sname = if cols.contains("40090") { "\"40090\"" } else if cols.contains("40093") { "\"40093\"" } else { "NULL" };
            let sel_id = if cols.contains("40001") { "\"40001\"" } else { "NULL" };

            let sql = format!(
                "SELECT rowid, \"40050\", \"40800\", {sel_sender}, {sel_self}, {sel_sname}, {sel_id} \
                 FROM \"{table}\" WHERE {where_sql} ORDER BY \"40050\" {order}, rowid {order} LIMIT {limit}"
            );

            let mut rows_data = Vec::new();
            if let Ok(mut stmt) = conn.prepare(&sql) {
                if let Ok(iter) = stmt.query_map(rusqlite::params_from_iter(params), |r| {
                    let rowid: i64 = r.get(0)?;
                    let ts: i64 = r.get::<_, Option<i64>>(1)?.unwrap_or(0);
                    let blob: Vec<u8> = match r.get_ref(2)? {
                        rusqlite::types::ValueRef::Blob(b) => b.to_vec(),
                        rusqlite::types::ValueRef::Text(t) => t.to_vec(),
                        _ => Vec::new(),
                    };
                    let s1 = val_to_string(r.get_ref(3)?);
                    let s2 = val_to_string(r.get_ref(4)?);
                    let sname = val_to_string(r.get_ref(5)?);
                    let sid_col = val_to_string(r.get_ref(6)?);
                    Ok((rowid, ts, blob, s1, s2, sname, sid_col))
                }) {
                    for item in iter.flatten() {
                        rows_data.push(item);
                    }
                }
            }

            if cur.is_none() {
                rows_data.reverse();
            }

            if rows_data.is_empty() {
                self.state.cursors.insert(
                    state_key,
                    QQCursor {
                        last_time: max_ts,
                        last_rowid: cur.map(|c| c.last_rowid).unwrap_or(0),
                    },
                );
                continue;
            }

            let title = if kind == "group" {
                groups.get(&peer_id).cloned().unwrap_or_else(|| peer_id.clone())
            } else {
                profiles.get(&peer_id).cloned().unwrap_or_else(|| peer_id.clone())
            };

            let conv_tag = &md5_hex(peer_id.as_bytes())[..12];
            let mut normalized = Vec::with_capacity(rows_data.len());
            let mut newest = QQCursor::default();

            for (rowid, ts, blob, s1, s2, sname, sid_col) in &rows_data {
                let sender = if !s1.is_empty() { s1.clone() } else { s2.clone() };
                let is_self = s2 == &account_id || uins.get(&sender).is_some_and(|u| u == &account_id);
                let sender_name = if !sname.is_empty() {
                    Some(sname.clone())
                } else if !sender.is_empty() {
                    Some(profiles.get(&sender).cloned().unwrap_or_else(|| sender.clone()))
                } else {
                    None
                };
                let stable = if !sid_col.is_empty() && sid_col != "0" {
                    format!("qq:{conv_tag}:{sid_col}")
                } else {
                    format!("qq:{conv_tag}:db:{table}:{rowid}")
                };
                let text = decode_qq_protobuf(&blob);
                normalized.push(NormalizedMessage {
                    platform: "qq".to_string(),
                    account_id: account_id.clone(),
                    conversation_id: peer_id.clone(),
                    message_id: stable,
                    title: Some(title.clone()),
                    sender_id: if sender.is_empty() { None } else { Some(sender) },
                    sender_name,
                    timestamp: *ts,
                    is_self,
                    text: Some(text),
                });
                newest = QQCursor {
                    last_time: *ts,
                    last_rowid: *rowid,
                };
            }

            for chunk in normalized.chunks(MAX_BATCH_MESSAGES) {
                let idem = sha256_hex(&format!(
                    "qq-live:{}:{}:{}:{}:{}",
                    account_id,
                    peer_id,
                    chunk[0].message_id,
                    chunk[chunk.len() - 1].message_id,
                    chunk.len()
                ));
                let receipt = store
                    .ingest(
                        PRODUCER_ID,
                        IngestRequest {
                            idempotency_key: idem,
                            messages: chunk.to_vec(),
                        },
                    )
                    .map_err(|e| format!("qq ingest failed: {e}"))?;
                total_created += receipt.created;
                total_revised += receipt.revised;
                total_unchanged += receipt.unchanged;
            }

            self.state.cursors.insert(state_key, newest);
        }

        self.save_state()?;

        Ok(QQSyncStats {
            account_id,
            conversations_checked: total_convs,
            conversations_changed: processed,
            created: total_created,
            revised: total_revised,
            unchanged: total_unchanged,
        })
    }

    fn save_state(&self) -> Result<(), String> {
        let tmp = self.state_path.with_extension("json.tmp");
        let data = serde_json::to_string_pretty(&self.state).map_err(|e| e.to_string())?;
        fs::write(&tmp, data).map_err(|e| format!("write qq state: {e}"))?;
        fs::rename(&tmp, &self.state_path).map_err(|e| format!("rename qq state: {e}"))?;
        Ok(())
    }

    fn ensure_decrypted(
        &self,
        name: &str,
        src: &Path,
        password: &str,
    ) -> Result<(PathBuf, bool), String> {
        let dst = self.cache_dir.join(name);
        let stamp_path = self.cache_dir.join(format!("{name}.stamp"));
        let wal = PathBuf::from(format!("{}-wal", src.display()));

        let (db_len, db_mtime_ns) = file_meta(src)?;
        let (wal_len, wal_mtime_ns) = if wal.exists() {
            file_meta(&wal).unwrap_or((0, 0))
        } else {
            (0, 0)
        };
        let stamp = DbStamp { db_len, db_mtime_ns, wal_len, wal_mtime_ns };

        let prev: Option<DbStamp> = fs::read_to_string(&stamp_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok());
        if prev.as_ref() == Some(&stamp) && dst.exists() {
            return Ok((dst, false));
        }

        let tmp = self.cache_dir.join(format!("{name}.tmp"));
        decrypt_qq_db(src, if wal.exists() { Some(wal.as_path()) } else { None }, &tmp, password)?;
        fs::rename(&tmp, &dst).map_err(|e| format!("rename qq db: {e}"))?;
        let _ = fs::write(&stamp_path, serde_json::to_string(&stamp).unwrap_or_default());
        Ok((dst, true))
    }
}

pub fn run_background_sync(
    config_path: PathBuf,
    live_dir: PathBuf,
    store: EventStore,
    interval: Duration,
) {
    let base = live_dir.parent().unwrap_or(&live_dir);
    let cache_dir = base.join(".lmh-qq-cache");
    let state_path = base.join("qq_sync_cursors.json");

    let mut syncer = match QQLocalSyncer::new(config_path, cache_dir, state_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("qq-local-sync init error: {e}");
            return;
        }
    };

    loop {
        match syncer.sync_once(&store) {
            Ok(stats) => {
                if stats.created > 0 || stats.revised > 0 {
                    eprintln!(
                        "qq-local-sync: account={} changed={} created={} revised={}",
                        stats.account_id, stats.conversations_changed, stats.created, stats.revised
                    );
                }
            }
            Err(e) => {
                eprintln!("qq-local-sync warning: {e}");
            }
        }
        std::thread::sleep(interval);
    }
}

fn unlock_qq_key(raw: &str) -> Result<String, String> {
    if let Some(b64) = raw.strip_prefix(DPAPI_PREFIX) {
        let encrypted = BASE64.decode(b64.trim()).map_err(|e| format!("base64 qq key: {e}"))?;
        let plain = dpapi_unprotect(&encrypted, DPAPI_ENTROPY)?;
        String::from_utf8(plain).map_err(|e| format!("utf8 qq key: {e}"))
    } else if raw.is_empty() {
        Err("QQ database key is empty".to_string())
    } else {
        Ok(raw.to_string())
    }
}

fn discover_qq_db(data_dir: Option<&str>) -> Result<(PathBuf, String), String> {
    let mut candidates = Vec::new();
    if let Some(dir) = data_dir {
        let root = PathBuf::from(dir);
        for p in [
            root.clone(),
            root.join("nt_msg.db"),
            root.join("nt_db").join("nt_msg.db"),
            root.join("nt_qq").join("nt_db").join("nt_msg.db"),
        ] {
            if p.is_file() {
                candidates.push(p);
            }
        }
    } else if let Ok(profile) = std::env::var("USERPROFILE") {
        let base = PathBuf::from(profile).join("Documents").join("Tencent Files");
        if let Ok(entries) = fs::read_dir(&base) {
            for e in entries.flatten() {
                let p = e.path().join("nt_qq").join("nt_db").join("nt_msg.db");
                if p.is_file() {
                    candidates.push(p);
                }
            }
        }
    }
    let src = candidates.into_iter().next().ok_or_else(|| "QQNT nt_msg.db not found".to_string())?;
    let account = src
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .and_then(|p| p.file_name())
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "qq-local".to_string());
    Ok((src, account))
}

fn derive_qq_key(password: &str, salt: &[u8]) -> Result<[u8; 32], String> {
    if let Some(hex_str) = password.strip_prefix("hex:") {
        let b = hex::decode(hex_str).map_err(|e| format!("invalid qq hex key: {e}"))?;
        if b.len() == 32 {
            return Ok(b.try_into().unwrap());
        }
    }
    let mut key = [0u8; 32];
    pbkdf2::derive(
        pbkdf2::PBKDF2_HMAC_SHA512,
        NonZeroU32::new(4000).unwrap(),
        salt,
        password.as_bytes(),
        &mut key,
    );
    Ok(key)
}

fn decrypt_qq_page(page: &[u8], pgno: usize, key: &[u8; 32]) -> Vec<u8> {
    let cipher_slice = &page[if pgno == 1 { 16 } else { 0 }..PAGE_SZ - RESERVE_SZ];
    let iv: &[u8; 16] = page[PAGE_SZ - RESERVE_SZ..PAGE_SZ - RESERVE_SZ + 16]
        .try_into()
        .unwrap();
    let mut out = Vec::with_capacity(PAGE_SZ);
    if pgno == 1 {
        out.extend_from_slice(b"SQLite format 3\0");
    }
    out.extend_from_slice(&aes_cbc_decrypt(key, iv, cipher_slice));
    out.extend_from_slice(&page[PAGE_SZ - RESERVE_SZ..]);
    out
}

fn decrypt_qq_db(src: &Path, wal: Option<&Path>, dst: &Path, password: &str) -> Result<(), String> {
    let mut fin = File::open(src).map_err(|e| format!("open qq db: {e}"))?;
    let mut hdr = [0u8; HEADER_BYTES];
    fin.read_exact(&mut hdr).map_err(|e| format!("read qq hdr: {e}"))?;
    if &hdr[..16] == b"SQLite format 3\0" {
        drop(fin);
        fs::copy(src, dst).map_err(|e| format!("copy plain qq db: {e}"))?;
        return Ok(());
    }

    let mut page1 = [0u8; PAGE_SZ];
    fin.read_exact(&mut page1).map_err(|e| format!("read qq page1: {e}"))?;
    let key = derive_qq_key(password, &page1[..16])?;

    let mut fout = File::create(dst).map_err(|e| format!("create qq dst: {e}"))?;
    let plain1 = decrypt_qq_page(&page1, 1, &key);
    if &plain1[..16] != b"SQLite format 3\0" {
        return Err("QQ database key verification failed".to_string());
    }
    fout.write_all(&plain1).map_err(|e| e.to_string())?;

    let mut buf = [0u8; PAGE_SZ];
    let mut pgno = 2usize;
    while let Ok(()) = fin.read_exact(&mut buf) {
        let plain = decrypt_qq_page(&buf, pgno, &key);
        fout.write_all(&plain).map_err(|e| e.to_string())?;
        pgno += 1;
    }

    if let Some(wal_path) = wal {
        let mut wf = File::open(wal_path).map_err(|e| format!("open qq wal: {e}"))?;
        let wlen = wf.metadata().map_err(|e| e.to_string())?.len() as usize;
        if wlen >= WAL_HEADER_SZ {
            let mut whdr = [0u8; WAL_HEADER_SZ];
            if wf.read_exact(&mut whdr).is_ok() {
                let salt1 = &whdr[16..20];
                let salt2 = &whdr[20..24];
                let mut fbuf = vec![0u8; WAL_FRAME_SZ];
                let mut committed = Vec::new();
                let mut pending = Vec::new();
                while wf.read_exact(&mut fbuf).is_ok() {
                    if &fbuf[8..12] != salt1 || &fbuf[12..16] != salt2 {
                        break;
                    }
                    let pno = u32::from_be_bytes(fbuf[0..4].try_into().unwrap()) as usize;
                    let db_sz = u32::from_be_bytes(fbuf[4..8].try_into().unwrap()) as usize;
                    if pno == 0 {
                        break;
                    }
                    let pt = decrypt_qq_page(&fbuf[24..24 + PAGE_SZ], pno, &key);
                    pending.push((pno, pt));
                    if db_sz > 0 {
                        committed.append(&mut pending);
                    }
                }
                for (pno, pt) in committed {
                    fout.seek(SeekFrom::Start(((pno - 1) * PAGE_SZ) as u64)).map_err(|e| e.to_string())?;
                    fout.write_all(&pt).map_err(|e| e.to_string())?;
                }
            }
        }
    }

    fout.flush().map_err(|e| e.to_string())?;
    Ok(())
}

fn decode_qq_protobuf(blob: &[u8]) -> String {
    let Ok(fields) = parse_pb_fields(blob) else {
        return "[无法解析的 QQ 消息]".to_string();
    };
    let mut elements: Vec<&[u8]> = fields
        .iter()
        .filter_map(|f| if f.number == 40800 && f.wire == 2 {
            if let PbValue::Bytes(b) = f.value { Some(b) } else { None }
        } else { None })
        .collect();
    if elements.is_empty() && fields.iter().any(|f| f.number == 45002) {
        elements.push(blob);
    }
    let mut parts = Vec::new();
    for el in elements.into_iter().take(101) {
        let Ok(detail) = parse_pb_fields(el) else { continue };
        let kind = match pb_find(&detail, 45002, 0) {
            Some(PbValue::Varint(v)) => Some(v),
            _ => None,
        };
        let text = match pb_find(&detail, 45101, 0) {
            Some(PbValue::Bytes(b)) => String::from_utf8_lossy(b).to_string(),
            _ => String::new(),
        };
        match kind {
            Some(1) | None if !text.is_empty() => parts.push(text),
            Some(2) => parts.push("[图片]".to_string()),
            Some(3) => parts.push("[文件]".to_string()),
            Some(4) => {
                if let Some(PbValue::Bytes(b)) = pb_find(&detail, 45923, 0) {
                    let tr = String::from_utf8_lossy(b);
                    if !tr.is_empty() {
                        parts.push(tr.to_string());
                    } else {
                        parts.push("[语音]".to_string());
                    }
                } else {
                    parts.push("[语音]".to_string());
                }
            }
            Some(5) => parts.push("[视频]".to_string()),
            Some(6) => {
                let name = match pb_find(&detail, 47602, 0) {
                    Some(PbValue::Bytes(b)) => String::from_utf8_lossy(b).to_string(),
                    _ => String::new(),
                };
                parts.push(if name.is_empty() { "[表情]".to_string() } else { format!("[{name}]") });
            }
            Some(7) => {
                let qtext = match pb_find(&detail, 47413, 0) {
                    Some(PbValue::Bytes(b)) => String::from_utf8_lossy(b).to_string(),
                    _ => String::new(),
                };
                if !qtext.is_empty() {
                    parts.push(format!("[引用: {qtext}]"));
                } else {
                    parts.push("[引用消息]".to_string());
                }
            }
            Some(10) => parts.push("[卡片]".to_string()),
            Some(11) => parts.push("[商城表情]".to_string()),
            _ => {
                if !text.is_empty() {
                    parts.push(text);
                }
            }
        }
    }
    let joined = parts.join("");
    if joined.trim().is_empty() {
        "[QQ 消息]".to_string()
    } else {
        joined
    }
}

fn load_qq_profiles(db: &Path, profiles: &mut HashMap<String, String>, uins: &mut HashMap<String, String>) {
    let Ok(conn) = open_ro(db) else { return };
    let cols = table_columns(&conn, "profile_info_v6");
    if cols.contains("1000") && cols.contains("20002") && cols.contains("20009") {
        if let Ok(mut stmt) = conn.prepare("SELECT \"1000\", \"20002\", \"20009\" FROM profile_info_v6") {
            if let Ok(rows) = stmt.query_map([], |r| {
                Ok((
                    val_to_string(r.get_ref(0)?),
                    val_to_string(r.get_ref(1)?),
                    val_to_string(r.get_ref(2)?),
                ))
            }) {
                for (uid, nick, remark) in rows.flatten() {
                    let name = if !remark.is_empty() { remark } else { nick };
                    if !uid.is_empty() && !name.is_empty() {
                        profiles.insert(uid, name);
                    }
                }
            }
        }
    }
    if cols.contains("1000") && cols.contains("1002") {
        if let Ok(mut stmt) = conn.prepare("SELECT \"1000\", \"1002\" FROM profile_info_v6") {
            if let Ok(rows) = stmt.query_map([], |r| Ok((val_to_string(r.get_ref(0)?), val_to_string(r.get_ref(1)?)))) {
                for (uid, uin) in rows.flatten() {
                    if !uid.is_empty() && !uin.is_empty() {
                        uins.insert(uid, uin);
                    }
                }
            }
        }
    }
}

fn load_qq_groups(db: &Path, groups: &mut HashMap<String, String>) {
    let Ok(conn) = open_ro(db) else { return };
    let cols = table_columns(&conn, "group_list");
    if cols.contains("60001") && cols.contains("60007") {
        if let Ok(mut stmt) = conn.prepare("SELECT \"60001\", \"60007\" FROM group_list") {
            if let Ok(rows) = stmt.query_map([], |r| Ok((val_to_string(r.get_ref(0)?), val_to_string(r.get_ref(1)?)))) {
                for (gid, name) in rows.flatten() {
                    if !gid.is_empty() && !name.is_empty() {
                        groups.insert(gid, name);
                    }
                }
            }
        }
    }
}

fn load_uid_mapping(conn: &Connection, uins: &mut HashMap<String, String>) {
    let cols = table_columns(conn, "nt_uid_mapping_table");
    if cols.contains("48902") && cols.contains("1002") {
        if let Ok(mut stmt) = conn.prepare("SELECT \"48902\", \"1002\" FROM nt_uid_mapping_table") {
            if let Ok(rows) = stmt.query_map([], |r| Ok((val_to_string(r.get_ref(0)?), val_to_string(r.get_ref(1)?)))) {
                for (uid, uin) in rows.flatten() {
                    if !uid.is_empty() && !uin.is_empty() {
                        uins.insert(uid, uin);
                    }
                }
            }
        }
    }
}

fn table_columns(conn: &Connection, table: &str) -> std::collections::HashSet<String> {
    let mut set = std::collections::HashSet::new();
    let sql = format!("PRAGMA table_info(\"{}\")", table.replace('"', "\"\""));
    if let Ok(mut stmt) = conn.prepare(&sql) {
        if let Ok(rows) = stmt.query_map([], |r| r.get::<_, String>(1)) {
            for c in rows.flatten() {
                set.insert(c);
            }
        }
    }
    set
}

fn val_to_string(v: rusqlite::types::ValueRef<'_>) -> String {
    match v {
        rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).to_string(),
        rusqlite::types::ValueRef::Integer(i) => i.to_string(),
        _ => String::new(),
    }
}

fn open_ro(path: &Path) -> Result<Connection, String> {
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|e| format!("open qq sqlite {}: {e}", path.display()))
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

fn sha256_hex(input: &str) -> String {
    let mut h = Sha256::new();
    h.update(input.as_bytes());
    hex::encode(h.finalize())
}
