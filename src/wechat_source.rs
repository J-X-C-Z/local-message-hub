//! Bridge already-detached personal-WeChat text into the supervised source SDK.
//! The adapter owns selection and durable upstream bookkeeping; this module
//! neither reads a provider nor creates a process, archive, or network client.
use crate::{
    events::{IngestRequest,NormalizedMessage,MAX_BATCH_BYTES},
    plugins::AccountScope,
    wechat_detached::{self,hash,valid_scope},
};
use serde::{Deserialize,Serialize};
use std::{collections::{BTreeMap,BTreeSet},io::{BufRead,Read,Write}};
use zeroize::Zeroizing;

pub mod cursor;
#[cfg(target_os="linux")]
pub(crate) mod paged_export;

const PROTOCOL:u8=1;
const MAX_SEQUENCE:u64=9_007_199_254_740_991;
const MAX_CONTROL:u64=2048;
const MAX_TRANSPORT:usize=530*1024;

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("invalid personal WeChat source account or namespace")]
    Scope,
    #[error("invalid source SDK protocol frame")]
    Frame,
    #[error("detached contents do not match the expected bounded WeChat format")]
    Detached,
    #[error("unsupported or ambiguous original WeChat Hub JSON export row")]
    LegacyExport,
    #[error("selection does not follow the current source sequence")]
    Sequence,
    #[error("source SDK I/O did not finish")]
    Io,
    #[error("source batch may have committed; check a fresh durable checkpoint before retrying")]
    OutcomeUnknown,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Resume {
    protocol:u8,
    #[serde(rename="type")]
    kind:String,
    scope:AccountScope,
    sequence:u64,
}
#[derive(Serialize)]
struct Frame {
    protocol:u8,
    #[serde(rename="type")]
    kind:&'static str,
    sequence:u64,
    batch:IngestRequest,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reply {
    protocol:u8,
    #[serde(rename="type")]
    kind:String,
    sequence:u64,
    created:usize,
    revised:usize,
    unchanged:usize,
}

pub struct Session<'a>{account:&'a str,namespace:&'a str,previous:u64}
impl<'a> Session<'a> {
    /// Validate one LF-terminated broker resume frame against the adapter's
    /// locally configured account. No credentials appear in the frame.
    pub fn from_resume(line:&[u8],exact_account:&'a str,source_namespace:&'a str)
        ->Result<Self,Error> {
        if !valid_scope(exact_account,source_namespace){return Err(Error::Scope);}
        let resume:Resume=decode(line)?;
        if resume.protocol!=PROTOCOL||resume.kind!="resume"||resume.sequence>MAX_SEQUENCE {
            return Err(Error::Frame);
        }
        if resume.scope.platform!="wechat"||resume.scope.account_id!=exact_account {
            return Err(Error::Scope);
        }
        Ok(Self{account:exact_account,namespace:source_namespace,previous:resume.sequence})
    }

    pub fn previous_sequence(&self)->u64 {self.previous}

    /// Prepare exactly one explicit successor page. The caller must supply its
    /// own persisted/selected predecessor sequence; don't relabel a stale page
    /// with the sequence reported by the broker.
    pub fn prepare(self,selected_after:u64,detached_json:&[u8])->Result<Pending,Error> {
        let sequence=self.successor(selected_after)?;
        let (messages,_)=wechat_detached::normalized(detached_json,self.account,self.namespace)
            .map_err(|error|match error {
                wechat_detached::Error::Scope=>Error::Scope,
                _=>Error::Detached,
            })?;
        self.pack(sequence,messages,Error::Detached)
    }

    /// Use a selected JSON array from the pinned original Hub's normalized JSON
    /// export. Keeps legacy identifiers verbatim rather than rehashing them.
    pub fn prepare_legacy_export(self,selected_after:u64,export_json:&[u8])->Result<Pending,Error> {
        let sequence=self.successor(selected_after)?;
        let messages=legacy_rows(export_json,self.account)?;
        self.pack(sequence,messages,Error::LegacyExport)
    }

    fn successor(&self,selected_after:u64)->Result<u64,Error> {
        if selected_after!=self.previous||self.previous==MAX_SEQUENCE{return Err(Error::Sequence);}
        Ok(self.previous+1)
    }

    fn pack(self,sequence:u64,messages:Vec<NormalizedMessage>,format_error:Error)->Result<Pending,Error> {
        // Stable for retries across whitespace changes. The Hub also compares the
        // actual body+advance digest, so a changed body under this key cannot replay.
        let key=hash(b"lmh-wechat-source-sdk-batch-v1",&[
            self.account.as_bytes(),self.namespace.as_bytes(),&sequence.to_be_bytes(),
        ]);
        let count=messages.len();
        let batch=IngestRequest{idempotency_key:key,messages};
        if batch.validate("@wechat/source-sdk-v1").is_err()
            || serde_json::to_vec(&batch.messages).map_or(true,|encoded|encoded.len()>MAX_BATCH_BYTES) {
            return Err(format_error);
        }
        let mut outbound=Zeroizing::new(serde_json::to_vec(&Frame {
            protocol:PROTOCOL,kind:"batch",sequence,batch,
        }).map_err(|_|Error::Frame)?);
        if outbound.len()>=MAX_TRANSPORT{return Err(format_error);}
        outbound.push(b'\n');
        Ok(Pending{outbound,sequence,count})
    }
}

// The pinned writer copies each Hub row into JSON, plus decoration fields.
// Only these named fields participate in the projection. All other fields
// are dropped, not interpreted as locators/resources/credentials or stored.
#[derive(Deserialize)]
struct LegacyRow {
    id:String,
    platform:String,
    account_id:String,
    conversation_id:String,
    conversation_title:Option<String>,
    sender_id:String,
    sender_name:Option<String>,
    timestamp:i64,
    #[serde(rename="type")]
    kind:String,
    text:String,
    is_self:bool,
}

fn legacy_message_id(id:&str,conversation:&str)->bool {
    if id.len()>256 {return false;}
    let Some(rest)=id.strip_prefix(conversation).and_then(|tail|tail.strip_prefix(':'))
        else {return false;};
    let Some((shard,local))=rest.split_once(':') else {return false;};
    // The original rendered session:shard:local_id format is kept exactly.
    // Reject the empty legacy shard fallback and any ambiguous components.
    wechat_detached::source_id(shard)&&wechat_detached::local_id(local)
}

pub(crate) fn legacy_rows(bytes:&[u8],account:&str)->Result<Vec<NormalizedMessage>,Error> {
    if bytes.len()>MAX_BATCH_BYTES {return Err(Error::LegacyExport);}
    let rows:Vec<LegacyRow>=serde_json::from_slice(bytes).map_err(|_|Error::LegacyExport)?;
    normalize_legacy_rows(rows,account)
}

#[cfg(target_os="linux")]
pub(crate) fn is_legacy_projection(row:&NormalizedMessage,account:&str)->bool {
    row.platform=="wechat" && row.account_id==account
        && wechat_detached::source_id(&row.conversation_id)
        && legacy_message_id(&row.message_id,&row.conversation_id)
        && row.sender_id.as_deref().is_some_and(|sender|
            wechat_detached::source_id(sender) && row.is_self==(sender==account))
        && (1..=253_402_300_799).contains(&row.timestamp)
        && row.text.as_deref().is_some_and(wechat_detached::text)
        && wechat_detached::label(row.sender_name.as_deref())
        && wechat_detached::label(row.title.as_deref())
}

fn normalize_legacy_rows(rows:Vec<LegacyRow>,account:&str)->Result<Vec<NormalizedMessage>,Error> {
    normalize_export_rows(rows,account,100)
}

fn normalize_export_rows(rows:Vec<LegacyRow>,account:&str,limit:usize)->Result<Vec<NormalizedMessage>,Error> {
    if !(1..=limit).contains(&rows.len()){return Err(Error::LegacyExport);}
    let mut seen=BTreeSet::new();
    let mut titles=BTreeMap::new();
    let mut out=Vec::with_capacity(rows.len());
    for row in rows {
        if row.platform!="wechat"||row.account_id!=account{return Err(Error::Scope);}
        if row.kind!="text"||!wechat_detached::source_id(&row.conversation_id)
            ||!wechat_detached::source_id(&row.sender_id)
            ||!legacy_message_id(&row.id,&row.conversation_id)
            ||!(1..=253_402_300_799).contains(&row.timestamp)
            ||row.is_self!=(row.sender_id==account)||!wechat_detached::text(&row.text)
            ||!wechat_detached::label(row.sender_name.as_deref())
            ||!wechat_detached::label(row.conversation_title.as_deref())
            ||!seen.insert(row.id.clone()) {
            return Err(Error::LegacyExport);
        }
        if let Some(prior)=titles.insert(row.conversation_id.clone(),row.conversation_title.clone()) {
            if prior!=row.conversation_title{return Err(Error::LegacyExport);}
        }
        out.push(NormalizedMessage{
            platform:row.platform,account_id:row.account_id,conversation_id:row.conversation_id,
            message_id:row.id,title:row.conversation_title,sender_id:Some(row.sender_id),
            sender_name:row.sender_name,timestamp:row.timestamp,is_self:row.is_self,text:Some(row.text),
        });
    }
    Ok(out)
}

pub struct Pending{outbound:Zeroizing<Vec<u8>>,sequence:u64,count:usize}
impl Pending {
    pub fn frame(&self)->&[u8] {&self.outbound}

    /// Accept only a matching sanitized broker acknowledgement; any failure
    /// after sending leaves the outcome unknown, not safe for blind replay.
    pub fn acknowledge(self,line:&[u8])->Result<Committed,Error> {
        let reply:Reply=decode(line).map_err(|_|Error::OutcomeUnknown)?;
        let total=reply.created.checked_add(reply.revised)
            .and_then(|n|n.checked_add(reply.unchanged));
        if reply.protocol!=PROTOCOL||reply.kind!="committed"||reply.sequence!=self.sequence
            ||total!=Some(self.count) {return Err(Error::OutcomeUnknown);}
        Ok(Committed {sequence:reply.sequence,created:reply.created,
            revised:reply.revised,unchanged:reply.unchanged})
    }
}

/// Caller-controlled selection from an already-authorized local source.
pub enum Selection {
    Idle,
    Detached {after_sequence:u64,utf8_json:Zeroizing<Vec<u8>>},
    LegacyHubExport {after_sequence:u64,utf8_json:Zeroizing<Vec<u8>>},
}

#[derive(Clone,Copy,Serialize)]
pub struct Committed {
    pub sequence:u64,pub created:usize,pub revised:usize,pub unchanged:usize,
}

/// Run the child side on caller-owned private request/reply pipes. No paths,
/// command-line options, tokens, stdout diagnostics, polling or network.
///
/// Use the existing source broker so reads and writes have deadlines and
/// the child lifetime is managed; this helper limits bytes, not idle time.
/// The selection callback is deliberately the only adapter/provider boundary.
pub fn exchange(input:&mut impl BufRead,output:&mut impl Write,account:&str,namespace:&str,
    select:impl FnOnce(u64)->Result<Selection,Error>)->Result<Option<Committed>,Error> {
    let resume=read_frame(input)?;
    let session=Session::from_resume(&resume,account,namespace)?;
    let pending=match select(session.previous_sequence())? {
        Selection::Idle=>{
            output.write_all(b"{\"protocol\":1,\"type\":\"idle\"}\n").and_then(|_|output.flush())
                .map_err(|_|Error::Io)?;
            return Ok(None);
        }
        Selection::Detached{after_sequence,utf8_json}=>session.prepare(after_sequence,&utf8_json)?,
        Selection::LegacyHubExport{after_sequence,utf8_json}=>
            session.prepare_legacy_export(after_sequence,&utf8_json)?,
    };
    output.write_all(pending.frame()).and_then(|_|output.flush())
        .map_err(|_|Error::OutcomeUnknown)?;
    let reply=read_frame(input).map_err(|_|Error::OutcomeUnknown)?;
    pending.acknowledge(&reply).map(Some)
}

fn read_frame(input:&mut impl BufRead)->Result<Zeroizing<Vec<u8>>,Error> {
    let mut frame=Zeroizing::new(Vec::new());
    input.take(MAX_CONTROL+1).read_until(b'\n',&mut frame).map_err(|_|Error::Io)?;
    if frame.len() as u64>MAX_CONTROL || !frame.ends_with(b"\n") {return Err(Error::Frame);}
    Ok(frame)
}
fn decode<T:for<'de>Deserialize<'de>>(line:&[u8])->Result<T,Error> {
    if line.len() as u64>MAX_CONTROL{return Err(Error::Frame);}
    let frame=line.strip_suffix(b"\n").ok_or(Error::Frame)?;
    if frame.contains(&b'\n')||frame.contains(&b'\r'){return Err(Error::Frame);}
    serde_json::from_slice(frame).map_err(|_|Error::Frame)
}

#[cfg(target_os="linux")]
pub(crate) mod reply_export;
