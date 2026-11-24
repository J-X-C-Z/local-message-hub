//! Pinned WeCom rendered-text sync boundary; no provider acquisition.
use crate::{events::{EventError, EventStore, IngestRequest, MAX_BATCH_BYTES, NormalizedMessage},
    legacy_import::{OFFLINE_PRODUCER, read_stable_source}};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize, de::IgnoredAny};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, path::Path};

const FORMAT: &str = "wecom-rendered-sync-v1";
const FORMAT_WITH_NOTICES: &str = "wecom-rendered-sync-v2";
const MAX_NOTICE_BYTES: usize = 8 * 1024;
const JSONL_LINEAGES: &str = "native_wecom_rendered_jsonl_lineages_v1";
const JSONL_LINEAGE_SCHEMA: &str = "
CREATE TABLE native_wecom_rendered_jsonl_lineages_v1(
    account_id TEXT PRIMARY KEY NOT NULL,
    source_id TEXT NOT NULL CHECK(length(source_id)=32)
) WITHOUT ROWID;";

#[cfg(target_os = "linux")]
mod rendered_jsonl;
mod frozen_snapshot;
#[cfg(target_os="linux")]
mod frozen_roster;
#[cfg(target_os="linux")]
const CLAIM_TABLE:&str="native_wecom_claim_receipts_v1";
#[cfg(target_os="linux")]
const CLAIM_SCHEMA:&str="
CREATE TABLE native_wecom_claim_receipts_v1 (
 lane TEXT NOT NULL,job TEXT NOT NULL,job_version INTEGER NOT NULL CHECK(job_version>0),
 account_id TEXT NOT NULL,lineage TEXT NOT NULL,batch TEXT NOT NULL UNIQUE,
 checkpoint TEXT NOT NULL,credential TEXT NOT NULL CHECK(length(credential)=64),PRIMARY KEY(lane,job)
) WITHOUT ROWID;";

const MAX_ROWS: usize = 100;
const MAX_ACCOUNTS: i64 = 256;
const MAX_STAGE_CONVERSATIONS: i64 = 10_000;
const MAX_TOTAL_STAGE_ROWS: i64 = 100_000;
const MAX_TIME: i64 = 253_402_300_799;
const SCHEMA: &str = "
CREATE TABLE native_wecom_rendered_progress_v1(
    account_id TEXT PRIMARY KEY NOT NULL,
    source_id TEXT NOT NULL CHECK(length(source_id)=32),
    generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 4294967295),
    sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 4294967295),
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    checkpoint TEXT NOT NULL CHECK(length(checkpoint)=64)
) WITHOUT ROWID;";

// An empty-id marker records that this generation was observed from its
// first sequence under the staging rules. Old partially-applied snapshots
// without a marker cannot authorize reconciliation after upgrade.
const STAGE_SCHEMA: &str = "
CREATE TABLE native_wecom_rendered_metadata_v2(
    account_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 4294967295),
    conversation_id TEXT NOT NULL CHECK(length(conversation_id)<=256),
    title TEXT,
    participants INTEGER CHECK(participants BETWEEN 0 AND 100000),
    CHECK ((conversation_id='' AND title IS NULL AND participants IS NULL)
        OR (conversation_id<>'' AND length(title) BETWEEN 1 AND 512 AND participants IS NOT NULL
            AND title IS NOT NULL)),
    PRIMARY KEY (account_id,generation,conversation_id)
) WITHOUT ROWID;";

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("selected WeCom sync batch is unavailable")] Source,
    #[error("malformed or unsupported detached WeCom sync batch")] Format,
    #[error("WeCom sync scope does not match the selected account and source")] Scope,
    #[error("target is not an available ordinary native archive")] Archive,
    #[error("local WeCom sync checkpoint does not match the selected batch")] Cursor,
    #[error("confirmation does not bind the exact batch, target and scope")] Confirmation,
    #[error("WeCom message identity or preexisting conversation metadata conflicts")] Conflict,
    #[error("WeCom sync import did not finish; retry only the exact confirmed batch")] Apply,
}
type Result<T> = std::result::Result<T,Error>;

fn hex(value:&str,len:usize)->bool {
    value.len()==len && value.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn source_token(value:&str)->bool { hex(value,32) && value.bytes().any(|b| b!=b'0') }
fn id(value:&str)->bool {
    !value.is_empty() && value.len()<=256 && value.trim()==value
        && !value.chars().any(char::is_control) && !value.contains(['/', '\\'])
}
fn label(value:&str)->bool {
    !value.trim().is_empty() && value.len()<=512 && !value.chars().any(char::is_control)
}
fn text(value:&str)->bool {
    value.len()<=64*1024 && !value.chars().any(|c| c.is_control() && !matches!(c,'\r'|'\n'|'\t'))
}
fn fingerprint<T:Serialize>(data:&T)->Result<String> {
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(data).map_err(|_|Error::Format)?)))
}
fn scope(account:&str,source:&str)->Result<()> {
    if id(account) && source_token(source) { Ok(()) } else { Err(Error::Scope) }
}
fn target(path:&Path)->Result<EventStore> { EventStore::open(path).map_err(|_|Error::Archive) }

#[derive(Deserialize)]
#[serde(untagged)]
enum SourceId { Exact(String), Number(u64) }
impl SourceId {
    fn string(self, allow_empty:bool)->Result<String> {
        let s=match self { Self::Exact(s)=>s, Self::Number(n)=>n.to_string() };
        if id(&s) || (allow_empty && s.is_empty()) { Ok(s) } else { Err(Error::Format) }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    format:String,
    account_id:String,
    source_id:String,
    generation:u32,
    sequence:u32,
    previous_checkpoint:String,
    complete:bool,
    conversations:Vec<Conversation>,
    messages:Vec<Row>,
}

#[derive(Deserialize)]
struct Conversation {
    conversation_id:SourceId,
    name:String,
    participants:u32,
}
#[derive(Deserialize)]
struct Row {
    platform:Option<String>,
    account_id:Option<String>,
    message_id:SourceId,
    conversation_id:SourceId,
    conversation:Option<String>,
    sender_id:Option<SourceId>,
    sender:Option<String>,
    content_type:u32,
    send_time:i64,
    text:String,
    kind:String,
    // Required Boolean in the original renderer, never inferred or defaulted.
    me:bool,
    // Accepted only as classification evidence and never decoded or stored.
    #[serde(default)]
    media:Vec<IgnoredAny>,
}

/// Exact public rendered-text dialect, not a provider payload. In the pinned
/// renderer these four notice codes can carry actual text rather than a
/// synthesized label. This small subset is deliberately stricter than v1
/// ordinary chat text and never parses markup, paths, or structured fields.
fn is_plain_notice(row: &Row) -> bool {
    if !matches!(row.content_type, 38 | 1001 | 1011 | 1022) || row.kind != "text"
        || !row.media.is_empty() || row.text.len() > MAX_NOTICE_BYTES || !text(&row.text) {
        return false;
    }
    let body = row.text.trim();
    if !body.chars().any(char::is_alphabetic) {
        return false;
    }
    if body.chars().any(|c| matches!(c,
        '/' | '\\' | '\t' | ':' | '<' | '>' | '[' | ']' | '{' | '}' | '`' | '*' | '_'
        | '#' | '|' | '~' | '=' | '&' | '【' | '】'))
    {
        return false;
    }
    // The pinned renderer only suppresses bare hex at 24+ characters,
    // but its text selection can produce shorter tokens as well. Reject
    // any bare hex spelling here, even an ambiguous readable short word.
    let hex_body = body.strip_prefix("0x").or_else(|| body.strip_prefix("0X")).unwrap_or(body);
    if !hex_body.is_empty() && hex_body.bytes().all(|b| b.is_ascii_hexdigit()) {
        return false;
    }
    // The detached producer must supply human-readable material, not a
    // similarly opaque long ASCII token.
    !(body.len() >= 24 && body.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'-'))
}

#[derive(Debug,Clone,Serialize)]
pub struct Checkpoint {
    generation:u32,
    sequence:u32,
    complete:bool,
    checkpoint:String,
}
pub(crate) enum HeadRelation {AtHead,Superseded}
impl Checkpoint {
    fn initial()->Self { Self { generation:0,sequence:0,complete:false,checkpoint:"initial".into() } }
}

fn table_installed(db:&Connection,expected:&str)->std::result::Result<bool,EventError> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
        [expected],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object {
        None=>Ok(false),
        Some((name,kind)) if name==expected && kind=="table"=>Ok(true),
        _=>Err(EventError::Storage),
    }
}

fn installed(db:&Connection)->std::result::Result<bool,EventError> {
    table_installed(db,"native_wecom_rendered_progress_v1")
}

fn stage_installed(db:&Connection)->std::result::Result<bool,EventError> {
    table_installed(db,"native_wecom_rendered_metadata_v2")
}

fn load(db:&Connection,account:&str)->std::result::Result<Option<(String,Checkpoint)>,EventError> {
    if !installed(db)? { return Ok(None); }
    let row:Option<(String,Checkpoint)>=db.query_row(
        "SELECT source_id,generation,sequence,complete,checkpoint
         FROM native_wecom_rendered_progress_v1 WHERE account_id=?", [account],
        |r|Ok((r.get(0)?,Checkpoint{generation:r.get(1)?,sequence:r.get(2)?,complete:r.get(3)?,checkpoint:r.get(4)?})))
        .optional()?;
    if row.as_ref().is_some_and(|(src,state)| !source_token(src) || !hex(&state.checkpoint,64)
        || state.generation==0 || state.sequence==0) { return Err(EventError::Storage); }
    Ok(row)
}
fn jsonl_lineage(db:&Connection,account:&str)->std::result::Result<Option<String>,EventError> {
    if !table_installed(db,JSONL_LINEAGES)? { return Ok(None); }
    let lineage:Option<String>=db.query_row(
        "SELECT source_id FROM native_wecom_rendered_jsonl_lineages_v1 WHERE account_id COLLATE BINARY=?",
        [account], |r|r.get(0)).optional()?;
    if lineage.as_deref().is_some_and(|value|!source_token(value)) {
        return Err(EventError::Storage);
    }
    Ok(lineage)
}

fn map_apply(err:EventError)->Error {
    match err { EventError::SourceCheckpointConflict=>Error::Cursor,EventError::Conflict=>Error::Conflict,_=>Error::Apply }
}

struct ConversationMeta { title:String, participants:u32 }

pub(crate) struct Advance {
    account:String,
    source:String,
    expected:String,
    next:Checkpoint,
    jsonl:bool,
    frozen:Option<frozen_snapshot::Step>,
    skipped:usize,
    conversations:BTreeMap<String,ConversationMeta>,
    contacts:BTreeMap<String,String>,
    #[cfg(target_os="linux")]
    claim:Option<ClaimedBatch>,
}
impl Advance {
    pub(crate) fn matches_account(&self,account:&str)->bool { self.account==account }

    pub(crate) fn is_claimed_frozen(&self)->bool {
        #[cfg(target_os="linux")]
        { self.frozen.is_some() && self.claim.is_some() && !self.jsonl }
        #[cfg(not(target_os="linux"))]
        { false }
    }

    pub(crate) fn check(&self,db:&Connection,create:bool)->std::result::Result<(),EventError> {
        #[cfg(target_os="linux")]
        if let Some(claim)=&self.claim { claim.check_new(db,create)?; }

        frozen_snapshot::check(db,self)?;
        let prior=load(db,&self.account)?;
        let marker=jsonl_lineage(db,&self.account)?;
        let mode_matches=match (self.jsonl,marker.as_deref(),prior.as_ref()) {
            (false,None,_) | (true,None,None)=>true,
            (true,Some(lineage),Some((source,head)))=>{
                lineage==self.source.as_str() && source.as_str()==lineage && head.generation==1 && !head.complete
            }
            _=>false,
        };
        if !mode_matches || (self.jsonl && (self.next.generation!=1 || self.next.complete
            || !self.conversations.is_empty())) {
            return Err(EventError::SourceCheckpointConflict);
        }
        let first_jsonl=self.jsonl && prior.is_none();
        let aligned=match prior {
            None=>self.expected=="initial" && self.next.generation==1 && self.next.sequence==1,
            Some((source,old))=>{
                source==self.source && self.expected==old.checkpoint
                    && if old.complete || frozen_snapshot::replacement(self) {
                        old.generation.checked_add(1)==Some(self.next.generation) && self.next.sequence==1
                    } else {
                        old.generation==self.next.generation && old.sequence.checked_add(1)==Some(self.next.sequence)
                    }
            }
        };
        if !aligned { return Err(EventError::SourceCheckpointConflict); }
        if create {
            if !installed(db)? { db.execute_batch(SCHEMA)?; }
            if self.expected=="initial" {
                let existing:i64=db.query_row(
                    "SELECT count(*) FROM native_wecom_rendered_progress_v1",[],|r|r.get(0))?;
                if existing>=MAX_ACCOUNTS { return Err(EventError::Storage); }
            }
            if first_jsonl {
                if !table_installed(db,JSONL_LINEAGES)? { db.execute_batch(JSONL_LINEAGE_SCHEMA)?; }
                db.execute("INSERT INTO native_wecom_rendered_jsonl_lineages_v1(account_id,source_id)
                    VALUES(?,?)",(&self.account,&self.source))?;
            }
        }
        Ok(())
    }

    pub(crate) fn verify_retry(&self,db:&Connection)->std::result::Result<(),EventError> {
        self.verified_head_relation(db,false).map(|_|())
    }

    #[cfg(target_os="linux")]
    pub(crate) fn claimed_head_relation(&self,db:&Connection)->std::result::Result<HeadRelation,EventError> {
        if self.claim.is_none(){return Err(EventError::Conflict);}
        self.verified_head_relation(db,true)
    }

    fn verified_head_relation(&self,db:&Connection,verify_completion:bool)->std::result::Result<HeadRelation,EventError> {
        #[cfg(target_os="linux")]
        if let Some(claim)=&self.claim { claim.check_retry(db,&self.next.checkpoint)?; }

        let (source,saved)=load(db,&self.account)?.ok_or(EventError::Storage)?;
        if source!=self.source {return Err(EventError::Storage);}
        frozen_snapshot::verify_replay(db,self,&saved)?;
        let marker=jsonl_lineage(db,&self.account)?;
        // Older envelope receipts remain readable only for their original
        // mode; a reopened JSONL attempt must still find its durable marker.
        if (self.jsonl && (marker.as_deref()!=Some(self.source.as_str()) ||
            saved.generation!=1 || saved.complete)) || (!self.jsonl && marker.is_some()) {
            return Err(EventError::Storage);
        }
        match (saved.generation,saved.sequence).cmp(&(self.next.generation,self.next.sequence)) {
            std::cmp::Ordering::Greater=>Ok(HeadRelation::Superseded),
            std::cmp::Ordering::Equal if saved.checkpoint==self.next.checkpoint
                && (!verify_completion || saved.complete==self.next.complete)=>Ok(HeadRelation::AtHead),
            _=>Err(EventError::Storage),
        }
    }

    fn has_stage(&self,db:&Connection)->std::result::Result<bool,EventError> {
        if !stage_installed(db)? { return Ok(false); }
        Ok(db.query_row("SELECT EXISTS(SELECT 1 FROM native_wecom_rendered_metadata_v2
             WHERE account_id=? AND generation=? AND conversation_id='')",
             params![self.account,self.next.generation],|r|r.get(0))?)
    }

    fn record_conversations(&self,db:&Connection)->std::result::Result<(),EventError> {
        if self.next.sequence==1 {
            if !stage_installed(db)? { db.execute_batch(STAGE_SCHEMA)?; }
            let leftover:bool=db.query_row("SELECT EXISTS(SELECT 1
                FROM native_wecom_rendered_metadata_v2 WHERE account_id=?)",
                [&self.account],|r|r.get(0))?;
            if leftover { return Err(EventError::Storage); }
            db.execute("INSERT INTO native_wecom_rendered_metadata_v2
                (account_id,generation,conversation_id) VALUES(?,?,'')",
                params![self.account,self.next.generation])?;
        } else if !self.has_stage(db)? {
            // A generation begun by the previous version can finish importing
            // its messages. Its partial metadata never changes kind or count.
            return Ok(());
        }

        for (id,meta) in &self.conversations {
            let changed=db.execute("INSERT INTO native_wecom_rendered_metadata_v2
                (account_id,generation,conversation_id,title,participants) VALUES(?,?,?,?,?)
                ON CONFLICT(account_id,generation,conversation_id) DO UPDATE SET title=excluded.title
                WHERE native_wecom_rendered_metadata_v2.title=excluded.title
                  AND native_wecom_rendered_metadata_v2.participants=excluded.participants",
                params![self.account,self.next.generation,id,meta.title,meta.participants])?;
            if changed!=1 { return Err(EventError::Conflict); }
        }

        let account_rows:i64=db.query_row("SELECT count(*) FROM
            (SELECT 1 FROM native_wecom_rendered_metadata_v2 WHERE account_id=? LIMIT ?)",
            params![self.account,MAX_STAGE_CONVERSATIONS+2],|r|r.get(0))?;
        if account_rows>MAX_STAGE_CONVERSATIONS+1 { return Err(EventError::Storage); }
        let total_rows:i64=db.query_row("SELECT count(*) FROM
            (SELECT 1 FROM native_wecom_rendered_metadata_v2 LIMIT ?)",
            [MAX_TOTAL_STAGE_ROWS+1],|r|r.get(0))?;
        if total_rows>MAX_TOTAL_STAGE_ROWS { return Err(EventError::Storage); }

        if self.next.complete {
            let incompatible:bool=db.query_row("SELECT EXISTS(SELECT 1
                FROM native_wecom_rendered_metadata_v2 m JOIN conversations c
                ON c.platform='wecom' AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE m.account_id=? AND m.generation=? AND m.conversation_id<>''
                  AND c.kind IS NOT NULL AND c.kind NOT IN ('','direct','group','private'))",
                params![self.account,self.next.generation],|r|r.get(0))?;
            if incompatible { return Err(EventError::Conflict); }
            db.execute("INSERT INTO conversations(platform,account_id,id,title,kind,member_count,message_count)
                 SELECT 'wecom',account_id,conversation_id,title,
                     CASE WHEN participants>2 THEN 'group' ELSE 'direct' END,participants,0
                 FROM native_wecom_rendered_metadata_v2
                 WHERE account_id=? AND generation=? AND conversation_id<>''
                 ON CONFLICT(platform,account_id,id) DO UPDATE SET
                     title=COALESCE(NULLIF(conversations.title,''),excluded.title),
                     kind=excluded.kind, member_count=excluded.member_count",
                params![self.account,self.next.generation])?;
            #[cfg(target_os="linux")]
            frozen_roster::publish_final(db,self)?;
            db.execute("DELETE FROM native_wecom_rendered_metadata_v2 WHERE account_id=? AND generation=?",
                params![self.account,self.next.generation])?;
        }
        Ok(())
    }

    pub(crate) fn record(&self,db:&Connection)->std::result::Result<(),EventError> {
        #[cfg(target_os="linux")]
        if let Some(claim)=&self.claim { claim.record(db,&self.next.checkpoint)?; }
        frozen_snapshot::retire_replaced_stage(db,self)?;
        if !self.jsonl { self.record_conversations(db)?; }
        frozen_snapshot::record(db,self)?;
        for (id,name) in &self.contacts {
            db.execute("INSERT INTO contacts(platform,account_id,id,name)
                VALUES('wecom',?,?,?) ON CONFLICT(platform,account_id,id)
                DO UPDATE SET name=excluded.name WHERE contacts.name IS NULL OR contacts.name=''",
                params![self.account,id,name])?;
        }
        db.execute("INSERT INTO native_wecom_rendered_progress_v1
            (account_id,source_id,generation,sequence,complete,checkpoint) VALUES(?,?,?,?,?,?)
            ON CONFLICT(account_id) DO UPDATE SET generation=excluded.generation,
                sequence=excluded.sequence,complete=excluded.complete,checkpoint=excluded.checkpoint",
            params![self.account,self.source,self.next.generation,self.next.sequence,self.next.complete,self.next.checkpoint])?;
        Ok(())
    }
}

struct Plan { request:IngestRequest,advance:Advance,skipped:usize }

fn plan(source:&Path,store:&EventStore,account:&str,lineage:&str)->Result<Plan> {
    scope(account,lineage)?;
    let bytes=read_stable_source(source,MAX_BATCH_BYTES).map_err(|_|Error::Source)?;
    let input:Envelope=serde_json::from_slice(&bytes).map_err(|_|Error::Format)?;
    // Exact input bytes include the format. Existing hashes remain immutable.
    let input_hash=hex::encode(Sha256::digest(&bytes));
    plan_input(input,input_hash,false,store,account,lineage)
}

fn plan_input(input:Envelope,input_hash:String,jsonl:bool,store:&EventStore,account:&str,lineage:&str)->Result<Plan> {
    if input.account_id!=account || input.source_id!=lineage { return Err(Error::Scope); }
    let include_notices = match input.format.as_str() {
        FORMAT => false,
        FORMAT_WITH_NOTICES => true,
        _ => return Err(Error::Format),
    };
    if input.generation==0 || input.sequence==0
        || !(input.previous_checkpoint=="initial" || hex(&input.previous_checkpoint,64))
        || input.messages.len().saturating_add(input.conversations.len())>MAX_ROWS
        || (input.messages.is_empty() && input.conversations.is_empty() && !input.complete)
    { return Err(Error::Format); }
    let mut conversations=BTreeMap::new();
    for item in input.conversations {
        let conv=item.conversation_id.string(false)?;
        if !label(&item.name) || item.participants>100_000 ||
            conversations.insert(conv,ConversationMeta{title:item.name,participants:item.participants}).is_some()
        { return Err(Error::Format); }
    }
    let mut messages=Vec::new();
    let mut seen=BTreeSet::new();
    let mut titles=BTreeMap::new();
    let mut contacts=BTreeMap::new();
    let mut skipped=0;
    for row in input.messages {
        if row.platform.as_deref().is_some_and(|v|v!="wecom")
            || row.account_id.as_deref().is_some_and(|v|v!=account) { return Err(Error::Scope); }
        if !id(&row.kind) { return Err(Error::Format); }
        let ordinary_text = row.content_type == 0 && !matches!(row.kind.as_str(), "sys" | "system")
            && row.media.is_empty();
        if !(ordinary_text || (include_notices && is_plain_notice(&row))) {
            skipped+=1;
            continue;
        }
        let message_id=row.message_id.string(false)?;
        let conversation_id=row.conversation_id.string(false)?;
        let sender_id=row.sender_id.map(|sid|sid.string(true)).transpose()?.filter(|v|!v.is_empty());
        let title=row.conversation.filter(|v|!v.is_empty());
        let sender_name=row.sender.filter(|v|!v.is_empty());
        if !seen.insert(message_id.clone()) || !(1..=MAX_TIME).contains(&row.send_time) || !text(&row.text)
            || title.as_deref().is_some_and(|v|!label(v)) || sender_name.as_deref().is_some_and(|v|!label(v)) {
            return Err(Error::Format);
        }
        if let Some(prior)=titles.insert(conversation_id.clone(),title.clone()) {
            if prior!=title { return Err(Error::Format); }
        }
        if let Some(sid)=&sender_id {
            let name=sender_name.as_ref().unwrap_or(sid).clone();
            if let Some(prior)=contacts.insert(sid.clone(),name.clone()) {
                if prior!=name { return Err(Error::Format); }
            }
        }
        messages.push(NormalizedMessage{platform:"wecom".into(),account_id:account.to_owned(),conversation_id,
            message_id,title,sender_id,sender_name,timestamp:row.send_time,is_self:row.me,text:Some(row.text)});
    }
    let batch=fingerprint(&("hub-wecom-rendered-sync-batch-v1",store.archive_id(),account,lineage,input_hash))?;
    let checkpoint=fingerprint(&("hub-wecom-rendered-local-checkpoint-v1",store.archive_id(),account,lineage,
        input.generation,input.sequence,input.complete,&batch))?;
    let request=IngestRequest{idempotency_key:batch,messages};
    if !request.messages.is_empty() { request.validate(OFFLINE_PRODUCER).map_err(|_|Error::Format)?; }
    if serde_json::to_vec(&request.messages).map_err(|_|Error::Format)?.len()>MAX_BATCH_BYTES { return Err(Error::Format); }
    Ok(Plan{request,skipped,advance:Advance{
        account:account.to_owned(),source:lineage.to_owned(),expected:input.previous_checkpoint,
        next:Checkpoint{generation:input.generation,sequence:input.sequence,complete:input.complete,checkpoint},
        jsonl,frozen:None,skipped,conversations,contacts,
        #[cfg(target_os="linux")]
        claim:None,
    }})
}

#[derive(Serialize)]
pub struct Preview {
    confirmation:String, next:Checkpoint, text_rows:usize, conversation_rows:usize, skipped_non_text:usize,
    conversation_metadata:&'static str,
}
#[derive(Serialize)]
pub struct Receipt {
    batch_id:String, next:Checkpoint, created:usize, unchanged:usize, skipped_non_text:usize,
}

pub fn status(directory:&Path,account:&str,lineage:&str)->Result<Checkpoint> {
    scope(account,lineage)?;
    let store=target(directory)?;
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    match load(&tx,account).map_err(map_apply)? {
        None=>Ok(Checkpoint::initial()),
        Some((source,prior)) if source==lineage=>Ok(prior),
        _=>Err(Error::Scope),
    }
}

pub fn preview(source:&Path,directory:&Path,account:&str,lineage:&str)->Result<Preview> {
    let store=target(directory)?;
    let plan=plan(source,&store,account,lineage)?;
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    plan.advance.check(&tx,false).map_err(map_apply)?;
    let whole=plan.advance.next.sequence==1 || plan.advance.has_stage(&tx).map_err(map_apply)?;
    let conversation_metadata=if !whole { "prior_generation_not_fully_observed" }
        else if plan.advance.next.complete { "apply_completed_generation" }
        else { "stage_until_generation_complete" };
    Ok(Preview { confirmation:plan.request.idempotency_key,next:plan.advance.next,
        text_rows:plan.request.messages.len(),conversation_rows:plan.advance.conversations.len(),
        skipped_non_text:plan.skipped,conversation_metadata })
}

pub fn commit(source:&Path,directory:&Path,account:&str,lineage:&str,confirmation:&str)->Result<Receipt> {
    if !hex(confirmation,64) { return Err(Error::Confirmation); }
    let store=target(directory)?;
    let plan=plan(source,&store,account,lineage)?;
    if plan.request.idempotency_key!=confirmation { return Err(Error::Confirmation); }
    let applied=store.import_wecom_sync(plan.request,account,&plan.advance).map_err(map_apply)?;
    Ok(Receipt{batch_id:confirmation.to_owned(),next:plan.advance.next,created:applied.created,
        unchanged:applied.unchanged,skipped_non_text:plan.skipped})
}

#[cfg(target_os="linux")]
struct ClaimedBatch {
    claim:crate::source_jobs::LocalDetachedClaim,
    account:String,
    lineage:String,
    batch:String,
}
#[cfg(target_os="linux")]
impl ClaimedBatch {
    fn check_new(&self,db:&Connection,create:bool)->std::result::Result<(),EventError> {
        self.claim.verify_in(db)?;
        if !table_installed(db,CLAIM_TABLE)? {
            if create {db.execute_batch(CLAIM_SCHEMA)?;}
            return Ok(());
        }
        let spent:bool=db.query_row(
            "SELECT EXISTS(SELECT 1 FROM native_wecom_claim_receipts_v1
             WHERE (lane=? AND job=?) OR batch=?)",
            params![self.claim.lane,self.claim.job,self.batch],|r|r.get(0))?;
        if spent {return Err(EventError::Conflict);}
        Ok(())
    }

    fn check_retry(&self,db:&Connection,checkpoint:&str)->std::result::Result<(),EventError> {
        if !table_installed(db,CLAIM_TABLE)? {return Err(EventError::Conflict);}
        let credential:Option<String>=db.query_row(
            "SELECT credential FROM native_wecom_claim_receipts_v1 WHERE
             lane=? AND job=? AND job_version=? AND account_id=? AND lineage=? AND batch=? AND checkpoint=?",
            params![self.claim.lane,self.claim.job,self.claim.version,self.account,self.lineage,self.batch,checkpoint],
            |r|r.get(0)).optional()?;
        use subtle::ConstantTimeEq;
        if !credential.as_deref().is_some_and(|stored|
            bool::from(stored.as_bytes().ct_eq(self.claim.credential.as_bytes()))) {
            return Err(EventError::Conflict);
        }
        Ok(())
    }

    fn record(&self,db:&Connection,checkpoint:&str)->std::result::Result<(),EventError> {
        db.execute(
            "INSERT INTO native_wecom_claim_receipts_v1(lane,job,job_version,account_id,lineage,batch,checkpoint,credential)
             VALUES (?,?,?,?,?,?,?,?)",
            params![self.claim.lane,self.claim.job,self.claim.version,self.account,self.lineage,self.batch,checkpoint,self.claim.credential],
        )?;
        Ok(())
    }
}

#[cfg(target_os="linux")]
struct OperatorSecret(zeroize::Zeroizing<String>);
#[cfg(target_os="linux")]
impl<'de> Deserialize<'de> for OperatorSecret {
    fn deserialize<D:serde::Deserializer<'de>>(d:D)->std::result::Result<Self,D::Error>{
        String::deserialize(d).map(|s|Self(zeroize::Zeroizing::new(s)))
    }
}
#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Operator {
    token:OperatorSecret,
    job_id:String,
    expected_version:i64,
    lineage:String,
    confirmation:Option<String>,
    rendered_jsonl_page:Option<rendered_jsonl::Selection>,
    frozen_snapshot_page:Option<frozen_snapshot::Selection>,
}

#[cfg(target_os="linux")]
pub fn claimed_local_cli(source:&Path,directory:&Path,grants:&Path,import:bool)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    run_claim_operator(source,directory,grants,if import{ClaimCommand::Import}else{ClaimCommand::Preview})
}

#[cfg(target_os="linux")]
pub fn claimed_local_status_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    run_claim_operator(source,directory,grants,ClaimCommand::Status)
}

#[cfg(target_os="linux")]
#[derive(Clone,Copy)]
enum ClaimCommand {Preview,Import,Status}

#[cfg(target_os="linux")]
fn run_claim_operator(source:&Path,directory:&Path,grants:&Path,command:ClaimCommand)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    use crate::export_schedule_cli::{private_pipes,read_frame,reply};
    private_pipes()?;
    let result=read_frame::<Operator>().map_err(Into::<Box<dyn std::error::Error>>::into)
        .and_then(|operator|claim_operation(source,directory,grants,command,operator));
    let selected=if matches!(command,ClaimCommand::Status) {
        result.unwrap_or_else(|_|serde_json::json!({"state":"not_confirmed"}))
    } else {result?};
    reply(selected,None)?;
    Ok(())
}

#[cfg(target_os="linux")]
fn claim_operation(source:&Path,directory:&Path,grants:&Path,command:ClaimCommand,operator:Operator)
    ->std::result::Result<serde_json::Value,Box<dyn std::error::Error>> {
    use crate::export_schedule_cli::private_grants;
    use subtle::ConstantTimeEq;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(operator.token.0.as_str()).ok_or(Error::Scope)?;
    let account=grant.account_scope.as_ref().ok_or(Error::Scope)?.account_id.as_str();
    let store=target(directory)?;
    store.require_ordinary().map_err(|_|Error::Archive)?;
    let claim=crate::source_jobs::LocalDetachedClaim::new(&store,&grant,"wecom",&operator.job_id,operator.expected_version,operator.token.0.as_str())
        .map_err(|_|Error::Scope)?;
    let (mut plan,rendered_review,frozen_review)=match (operator.rendered_jsonl_page,operator.frozen_snapshot_page) {
        (Some(page),None)=>{
            let (plan,review)=rendered_jsonl::plan(source,&store,account,&operator.lineage,
                &claim,command,page)?;
            (plan,Some(review),None)
        }
        (None,Some(page))=>{
            let (plan,review)=frozen_snapshot::plan(source,&store,account,&operator.lineage,
                &claim,command,page)?;
            (plan,None,Some(review))
        }
        (None,None)=>(plan(source,&store,account,&operator.lineage)?,None,None),
        _=>return Err(Error::Format.into()),
    };
    let confirmation=fingerprint(&("claimed-local-wecom-v1",store.archive_id(),
        &claim.lane,&claim.job,claim.version,&plan.request.idempotency_key))?;
    plan.advance.claim=Some(ClaimedBatch{claim,account:account.to_owned(),
        lineage:operator.lineage,batch:plan.request.idempotency_key.clone()});
    if !matches!(command,ClaimCommand::Preview) {
        let submitted=operator.confirmation.as_deref().filter(|text|hex(text,64)).ok_or(Error::Confirmation)?;
        if !bool::from(confirmation.as_bytes().ct_eq(submitted.as_bytes())) {return Err(Error::Confirmation.into());}
    }
    if matches!(command,ClaimCommand::Status) {
        let Some((receipt,relation))=store.read_claimed_wecom_receipt(&plan.request,account,&plan.advance)?
            else {return Ok(serde_json::json!({"state":"not_confirmed"}));};
        let mut result=serde_json::json!({"state":"detached_batch_recorded",
            "created":receipt.created,"unchanged":receipt.unchanged,"skipped_non_text":plan.skipped});
        match relation {
            HeadRelation::AtHead=>{
                result["position"]=serde_json::json!("at_head");
                result["next"]=serde_json::json!(plan.advance.next);
            }
            HeadRelation::Superseded=>{result["position"]=serde_json::json!("superseded");}
        }
        return Ok(result);
    }
    if matches!(command,ClaimCommand::Import) {
        let applied=store.import_wecom_sync(plan.request,account,&plan.advance).map_err(map_apply)?;
        Ok(serde_json::json!({
            "state":"detached_batch_recorded","job_id":operator.job_id,
            "job_version":operator.expected_version,"batch_id":plan.advance.claim.as_ref().ok_or(Error::Apply)?.batch,
            "next":plan.advance.next,"created":applied.created,"unchanged":applied.unchanged,
            "skipped_non_text":plan.skipped,"adapter_launched":false,"source_job_completed":false,
        }))
    } else {
        if operator.confirmation.is_some() {return Err(Error::Confirmation.into());}
        let mut db=store.read_connection().map_err(|_|Error::Archive)?;
        let tx=db.transaction().map_err(|_|Error::Archive)?;
        store.verify_connection(&tx).map_err(|_|Error::Archive)?;
        plan.advance.check(&tx,false).map_err(map_apply)?;
        drop(tx);
        let mut preview=serde_json::json!({
            "state":"operator_preview","confirmation":confirmation,"job_id":operator.job_id,
            "job_version":operator.expected_version,"next":plan.advance.next,
            "text_rows":plan.request.messages.len(),"conversation_rows":plan.advance.conversations.len(),
            "skipped_non_text":plan.skipped,"adapter_launched":false,"source_job_completed":false,
        });
        if let Some(review)=rendered_review {
            preview["rendered_jsonl_page"]=serde_json::to_value(review).map_err(|_|Error::Format)?;
        }
        if let Some(review)=frozen_review {
            preview["frozen_snapshot_page"]=serde_json::to_value(review).map_err(|_|Error::Format)?;
        }
        Ok(preview)
    }
}


#[cfg(target_os="linux")]
pub fn inventory_frozen_snapshot_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    frozen_snapshot::inventory_cli(source,directory,grants)
}

#[cfg(target_os="linux")]
pub fn inspect_frozen_snapshot_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    frozen_snapshot::inspect_cli(source,directory,grants)
}


#[cfg(target_os="linux")]
pub fn frozen_roster_cli(directory:&Path,grants:&Path)->std::result::Result<(),Box<dyn std::error::Error>> {
    frozen_roster::cli(directory,grants)
}


#[cfg(target_os="linux")]
pub(crate) use frozen_roster::Display as FrozenConversationDisplay;

pub(crate) const FROZEN_CONVERSATION_DISPLAY:&str="/api/v1/conversations/wecom-rendered-display";

pub(crate) fn display_conversation_id_valid(value:&str)->bool { id(value) }

/// Linux ordinary live server, after exact Desktop permission admission.
/// Use a verified read transaction; never open a producer path or emit
/// private roster lineage, checkpoints or generation state.
#[cfg(target_os="linux")]
pub(crate) fn frozen_conversation_display(store:&EventStore,account:&str,conversation:&str)
    ->Result<Option<FrozenConversationDisplay>>
{
    store.require_ordinary().map_err(|_|Error::Archive)?;
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    frozen_roster::indexed_display(&tx,account,conversation)
}

#[cfg(target_os="linux")]
pub fn preflight_frozen_snapshot_target_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    frozen_snapshot::target_fit_cli(source,directory,grants)
}


#[cfg(target_os="linux")]
pub fn frozen_snapshot_outcome_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>> {
    frozen_snapshot::outcomes::cli(source,directory,grants)
}
