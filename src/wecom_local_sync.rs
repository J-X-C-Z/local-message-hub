//! Pure-Rust WeCom (WXWork) local database decryptor and incremental live-archive syncer.
//! Reads `wecom.json` (`database_root`, `user_slot_id`, `key_hex`), decrypts `message.db`, `user.db`,
//! and `session.db` (ct1 AES-128-CBC or ct2 SM4-CBC + WAL merge), and ingests into `EventStore`.

use crate::events::{EventStore, IngestRequest, NormalizedMessage};
use crate::local_crypto::{aes_cbc_decrypt, md5_bytes, parse_pb_fields, sm4_cbc_decrypt, PbValue};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

const PAGE_SZ: usize = 4096;
const WAL_HEADER_SZ: usize = 32;
const WAL_FRAME_SZ: usize = 24 + PAGE_SZ;
const MAGIC: &[u8; 16] = b"SQLite format 3\0";
const MAX_BATCH_MESSAGES: usize = 100;
const MAX_MESSAGES_PER_TICK: usize = 2000;
const PRODUCER_ID: &str = "local-wecom-live-v1";

#[derive(Debug, Deserialize)]
struct WeComConfigFile {
    database_root: String,
    user_slot_id: String,
    key_hex: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct WeComSyncState {
    user_slot_id: String,
    last_send_time: i64,
    last_message_id: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct DbStamp {
    db_len: u64,
    db_mtime_ns: u128,
    wal_len: u64,
    wal_mtime_ns: u128,
}

#[derive(Debug, Clone)]
pub struct WeComSyncStats {
    pub account_id: String,
    pub created: usize,
    pub revised: usize,
    pub unchanged: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CipherFamily {
    Ct1Aes128,
    Ct2Sm4,
}

pub struct WeComLocalSyncer {
    config_path: PathBuf,
    cache_dir: PathBuf,
    state_path: PathBuf,
    state: WeComSyncState,
}

impl WeComLocalSyncer {
    pub fn new(config_path: PathBuf, cache_dir: PathBuf, state_path: PathBuf) -> Result<Self, String> {
        fs::create_dir_all(&cache_dir).map_err(|e| format!("create wecom cache dir: {e}"))?;
        let state = if state_path.exists() {
            let raw = fs::read_to_string(&state_path).unwrap_or_default();
            serde_json::from_str(&raw).unwrap_or_default()
        } else {
            WeComSyncState::default()
        };
        Ok(Self {
            config_path,
            cache_dir,
            state_path,
            state,
        })
    }

    pub fn sync_once(&mut self, store: &EventStore) -> Result<WeComSyncStats, String> {
        let raw = fs::read_to_string(&self.config_path)
            .map_err(|e| format!("read wecom config {}: {e}", self.config_path.display()))?;
        let cfg: WeComConfigFile =
            serde_json::from_str(&raw).map_err(|e| format!("parse wecom config: {e}"))?;
        let key_bytes = hex::decode(cfg.key_hex.trim())
            .map_err(|e| format!("invalid wecom key_hex: {e}"))?;
        let key16: [u8; 16] = key_bytes
            .try_into()
            .map_err(|_| "wecom key_hex must be 16 bytes".to_string())?;

        if self.state.user_slot_id != cfg.user_slot_id {
            self.state = WeComSyncState {
                user_slot_id: cfg.user_slot_id.clone(),
                last_send_time: 0,
                last_message_id: 0,
            };
        }

        let dbs = find_wecom_dbs(Path::new(&cfg.database_root), &cfg.user_slot_id);
        let msg_src = pick_best_message_db(&dbs.message_dbs, &self.cache_dir, &key16)?;

        let mut names = HashMap::new();
        for user_src in &dbs.user_dbs {
            if let Ok((dec, _)) = self.ensure_decrypted("user.db", user_src, &key16) {
                load_wecom_users(&dec, &mut names);
            }
        }
        let mut convs = HashMap::new();
        for ses_src in &dbs.session_dbs {
            if let Ok((dec, _)) = self.ensure_decrypted("session.db", ses_src, &key16) {
                load_wecom_sessions(&dec, &mut convs);
            }
        }

        let (msg_db, _) = self.ensure_decrypted("message.db", &msg_src, &key16)?;
        let conn = open_ro(&msg_db)?;
        let mut stmt = conn
            .prepare(
                "SELECT message_id, sender_id, conversation_id, content_type, send_time, content \
                 FROM message_table \
                 WHERE send_time > ?1 OR (send_time = ?1 AND message_id > ?2) \
                 ORDER BY send_time ASC, message_id ASC LIMIT ?3",
            )
            .map_err(|e| format!("prepare wecom message_table: {e}"))?;

        let rows: Vec<(i64, String, String, i64, i64, Vec<u8>)> = stmt
            .query_map(
                rusqlite::params![self.state.last_send_time, self.state.last_message_id, MAX_MESSAGES_PER_TICK as i64],
                |r| {
                    let mid: i64 = r.get(0)?;
                    let sender = val_to_string(r.get_ref(1)?);
                    let conv = val_to_string(r.get_ref(2)?);
                    let ctype: i64 = r.get::<_, Option<i64>>(3)?.unwrap_or(0);
                    let st: i64 = r.get::<_, Option<i64>>(4)?.unwrap_or(0);
                    let content: Vec<u8> = match r.get_ref(5)? {
                        rusqlite::types::ValueRef::Blob(b) => b.to_vec(),
                        rusqlite::types::ValueRef::Text(t) => t.to_vec(),
                        _ => Vec::new(),
                    };
                    Ok((mid, sender, conv, ctype, st, content))
                },
            )
            .map_err(|e| format!("query wecom messages: {e}"))?
            .flatten()
            .collect();

        if rows.is_empty() {
            return Ok(WeComSyncStats {
                account_id: cfg.user_slot_id,
                created: 0,
                revised: 0,
                unchanged: 0,
            });
        }

        let mut normalized = Vec::with_capacity(rows.len());
        let mut last_st = self.state.last_send_time;
        let mut last_mid = self.state.last_message_id;

        for (mid, sender, conv, ctype, st, content) in &rows {
            let title = resolve_conv_name(conv, &convs, &names, &cfg.user_slot_id);
            let sender_name = if sender.is_empty() {
                None
            } else {
                Some(names.get(sender).cloned().unwrap_or_else(|| sender.clone()))
            };
            let text = decode_wecom_content(content, *ctype);
            normalized.push(NormalizedMessage {
                platform: "wecom".to_string(),
                account_id: cfg.user_slot_id.clone(),
                conversation_id: conv.clone(),
                message_id: format!("wecom:{}:{}", conv, mid),
                title: Some(title),
                sender_id: if sender.is_empty() { None } else { Some(sender.clone()) },
                sender_name,
                timestamp: *st,
                is_self: sender == &cfg.user_slot_id,
                text: Some(text),
            });
            last_st = *st;
            last_mid = *mid;
        }

        let mut total_created = 0usize;
        let mut total_revised = 0usize;
        let mut total_unchanged = 0usize;

        for chunk in normalized.chunks(MAX_BATCH_MESSAGES) {
            let idem = sha256_hex(&format!(
                "wecom-live:{}:{}:{}:{}",
                cfg.user_slot_id,
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
                .map_err(|e| format!("wecom ingest failed: {e}"))?;
            total_created += receipt.created;
            total_revised += receipt.revised;
            total_unchanged += receipt.unchanged;
        }

        self.state.last_send_time = last_st;
        self.state.last_message_id = last_mid;
        self.save_state()?;

        Ok(WeComSyncStats {
            account_id: cfg.user_slot_id,
            created: total_created,
            revised: total_revised,
            unchanged: total_unchanged,
        })
    }

    fn save_state(&self) -> Result<(), String> {
        let tmp = self.state_path.with_extension("json.tmp");
        let data = serde_json::to_string_pretty(&self.state).map_err(|e| e.to_string())?;
        fs::write(&tmp, data).map_err(|e| format!("write wecom state: {e}"))?;
        fs::rename(&tmp, &self.state_path).map_err(|e| format!("rename wecom state: {e}"))?;
        Ok(())
    }

    fn ensure_decrypted(
        &self,
        name: &str,
        src: &Path,
        key16: &[u8; 16],
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
        decrypt_wecom_db(src, if wal.exists() { Some(wal.as_path()) } else { None }, &tmp, key16)?;
        fs::rename(&tmp, &dst).map_err(|e| format!("rename wecom db: {e}"))?;
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
    let cache_dir = base.join(".lmh-wecom-cache");
    let state_path = base.join("wecom_sync_cursors.json");

    let mut syncer = match WeComLocalSyncer::new(config_path, cache_dir, state_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("wecom-local-sync init error: {e}");
            return;
        }
    };

    loop {
        match syncer.sync_once(&store) {
            Ok(stats) => {
                if stats.created > 0 || stats.revised > 0 {
                    eprintln!(
                        "wecom-local-sync: account={} created={} revised={}",
                        stats.account_id, stats.created, stats.revised
                    );
                }
            }
            Err(e) => {
                eprintln!("wecom-local-sync warning: {e}");
            }
        }
        std::thread::sleep(interval);
    }
}

struct FoundDbs {
    message_dbs: Vec<PathBuf>,
    user_dbs: Vec<PathBuf>,
    session_dbs: Vec<PathBuf>,
}

fn find_wecom_dbs(root: &Path, slot: &str) -> FoundDbs {
    let mut out = FoundDbs {
        message_dbs: Vec::new(),
        user_dbs: Vec::new(),
        session_dbs: Vec::new(),
    };
    let slot_dir = root.join(slot);
    let search_root = if slot_dir.is_dir() { slot_dir } else { root.to_path_buf() };
    let mut stack = vec![search_root];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                stack.push(p);
            } else if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                match name {
                    "message.db" => out.message_dbs.push(p),
                    "user.db" => out.user_dbs.push(p),
                    "session.db" => out.session_dbs.push(p),
                    _ => {}
                }
            }
        }
    }
    out
}

fn pick_best_message_db(candidates: &[PathBuf], cache_dir: &Path, key16: &[u8; 16]) -> Result<PathBuf, String> {
    let mut best: Option<(i64, PathBuf)> = None;
    for (idx, src) in candidates.iter().enumerate() {
        let probe_dst = cache_dir.join(format!("probe_msg_{idx}.db"));
        if decrypt_wecom_db(src, None, &probe_dst, key16).is_ok() {
            if let Ok(conn) = open_ro(&probe_dst) {
                let count: i64 = conn
                    .query_row("SELECT COUNT(*) FROM message_table", [], |r| r.get(0))
                    .unwrap_or(-1);
                if count >= 0 && best.as_ref().is_none_or(|(b, _)| count > *b) {
                    best = Some((count, src.clone()));
                }
            }
        }
        let _ = fs::remove_file(&probe_dst);
    }
    best.map(|(_, p)| p)
        .ok_or_else(|| "no decryptable WeCom message.db with message_table found".to_string())
}

fn lcg16(pgno: usize) -> [u8; 16] {
    let mut n = (pgno as i64) + 1;
    let mut out = [0u8; 16];
    for i in 0..4 {
        let q = n / 0xce26;
        let r = n % 0xce26;
        n = r * 0x9ef4 - q * 0x0ecf;
        if n < 0 {
            n += 0x7fffff07;
        }
        out[i * 4..(i + 1) * 4].copy_from_slice(&(n as u32).to_le_bytes());
    }
    out
}

fn dec_page_ct1(key16: &[u8; 16], pgno: usize, raw: &[u8]) -> Vec<u8> {
    let mut k_in = [0u8; 24];
    k_in[..16].copy_from_slice(key16);
    k_in[16..20].copy_from_slice(&(pgno as u32).to_le_bytes());
    k_in[20..24].copy_from_slice(b"sAlT");
    let k = md5_bytes(&k_in);
    let iv = md5_bytes(&lcg16(pgno));
    if pgno == 1 {
        let mut enc = Vec::with_capacity(PAGE_SZ - 16);
        enc.extend_from_slice(&raw[8..16]);
        enc.extend_from_slice(&raw[24..PAGE_SZ]);
        let mut out = Vec::with_capacity(PAGE_SZ);
        out.extend_from_slice(MAGIC);
        out.extend_from_slice(&aes_cbc_decrypt(&k, &iv, &enc));
        out
    } else {
        aes_cbc_decrypt(&k, &iv, &raw[..PAGE_SZ])
    }
}

fn dec_page_ct2(key16: &[u8; 16], pgno: usize, raw: &[u8]) -> Vec<u8> {
    if pgno == 1 {
        let mut tmp = raw[..PAGE_SZ].to_vec();
        tmp.copy_within(8..16, 16);
        let body = sm4_cbc_decrypt(key16, key16, &tmp[16..PAGE_SZ]);
        let mut out = Vec::with_capacity(PAGE_SZ);
        out.extend_from_slice(MAGIC);
        out.extend_from_slice(&body);
        out
    } else {
        sm4_cbc_decrypt(key16, key16, &raw[..PAGE_SZ])
    }
}

fn decrypt_wecom_db(src: &Path, wal: Option<&Path>, dst: &Path, key16: &[u8; 16]) -> Result<(), String> {
    let mut fin = File::open(src).map_err(|e| format!("open wecom db: {e}"))?;
    let mut page1 = [0u8; PAGE_SZ];
    fin.read_exact(&mut page1).map_err(|e| format!("read wecom page1: {e}"))?;
    if &page1[..16] == MAGIC {
        drop(fin);
        fs::copy(src, dst).map_err(|e| format!("copy plain wecom db: {e}"))?;
        return Ok(());
    }

    let p1_ct1 = dec_page_ct1(key16, 1, &page1);
    let (family, plain1) = if &p1_ct1[..16] == MAGIC && p1_ct1[16..20] != [0, 0, 0, 0] {
        (CipherFamily::Ct1Aes128, p1_ct1)
    } else {
        let p1_ct2 = dec_page_ct2(key16, 1, &page1);
        if &p1_ct2[..16] == MAGIC {
            (CipherFamily::Ct2Sm4, p1_ct2)
        } else {
            return Err(format!("wecom key verification failed for {}", src.display()));
        }
    };

    let mut fout = File::create(dst).map_err(|e| format!("create wecom dst: {e}"))?;
    fout.write_all(&plain1).map_err(|e| e.to_string())?;

    let mut buf = [0u8; PAGE_SZ];
    let mut pgno = 2usize;
    while let Ok(()) = fin.read_exact(&mut buf) {
        let plain = match family {
            CipherFamily::Ct1Aes128 => dec_page_ct1(key16, pgno, &buf),
            CipherFamily::Ct2Sm4 => dec_page_ct2(key16, pgno, &buf),
        };
        fout.write_all(&plain).map_err(|e| e.to_string())?;
        pgno += 1;
    }

    if let Some(wal_path) = wal {
        if let Ok(mut wf) = File::open(wal_path) {
            let wlen = wf.metadata().map(|m| m.len() as usize).unwrap_or(0);
            if wlen >= WAL_HEADER_SZ {
                let mut whdr = [0u8; WAL_HEADER_SZ];
                if wf.read_exact(&mut whdr).is_ok() {
                    let s1 = &whdr[16..20];
                    let s2 = &whdr[20..24];
                    let mut fbuf = vec![0u8; WAL_FRAME_SZ];
                    while wf.read_exact(&mut fbuf).is_ok() {
                        if &fbuf[12..16] != s1 || &fbuf[16..20] != s2 {
                            break;
                        }
                        let pno = u32::from_be_bytes(fbuf[4..8].try_into().unwrap()) as usize;
                        if pno == 0 {
                            continue;
                        }
                        let pt = match family {
                            CipherFamily::Ct1Aes128 => dec_page_ct1(key16, pno, &fbuf[24..24 + PAGE_SZ]),
                            CipherFamily::Ct2Sm4 => dec_page_ct2(key16, pno, &fbuf[24..24 + PAGE_SZ]),
                        };
                        let _ = fout.seek(SeekFrom::Start(((pno - 1) * PAGE_SZ) as u64));
                        let _ = fout.write_all(&pt);
                    }
                }
            }
        }
    }

    fout.flush().map_err(|e| e.to_string())?;
    Ok(())
}

fn decode_wecom_content(raw: &[u8], ctype: i64) -> String {
    let placeholder = match ctype {
        2 => "[图片]",
        4 => "[文件]",
        6 => "[位置]",
        13 => "[链接]",
        14 => "[小程序]",
        15 => "[图文]",
        23 => "[语音]",
        36 => "[视频]",
        38 => "[登录通知]",
        1001 => "[会话名]",
        1002 | 1011 => "[系统提示]",
        1022 => "[群公告]",
        1052 => "[置顶]",
        1055 => "[取消置顶]",
        2101 => "[公告]",
        _ => "",
    };
    if !placeholder.is_empty() {
        return placeholder.to_string();
    }
    if let Ok(fields) = parse_pb_fields(raw) {
        let mut texts = Vec::new();
        collect_pb_texts(&fields, &mut texts, 0);
        let joined = texts.join(" ").trim().to_string();
        if !joined.is_empty() {
            return joined;
        }
    }
    if let Ok(s) = std::str::from_utf8(raw) {
        let cleaned: String = s.chars().filter(|c| !c.is_control() || matches!(c, '\n' | '\r' | '\t')).collect();
        let t = cleaned.trim();
        if !t.is_empty() {
            return t.to_string();
        }
    }
    format!("[type{ctype}]")
}

fn collect_pb_texts(fields: &[crate::local_crypto::PbField<'_>], out: &mut Vec<String>, depth: usize) {
    if depth > 4 {
        return;
    }
    for f in fields {
        if let PbValue::Bytes(b) = f.value {
            if let Ok(sub) = parse_pb_fields(b) {
                if !sub.is_empty() {
                    collect_pb_texts(&sub, out, depth + 1);
                    continue;
                }
            }
            if let Ok(s) = std::str::from_utf8(b) {
                let cleaned: String = s
                    .chars()
                    .filter(|c| !c.is_control() || matches!(c, '\n' | '\r' | '\t'))
                    .collect();
                let t = cleaned.trim();
                if !t.is_empty() && !(t.len() >= 24 && t.bytes().all(|x| x.is_ascii_hexdigit())) {
                    out.push(t.to_string());
                }
            }
        }
    }
}

fn load_wecom_users(db: &Path, names: &mut HashMap<String, String>) {
    let Ok(conn) = open_ro(db) else { return };
    if let Ok(mut stmt) = conn.prepare("SELECT id, name FROM user_table") {
        if let Ok(rows) = stmt.query_map([], |r| Ok((val_to_string(r.get_ref(0)?), val_to_string(r.get_ref(1)?)))) {
            for (id, name) in rows.flatten() {
                if !id.is_empty() && !name.is_empty() {
                    names.entry(id).or_insert(name);
                }
            }
        }
    }
}

fn load_wecom_sessions(db: &Path, convs: &mut HashMap<String, String>) {
    let Ok(conn) = open_ro(db) else { return };
    let Ok(mut t_stmt) = conn.prepare("SELECT name FROM sqlite_master WHERE type='table'") else { return };
    let tables: Vec<String> = t_stmt
        .query_map([], |r| r.get::<_, String>(0))
        .ok()
        .into_iter()
        .flatten()
        .flatten()
        .filter(|n| n.to_ascii_lowercase().contains("conversation"))
        .collect();
    for table in tables {
        let mut cols = Vec::new();
        if let Ok(mut c_stmt) = conn.prepare(&format!("PRAGMA table_info(\"{table}\")")) {
            if let Ok(iter) = c_stmt.query_map([], |r| r.get::<_, String>(1)) {
                cols.extend(iter.flatten());
            }
        }
        let id_col = cols.iter().find(|c| matches!(c.to_ascii_lowercase().as_str(), "conversation_id" | "conv_id" | "id"));
        let name_col = cols.iter().find(|c| matches!(c.to_ascii_lowercase().as_str(), "name" | "title" | "conversation_name"));
        if let (Some(ic), Some(nc)) = (id_col, name_col) {
            if let Ok(mut stmt) = conn.prepare(&format!("SELECT \"{ic}\", \"{nc}\" FROM \"{table}\"")) {
                if let Ok(rows) = stmt.query_map([], |r| Ok((val_to_string(r.get_ref(0)?), val_to_string(r.get_ref(1)?)))) {
                    for (id, name) in rows.flatten() {
                        if !id.is_empty() && !name.trim().is_empty() {
                            convs.entry(id).or_insert_with(|| name.trim().to_string());
                        }
                    }
                }
            }
        }
    }
}

fn resolve_conv_name(cid: &str, convs: &HashMap<String, String>, names: &HashMap<String, String>, self_id: &str) -> String {
    if let Some(name) = convs.get(cid) {
        return name.clone();
    }
    if let Some(rest) = cid.strip_prefix("S:") {
        for part in rest.split('_') {
            if part != self_id {
                if let Some(name) = names.get(part) {
                    return name.clone();
                }
            }
        }
    }
    cid.to_string()
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
    .map_err(|e| format!("open wecom sqlite {}: {e}", path.display()))
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
