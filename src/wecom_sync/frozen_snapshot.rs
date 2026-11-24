//! Operator-attested pair of rendered lists from one frozen refresh, not a provider cursor.
use super::{Conversation,Envelope,Error,EventError,EventStore,FORMAT_WITH_NOTICES,
    MAX_BATCH_BYTES,MAX_ROWS,Plan,Result,Row,fingerprint,hex,jsonl_lineage,label,load,map_apply,
    plan_input,read_stable_source,scope,table_installed};
#[cfg(target_os="linux")]
use super::ClaimCommand;
#[cfg(target_os="linux")]
use crate::{plugins::{Permission,PluginKind},source_jobs::LocalDetachedClaim};
use rusqlite::{Connection,params};
use serde::{Deserialize,Deserializer,Serialize,de::{self,SeqAccess,Visitor}};
use serde_json::value::RawValue;
use sha2::{Digest,Sha256};
use std::{collections::BTreeSet,fmt,path::Path};
use zeroize::Zeroizing;

#[cfg(target_os="linux")]
pub(super) mod outcomes;

const MAX_BYTES:usize=8*1024*1024;
const MAX_ITEMS:usize=10_000;
const MAX_PAGES:usize=128;
const FORMAT:&str="wecom-frozen-rendered-pair-v1";
const TABLE:&str="native_wecom_frozen_snapshot_v1";
const SCHEMA:&str="
CREATE TABLE native_wecom_frozen_snapshot_v1(
 account_id TEXT PRIMARY KEY NOT NULL, source_id TEXT NOT NULL CHECK(length(source_id)=32),
 generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 4294967295),
 file_hash TEXT NOT NULL CHECK(length(file_hash)=64),
 pages INTEGER NOT NULL CHECK(pages BETWEEN 1 AND 128),
 committed INTEGER NOT NULL CHECK(committed BETWEEN 1 AND pages)
) WITHOUT ROWID;";

#[derive(Deserialize,Serialize)]
#[serde(rename_all="snake_case")]
enum Attestation { AuthorizedSingleRenderedRefresh }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Pair<'a> {
    format:String,
    account_binding:Attestation,
    account_id:String,
    #[serde(borrow)]
    conversations:Tokens<'a>,
    #[serde(borrow)]
    messages:Tokens<'a>,
}
struct Tokens<'a>(Vec<&'a RawValue>);
impl<'de:'a,'a> Deserialize<'de> for Tokens<'a> {
    fn deserialize<D:Deserializer<'de>>(input:D)->std::result::Result<Self,D::Error> {
        struct Bounded;
        impl<'de> Visitor<'de> for Bounded {
            type Value=Tokens<'de>;
            fn expecting(&self,fmt:&mut fmt::Formatter<'_>)->fmt::Result{
                fmt.write_str("bounded rendered snapshot rows")
            }
            fn visit_seq<S:SeqAccess<'de>>(self,mut seq:S)->std::result::Result<Tokens<'de>,S::Error>{
                let mut out=Vec::new();
                while let Some(raw)=seq.next_element::<&'de RawValue>()? {
                    if out.len()==MAX_ITEMS||raw.get().len()>MAX_BATCH_BYTES-2 {
                        return Err(de::Error::custom("snapshot budget exceeded"));
                    }
                    out.push(raw);
                }
                Ok(Tokens(out))
            }
        }
        let Tokens(rows)=input.deserialize_seq(Bounded)?;
        Ok(Tokens(rows))
    }
}

#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Selection {
    file_sha256:String,
    index:usize,
    position:Option<Position>,
    #[serde(skip_serializing_if="Option::is_none")]
    replace_incomplete_sha256:Option<String>,
}
#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Position {
    generation:u32,
    previous_checkpoint:String,
}

#[derive(Clone)]
pub(super) struct Step {
    hash:String,index:usize,pages:usize,replace:Option<String>,
}

struct Head {
    source:String,generation:u32,hash:String,pages:usize,committed:usize,
}

fn head(db:&Connection,account:&str)->std::result::Result<Option<Head>,EventError>{
    if !table_installed(db,TABLE)?{return Ok(None);}
    let mut stmt=db.prepare(
        "SELECT source_id,generation,file_hash,pages,committed FROM native_wecom_frozen_snapshot_v1
         WHERE account_id COLLATE BINARY=? LIMIT 2")?;
    let mut rows=stmt.query([account])?;
    let result=match rows.next()? {
        None=>None,
        Some(r)=>Some(Head{source:r.get(0)?,generation:r.get(1)?,hash:r.get(2)?,pages:r.get(3)?,committed:r.get(4)?}),
    };
    if result.is_some()&&rows.next()?.is_some(){return Err(EventError::Storage);}
    if result.as_ref().is_some_and(|h|!super::source_token(&h.source)||!hex(&h.hash,64)
        ||h.generation==0||!(1..=MAX_PAGES).contains(&h.pages)||!(1..=h.pages).contains(&h.committed)){
        return Err(EventError::Storage);
    }
    Ok(result)
}

#[cfg(target_os="linux")]
pub(super) fn validated_head_for_roster(db:&Connection,account:&str,lineage:Option<&str>)
    ->Result<Option<(String,super::Checkpoint)>>{
    if jsonl_lineage(db,account).map_err(map_apply)?.is_some(){return Ok(None);}
    let Some(marker)=head(db,account).map_err(map_apply)? else{return Ok(None);};
    let Some((source,progress))=load(db,account).map_err(map_apply)? else{return Ok(None);};
    if marker.source!=source || lineage.is_some_and(|wanted|wanted!=source.as_str())
        || marker.generation!=progress.generation
        || marker.committed!=progress.sequence as usize || progress.complete!=(marker.committed==marker.pages){
        return Ok(None);
    }
    Ok(Some((source,progress)))
}

// Every fresh page must follow both the legacy local checkpoint and the
// immutable snapshot's ordinal. Replacement requires its own first-page proof.
pub(super) fn check(db:&Connection,advance:&super::Advance)->std::result::Result<(),EventError>{
    if let Some(step)=&advance.frozen {
        if !(1..=MAX_PAGES).contains(&step.pages)||step.index>=step.pages
            || advance.next.sequence as usize!=step.index+1
            || advance.next.complete!=(step.index+1==step.pages)
            || !hex(&step.hash,64)
            || step.replace.as_deref().is_some_and(|old|
                step.index!=0 || !hex(old,64) || old==step.hash.as_str()) {
            return Err(EventError::SourceCheckpointConflict);
        }
    }
    let marker=head(db,&advance.account)?;
    if advance.frozen.is_none() {
        return if marker.is_none(){Ok(())}else{Err(EventError::SourceCheckpointConflict)};
    }
    match (&advance.frozen,marker,load(db,&advance.account)?) {
        (Some(step),None,None) if step.index==0&&advance.next.generation==1
            && step.replace.is_none()&&advance.next.sequence==1&&advance.expected=="initial"=>Ok(()),
        (Some(step),Some(saved),Some((source,previous)))
            if coherent(advance,&saved,&source,&previous) && advance.expected==previous.checkpoint &&
            (match (previous.complete,step.replace.as_deref()) {
                (false,Some(old))=> old==saved.hash.as_str() && step.hash!=saved.hash
                    && replacement(advance)
                    && previous.generation.checked_add(1)==Some(advance.next.generation),
                (true,None)=>step.index==0&&step.hash!=saved.hash
                    &&previous.generation.checked_add(1)==Some(advance.next.generation),
                (false,None)=>step.hash==saved.hash&&step.pages==saved.pages&&step.index==saved.committed
                    &&advance.next.generation==saved.generation,
                (true,Some(_))=>false,
            })=>Ok(()),
        _=>Err(EventError::SourceCheckpointConflict),
    }
}

pub(super) fn replacement(advance:&super::Advance)->bool{
    #[cfg(target_os="linux")]
    {
        advance.claim.is_some() && !advance.jsonl
            && advance.frozen.as_ref().is_some_and(|step|step.index==0&&step.replace.is_some())
    }
    #[cfg(not(target_os="linux"))]
    { let _=advance; false }
}

// Called only after the strongly pinned writer admission above, on the
// same transaction, never as a separate abandonment operation.
pub(super) fn retire_replaced_stage(db:&Connection,advance:&super::Advance)
    ->std::result::Result<(),EventError>{
    if !replacement(advance){return Ok(());}
    let generation=advance.next.generation.checked_sub(1).ok_or(EventError::Storage)?;
    if !super::stage_installed(db)?{return Err(EventError::Storage);}
    let exists:bool=db.query_row(
        "SELECT EXISTS(SELECT 1 FROM native_wecom_rendered_metadata_v2
        WHERE account_id COLLATE BINARY=?1 AND generation=?2 AND conversation_id='')",
        params![advance.account,generation],|r|r.get(0))?;
    if !exists{return Err(EventError::Storage);}
    db.execute("DELETE FROM native_wecom_rendered_metadata_v2 WHERE account_id COLLATE BINARY=?1 AND generation=?2",
        params![advance.account,generation])?;
    Ok(())
}

fn coherent(advance:&super::Advance,saved:&Head,source:&str,previous:&super::Checkpoint)->bool {
    saved.source==advance.source&&source==advance.source&&saved.generation==previous.generation
        && previous.sequence as usize==saved.committed&&previous.complete==(saved.committed==saved.pages)
}

pub(super) fn verify_replay(db:&Connection,advance:&super::Advance,saved:&super::Checkpoint)
    ->std::result::Result<(),EventError>{
    match (&advance.frozen,head(db,&advance.account)?) {
        (None,None)=>Ok(()),
        (Some(step),Some(marker)) if coherent(advance,&marker,&advance.source,saved)
            && (saved.generation>advance.next.generation || (saved.generation==advance.next.generation
                &&marker.hash==step.hash&&marker.pages==step.pages&&marker.committed>step.index))=>Ok(()),
        _=>Err(EventError::Conflict),
    }
}

pub(super) fn record(db:&Connection,advance:&super::Advance)->std::result::Result<(),EventError>{
    let Some(step)=&advance.frozen else{return Ok(());};
    #[cfg(target_os="linux")]
    outcomes::record(db,advance,step)?;
    if !table_installed(db,TABLE)? {db.execute_batch(SCHEMA)?;}
    db.execute("INSERT INTO native_wecom_frozen_snapshot_v1(account_id,source_id,generation,file_hash,pages,committed)
        VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
        generation=excluded.generation,file_hash=excluded.file_hash,pages=excluded.pages,committed=excluded.committed",
        params![advance.account,advance.source,advance.next.generation,step.hash,step.pages,step.index+1])?;
    Ok(())
}

struct Page<'a> { conversations:Vec<&'a RawValue>,messages:Vec<&'a RawValue>,bytes:usize }
impl<'a> Page<'a> {
    fn new()->Self{Self{conversations:Vec::new(),messages:Vec::new(),bytes:2}}
    fn len(&self)->usize{self.conversations.len()+self.messages.len()}
    fn push(&mut self,raw:&'a RawValue,message:bool){
        self.bytes+=raw.get().len()+(self.len()!=0) as usize;
        if message{self.messages.push(raw);}else{self.conversations.push(raw);}
    }
}

struct Inventory<'a> {hash:String,pages:Vec<Page<'a>>,conversations:usize,messages:usize}
// The inventory pass checks the original row's complete JSON shape, but only
// page projection needs to keep the decoded body. Mirror serde's owned String
// visitor so malformed text still fails before that pass looks at row scope.
struct ValidatedDiscardedText;
impl<'de> Deserialize<'de> for ValidatedDiscardedText {
    fn deserialize<D:Deserializer<'de>>(input:D)->std::result::Result<Self,D::Error>{
        struct StringShape;
        impl<'de> Visitor<'de> for StringShape {
            type Value=ValidatedDiscardedText;

            fn expecting(&self,fmt:&mut fmt::Formatter<'_>)->fmt::Result{
                fmt.write_str("a string")
            }

            fn visit_str<E:de::Error>(self,_:&str)->std::result::Result<Self::Value,E>{
                Ok(ValidatedDiscardedText)
            }

            fn visit_string<E:de::Error>(self,_:String)->std::result::Result<Self::Value,E>{
                Ok(ValidatedDiscardedText)
            }

            fn visit_bytes<E:de::Error>(self,value:&[u8])->std::result::Result<Self::Value,E>{
                match std::str::from_utf8(value) {
                    Ok(_)=>Ok(ValidatedDiscardedText),
                    Err(_)=>Err(E::invalid_value(de::Unexpected::Bytes(value),&self)),
                }
            }

            fn visit_byte_buf<E:de::Error>(self,value:Vec<u8>)->std::result::Result<Self::Value,E>{
                self.visit_bytes(&value)
            }
        }
        input.deserialize_string(StringShape)
    }
}

impl<'a> Inventory<'a> {
    fn parse(bytes:&'a [u8],account:&str)->Result<Self>{
        let pair:Pair<'a>=serde_json::from_slice(bytes).map_err(|_|Error::Format)?;
        if pair.format!=FORMAT||pair.account_id!=account {return Err(Error::Scope);}
        // The closed one-variant enum makes independent capture consent explicit.
        let Attestation::AuthorizedSingleRenderedRefresh=pair.account_binding;
        let conversations=pair.conversations.0.len();
        let messages=pair.messages.0.len();
        if conversations+messages>MAX_ITEMS{return Err(Error::Format);}
        let mut roster=BTreeSet::new();
        let mut ids=BTreeSet::new();
        let mut pages=vec![Page::new()];
        for (raw,message) in pair.conversations.0.into_iter().map(|raw|(raw,false))
            .chain(pair.messages.0.into_iter().map(|raw|(raw,true))) {
            if message {
                let row:Row<ValidatedDiscardedText>=serde_json::from_str(raw.get()).map_err(|_|Error::Format)?;
                if row.platform.as_deref().is_some_and(|p|p!="wecom")
                    ||row.account_id.as_deref().is_some_and(|a|a!=account){return Err(Error::Scope);}
                if !ids.insert(row.message_id.string(false)?)
                    || !roster.contains(&row.conversation_id.string(false)?) {return Err(Error::Format);}
            } else {
                let row:Conversation=serde_json::from_str(raw.get()).map_err(|_|Error::Format)?;
                if !label(&row.name)||row.participants>100_000
                    ||!roster.insert(row.conversation_id.string(false)?) {return Err(Error::Format);}
            }
            let current=pages.last().ok_or(Error::Format)?;
            let size=raw.get().len()+(current.len()!=0) as usize;
            if current.len()==MAX_ROWS||current.bytes+size>MAX_BATCH_BYTES{
                if pages.len()==MAX_PAGES {return Err(Error::Format);}
                pages.push(Page::new());
            }
            pages.last_mut().ok_or(Error::Format)?.push(raw,message);
        }
        Ok(Self{hash:hex::encode(Sha256::digest(bytes)),pages,conversations,messages})
    }

    fn project(&self,index:usize,store:&EventStore,account:&str,lineage:&str,position:&Position,replace:Option<&str>)->Result<Plan>{
        let page=self.pages.get(index).ok_or(Error::Format)?;
        fn rows<T:serde::de::DeserializeOwned>(raw:&[&RawValue])->Result<Vec<T>>{
            raw.iter().map(|r|serde_json::from_str(r.get()).map_err(|_|Error::Format)).collect()
        }
        let sequence=u32::try_from(index+1).map_err(|_|Error::Format)?;
        let complete=index+1==self.pages.len();
        let proof=if let Some(old)=replace {
            fingerprint(&("wecom-frozen-replace-incomplete-v1",FORMAT,old,&self.hash,index,self.pages.len(),position))?
        } else {fingerprint(&(FORMAT,&self.hash,index,self.pages.len(),position))?};
        let input=Envelope{format:FORMAT_WITH_NOTICES.to_owned(),account_id:account.to_owned(),
            source_id:lineage.to_owned(),generation:position.generation,sequence,
            previous_checkpoint:position.previous_checkpoint.clone(),complete,
            conversations:rows(&page.conversations)?,messages:rows(&page.messages)?};
        let mut plan=plan_input(input,proof,false,store,account,lineage)?;
        plan.advance.frozen=Some(Step{hash:self.hash.clone(),index,pages:self.pages.len(),replace:replace.map(str::to_owned)});
        Ok(plan)
    }
}

#[cfg(target_os="linux")]
enum Progress {
    First(Position),
    NextGeneration(Position),
    Resume{position:Position,index:usize},
    Applied{generation:u32},
    Unavailable,
}

#[cfg(target_os="linux")]
fn progress(db:&Connection,account:&str,lineage:&str,inventory:&Inventory<'_>)->Result<Progress>{
    if jsonl_lineage(db,account).map_err(map_apply)?.is_some(){return Ok(Progress::Unavailable);}
    match (head(db,account).map_err(map_apply)?,load(db,account).map_err(map_apply)?) {
        (None,None)=>Ok(Progress::First(Position{generation:1,previous_checkpoint:"initial".into()})),
        (Some(mark),Some((source,prior))) if source==lineage&&mark.source==lineage
            && mark.generation==prior.generation&&mark.committed==prior.sequence as usize
            && prior.complete==(mark.committed==mark.pages)=>{
            if mark.hash==inventory.hash {
                if mark.pages!=inventory.pages.len(){return Ok(Progress::Unavailable);}
                if prior.complete {return Ok(Progress::Applied{generation:prior.generation});}
                return Ok(Progress::Resume{
                    position:Position{generation:prior.generation,previous_checkpoint:prior.checkpoint},
                    index:mark.committed,
                });
            }
            if prior.complete {
                if let Some(generation)=prior.generation.checked_add(1){
                    return Ok(Progress::NextGeneration(Position{generation,previous_checkpoint:prior.checkpoint}));
                }
            }
            Ok(Progress::Unavailable)
        }
        _=>Ok(Progress::Unavailable),
    }
}

#[cfg(target_os="linux")]
fn next_position(store:&EventStore,account:&str,lineage:&str,claim:&LocalDetachedClaim,
    inventory:&Inventory<'_>,index:usize,replace:Option<&str>)->Result<Position>{
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    claim.verify_in(&tx).map_err(map_apply)?;
    if let Some(old)=replace {
        if index!=0 || old==inventory.hash.as_str() || jsonl_lineage(&tx,account).map_err(map_apply)?.is_some(){
            return Err(Error::Cursor);
        }
        let marker=head(&tx,account).map_err(map_apply)?.ok_or(Error::Cursor)?;
        let (source,prior)=load(&tx,account).map_err(map_apply)?.ok_or(Error::Cursor)?;
        if prior.complete || source!=lineage || marker.source!=lineage || marker.hash!=old
            || marker.generation!=prior.generation || marker.committed!=prior.sequence as usize
            || marker.committed>=marker.pages {
            return Err(Error::Cursor);
        }
        let generation=prior.generation.checked_add(1).ok_or(Error::Cursor)?;
        return Ok(Position{generation,previous_checkpoint:prior.checkpoint});
    }
    match progress(&tx,account,lineage,inventory)? {
        Progress::First(position)|Progress::NextGeneration(position) if index==0=>Ok(position),
        Progress::Resume{position,index:next} if index==next=>Ok(position),
        _=>Err(Error::Cursor),
    }
}

#[cfg(target_os="linux")]
pub(super) fn plan(source:&Path,store:&EventStore,account:&str,lineage:&str,claim:&LocalDetachedClaim,
    command:ClaimCommand,mut selection:Selection)->Result<(Plan,Selection)>{
    scope(account,lineage)?;
    if !hex(&selection.file_sha256,64){return Err(Error::Confirmation);}
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?);
    let inventory=Inventory::parse(bytes.as_slice(),account)?;
    if inventory.hash!=selection.file_sha256{return Err(Error::Confirmation);}
    let replace=selection.replace_incomplete_sha256.as_deref();
    if replace.is_some_and(|old|!hex(old,64)||old==inventory.hash.as_str()||selection.index!=0) {
        return Err(Error::Confirmation);
    }
    selection.position=Some(match (command,selection.position.take()){
        (ClaimCommand::Preview,None)=>next_position(store,account,lineage,claim,&inventory,selection.index,replace)?,
        (ClaimCommand::Import|ClaimCommand::Status,Some(position)) if position.generation>0
            && (position.previous_checkpoint=="initial"||hex(&position.previous_checkpoint,64))=>position,
        _=>return Err(Error::Cursor),
    });
    let position=selection.position.as_ref().ok_or(Error::Cursor)?;
    if selection.index>=inventory.pages.len(){return Err(Error::Format);}
    let mut selected=None;
    // Revalidate every page of the exact file before accepting any page.
    // Only the selected normalized plan is retained across iterations.
    for index in 0..inventory.pages.len() {
        let replacement=if index==selection.index {replace}else{None};
        let projected=inventory.project(index,store,account,lineage,position,replacement)?;
        if index==selection.index{selected=Some(projected);}
    }
    Ok((selected.ok_or(Error::Format)?,selection))
}

#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InventoryInput {token:super::OperatorSecret,lineage:String}
#[cfg(target_os="linux")]
pub(super) fn inventory_cli(source:&Path,directory:&Path,grants:&Path)->std::result::Result<(),Box<dyn std::error::Error>>{
    use crate::export_schedule_cli::{private_grants,private_pipes,read_frame,reply};
    private_pipes()?;
    let input:InventoryInput=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(input.token.0.as_str()).filter(|g|g.kind==PluginKind::ScopedProducer
        &&g.permissions.contains(&Permission::Ingest)&&g.permissions.contains(&Permission::SourceJobsManage))
        .ok_or(Error::Scope)?;
    let account=&grant.account_scope.as_ref().filter(|s|s.platform=="wecom").ok_or(Error::Scope)?.account_id;
    scope(account,&input.lineage)?;
    let store=super::target(directory)?;
    store.require_ordinary()?;
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?);
    let inventory=Inventory::parse(bytes.as_slice(),account)?;
    let position=Position{generation:1,previous_checkpoint:"initial".into()};
    let mut report=Vec::with_capacity(inventory.pages.len());
    for (index,page) in inventory.pages.iter().enumerate(){
        let plan=inventory.project(index,&store,account,&input.lineage,&position,None)?;
        let normalized=serde_json::to_vec(&plan.request.messages).map_err(|_|Error::Format)?;
        report.push((index,page.bytes,page.conversations.len(),page.messages.len(),
            plan.request.messages.len(),plan.skipped,normalized.len(),
            hex::encode(Sha256::digest(&normalized))));
    }
    reply(serde_json::json!({"format":FORMAT,"file_sha256":inventory.hash,"conversations":inventory.conversations,
        "messages":inventory.messages,"page_fields":["index","raw_token_bytes","conversations","messages",
            "text_rows","skipped_non_text","normalized_bytes","normalized_sha256"],"pages":report}),None)?;
    Ok(())
}


#[cfg(target_os="linux")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InspectInput {token:super::OperatorSecret,lineage:String,file_sha256:String}

#[cfg(target_os="linux")]
pub(super) fn inspect_cli(source:&Path,directory:&Path,grants:&Path)->std::result::Result<(),Box<dyn std::error::Error>>{
    use crate::export_schedule_cli::{private_grants,private_pipes,read_frame,reply};
    private_pipes()?;
    let InspectInput{token,lineage,file_sha256}=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(token.0.as_str()).filter(|g|g.kind==PluginKind::ScopedProducer
        &&g.permissions.contains(&Permission::Ingest)&&g.permissions.contains(&Permission::SourceJobsManage))
        .ok_or(Error::Scope)?;
    drop(token);
    let account=&grant.account_scope.as_ref().filter(|s|s.platform=="wecom").ok_or(Error::Scope)?.account_id;
    scope(account,&lineage)?;
    if !hex(&file_sha256,64){return Err(Error::Confirmation.into());}
    let store=super::target(directory)?;
    store.require_ordinary()?;
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?);
    let inventory=Inventory::parse(bytes.as_slice(),account)?;
    if inventory.hash!=file_sha256{return Err(Error::Confirmation.into());}
    let validation=Position{generation:1,previous_checkpoint:"initial".into()};
    for index in 0..inventory.pages.len(){inventory.project(index,&store,account,&lineage,&validation,None)?;}
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    let state=progress(&tx,account,&lineage,&inventory).unwrap_or(Progress::Unavailable);
    drop(tx);
    store.verify().map_err(|_|Error::Archive)?;
    use serde_json::json;
    let result=match state {
        Progress::First(_)=>json!({"state":"ready_first_generation","next_index":0}),
        Progress::NextGeneration(_)=>json!({"state":"ready_new_generation","next_index":0}),
        Progress::Resume{index,..}=>json!({"state":"resume_matching_snapshot","next_index":index}),
        Progress::Applied{generation}=>json!({"state":"local_projection_already_applied","generation":generation}),
        Progress::Unavailable=>json!({"state":"continuation_unavailable"}),
    };
    reply(result,None)?;
    Ok(())
}

#[cfg(target_os="linux")]
pub(super) fn target_fit_cli(source:&Path,directory:&Path,grants:&Path)
    ->std::result::Result<(),Box<dyn std::error::Error>>{
    use crate::{events::{MessageFit,frozen_wecom_message_fit},
        export_schedule_cli::{private_grants,private_pipes,read_frame,reply}};
    private_pipes()?;
    let InspectInput{token,lineage,file_sha256}=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(token.0.as_str()).filter(|g|g.kind==PluginKind::ScopedProducer
        &&g.permissions.contains(&Permission::Ingest)&&g.permissions.contains(&Permission::SourceJobsManage))
        .ok_or(Error::Scope)?;
    drop(token);
    let account=&grant.account_scope.as_ref().filter(|s|s.platform=="wecom").ok_or(Error::Scope)?.account_id;
    scope(account,&lineage)?;
    if !hex(&file_sha256,64){return Err(Error::Confirmation.into());}
    let store=super::target(directory)?;
    store.require_ordinary()?;
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?);
    let inventory=Inventory::parse(bytes.as_slice(),account)?;
    if inventory.hash!=file_sha256{return Err(Error::Confirmation.into());}
    let mut db=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=db.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    let vacant=head(&tx,account).map_err(map_apply)?.is_none()
        && load(&tx,account).map_err(map_apply)?.is_none()
        && jsonl_lineage(&tx,account).map_err(map_apply)?.is_none();
    let eligible=vacant || validated_head_for_roster(&tx,account,Some(lineage.as_str()))?.is_some();
    if !eligible {
        drop(tx);
        store.verify().map_err(|_|Error::Archive)?;
        reply(serde_json::json!({"state":"target_fit_unavailable"}),None)?;
        return Ok(());
    }

    let validation=Position{generation:1,previous_checkpoint:"initial".into()};
    let (mut checked,mut conflicts)=(0usize,0usize);
    let mut pages=Vec::new();
    for index in 0..inventory.pages.len(){
        let plan=inventory.project(index,&store,account,&lineage,&validation,None)?;
        let mut page_conflicts=0usize;
        for row in &plan.request.messages{
            checked=checked.checked_add(1).filter(|n|*n<=MAX_ITEMS).ok_or(Error::Format)?;
            match frozen_wecom_message_fit(&tx,account,row).map_err(map_apply)?{
                MessageFit::New|MessageFit::Identical=>{},
                MessageFit::WouldRevise|MessageFit::Conflict=>page_conflicts+=1,
            }
        }
        if page_conflicts!=0{
            conflicts+=page_conflicts;
            pages.push((index,page_conflicts));
        }
    }
    drop(tx);
    store.verify().map_err(|_|Error::Archive)?;
    reply(serde_json::json!({"state":"target_fit_checked","checked_projected_messages":checked,
        "conflicting_messages":conflicts,"conflict_page_fields":["index","count"],"conflict_pages":pages}),None)?;
    Ok(())
}
