//! Detached Telegram Desktop text export; no Telegram account, APIs or sidecar files.
use crate::events::{EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage};
use serde::{Deserialize, Serialize, de::IgnoredAny};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, io, path::Path};

pub(crate) const PRODUCER: &str = "@offline/telegram-desktop-v1";
const MAX_SOURCE: usize = 512 * 1024;
const MAX_ROWS: usize = 1_000;
const MAX_CHATS: usize = 32;
const MAX_PEER_ID: u64 = (1 << 52) - 1;
const MAX_TIMESTAMP: u64 = 253_402_300_799;
const MAX_TEXT_BYTES: usize = 64 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum ImportError {
    #[error("detached Telegram Desktop JSON export unavailable or too large")]
    Source,
    #[error("invalid or unsupported Telegram Desktop text export")]
    Invalid,
    #[error("Telegram export requires an explicit canonical user account matching the export")]
    Account,
    #[error("Telegram export contains no supported plain-text messages")]
    Empty,
    #[error("target is not an available initialized ordinary Rust archive")]
    Archive,
    #[error("confirmation does not match this exact Telegram export preview")]
    Confirmation,
    #[error("Telegram text import was not confirmed; retry the exact preview if its result is uncertain")]
    Apply,
}
type Result<T> = std::result::Result<T, ImportError>;

#[derive(Deserialize)]
struct Owner { user_id: u64 }

#[derive(Deserialize)]
struct Chats { list: Vec<Chat> }

#[derive(Deserialize)]
struct Document {
    personal_information: Option<Owner>,
    chats: Option<Chats>,
    id: Option<u64>,
    name: Option<String>,
    #[serde(rename="type")]
    kind: Option<String>,
    messages: Option<Vec<Row>>,
}

#[derive(Deserialize)]
struct Chat {
    id: u64,
    name: Option<String>,
    #[serde(rename="type")]
    kind: String,
    messages: Vec<Row>,
}

#[derive(Deserialize)]
struct Row {
    id: u64,
    #[serde(rename="type")]
    kind: String,
    date_unixtime: Option<String>,
    from_id: Option<String>,
    from: Option<String>,
    text: Option<Text>,
    text_entities: Option<Vec<Entity>>,
    // Keep field names only. Unknown content (including every media payload)
    // is discarded; it is never interpreted as a path, URL or text.
    #[serde(flatten)]
    extras: BTreeMap<String,IgnoredAny>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Text { Plain(String), Parts(Vec<Part>) }

#[derive(Deserialize)]
#[serde(untagged)]
enum Part { Plain(String), Entity(Entity) }

#[derive(Deserialize)]
struct Entity {
    #[serde(rename="type")]
    _kind: String,
    text: String,
}

fn flatten<'a>(parts: impl Iterator<Item=&'a str>) -> Result<String> {
    let mut text = String::new();
    for fragment in parts {
        if text.len() + fragment.len() > MAX_TEXT_BYTES { return Err(ImportError::Invalid); }
        text.push_str(fragment);
    }
    Ok(text)
}

impl Text {
    fn into_flat(self) -> Result<String> {
        match self {
            Self::Plain(text) if text.len() <= MAX_TEXT_BYTES => Ok(text),
            Self::Plain(_) => Err(ImportError::Invalid),
            Self::Parts(parts) => flatten(parts.iter().map(|part| match part {
                Part::Plain(text) => text.as_str(),
                Part::Entity(entity) => &entity.text,
            })),
        }
    }
}

// The old path first fully flattened the entities (including its byte cap)
// and only then compared the strings. Keep that order, but do not retain a
// second copy of the text: track a matching suffix while checking every part.
fn validate_entity_mirror(primary: &str, entities: &[Entity]) -> Result<()> {
    let mut rest = Some(primary.as_bytes());
    let mut len = 0usize;
    for entity in entities {
        let bytes = entity.text.as_bytes();
        len = len.checked_add(bytes.len()).ok_or(ImportError::Invalid)?;
        if len > MAX_TEXT_BYTES { return Err(ImportError::Invalid); }
        rest = rest.and_then(|suffix| suffix.strip_prefix(bytes));
    }
    if rest.is_some_and(|suffix| suffix.is_empty()) { Ok(()) } else { Err(ImportError::Invalid) }
}

fn canonical_decimal(value: &str) -> Option<u64> {
    if value.len() > 16 || value.is_empty() || (value.starts_with('0') && value.len() != 1)
        || !value.bytes().all(|b| b.is_ascii_digit()) { return None; }
    value.parse().ok()
}

fn peer_id(id: u64) -> bool { (1..=MAX_PEER_ID).contains(&id) }

fn numeric_peer(prefix: &str, sender: &str) -> Option<u64> {
    sender.strip_prefix(prefix).and_then(canonical_decimal).filter(|&n|peer_id(n))
}

const ALLOWED_EXTRA_TEXT_KEYS: &[&str] = &[
    "date","edited","edited_unixtime","reply_to_message_id","reply_to_peer_id",
    "forwarded_from","saved_from","via_bot","author","reactions","is_outgoing",
];

#[derive(Debug, Default, Serialize)]
struct Counts { rows: usize, skipped_messages: usize, skipped_chats: usize }

#[derive(Debug, Serialize)]
pub struct Preview { confirmation: String, #[serde(flatten)] counts: Counts }

#[derive(Debug, Serialize)]
pub struct Receipt {
    batch_id: String,
    #[serde(flatten)]
    counts: Counts,
    created: usize,
    unchanged: usize,
}

fn decode(bytes: &[u8], account: &str) -> Result<(Vec<NormalizedMessage>,Counts)> {
    let owner = numeric_peer("user",account).ok_or(ImportError::Account)?;
    let doc: Document = serde_json::from_slice(bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes))
        .map_err(|_|ImportError::Invalid)?;
    let claimed = doc.personal_information.map(|personal|personal.user_id);
    if claimed.is_some_and(|id|id != owner) { return Err(ImportError::Account); }
    let chats = match (doc.chats,doc.id,doc.kind,doc.messages) {
        (Some(chats),None,None,None) if doc.name.is_none() => {
            if claimed.is_none() { return Err(ImportError::Account); }
            chats.list
        }
        (None,Some(id),Some(kind),Some(messages)) =>
            vec![Chat { id, name:doc.name, kind, messages }],
        _ => return Err(ImportError::Invalid),
    };
    if chats.len() > MAX_CHATS || chats.iter().map(|c|c.messages.len()).sum::<usize>() > MAX_ROWS {
        return Err(ImportError::Invalid);
    }
    let mut counts = Counts::default();
    let mut messages = Vec::new();
    let mut seen = BTreeSet::new();
    for chat in chats {
        let prefix = match chat.kind.as_str() {
            "personal_chat" => "user",
            "private_group" => "chat",
            "private_supergroup" => "channel",
            _ => {
                counts.skipped_chats += 1;
                counts.skipped_messages += chat.messages.len();
                continue;
            }
        };
        if !peer_id(chat.id) || (prefix == "user" && chat.id == owner) || !seen.insert((prefix,chat.id)) {
            return Err(ImportError::Invalid);
        }
        let conversation_id = format!("tg:{prefix}:{}",chat.id);
        let mut message_ids = BTreeSet::new();
        for row in chat.messages {
            if row.kind != "message" || row.extras.keys().any(|key| !ALLOWED_EXTRA_TEXT_KEYS.contains(&key.as_str())) {
                counts.skipped_messages += 1;
                continue;
            }
            let primary = row.text.map(Text::into_flat).transpose()?;
            let text = match (primary, row.text_entities) {
                (Some(text), Some(entities)) => {
                    validate_entity_mirror(&text, &entities)?;
                    Some(text)
                }
                (Some(text), None) => Some(text),
                (None, Some(entities)) => Some(flatten(entities.iter().map(|e| e.text.as_str()))?),
                (None, None) => None,
            };
            let Some(text) = text.filter(|s| !s.is_empty()) else {
                counts.skipped_messages += 1;
                continue;
            };
            let timestamp = row.date_unixtime.as_deref().and_then(canonical_decimal)
                .filter(|&n|n <= MAX_TIMESTAMP).ok_or(ImportError::Invalid)?;
            let sender = row.from_id.ok_or(ImportError::Invalid)?;
            if !["user","channel","chat"].iter().any(|p|numeric_peer(p,&sender).is_some())
                || (prefix == "user" && sender != account && numeric_peer("user",&sender) != Some(chat.id))
                || !peer_id(row.id) || !message_ids.insert(row.id) {
                return Err(ImportError::Invalid);
            }
            messages.push(NormalizedMessage {
                platform:"telegram".into(), account_id:account.into(),
                conversation_id:conversation_id.clone(), message_id:format!("{conversation_id}:{}",row.id),
                title:chat.name.clone(), sender_name:row.from, is_self:sender == account, sender_id:Some(sender),
                timestamp:timestamp as i64, text:Some(text),
            });
        }
    }
    if messages.is_empty() { return Err(ImportError::Empty); }
    counts.rows = messages.len();
    Ok((messages,counts))
}

#[derive(Default)]
struct EncodedLen(usize);

impl io::Write for EncodedLen {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0 = self.0.checked_add(bytes.len())
            .ok_or_else(|| io::Error::other("normalized JSON size overflow"))?;
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> { Ok(()) }
}

fn check_normalized_size(messages: &[NormalizedMessage]) -> Result<()> {
    let mut len = EncodedLen::default();
    serde_json::to_writer(&mut len, messages).map_err(|_| ImportError::Invalid)?;
    if len.0 > MAX_BATCH_BYTES { return Err(ImportError::Invalid); }
    Ok(())
}

fn plan(source: &Path, archive: &EventStore, account: &str) -> Result<(IngestRequest,Counts)> {
    let bytes = crate::legacy_import::read_stable_source(source,MAX_SOURCE).map_err(|_|ImportError::Source)?;
    let (messages,counts) = decode(&bytes,account)?;
    let inputs = serde_json::to_vec(&(PRODUCER,archive.archive_id(),account,hex::encode(Sha256::digest(&bytes))))
        .map_err(|_|ImportError::Invalid)?;
    let idempotency_key = hex::encode(Sha256::digest(inputs));
    let request = IngestRequest {idempotency_key,messages};
    request.validate(PRODUCER).map_err(|_|ImportError::Invalid)?;
    check_normalized_size(&request.messages)?;
    Ok((request,counts))
}

pub fn preview(source: &Path, directory: &Path, account: &str) -> Result<Preview> {
    let archive = EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    let (request,counts) = plan(source,&archive,account)?;
    Ok(Preview {confirmation:request.idempotency_key,counts})
}

pub fn commit(source: &Path, directory: &Path, account: &str, confirmation: &str) -> Result<Receipt> {
    let archive = EventStore::open(directory).map_err(|_|ImportError::Archive)?;
    let (request,counts) = plan(source,&archive,account)?;
    if request.idempotency_key != confirmation { return Err(ImportError::Confirmation); }
    let batch_id = request.idempotency_key.clone();
    let receipt = archive.import_telegram(request).map_err(|_|ImportError::Apply)?;
    Ok(Receipt {batch_id,counts,created:receipt.created,unchanged:receipt.unchanged})
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value,json};

    fn rows(chat: Value) -> Result<(Vec<NormalizedMessage>,Counts)> {
        decode(&serde_json::to_vec(&chat).unwrap(),"user900")
    }

    fn row(id: u64, sender: &str, body: Value) -> Value {
        json!({"id":id,"type":"message","from_id":sender,"text":body,
            "date":"ignored-local-clock","date_unixtime":"1693753543"})
    }

    fn chat(kind: &str, id: u64, messages: Vec<Value>) -> Value {
        json!({"id":id,"name":"example","type":kind,"messages":messages})
    }

    #[test]
    fn full_owner_chat_namespace_and_visible_text_are_deterministic() {
        let entity = json!({"type":"text_link","text":"💬","href":"file:///ignored"});
        let pm = chat("personal_chat",42,vec![row(7,"user42",json!(["你",entity]))]);
        let basic = chat("private_group",42,vec![row(7,"user900",json!("same raw ID"))]);
        let supergroup = chat("private_supergroup",42,vec![row(7,"channel42",json!("same raw ID"))]);
        let unsupported = chat("public_channel",42,vec![row(7,"user42",json!("not copied"))]);
        let full = json!({"personal_information":{"user_id":900,"phone_number":"not-read"},
            "chats":{"list":[pm,basic,supergroup,unsupported]}});
        let (rows,counts) = rows(full.clone()).unwrap();
        assert_eq!(counts.rows,3);
        assert_eq!((counts.skipped_messages,counts.skipped_chats),(1,1));
        assert_eq!(rows[0].text.as_deref(),Some("你💬"));
        assert_eq!(rows[0].timestamp,1693753543);
        assert!(!rows[0].is_self && rows[1].is_self && !rows[2].is_self);
        assert_eq!(rows.iter().map(|r|r.message_id.as_str()).collect::<Vec<_>>(),
            ["tg:user:42:7","tg:chat:42:7","tg:channel:42:7"]);
        let bytes=serde_json::to_vec(&full).unwrap();
        assert!(matches!(decode(&bytes,"user901"),Err(ImportError::Account)));
        assert!(matches!(decode(&bytes,"user0900"),Err(ImportError::Account)));
    }

    #[test]
    fn allowlist_drops_media_and_rejects_ambiguous_supported_rows() {
        let mut supported = row(1,"user42",json!("visible"));
        let mut media = row(2,"user42",json!(["media caption"]));
        media["photo"] = json!("../../unopened");
        media["future_media"] = json!({"path":"whatever"});
        let service = json!({"id":3,"type":"service","action":"create_group"});
        let (messages,counts) = rows(chat("personal_chat",42,vec![supported.clone(),media,service])).unwrap();
        assert_eq!(messages.len(),1);
        assert_eq!(counts.skipped_messages,2);
        assert!(matches!(rows(chat("personal_chat",42,vec![supported.clone(),supported.clone()])), Err(ImportError::Invalid)));
        supported["text_entities"]=json!([{"type":"plain","text":"different"}]);
        assert!(matches!(rows(chat("personal_chat",42,vec![supported])),Err(ImportError::Invalid)));
        let missing = json!({"id":1,"type":"message","from_id":"user42","text":"x","date":"2026-01-01T12:00:00"});
        assert!(matches!(rows(chat("personal_chat",42,vec![missing])),Err(ImportError::Invalid)));
        assert!(matches!(rows(chat("personal_chat",42,vec![row(1,"user43",json!("unexpected third party"))])),
            Err(ImportError::Invalid)));
    }


    #[test]
    fn counted_normalized_size_matches_serde_at_exact_batch_boundary() {
        let escaped = "你, \"引号\"\\\r\n🦀";
        let mut messages = chat("personal_chat",42,(1..=96)
            .map(|n| row(n,"user42",json!(["long ",{"type":"bold","text":escaped}])))
            .collect());
        let (rows,_) = rows(messages.clone()).unwrap();
        let expected = format!("long {escaped}");
        assert!(rows.iter().all(|r|r.text.as_deref()==Some(expected.as_str())));
        let base = serde_json::to_vec(&rows).unwrap().len();
        for target in [MAX_BATCH_BYTES-1,MAX_BATCH_BYTES,MAX_BATCH_BYTES+1] {
            let remaining = target-base;
            for (n,item) in messages["messages"].as_array_mut().unwrap().iter_mut().enumerate() {
                let cjk = remaining / 96 / 3;
                let fill = remaining / 96 + usize::from(n < remaining % 96);
                item["text"] = json!(format!("long {escaped}{}{}", "字".repeat(cjk), "x".repeat(fill - cjk * 3)));
            }
            let source = serde_json::to_vec(&messages).unwrap();
            assert!(source.len() <= MAX_SOURCE);
            let (rows,_) = decode(&source,"user900").unwrap();
            let request = IngestRequest {idempotency_key:"fixture".into(),messages:rows};
            request.validate(PRODUCER).unwrap();
            assert_eq!(serde_json::to_vec(&request.messages).unwrap().len(),target);
            match check_normalized_size(&request.messages) {
                Ok(()) => assert!(target <= MAX_BATCH_BYTES),
                Err(ImportError::Invalid) => assert!(target > MAX_BATCH_BYTES),
                Err(e) => panic!("unexpected cap error: {e}"),
            }
        }
    }

    #[test]
    fn source_rows_are_bounded_even_when_skipped() {
        let service = json!({"id":1,"type":"service"});
        assert!(matches!(rows(chat("personal_chat",42,vec![service;MAX_ROWS+1])),Err(ImportError::Invalid)));
        let too_long = row(1,"user42",json!("你".repeat(MAX_TEXT_BYTES / 3 + 1)));
        assert!(matches!(rows(chat("personal_chat",42,vec![too_long])),Err(ImportError::Invalid)));
    }
}
