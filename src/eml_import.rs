//! Offline, bounded single-part text/plain RFC 5322 message import.
use crate::{events::{EventStore,IngestRequest,NormalizedMessage},legacy_import::read_stable_source};
use base64::{Engine,engine::general_purpose::STANDARD};
use chrono::DateTime;
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::{collections::BTreeMap,path::Path};

pub(crate) const PRODUCER: &str = "@offline/eml-v1";
pub(crate) const MAX_FILE: usize = 256 * 1024;
const MAX_HEADERS: usize = 16 * 1024;
const MAX_HEADER_VALUE: usize = 4 * 1024;
const MAX_HEADER_LINES: usize = 128;
const MAX_BODY: usize = 64 * 1024;
const USED_HEADERS: &[&str] = &[
    "from","to","date","subject","message-id","references","in-reply-to",
    "mime-version","content-type","content-transfer-encoding","content-disposition",
];

#[derive(Debug,thiserror::Error)]
pub enum ImportError {
    #[error("detached EML file unavailable or too large")]
    Source,
    #[error("expected a bounded single-part text/plain EML message")]
    Format,
    #[error("EML needs one simple exact From and To mailbox including the explicit target mailbox")]
    Account,
    #[error("EML needs one valid Message-ID and unambiguous optional thread IDs")]
    Identity,
    #[error("EML needs a valid date with explicit numeric time offset")]
    Date,
    #[error("target is not an available initialized ordinary Rust archive")]
    Archive,
    #[error("confirmation does not match this exact EML preview")]
    Confirmation,
    #[error("EML import was not confirmed; retry this exact preview if the result is uncertain")]
    Apply,
}
type Result<T> = std::result::Result<T,ImportError>;

fn split_headers(mut bytes: &[u8]) -> Result<(BTreeMap<String,String>,&[u8])> {
    let mut values: BTreeMap<String,String> = BTreeMap::new();
    let mut last: Option<String> = None;
    let mut end = 0;
    let mut has_header = false;
    for _ in 0..MAX_HEADER_LINES {
        let newline = bytes.iter().take(1_000).position(|&b| b == b'\n').ok_or(ImportError::Format)?;
        let (line,rest) = bytes.split_at(newline+1);
        end += line.len();
        if end > MAX_HEADERS { return Err(ImportError::Format); }
        bytes = rest;
        let line = &line[..line.len()-1];
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.len() > 998 || !line.iter().all(|&b| b == b'\t' || (b' '..=b'~').contains(&b)) {
            return Err(ImportError::Format);
        }
        let text = std::str::from_utf8(line).map_err(|_|ImportError::Format)?;
        if text.is_empty() { return Ok((values,bytes)); }
        if text.starts_with([' ','\t']) {
            if !has_header { return Err(ImportError::Format); }
            if let Some(key) = &last {
                let field = values.get_mut(key).ok_or(ImportError::Format)?;
                field.push(' ');
                field.push_str(text.trim());
                if field.len() > MAX_HEADER_VALUE { return Err(ImportError::Format); }
            }
            continue;
        }
        let (name,value) = text.split_once(':').ok_or(ImportError::Format)?;
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
            return Err(ImportError::Format);
        }
        has_header = true;
        let key = name.to_ascii_lowercase();
        last = if USED_HEADERS.contains(&key.as_str()) {
            if values.insert(key.clone(),value.trim().to_owned()).is_some() { return Err(ImportError::Format); }
            Some(key)
        } else { None };
    }
    Err(ImportError::Format)
}

fn mailbox(label: &str) -> Option<String> {
    if label.len() > 254 { return None; }
    let (local,domain) = label.split_once('@')?;
    if !(1..=64).contains(&local.len()) || !local.split('.').all(|part|
        !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || b"_+-".contains(&b))) {
        return None;
    }
    if !domain.contains('.') || !domain.split('.').all(|part|
        (1..=63).contains(&part.len()) && !part.starts_with('-') && !part.ends_with('-')
            && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')) {
        return None;
    }
    Some(format!("{local}@{}",domain.to_ascii_lowercase()))
}

fn address(header: &str) -> Option<(String,String)> {
    if let Some((name,bracket)) = header.split_once('<') {
        let name = name.trim();
        if !name.bytes().all(|b| b.is_ascii_alphanumeric() || b" .'-_".contains(&b)) { return None; }
        let id = mailbox(bracket.strip_suffix('>')?)?;
        let name = if name.is_empty() { id.clone() } else { name.to_owned() };
        Some((id,name))
    } else {
        let id = mailbox(header)?;
        Some((id.clone(),id))
    }
}

fn identities(header: &str) -> Result<Vec<&str>> {
    let values: Vec<_> = header.split_ascii_whitespace().collect();
    if values.is_empty() || values.len() > 16 || values.iter().any(|v|
        v.strip_prefix('<').and_then(|v|v.strip_suffix('>')).and_then(mailbox).is_none()) {
        return Err(ImportError::Identity);
    }
    Ok(values)
}

fn stable(account: &str, literal: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(account);
    hash.update([0]);
    hash.update(literal);
    format!("email-{}",hex::encode(hash.finalize()))
}

fn utc_seconds(value: &str) -> Option<i64> {
    let zone = value.split_ascii_whitespace().last()?;
    if zone.len() != 5 || zone == "-0000" || !zone.starts_with(['+','-'])
        || !zone[1..].bytes().all(|b| b.is_ascii_digit()) { return None; }
    let date = DateTime::parse_from_rfc2822(value).ok()?;
    (date.timestamp() >= 0 && date.timestamp_subsec_nanos() == 0).then_some(date.timestamp())
}

fn quoted_printable(raw: &[u8]) -> Result<Vec<u8>> {
    fn hex(b: u8) -> Option<u8> {
        match b { b'0'..=b'9'=>Some(b-b'0'),b'A'..=b'F'=>Some(b-b'A'+10),_=>None }
    }
    let mut i=0;
    let mut decoded=Vec::new();
    while i < raw.len() {
        let b=raw[i];
        if b == b'=' {
            let remaining=&raw[i+1..];
            if remaining.starts_with(b"\r\n") { i+=3; continue; }
            if remaining.starts_with(b"\n") { i+=2; continue; }
            let high=remaining.first().copied().and_then(hex).ok_or(ImportError::Format)?;
            let low=remaining.get(1).copied().and_then(hex).ok_or(ImportError::Format)?;
            decoded.push(high*16+low);
            i+=3;
        } else {
            if !(b == b'\t' || b == b'\r' || b == b'\n' || (b' '..=b'~').contains(&b)) {
                return Err(ImportError::Format);
            }
            decoded.push(b);
            i+=1;
        }
        if decoded.len() > MAX_BODY { return Err(ImportError::Format); }
    }
    Ok(decoded)
}

fn body(headers: &BTreeMap<String,String>, raw: &[u8]) -> Result<String> {
    let get = |key: &str| headers.get(key).map(String::as_str);
    if get("mime-version").is_some_and(|v| v != "1.0") || get("content-disposition").is_some() {
        return Err(ImportError::Format);
    }
    let mut content = get("content-type").unwrap_or("text/plain").split(';').map(str::trim);
    if !content.next().is_some_and(|v|v.eq_ignore_ascii_case("text/plain")) { return Err(ImportError::Format); }
    let mut ascii=true;
    if let Some(parameter)=content.next() {
        let (key,value)=parameter.split_once('=').ok_or(ImportError::Format)?;
        if !key.trim().eq_ignore_ascii_case("charset") || content.next().is_some() { return Err(ImportError::Format); }
        let value=value.trim();
        let value=value.strip_prefix('"').and_then(|inner|inner.strip_suffix('"')).unwrap_or(value);
        if value.eq_ignore_ascii_case("utf-8") { ascii=false; }
        else if !value.eq_ignore_ascii_case("us-ascii") { return Err(ImportError::Format); }
    }
    let encoded=get("content-transfer-encoding").unwrap_or("7bit");
    let decoded = if encoded.eq_ignore_ascii_case("base64") {
        let compact: Vec<u8> = raw.iter().copied().filter(|b| !matches!(b,b'\r'|b'\n')).collect();
        if compact.len() > (MAX_BODY.div_ceil(3))*4 { return Err(ImportError::Format); }
        STANDARD.decode(compact).map_err(|_|ImportError::Format)?
    } else if encoded.eq_ignore_ascii_case("quoted-printable") {
        quoted_printable(raw)?
    } else if encoded.eq_ignore_ascii_case("8bit") || (encoded.eq_ignore_ascii_case("7bit") && raw.is_ascii()) {
        if raw.len() > MAX_BODY { return Err(ImportError::Format); }
        raw.to_vec()
    } else { return Err(ImportError::Format); };
    if decoded.len() > MAX_BODY || (ascii && !decoded.is_ascii())
        || decoded.iter().any(|b| *b == 0 || (*b < b' ' && !matches!(b,b'\n'|b'\r'|b'\t')) || *b == 127) {
        return Err(ImportError::Format);
    }
    let text=std::str::from_utf8(&decoded).map_err(|_|ImportError::Format)?.trim();
    if text.is_empty() { return Err(ImportError::Format); }
    Ok(text.to_owned())
}

pub(crate) fn canonical_account(account: &str) -> bool { mailbox(account).as_deref() == Some(account) }

pub(crate) fn decode(raw: &[u8], account: &str) -> Result<NormalizedMessage> {
    if !canonical_account(account) { return Err(ImportError::Account); }
    let (headers,raw_body)=split_headers(raw)?;
    let get=|key: &str|headers.get(key).map(String::as_str);
    let (from,name)=get("from").and_then(address).ok_or(ImportError::Account)?;
    let (to,_)=get("to").and_then(address).ok_or(ImportError::Account)?;
    if from != account && to != account { return Err(ImportError::Account); }
    let ids=identities(get("message-id").ok_or(ImportError::Identity)?)?;
    if ids.len()!=1 { return Err(ImportError::Identity); }
    let references=get("references").map(identities).transpose()?;
    let reply=get("in-reply-to").map(identities).transpose()?;
    let root=references.as_ref().or(reply.as_ref()).map_or(ids[0],|ids|ids[0]);
    let timestamp=get("date").and_then(utc_seconds).ok_or(ImportError::Date)?;
    let subject=get("subject").filter(|s| !s.is_empty()).unwrap_or("（无主题）");
    if subject.contains("=?") || subject.contains('\t') { return Err(ImportError::Format); }
    let text=body(&headers,raw_body)?;
    Ok(NormalizedMessage{
        platform:"email".into(),account_id:account.into(),conversation_id:stable(account,root),
        message_id:stable(account,ids[0]),title:Some(subject.to_owned()),
        is_self:from==account,sender_id:Some(from),sender_name:Some(name),timestamp,text:Some(text),
    })
}

#[derive(Debug,Serialize)]
pub struct Preview { confirmation:String, rows:usize }

#[derive(Debug,Serialize)]
pub struct Receipt { batch_id:String, rows:usize, created:usize, unchanged:usize }

fn plan(source: &Path, archive: &EventStore, account: &str) -> Result<IngestRequest> {
    let bytes=read_stable_source(source,MAX_FILE).map_err(|_|ImportError::Source)?;
    let message=decode(&bytes,account)?;
    let consent=serde_json::to_vec(&(PRODUCER,archive.archive_id(),account,hex::encode(Sha256::digest(&bytes))))
        .map_err(|_|ImportError::Format)?;
    let request=IngestRequest{idempotency_key:hex::encode(Sha256::digest(consent)),messages:vec![message]};
    request.validate(PRODUCER).map_err(|_|ImportError::Format)?;
    Ok(request)
}

pub fn preview(source: &Path, directory: &Path, account: &str) -> Result<Preview> {
    let archive=EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    Ok(Preview{confirmation:plan(source,&archive,account)?.idempotency_key,rows:1})
}

pub fn commit(source: &Path, directory: &Path, account: &str, confirmation: &str) -> Result<Receipt> {
    let archive=EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    let request=plan(source,&archive,account)?;
    if request.idempotency_key!=confirmation { return Err(ImportError::Confirmation); }
    let batch_id=request.idempotency_key.clone();
    let receipt=archive.import_eml(request).map_err(|_|ImportError::Apply)?;
    Ok(Receipt{batch_id,rows:1,created:receipt.created,unchanged:receipt.unchanged})
}

#[cfg(test)]
mod tests {
    use super::*;

    const OWNER: &str = "Owner@example.invalid";
    const FIELDS: &str =
        "From: Sender <friend@EXAMPLE.INVALID>\r\nTo: Owner@example.invalid\r\n\
         Date: Tue, 20 Aug 2024 08:20:00 +0800\r\nMessage-ID: <One@example.invalid>\r\n\
         References: <root@example.invalid>\r\n <other@example.invalid>\r\n";
    fn input(fields:&str,mime:&str,body:&str)->Vec<u8>{
        format!("{fields}{mime}\r\n{body}").into_bytes()
    }

    #[test]
    fn mail_scope_is_case_sensitive_on_localpart_and_preserves_id_and_thread() {
        let data=input(FIELDS,"", "plain body\r\n");
        let row=decode(&data,OWNER).unwrap();
        assert_eq!(row.sender_id.as_deref(),Some("friend@example.invalid"));
        assert_eq!(row.sender_name.as_deref(),Some("Sender"));
        assert!(!row.is_self);
        assert_eq!(row.conversation_id,stable(OWNER,"<root@example.invalid>"));
        assert_eq!(row.message_id,stable(OWNER,"<One@example.invalid>"));
        assert_eq!(row.text.as_deref(),Some("plain body"));
        assert_eq!(row.timestamp,1_724_113_200);
        assert!(matches!(decode(&data,"owner@example.invalid"),Err(ImportError::Account)));
        assert!(matches!(decode(&data,"Owner@EXAMPLE.INVALID"),Err(ImportError::Account)));
        let send=input(&FIELDS.replace("friend@EXAMPLE.INVALID",OWNER).replace(
            "To: Owner@example.invalid","To: friend@example.invalid"),"","my outgoing");
        assert!(decode(&send,OWNER).unwrap().is_self);
    }

    #[test]
    fn validates_mime_encoding_and_never_falls_back_on_malformed_body() {
        let mime="MIME-Version: 1.0\r\nContent-Type: text/plain; charset=\"UTF-8\"\r\n";
        let b64=input(FIELDS,&format!("{mime}Content-Transfer-Encoding: base64\r\n"),"5L2g\r\n5aW9\r\n");
        let qp=input(FIELDS,&format!("{mime}Content-Transfer-Encoding: quoted-printable\r\n"),"=E4=BD=A0=\r\n=E5=A5=BD");
        assert_eq!(decode(&b64,OWNER).unwrap().text.as_deref(),Some("你好"));
        assert_eq!(decode(&qp,OWNER).unwrap().text,decode(&b64,OWNER).unwrap().text);
        for (m,b) in [
            ("Content-Type: multipart/mixed; boundary=unopened\r\n","text"),
            ("Content-Type: text/plain; charset=latin1\r\n","text"),
            ("Content-Disposition: attachment; filename=ignored\r\n","text"),
            ("Content-Transfer-Encoding: base64\r\n","broken!"),
            ("Content-Transfer-Encoding: quoted-printable\r\n","=AA"),
            ("Content-Type: text/plain; charset=utf-8\r\n","你好"),
        ] {
            assert!(matches!(decode(&input(FIELDS,m,b),OWNER),Err(ImportError::Format)));
        }
    }

    #[test]
    fn strict_ambiguity_and_header_and_body_budgets() {
        let duplicate=input(FIELDS,"From: other@example.invalid\r\n","body");
        assert!(matches!(decode(&duplicate,OWNER),Err(ImportError::Format)));
        assert!(matches!(decode(&input(&FIELDS.replace("+0800","-0000"),"","body"), OWNER),Err(ImportError::Date)));
        assert!(matches!(decode(&input(&FIELDS.replace("<One@example.invalid>","x-unknown"),"","body"), OWNER),Err(ImportError::Identity)));
        assert!(matches!(decode(&input(FIELDS,"Subject: x\r\n",&"a".repeat(MAX_BODY+1)), OWNER),Err(ImportError::Format)));
        assert!(matches!(decode(&input(FIELDS,&format!("Subject: {}\r\n","x".repeat(1000)),"body"), OWNER),Err(ImportError::Format)));
    }
}
