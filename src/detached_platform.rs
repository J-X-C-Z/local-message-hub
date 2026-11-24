//! Offline adapters for selected, already-rendered legacy provider records.
use crate::{
    events::{EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage},
    legacy_import::{OFFLINE_PRODUCER, read_stable_source},
};
use serde::{Deserialize, Serialize, de::IgnoredAny};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, io, path::Path};

const WE_COM_FORMAT: &str = "wecom-rendered-v1";
const WE_CHAT_FORMAT: &str = "wechat-provider-page-v1";
const WE_COM_DOMAIN: &str = "lmh-offline-wecom-rendered-v1";
const MAX_SOURCE_ROWS: usize = 100;
const MAX_ID_BYTES: usize = 256;
const MAX_TEXT_BYTES: usize = 64 * 1024;
const LAST_YEAR_9999: i64 = 253_402_300_799;

type Result<T> = std::result::Result<T, AdapterError>;

#[derive(Debug, thiserror::Error)]
pub enum AdapterError {
    #[error("selected detached export is unavailable")]
    Source,
    #[error("detached export is malformed, unsupported, or outside its limits")]
    Format,
    #[error("detached export does not match the selected account")]
    Account,
    #[error("target is not an available initialized ordinary native archive")]
    Archive,
    #[error("confirmation does not match this exact source, archive, and account")]
    Confirmation,
    #[error("import did not complete; retry the exact preview if its result is uncertain")]
    Apply,
}

fn id(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_ID_BYTES
        && value.trim() == value && !value.chars().any(char::is_control)
}

fn date(value: i64) -> bool {
    (1..=LAST_YEAR_9999).contains(&value)
}

fn plain_text(value: &str) -> bool {
    !value.is_empty() && value.len() <= MAX_TEXT_BYTES && !value.contains('\0')
}

fn operand(value: &str) -> bool {
    id(value) && !value.contains(':')
}

fn digest(value: &[u8]) -> String {
    hex::encode(Sha256::digest(value))
}

fn source_bytes(path: &Path) -> Result<Vec<u8>> {
    read_stable_source(path, MAX_BATCH_BYTES).map_err(|_| AdapterError::Source)
}

fn check_scope(supplied: &str, selected: &str) -> Result<()> {
    if id(selected) && supplied == selected {
        Ok(())
    } else {
        Err(AdapterError::Account)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope<Row> {
    format: String,
    account_id: String,
    messages: Vec<Row>,
}

/// The pinned WeCom renderer uses an integer message ID before Hub stringifies
/// it. Accept its existing textual representation too, without any rounding.
#[derive(Deserialize)]
#[serde(untagged)]
enum SourceId {
    Text(String),
    Number(u64),
}

impl SourceId {
    fn normalized(self) -> Result<String> {
        let value = match self {
            Self::Text(s) => s,
            Self::Number(n) if n > 0 => n.to_string(),
            Self::Number(_) => return Err(AdapterError::Format),
        };
        if id(&value) { Ok(value) } else { Err(AdapterError::Format) }
    }
}

#[derive(Deserialize)]
struct WeComRow {
    platform: Option<String>,
    account_id: Option<String>,
    message_id: SourceId,
    conversation_id: String,
    conversation: Option<String>,
    sender_id: String,
    sender: Option<String>,
    content_type: u32,
    send_time: i64,
    text: String,
    kind: String,
    me: bool,
    #[serde(default)]
    media: Vec<IgnoredAny>,
}

#[derive(Debug, Serialize)]
pub struct WeComPreview {
    confirmation: String,
    rows: usize,
    skipped_non_text: usize,
}

#[derive(Debug, Serialize)]
pub struct WeComReceipt {
    batch_id: String,
    rows: usize,
    skipped_non_text: usize,
    created: usize,
    unchanged: usize,
}

/// Count the same compact normalized JSON without keeping a byte buffer.
fn check_wecom_size(messages: &[NormalizedMessage]) -> Result<()> {
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

    let mut len = EncodedLen::default();
    serde_json::to_writer(&mut len, messages).map_err(|_| AdapterError::Format)?;
    if len.0 > MAX_BATCH_BYTES { return Err(AdapterError::Format); }
    Ok(())
}

struct WeComPlan {
    request: IngestRequest,
    skipped_non_text: usize,
}

impl WeComPlan {
    fn from_source(source: &Path, archive: &EventStore, account: &str) -> Result<Self> {
        let bytes = source_bytes(source)?;
        let envelope: Envelope<WeComRow> = serde_json::from_slice(&bytes)
            .map_err(|_| AdapterError::Format)?;
        if envelope.format != WE_COM_FORMAT || !(1..=MAX_SOURCE_ROWS).contains(&envelope.messages.len()) {
            return Err(AdapterError::Format);
        }
        check_scope(&envelope.account_id, account)?;

        let mut seen = BTreeSet::new();
        let mut messages = Vec::new();
        let mut titles = BTreeMap::new();
        let mut skipped_non_text = 0;
        for row in envelope.messages {
            if row.platform.as_deref().is_some_and(|v| v != "wecom")
                || row.account_id.as_deref().is_some_and(|v| v != account)
            {
                return Err(AdapterError::Account);
            }
            // Pinned CTYPE 0 explicitly means text. Do not relabel unknown
            // provider content as text or use any attachment locator.
            if row.kind != "text" || row.content_type != 0 || !row.media.is_empty() {
                skipped_non_text += 1;
                continue;
            }
            let message_id = row.message_id.normalized()?;
            if !id(&row.conversation_id) || !id(&row.sender_id)
                || !date(row.send_time) || !plain_text(&row.text)
                || !seen.insert(message_id.clone())
            {
                return Err(AdapterError::Format);
            }
            // A conversation has one title per detached batch. Do not let
            // packet order silently choose between contradictory titles.
            let title = row.conversation.filter(|v| !v.is_empty());
            if let Some(old) = titles.insert(row.conversation_id.clone(), title.clone()) {
                if old != title { return Err(AdapterError::Format); }
            }
            messages.push(NormalizedMessage {
                platform: "wecom".into(),
                account_id: account.to_owned(),
                conversation_id: row.conversation_id,
                message_id,
                title,
                sender_id: Some(row.sender_id),
                sender_name: row.sender.filter(|v| !v.is_empty()),
                timestamp: row.send_time,
                is_self: row.me,
                text: Some(row.text),
            });
        }

        let tag = serde_json::to_vec(&(
            WE_COM_DOMAIN, archive.archive_id(), "wecom", account, digest(&bytes),
        )).map_err(|_| AdapterError::Format)?;
        let request = IngestRequest { idempotency_key: digest(&tag), messages };
        // Use the established offline normalized-text owner: the same scoped
        // old Hub JSON row can deduplicate, but cannot be revised by import.
        request.validate(OFFLINE_PRODUCER).map_err(|_| AdapterError::Format)?;
        check_wecom_size(&request.messages)?;
        Ok(Self { request, skipped_non_text })
    }

    fn preview(self) -> WeComPreview {
        WeComPreview {
            confirmation: self.request.idempotency_key,
            rows: self.request.messages.len(),
            skipped_non_text: self.skipped_non_text,
        }
    }

    fn commit(self, archive: &EventStore, confirmation: &str) -> Result<WeComReceipt> {
        if confirmation != self.request.idempotency_key {
            return Err(AdapterError::Confirmation);
        }
        let rows = self.request.messages.len();
        let batch_id = self.request.idempotency_key.clone();
        let receipt = archive.import_wecom_rendered(self.request).map_err(|_| AdapterError::Apply)?;
        Ok(WeComReceipt {
            batch_id, rows, skipped_non_text: self.skipped_non_text,
            created: receipt.created, unchanged: receipt.unchanged,
        })
    }
}

pub fn preview_wecom(source: &Path, target: &Path, account: &str) -> Result<WeComPreview> {
    let archive = EventStore::open(target).map_err(|_| AdapterError::Archive)?;
    Ok(WeComPlan::from_source(source, &archive, account)?.preview())
}

pub fn import_wecom(source: &Path, target: &Path, account: &str, confirmation: &str) -> Result<WeComReceipt> {
    let archive = EventStore::open(target).map_err(|_| AdapterError::Archive)?;
    WeComPlan::from_source(source, &archive, account)?.commit(&archive, confirmation)
}

#[derive(Deserialize)]
struct WeChatProviderRow {
    id: String,
    account_id: Option<String>,
    platform: String,
    session_id: String,
    shard: String,
    local_id: u64,
    local_type: u64,
    timestamp: i64,
    #[serde(rename = "type")]
    kind: String,
    is_self: Option<bool>,
    content: IgnoredAny,
}

#[derive(Debug, Serialize)]
pub struct WeChatPreview {
    source_sha256: String,
    rows: usize,
    plain_text_candidates: usize,
    other_rows: usize,
    conversations: usize,
}

/// Inspect already normalized provider messages only. In particular a raw
/// client row does not prove either its owning account or send direction.
pub fn preview_wechat_provider_page(source: &Path, account: &str) -> Result<WeChatPreview> {
    let bytes = source_bytes(source)?;
    let envelope: Envelope<WeChatProviderRow> = serde_json::from_slice(&bytes)
        .map_err(|_| AdapterError::Format)?;
    if envelope.format != WE_CHAT_FORMAT || !(1..=MAX_SOURCE_ROWS).contains(&envelope.messages.len()) {
        return Err(AdapterError::Format);
    }
    check_scope(&envelope.account_id, account)?;
    let mut ids = BTreeSet::new();
    let mut sessions = BTreeSet::new();
    let mut plain_text_candidates = 0;
    let rows = envelope.messages.len();
    for row in envelope.messages {
        if row.account_id.as_deref().is_some_and(|v| v != account) {
            return Err(AdapterError::Account);
        }
        // The provider derives this composite from already detached rows, not
        // from raw DB paths or an assumed globally unique local ID.
        if row.platform != "wechat" || !operand(&row.session_id) || !operand(&row.shard)
            || row.local_id == 0 || !id(&row.id) || !date(row.timestamp) || row.is_self.is_none()
            || row.id != format!("{}:{}:{}", row.session_id, row.shard, row.local_id)
            || !ids.insert(row.id)
        {
            return Err(AdapterError::Format);
        }
        sessions.insert(row.session_id);
        // Payload is required by the provider shape, but preview never parses
        // or copies message content, or counts it as successfully importable.
        let _ = row.content;
        if row.kind == "text" && row.local_type == 1 {
            plain_text_candidates += 1;
        }
    }
    Ok(WeChatPreview {
        source_sha256: digest(&bytes), rows, plain_text_candidates,
        other_rows: rows - plain_text_candidates, conversations: sessions.len(),
    })
}
