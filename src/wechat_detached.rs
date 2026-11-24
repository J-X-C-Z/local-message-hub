//! Local operator-normalized WeChat text, deliberately separate from provider-page preview.
use crate::{
    events::{EventError, EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage},
    legacy_import::read_stable_source,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, path::Path};

pub(crate) const PRODUCER: &str = "@offline/wechat-detached-v1";
pub(crate) const CLAIM_REPLY_PRODUCER: &str = "@offline/wechat-claimed-reply-v1";
pub(crate) const CLAIM_LEGACY_PRODUCER: &str = "@offline/wechat-claimed-legacy-export-v1";
const FORMAT: &str = "wechat-detached-text-v1";
const MAX_ROWS: usize = 100;
const MAX_LABEL: usize = 512;
const MAX_TEXT: usize = 64 * 1024;
const MAX_TIMESTAMP: i64 = 253_402_300_799;
const MSG_PREFIX: &str = "wx-detached-message-v1:";
const CONV_PREFIX: &str = "wx-detached-conversation-v1:";

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("selected detached WeChat input is unavailable")]
    Source,
    #[error("invalid or unsupported detached WeChat text or direction")]
    Format,
    #[error("detached WeChat input does not match the explicit account and namespace")]
    Scope,
    #[error("target is not an available ordinary Rust-native archive")]
    Archive,
    #[error("confirmation does not match the exact source, target and scope")]
    Confirmation,
    #[error("a message identity already exists with different content or ownership")]
    Conflict,
    #[cfg(target_os="linux")]
    #[error("claimed WeChat handoff is absent, expired or no longer matches the exact grant and job")]
    Claim,
    #[cfg(target_os="linux")]
    #[error("claimed WeChat job, detached receipt or message identity conflicts")]
    ClaimConflict,
    #[error("detached WeChat import did not finish; retry only with its exact preview")]
    Apply,
}
type Result<T> = std::result::Result<T, Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    format: String,
    account_id: String,
    source_namespace: String,
    messages: Vec<Row>,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Kind { Text }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    conversation_id: String,
    shard_id: String,
    local_id: String,
    conversation_title: Option<String>,
    sender_id: String,
    sender_name: Option<String>,
    timestamp: i64,
    is_self: bool,
    kind: Kind,
    text: String,
}

fn hex(value: &str, len: usize) -> bool {
    value.len() == len && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn token(value: &str) -> bool { hex(value, 32) && value.bytes().any(|b| b != b'0') }
pub(crate) fn source_id(value: &str) -> bool {
    (1..=256).contains(&value.len()) && !matches!(value, "." | "..")
        && value.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b'@'))
}
pub(crate) fn local_id(value: &str) -> bool {
    (1..=20).contains(&value.len()) && !value.starts_with('0')
        && value.bytes().all(|b| b.is_ascii_digit()) && value.parse::<u64>().is_ok()
}
pub(crate) fn label(value: Option<&str>) -> bool {
    value.is_none_or(|s| !s.trim().is_empty() && s.len() <= MAX_LABEL && !s.chars().any(char::is_control))
}
pub(crate) fn text(value: &str) -> bool {
    !value.trim().is_empty() && value.len() <= MAX_TEXT
        && !value.chars().any(|c| c.is_control() && !matches!(c, '\r' | '\n' | '\t'))
}

// Domain tag then length-delimited operands, independent of JSON formatting.
pub(crate) fn hash(domain: &[u8], parts: &[&[u8]]) -> String {
    let mut state = Sha256::new();
    state.update(domain);
    state.update([0_u8]);
    for part in parts {
        state.update((part.len() as u32).to_be_bytes());
        state.update(part);
    }
    hex::encode(state.finalize())
}

fn conversation(account: &str, namespace: &str, id: &str) -> String {
    format!("{CONV_PREFIX}{}", hash(CONV_PREFIX.as_bytes(), &[
        namespace.as_bytes(), account.as_bytes(), id.as_bytes(),
    ]))
}

fn message(account: &str, namespace: &str, row: &Row) -> String {
    format!("{MSG_PREFIX}{}", hash(MSG_PREFIX.as_bytes(), &[
        namespace.as_bytes(), account.as_bytes(), row.conversation_id.as_bytes(),
        row.shard_id.as_bytes(), row.local_id.as_bytes(),
    ]))
}

pub(crate) fn is_canonical(row: &NormalizedMessage, account: &str) -> bool {
    row.platform == "wechat" && row.account_id == account
        && row.conversation_id.strip_prefix(CONV_PREFIX).is_some_and(|id| hex(id, 64))
        && row.message_id.strip_prefix(MSG_PREFIX).is_some_and(|id| hex(id, 64))
        && row.sender_id.as_deref().is_some_and(|id| row.is_self == (id == account))
}

struct Plan { request: IngestRequest, conversations: usize,
    #[cfg(target_os="linux")]
    replies:crate::wechat_source::reply_export::Displays,
}

pub(crate) fn valid_scope(account:&str,namespace:&str)->bool {
    source_id(account) && !account.eq_ignore_ascii_case("local") && token(namespace)
}

pub(crate) fn normalized(bytes:&[u8],account:&str,namespace:&str)->Result<(Vec<NormalizedMessage>,usize)> {
    if !valid_scope(account,namespace) {return Err(Error::Scope);}
    if bytes.len()>MAX_BATCH_BYTES {return Err(Error::Format);}
    let envelope: Envelope = serde_json::from_slice(bytes).map_err(|_| Error::Format)?;
    if envelope.format != FORMAT || !(1..=MAX_ROWS).contains(&envelope.messages.len()) {
        return Err(Error::Format);
    }
    if envelope.account_id != account || envelope.source_namespace != namespace {
        return Err(Error::Scope);
    }

    let mut messages = Vec::with_capacity(envelope.messages.len());
    let mut by_conversation = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for row in envelope.messages {
        if !source_id(&row.conversation_id) || !source_id(&row.sender_id)
            || !token(&row.shard_id) || !local_id(&row.local_id)
            || !(1..=MAX_TIMESTAMP).contains(&row.timestamp)
            || row.is_self != (row.sender_id == account) || !text(&row.text)
            || !label(row.conversation_title.as_deref()) || !label(row.sender_name.as_deref())
        {
            return Err(Error::Format);
        }
        let Kind::Text = row.kind;
        let message_id = message(account, namespace, &row);
        if !seen.insert(message_id.clone()) { return Err(Error::Format); }
        let conversation_id = conversation(account, namespace, &row.conversation_id);
        if let Some(previous) = by_conversation.insert(conversation_id.clone(), row.conversation_title.clone()) {
            if previous != row.conversation_title { return Err(Error::Format); }
        }
        messages.push(NormalizedMessage {
            platform: "wechat".into(), account_id: account.to_owned(), conversation_id,
            message_id, title: row.conversation_title, sender_id: Some(row.sender_id),
            sender_name: row.sender_name, timestamp: row.timestamp, is_self: row.is_self, text: Some(row.text),
        });
    }
    let encoded=serde_json::to_vec(&messages).map_err(|_|Error::Format)?;
    if encoded.len()>MAX_BATCH_BYTES {return Err(Error::Format);}
    Ok((messages,by_conversation.len()))
}

fn plan(file: &Path, archive: &EventStore, account: &str, namespace: &str) -> Result<Plan> {
    if !valid_scope(account,namespace) {return Err(Error::Scope);}
    let bytes=read_stable_source(file, MAX_BATCH_BYTES).map_err(|_|Error::Source)?;
    let (messages,conversations)=normalized(&bytes,account,namespace)?;
    let file_digest = Sha256::digest(&bytes);
    let receipt = hash(b"hub-wechat-detached-confirmation-v1", &[
        archive.archive_id().as_bytes(), account.as_bytes(), namespace.as_bytes(), file_digest.as_slice(),
    ]);
    let request = IngestRequest { idempotency_key: receipt, messages };
    request.validate(PRODUCER).map_err(|_| Error::Format)?;
    Ok(Plan { request, conversations,
        #[cfg(target_os="linux")]
        replies:Default::default(),
    })
}

#[derive(Debug, Serialize)]
pub struct Preview {
    confirmation: String,
    rows: usize,
    conversations: usize,
}

#[derive(Debug, Serialize)]
pub struct Receipt {
    batch_id: String,
    rows: usize,
    created: usize,
    unchanged: usize,
}

pub fn preview(file: &Path, target: &Path, account: &str, namespace: &str) -> Result<Preview> {
    let archive = EventStore::open(target).map_err(|_| Error::Archive)?;
    let plan = plan(file, &archive, account, namespace)?;
    Ok(Preview { confirmation: plan.request.idempotency_key, rows: plan.request.messages.len(),
        conversations: plan.conversations })
}

pub fn commit(file: &Path, target: &Path, account: &str, namespace: &str, confirmation: &str) -> Result<Receipt> {
    if !hex(confirmation, 64) { return Err(Error::Confirmation); }
    let archive = EventStore::open(target).map_err(|_| Error::Archive)?;
    let plan = plan(file, &archive, account, namespace)?;
    if plan.request.idempotency_key != confirmation { return Err(Error::Confirmation); }
    let rows = plan.request.messages.len();
    let receipt = archive.import_wechat_detached(plan.request, account).map_err(|err| match err {
        EventError::Conflict => Error::Conflict,
        _ => Error::Apply,
    })?;
    Ok(Receipt { batch_id: confirmation.to_owned(), rows, created: receipt.created, unchanged: receipt.unchanged })
}

#[cfg(target_os="linux")]
#[derive(Clone,Copy,Default,Deserialize)]
enum ClaimedFormat {
    #[default]
    #[serde(rename="wechat-detached-text-v1")]
    Detached,
    #[serde(rename="legacy_hub_export")]
    LegacyHubExport,
    #[serde(rename="legacy_hub_export_page_v1")]
    LegacyHubExportPage,
    #[serde(rename="legacy_hub_export_reply_display_v1")]
    LegacyReply,
    #[serde(rename="legacy_hub_export_reply_display_page_v1")]
    LegacyReplyPage,
}

#[cfg(target_os="linux")]
impl ClaimedFormat {
    fn name(self)->&'static str {
        match self {Self::Detached=>FORMAT,Self::LegacyHubExport=>"legacy_hub_export",
            Self::LegacyHubExportPage=>"legacy_hub_export_page_v1",Self::LegacyReply=>"legacy_hub_export_reply_display_v1",
            Self::LegacyReplyPage=>crate::wechat_source::paged_export::REPLY_FORMAT}
    }

    fn producer(self)->&'static str {
        match self {Self::Detached=>PRODUCER,Self::LegacyHubExport|Self::LegacyHubExportPage=>CLAIM_LEGACY_PRODUCER,
            Self::LegacyReply|Self::LegacyReplyPage=>CLAIM_REPLY_PRODUCER}
    }

    fn plan(self,file:&Path,archive:&EventStore,account:&str,namespace:&str,selection:Option<&PageSelection>)
        ->Result<(Plan,Option<crate::wechat_source::paged_export::Detail>)> {
        if matches!(self,Self::LegacyHubExportPage|Self::LegacyReplyPage) {
            return paged_plan(file,archive,account,namespace,selection.ok_or(Error::Format)?,self);
        }
        if selection.is_some() {return Err(Error::Format);}
        if matches!(self,Self::Detached) {return Ok((plan(file,archive,account,namespace)?,None));}
        if !valid_scope(account,namespace) {return Err(Error::Scope);}
        let bytes=read_stable_source(file,MAX_BATCH_BYTES).map_err(|_|Error::Source)?;
        let (messages,replies)=if matches!(self,Self::LegacyReply) {
            crate::wechat_source::reply_export::decode(&bytes,account)
        } else {
            crate::wechat_source::legacy_rows(&bytes,account).map(|messages|(messages,Default::default()))
        }.map_err(|error|match error {
            crate::wechat_source::Error::Scope=>Error::Scope,
            _=>Error::Format,
        })?;
        let conversations=messages.iter().map(|m|m.conversation_id.as_str()).collect::<BTreeSet<_>>().len();
        // Disjoint from all detached and standalone import receipts. Namespace
        // identifies this operator context but never affects the old row IDs.
        let digest=Sha256::digest(&bytes);
        let input_domain=if matches!(self,Self::LegacyReply) {b"hub-wechat-claimed-legacy-reply-input-v1".as_slice()}
            else {b"hub-wechat-claimed-legacy-export-input-v1".as_slice()};
        let key=hash(input_domain,&[
            archive.archive_id().as_bytes(),account.as_bytes(),namespace.as_bytes(),digest.as_slice(),
        ]);
        let request=IngestRequest{idempotency_key:key,messages};
        request.validate(self.producer()).map_err(|_|Error::Format)?;
        if serde_json::to_vec(&request.messages).map_err(|_|Error::Format)?.len()>MAX_BATCH_BYTES {
            return Err(Error::Format);
        }
        Ok((Plan{request,conversations,replies},None))
    }
}

#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PageSelection {source_sha256:String,index:usize}

#[cfg(target_os="linux")]
fn export_inventory(file:&Path,account:&str,replies:bool)->Result<crate::wechat_source::paged_export::Inventory> {
    use crate::wechat_source::{self,paged_export};
    let bytes=read_stable_source(file,paged_export::MAX_BYTES).map_err(|_|Error::Source)?;
    let inventory=if replies {paged_export::Inventory::parse_mode(&bytes,account,true)}
        else {paged_export::Inventory::parse(&bytes,account)};
    inventory.map_err(|err|match err {
        wechat_source::Error::Scope=>Error::Scope,_=>Error::Format,
    })
}

#[cfg(target_os="linux")]
fn paged_plan(file:&Path,archive:&EventStore,account:&str,namespace:&str,selection:&PageSelection,format:ClaimedFormat)
    ->Result<(Plan,Option<crate::wechat_source::paged_export::Detail>)> {
    use subtle::ConstantTimeEq;
    if !valid_scope(account,namespace) {return Err(Error::Scope);}
    if !hex(&selection.source_sha256,64) {return Err(Error::Confirmation);}
    let with_replies=matches!(format,ClaimedFormat::LegacyReplyPage);
    let inventory=export_inventory(file,account,with_replies)?;
    if !bool::from(inventory.source_sha256.as_bytes().ct_eq(selection.source_sha256.as_bytes())) {
        return Err(Error::Confirmation);
    }
    let (detail,messages,replies)=inventory.select(selection.index).ok_or(Error::Format)?;
    let domain=if with_replies {b"hub-wechat-claimed-legacy-reply-selected-page-v1".as_slice()}
        else {b"hub-wechat-claimed-legacy-export-selected-page-v1".as_slice()};
    let key=hash(domain,&[
        archive.archive_id().as_bytes(),account.as_bytes(),namespace.as_bytes(),
        selection.source_sha256.as_bytes(),&u64::try_from(detail.index).map_err(|_|Error::Format)?.to_be_bytes(),
        detail.normalized_sha256.as_bytes(),
    ]);
    let conversations=messages.iter().map(|m|m.conversation_id.as_str()).collect::<BTreeSet<_>>().len();
    let request=IngestRequest{idempotency_key:key,messages};
    request.validate(format.producer()).map_err(|_|Error::Format)?;
    Ok((Plan{request,conversations,replies},Some(detail)))
}

#[cfg(target_os="linux")]
const CLAIM_SCHEMA:&str="
CREATE TABLE native_wechat_claim_receipts_v1 (
    lane TEXT NOT NULL, job TEXT NOT NULL, job_version INTEGER NOT NULL CHECK(job_version>0),
    account_id TEXT NOT NULL, namespace TEXT NOT NULL, batch TEXT NOT NULL UNIQUE,
    credential TEXT NOT NULL CHECK(length(credential)=64), PRIMARY KEY(lane,job)
) WITHOUT ROWID;";

#[cfg(target_os="linux")]
pub(crate) struct ClaimedBatch {
    claim:crate::source_jobs::LocalDetachedClaim,
    account:String,
    namespace:String,
    batch:String,
    format:ClaimedFormat,
    replies:crate::wechat_source::reply_export::Displays,
}
#[cfg(target_os="linux")]
impl ClaimedBatch {
    fn installed(db:&rusqlite::Connection)->std::result::Result<bool,EventError> {
        use rusqlite::OptionalExtension;
        let found:Option<(String,String)>=db.query_row(
            "SELECT name,type FROM sqlite_schema WHERE name='native_wechat_claim_receipts_v1' COLLATE NOCASE",
            [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        match found.as_ref().map(|(name,kind)|(name.as_str(),kind.as_str())) {
            None=>Ok(false),
            Some(("native_wechat_claim_receipts_v1","table"))=>Ok(true),
            _=>Err(EventError::Storage),
        }
    }
    pub(crate) fn matches(&self,account:&str,batch:&str)->bool {
        self.account==account && self.batch==batch && valid_scope(account,&self.namespace)
    }
    pub(crate) fn producer(&self)->&'static str {self.format.producer()}
    pub(crate) fn allows_row(&self,row:&NormalizedMessage,account:&str)->bool {
        match self.format {
            ClaimedFormat::Detached=>is_canonical(row,account),
            ClaimedFormat::LegacyHubExport|ClaimedFormat::LegacyHubExportPage|ClaimedFormat::LegacyReply|ClaimedFormat::LegacyReplyPage=>
                crate::wechat_source::is_legacy_projection(row,account),
        }
    }
    pub(crate) fn record_reply(&self,db:&rusqlite::Connection,row:&NormalizedMessage,created:bool)
        ->std::result::Result<(),EventError> {
        if let Some(display)=self.replies.get(&row.message_id) {
            crate::wechat_source::reply_export::record(db,row,display,created)?;
        } else if matches!(self.format,ClaimedFormat::LegacyReply|ClaimedFormat::LegacyReplyPage) && !created
            && crate::wechat_source::reply_export::installed(db)?
            && crate::wechat_source::reply_export::get(db,&row.account_id,&row.conversation_id,&row.message_id)?.is_some() {
            return Err(EventError::Conflict);
        }
        Ok(())
    }
    pub(crate) fn check(&self,db:&rusqlite::Connection,create:bool)->std::result::Result<(),EventError>{
        self.claim.verify_in(db)?;
        if Self::installed(db)? {
            let spent:bool=db.query_row(
                "SELECT EXISTS(SELECT 1 FROM native_wechat_claim_receipts_v1 WHERE
                (lane=? AND job=?) OR batch=?)",
                rusqlite::params![self.claim.lane,self.claim.job,self.batch],|r|r.get(0))?;
            if spent { return Err(EventError::Conflict); }
        } else if create {db.execute_batch(CLAIM_SCHEMA)?;}
        Ok(())
    }
    pub(crate) fn verify_retry(&self,db:&rusqlite::Connection)->std::result::Result<(),EventError>{
        use rusqlite::OptionalExtension;
        use subtle::ConstantTimeEq;
        if !Self::installed(db)? {return Err(EventError::Conflict);}
        let stored:Option<String>=db.query_row(
            "SELECT credential FROM native_wechat_claim_receipts_v1 WHERE
             lane=? AND job=? AND job_version=? AND account_id=? AND namespace=? AND batch=?",
            rusqlite::params![self.claim.lane,self.claim.job,self.claim.version,self.account,self.namespace,self.batch],
            |r|r.get(0)).optional()?;
        if !stored.as_deref().is_some_and(|v|bool::from(v.as_bytes().ct_eq(self.claim.credential.as_bytes()))) {
            return Err(EventError::Conflict);
        }
        Ok(())
    }
    pub(crate) fn record(&self,db:&rusqlite::Connection)->std::result::Result<(),EventError>{
        db.execute("INSERT INTO native_wechat_claim_receipts_v1(lane,job,job_version,account_id,namespace,batch,credential)
                    VALUES (?,?,?,?,?,?,?)",
            rusqlite::params![self.claim.lane,self.claim.job,self.claim.version,self.account,self.namespace,self.batch,self.claim.credential])?;
        Ok(())
    }
}

#[cfg(target_os="linux")]
struct PrivateToken(zeroize::Zeroizing<String>);
#[cfg(target_os="linux")]
impl<'de> Deserialize<'de> for PrivateToken {
    fn deserialize<D:serde::Deserializer<'de>>(d:D)->std::result::Result<Self,D::Error> {
        String::deserialize(d).map(|s|Self(zeroize::Zeroizing::new(s)))
    }
}
#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Operator {
    token:PrivateToken,
    job_id:String,
    expected_version:i64,
    namespace:String,
    #[serde(default)]
    format:ClaimedFormat,
    #[serde(default)]
    page:Option<PageSelection>,
    confirmation:Option<String>,
}

#[cfg(target_os="linux")]
pub fn claimed_local_cli(source:&Path,target:&Path,grants:&Path,import:bool)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    run_claim_operator(source,target,grants,if import {ClaimCommand::Import} else {ClaimCommand::Preview})
}

#[cfg(target_os="linux")]
pub fn claimed_local_status_cli(source:&Path,target:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    run_claim_operator(source,target,grants,ClaimCommand::Status)
}

#[cfg(target_os="linux")]
#[derive(Clone,Copy)]
enum ClaimCommand {Preview,Import,Status}

#[cfg(target_os="linux")]
fn run_claim_operator(source:&Path,target:&Path,grants:&Path,command:ClaimCommand)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    use crate::export_schedule_cli::{private_pipes,read_frame,reply};
    private_pipes()?;
    let result=read_frame::<Operator>().map_err(Into::<Box<dyn std::error::Error>>::into)
        .and_then(|operator|claim_operation(source,target,grants,command,operator));
    let selected=if matches!(command,ClaimCommand::Status) {
        result.unwrap_or_else(|_|serde_json::json!({"state":"not_confirmed"}))
    } else {result?};
    reply(selected,None)?;
    Ok(())
}

#[cfg(target_os="linux")]
fn claim_operation(source:&Path,target:&Path,grants:&Path,command:ClaimCommand,operator:Operator)
    ->std::result::Result<serde_json::Value,Box<dyn std::error::Error>> {
    use crate::export_schedule_cli::private_grants;
    use subtle::ConstantTimeEq;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(operator.token.0.as_str()).ok_or(Error::Scope)?;
    let scope=grant.account_scope.as_ref().ok_or(Error::Scope)?;
    let archive=EventStore::open(target).map_err(|_|Error::Archive)?;
    archive.require_ordinary().map_err(|_|Error::Archive)?;
    let claim=crate::source_jobs::LocalDetachedClaim::new(&archive,&grant,"wechat",&operator.job_id,
        operator.expected_version,operator.token.0.as_str()).map_err(|_|Error::Scope)?;
    drop(operator.token);
    let format=operator.format;
    let (plan,page)=format.plan(source,&archive,&scope.account_id,&operator.namespace,operator.page.as_ref())?;
    let approval_domain=match format {
        ClaimedFormat::Detached=>b"hub-wechat-claimed-detached-v1".as_slice(),
        ClaimedFormat::LegacyHubExport=>b"hub-wechat-claimed-legacy-export-approval-v1".as_slice(),
        ClaimedFormat::LegacyHubExportPage=>b"hub-wechat-claimed-legacy-export-page-approval-v1".as_slice(),
        ClaimedFormat::LegacyReply=>b"hub-wechat-claimed-legacy-reply-approval-v1".as_slice(),
        ClaimedFormat::LegacyReplyPage=>b"hub-wechat-claimed-legacy-reply-page-approval-v1".as_slice(),
    };
    let approval=hash(approval_domain,&[
        archive.archive_id().as_bytes(),claim.lane.as_bytes(),claim.job.as_bytes(),
        &claim.version.to_be_bytes(),claim.credential.as_bytes(),plan.request.idempotency_key.as_bytes(),
    ]);
    let batch=ClaimedBatch{claim,account:scope.account_id.clone(),namespace:operator.namespace,
        batch:plan.request.idempotency_key.clone(),format,replies:plan.replies};
    let rows=plan.request.messages.len();
    if !matches!(command,ClaimCommand::Preview) {
        let confirmation=operator.confirmation.as_deref().filter(|s|hex(s,64)).ok_or(Error::Confirmation)?;
        if !bool::from(approval.as_bytes().ct_eq(confirmation.as_bytes())) {return Err(Error::Confirmation.into());}
    }
    if matches!(command,ClaimCommand::Status) {
        let receipt=archive.read_claimed_wechat_receipt(&plan.request,&scope.account_id,&batch)?;
        return Ok(match receipt {
            Some(receipt)=>serde_json::json!({"state":"detached_batch_recorded",
                "created":receipt.created,"unchanged":receipt.unchanged,"rows":rows}),
            None=>serde_json::json!({"state":"not_confirmed"}),
        });
    }
    let mut result=if matches!(command,ClaimCommand::Import) {
        let receipt=archive.import_claimed_wechat_detached(plan.request,&scope.account_id,&batch)
            .map_err(claim_apply_error)?;
        serde_json::json!({"state":"detached_batch_recorded","batch_id":batch.batch,
            "created":receipt.created,"unchanged":receipt.unchanged,
            "rows":rows,"job_id":operator.job_id,"job_version":operator.expected_version,
            "adapter_launched":false,"source_job_completed":false,"provider_cursor_advanced":false})
    } else {
        if operator.confirmation.is_some() {return Err(Error::Confirmation.into());}
        let mut db=archive.read_connection().map_err(|_|Error::Archive)?;
        let tx=db.transaction().map_err(|_|Error::Archive)?;
        archive.verify_connection(&tx).map_err(|_|Error::Archive)?;
        batch.check(&tx,false).map_err(claim_apply_error)?;
        drop(tx);
        serde_json::json!({"state":"operator_preview","confirmation":approval,"rows":rows,
            "conversations":plan.conversations,"job_id":operator.job_id,"job_version":operator.expected_version,
            "adapter_launched":false,"source_job_completed":false,"provider_cursor_advanced":false})
    };
    if !matches!(format,ClaimedFormat::Detached) {result["format"]=serde_json::json!(format.name());}
    if matches!(format,ClaimedFormat::LegacyReply) {result["reply_display_rows"]=serde_json::json!(batch.replies.len());}
    if let Some(page)=page {result["page"]=serde_json::json!(page);}
    Ok(result)
}

#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InventoryRequest {token:PrivateToken,format:Option<String>}

#[cfg(target_os="linux")]
pub fn inventory_legacy_export_cli(source:&Path,grants:&Path)->std::result::Result<(),Box<dyn std::error::Error>> {
    use crate::{export_schedule_cli::{private_grants,private_pipes,read_frame,reply},
        plugins::{Permission,PluginKind}};
    private_pipes()?;
    let input:InventoryRequest=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(input.token.0.as_str()).ok_or(Error::Scope)?;
    let with_replies=match input.format.as_deref(){
        None | Some("legacy_hub_export_page_v1")=>false,
        Some(crate::wechat_source::paged_export::REPLY_FORMAT)=>true,
        _=>return Err(Error::Format.into()),
    };
    drop(input);
    let scope=grant.account_scope.as_ref().filter(|scope|
        scope.platform=="wechat" && grant.kind==PluginKind::ScopedProducer
        && grant.permissions.contains(&Permission::Ingest)
        && grant.permissions.contains(&Permission::SourceJobsManage)).ok_or(Error::Scope)?;
    let inventory=export_inventory(source,&scope.account_id,with_replies)?;
    reply(inventory.report(),None)?;
    Ok(())
}

#[cfg(target_os="linux")]
fn claim_apply_error(error:EventError)->Error {
    match error {
        EventError::Conflict=>Error::ClaimConflict,
        EventError::SourceCheckpointConflict=>Error::Claim,
        _=>Error::Apply,
    }
}
