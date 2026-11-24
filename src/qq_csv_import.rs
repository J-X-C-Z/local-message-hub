//! Bounded "message export CSV" of legacy Local Message Hub, not Tencent/QCE raw input.
use crate::{
    events::{EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage},
    legacy_import::{OFFLINE_PRODUCER, read_stable_source},
};
use chrono::{FixedOffset, NaiveDateTime, TimeZone};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::path::Path;

const MODE: &str = "lmh-qq-message-csv-v1";
const HEADER: [&str; 13] = [
    "时间","平台","账号","会话","发送者","发送者ID","类型","内容",
    "媒体链接","文件名","附件","会话ID","消息ID",
];
const MAX_SOURCE_ROWS: usize = 1_000;

#[derive(Debug, thiserror::Error)]
pub enum ImportError {
    #[error("detached Hub CSV export unavailable or too large")]
    Source,
    #[error("expected complete legacy Hub QQ message CSV")]
    Invalid,
    #[error("QQ CSV must match the explicit exact account")]
    Account,
    #[error("QQ CSV requires a confirmed sender ID present in the batch and a sender ID on every imported row")]
    Sender,
    #[error("QQ CSV contains no supported text rows")]
    Empty,
    #[error("target is not an available initialized ordinary Rust archive")]
    Archive,
    #[error("confirmation does not match this QQ CSV preview and sender mapping")]
    Confirmation,
    #[error("QQ CSV import was not confirmed; retry the exact preview if its result is uncertain")]
    Apply,
}

type Result<T> = std::result::Result<T, ImportError>;

#[derive(Clone, Copy)]
enum Field { Start, Bare, Quoted, EndQuote }

/// A deliberately strict bounded dialect: UTF-8, exactly the writer's
/// column count, doubled quotes, complete LF or CRLF record terminators.
/// Newlines inside quoted cells are byte-for-byte data.
fn csv_rows(bytes: &[u8]) -> Result<Vec<Vec<String>>> {
    let rows=scan_csv(bytes,HEADER.len(),MAX_SOURCE_ROWS)?;
    if !rows.first().is_some_and(|row|row.iter().map(String::as_str).eq(HEADER))
        || rows.iter().any(|row|row.len()!=HEADER.len()) {
        return Err(ImportError::Invalid);
    }
    Ok(rows)
}

pub(crate) fn scan_csv(bytes:&[u8],max_columns:usize,max_data_rows:usize)->Result<Vec<Vec<String>>> {
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    let mut rows = Vec::new();
    let mut row = Vec::with_capacity(max_columns);
    let mut cell = Vec::new();
    let mut state = Field::Start;
    let mut stream = bytes.iter().copied();
    while let Some(byte) = stream.next() {
        match (state, byte) {
            (Field::Start,b'"') => state = Field::Quoted,
            (Field::Quoted,b'"') => state = Field::EndQuote,
            (Field::EndQuote,b'"') => { cell.push(b'"'); state = Field::Quoted; }
            (Field::Quoted,byte) => cell.push(byte),
            (Field::Bare,b'"') => return Err(ImportError::Invalid),
            (_,b','|b'\n'|b'\r') => {
                if row.len() >= max_columns { return Err(ImportError::Invalid); }
                row.push(String::from_utf8(std::mem::take(&mut cell)).map_err(|_|ImportError::Invalid)?);
                state = Field::Start;
                if byte != b',' {
                    if byte == b'\r' && stream.next() != Some(b'\n') { return Err(ImportError::Invalid); }
                    if rows.len() > max_data_rows {
                        return Err(ImportError::Invalid);
                    }
                    rows.push(std::mem::replace(&mut row,Vec::with_capacity(max_columns)));
                }
            }
            (Field::EndQuote,_) => return Err(ImportError::Invalid),
            (_,byte) => { cell.push(byte); state = Field::Bare; }
        }
    }
    if !matches!(state,Field::Start) || !row.is_empty() || !cell.is_empty() {
        return Err(ImportError::Invalid);
    }
    Ok(rows)
}

fn identifier(s: &str) -> bool {
    (1..=256).contains(&s.len()) && s.trim() == s && !s.chars().any(char::is_control)
}

pub(crate) fn timestamp(s: &str) -> Option<i64> {
    if s.is_empty() { return Some(0); } // Legacy writer's 0/missing escape.
    if s.len() != 19 { return None; }
    let local = NaiveDateTime::parse_from_str(s,"%Y-%m-%d %H:%M:%S").ok()?;
    if local.format("%Y-%m-%d %H:%M:%S").to_string() != s
        || local.and_utc().timestamp_subsec_nanos() != 0 { return None; }
    let zone = FixedOffset::east_opt(8*3600)?;
    let seconds = zone.from_local_datetime(&local).single()?.timestamp();
    (seconds >= 0).then_some(seconds)
}

fn some(s: String) -> Option<String> {
    (!s.is_empty()).then_some(s)
}

#[derive(Debug, Serialize)]
pub struct Preview { confirmation: String, rows: usize, skipped: usize }

#[derive(Debug, Serialize)]
pub struct Receipt { batch_id: String, rows: usize, skipped: usize, created: usize, unchanged: usize }

fn decode(bytes: &[u8], account: &str, self_sender: &str) -> Result<(Vec<NormalizedMessage>,usize)> {
    if !identifier(account) { return Err(ImportError::Account); }
    if !identifier(self_sender) { return Err(ImportError::Sender); }
    let mut messages = Vec::new();
    let mut skipped = 0;
    let mut found_self = false;
    for row in csv_rows(bytes)?.into_iter().skip(1) {
        let [date,platform,row_account,title,name,sender,kind,text,url,file,resources,conversation,id]:
            [String;HEADER.len()] = row.try_into().map_err(|_|ImportError::Invalid)?;
        // Even discarded records cannot smuggle a foreign account into a confirmed file.
        if platform != "qq" || row_account != account { return Err(ImportError::Account); }
        if kind != "text" || text.is_empty() || !url.is_empty() || !file.is_empty()
            || !matches!(resources.as_str(),""|"[]") {
            skipped += 1;
            continue;
        }
        if !identifier(&sender) { return Err(ImportError::Sender); }
        found_self |= sender == self_sender;
        if !identifier(&conversation) || !identifier(&id) { return Err(ImportError::Invalid); }
        messages.push(NormalizedMessage {
            platform, account_id:row_account, conversation_id:conversation, message_id:id,
            title:some(title), sender_name:some(name), is_self:sender == self_sender,
            sender_id:Some(sender), timestamp:timestamp(&date).ok_or(ImportError::Invalid)?, text:Some(text),
        });
    }
    if messages.is_empty() { return Err(ImportError::Empty); }
    if !found_self { return Err(ImportError::Sender); }
    Ok((messages,skipped))
}

fn plan(source: &Path, archive: &EventStore, account: &str, self_sender: &str) -> Result<(IngestRequest,usize)> {
    let bytes = read_stable_source(source,MAX_BATCH_BYTES).map_err(|_|ImportError::Source)?;
    let (messages,skipped) = decode(&bytes,account,self_sender)?;
    let digest = serde_json::to_vec(&(MODE,archive.archive_id(),account,self_sender,hex::encode(Sha256::digest(bytes))))
        .map_err(|_|ImportError::Invalid)?;
    let request = IngestRequest { idempotency_key:hex::encode(Sha256::digest(digest)), messages };
    request.validate(OFFLINE_PRODUCER).map_err(|_|ImportError::Invalid)?;
    if serde_json::to_vec(&request.messages).map_err(|_|ImportError::Invalid)?.len() > MAX_BATCH_BYTES {
        return Err(ImportError::Invalid);
    }
    Ok((request,skipped))
}

pub fn preview(source: &Path, directory: &Path, account: &str, self_sender: &str) -> Result<Preview> {
    let archive = EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    let (request,skipped) = plan(source,&archive,account,self_sender)?;
    Ok(Preview {rows:request.messages.len(),confirmation:request.idempotency_key,skipped})
}

pub fn commit(source: &Path, directory: &Path, account: &str, self_sender: &str, confirmation: &str) -> Result<Receipt> {
    let archive = EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    let (request,skipped) = plan(source,&archive,account,self_sender)?;
    if request.idempotency_key != confirmation { return Err(ImportError::Confirmation); }
    let rows = request.messages.len();
    let batch_id = request.idempotency_key.clone();
    let receipt = archive.import_legacy(request).map_err(|_|ImportError::Apply)?;
    Ok(Receipt {batch_id,rows,skipped,created:receipt.created,unchanged:receipt.unchanged})
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blob(rows: &[&str]) -> Vec<u8> {
        let mut out = HEADER.join(",") + "\r\n";
        for row in rows { out.push_str(row); }
        out.into_bytes()
    }

    #[test]
    fn typed_csv_preserves_escaping_utf8_and_epoch() {
        let data=blob(&["2024-08-20 12:34:56,qq,A,title,name,self,text,\"你, \"\"好\"\"\r\n🦀\",,,[],room,m1\r\n"]);
        let (rows,skipped)=decode(&data,"A","self").unwrap();
        assert_eq!(skipped,0);
        assert_eq!(rows[0].text.as_deref(),Some("你, \"好\"\r\n🦀"));
        assert_eq!(rows[0].timestamp,1_724_128_496);
        assert!(rows[0].is_self);
        assert!(matches!(decode(&data,"A","not-present"),Err(ImportError::Sender)));
    }

    #[test]
    fn rejects_truncation_malformed_utf8_and_noncanonical_header() {
        let good=",qq,A,title,name,self,text,plain,,,[],room,m1\r\n";
        assert!(csv_rows(&blob(&[good])).is_ok());
        for bad in [
            ",qq,A,title,name,self,text,plain,,,[],room,m1",
            ",qq,A,title,name,self,text,\"plain\" trailing,,,[],room,m1\n",
            ",qq,A,title,name,self,text,\"plain,,,[],room,m1\n",
            ",qq,A,title,name,self,text,plain,,,[],room,m1,m2\n",
            ",qq,A,title,name,self,text,plain,,,[],room,m1\r",
            ",qq,A,title,name,self,text,plain,,[],room,m1\n",
        ] {
            assert!(csv_rows(&blob(&[bad])).is_err(),"{bad:?}");
        }
        assert!(csv_rows(b"platform,account,id\n").is_err());
        let mut malformed=blob(&[good]);
        let off=malformed.len()-3;
        malformed[off]=0xff;
        assert!(csv_rows(&malformed).is_err());
    }

    #[test]
    fn validates_scope_even_for_skipped_rows_and_does_not_guess_senders() {
        let good=",qq,A,title,name,self,text,plain,,,[],room,m1\r\n";
        let foreign=",qq,B,title,name,self,image,caption,file:///not-opened,,[],room,m2\r\n";
        let source=blob(&[good,foreign]);
        assert!(matches!(decode(&source,"A","self"),Err(ImportError::Account)));
        let no_sender=",qq,A,title,name,,text,plain,,,[],room,m2\n";
        assert!(matches!(decode(&blob(&[good,no_sender]),"A","self"),Err(ImportError::Sender)));
        assert!(csv_rows(&blob(&vec![good;MAX_SOURCE_ROWS+1])).is_err());
    }
}
