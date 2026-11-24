//! Last finalized source titles independently of the canonical archive titles.
use super::{Advance,Error,EventError,MAX_STAGE_CONVERSATIONS,MAX_TOTAL_STAGE_ROWS,id,
    label,map_apply,scope,table_installed};
use crate::plugins::{Permission,PluginKind};
use rusqlite::{Connection,OptionalExtension,params};
use serde::{Deserialize,Serialize};
use zeroize::Zeroize;

const HEAD:&str="native_wecom_frozen_roster_head_v1";
const ROWS:&str="native_wecom_frozen_roster_rows_v1";
const SCHEMA:&str="
CREATE TABLE native_wecom_frozen_roster_head_v1(
 account_id TEXT PRIMARY KEY NOT NULL,source_id TEXT NOT NULL CHECK(length(source_id)=32),
 generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 4294967295),
 conversations INTEGER NOT NULL CHECK(conversations BETWEEN 0 AND 10000)
) WITHOUT ROWID;
CREATE TABLE native_wecom_frozen_roster_rows_v1(
 account_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
 title TEXT NOT NULL,participants INTEGER NOT NULL CHECK(participants BETWEEN 0 AND 100000),
 PRIMARY KEY(account_id,conversation_id)
) WITHOUT ROWID;";

fn ready(db:&Connection)->Result<bool,EventError>{
    match (table_installed(db,HEAD)?,table_installed(db,ROWS)?) {
        (true,true)=>Ok(true),(false,false)=>Ok(false),_=>Err(EventError::Storage),
    }
}

pub(super) fn publish_final(db:&Connection,advance:&Advance)->Result<(),EventError>{
    if !advance.next.complete || advance.frozen.is_none(){return Ok(());}
    if !ready(db)?{db.execute_batch(SCHEMA)?;}
    let existing:Option<(String,u32)>=db.query_row(
        "SELECT source_id,generation FROM native_wecom_frozen_roster_head_v1 WHERE account_id COLLATE BINARY=?",
        [&advance.account],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    if existing.is_some_and(|(source,generation)|source!=advance.source||generation>=advance.next.generation){
        return Err(EventError::Conflict);
    }
    let count:i64=db.query_row(
        "SELECT COUNT(*) FROM native_wecom_rendered_metadata_v2
         WHERE account_id COLLATE BINARY=? AND generation=? AND conversation_id<>''",
        params![advance.account,advance.next.generation],|r|r.get(0))?;
    if !(0..=MAX_STAGE_CONVERSATIONS).contains(&count){return Err(EventError::Storage);}
    let foreign:i64=db.query_row(
        "SELECT COUNT(*) FROM (SELECT 1 FROM native_wecom_frozen_roster_rows_v1
        WHERE account_id COLLATE BINARY<>? LIMIT ?)",
        params![advance.account,MAX_TOTAL_STAGE_ROWS+1],|r|r.get(0))?;
    if foreign+count>MAX_TOTAL_STAGE_ROWS{return Err(EventError::Storage);}
    db.execute("DELETE FROM native_wecom_frozen_roster_rows_v1 WHERE account_id COLLATE BINARY=?",[&advance.account])?;
    let inserted=db.execute(
        "INSERT INTO native_wecom_frozen_roster_rows_v1(account_id,conversation_id,title,participants)
         SELECT account_id,conversation_id,title,participants FROM native_wecom_rendered_metadata_v2
         WHERE account_id COLLATE BINARY=? AND generation=? AND conversation_id<>''",
        params![advance.account,advance.next.generation])?;
    if inserted as i64!=count{return Err(EventError::Storage);}
    db.execute(
        "INSERT INTO native_wecom_frozen_roster_head_v1(account_id,source_id,generation,conversations)
         VALUES(?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
         generation=excluded.generation,conversations=excluded.conversations",
        params![advance.account,advance.source,advance.next.generation,count])?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    token:super::OperatorSecret,lineage:String,limit:Option<usize>,generation:Option<u32>,after:Option<String>,
}

#[derive(Serialize)]
struct Conversation {
    conversation_id:String,
    source_title:String,
    kind:&'static str,
    participants:u32,
}
#[derive(Serialize)]
struct Page {
    state:&'static str,
    generation:u32,
    pending_generation:bool,
    total:usize,
    items:Vec<Conversation>,
    next_after:Option<String>,
}

fn finalized_head(db:&Connection,account:&str,expected_lineage:Option<&str>)
    ->super::Result<Option<(u32,usize,bool)>>
{
    let Some((source,active))=
        super::frozen_snapshot::validated_head_for_roster(db,account,expected_lineage)?
    else{return Ok(None);};
    if !ready(db).map_err(map_apply)?{return Ok(None);}
    let mut stmt=db.prepare(
        "SELECT source_id,generation,conversations FROM native_wecom_frozen_roster_head_v1
         WHERE account_id COLLATE BINARY=? LIMIT 2").map_err(|_|Error::Archive)?;
    let mut rows=stmt.query([account]).map_err(|_|Error::Archive)?;
    let Some(first)=rows.next().map_err(|_|Error::Archive)? else{return Ok(None);};
    let lineage:String=first.get(0).map_err(|_|Error::Archive)?;
    let generation:u32=first.get(1).map_err(|_|Error::Archive)?;
    let total:usize=first.get(2).map_err(|_|Error::Archive)?;
    if rows.next().map_err(|_|Error::Archive)?.is_some(){return Err(Error::Archive);}
    if lineage!=source || generation==0 || total>MAX_STAGE_CONVERSATIONS as usize
        || (if active.complete {generation!=active.generation} else {generation>=active.generation})
    {return Ok(None);}
    Ok(Some((generation,total,!active.complete)))
}

fn read(db:&Connection,account:&str,input:&Input,limit:usize)->super::Result<Option<Page>>{
    let Some((generation,total,pending_generation))=finalized_head(db,account,Some(&input.lineage))?
    else{return Ok(None);};
    if input.generation.is_some_and(|wanted|wanted!=generation){return Ok(None);}
    let count:usize=db.query_row("SELECT COUNT(*) FROM (SELECT 1 FROM native_wecom_frozen_roster_rows_v1
        WHERE account_id COLLATE BINARY=? LIMIT ?)",params![account,MAX_STAGE_CONVERSATIONS+1],|r|r.get(0))
        .map_err(|_|Error::Archive)?;
    if count!=total{return Err(Error::Archive);}
    if let Some(anchor)=input.after.as_deref(){
        let present:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM native_wecom_frozen_roster_rows_v1
            WHERE account_id COLLATE BINARY=? AND conversation_id COLLATE BINARY=?)",
            params![account,anchor],|r|r.get(0)).map_err(|_|Error::Archive)?;
        if !present{return Ok(None);}
    }

    let mut stmt=db.prepare("SELECT conversation_id,title,participants FROM native_wecom_frozen_roster_rows_v1
      WHERE account_id COLLATE BINARY=? AND conversation_id COLLATE BINARY>?
      ORDER BY conversation_id COLLATE BINARY LIMIT ?").map_err(|_|Error::Archive)?;
    let mut rows=stmt.query(params![account,input.after.as_deref().unwrap_or(""),limit+1]).map_err(|_|Error::Archive)?;
    let mut items=Vec::new();
    while let Some(row)=rows.next().map_err(|_|Error::Archive)?{
        let id_value=row.get_ref(0).map_err(|_|Error::Archive)?;
        let title_value=row.get_ref(1).map_err(|_|Error::Archive)?;
        let id_str=id_value.as_str().map_err(|_|Error::Archive)?;
        let title=title_value.as_str().map_err(|_|Error::Archive)?;
        let participants:u32=row.get(2).map_err(|_|Error::Archive)?;
        if !id(id_str)||!label(title)||participants>100_000{return Err(Error::Archive);}
        items.push(Conversation{conversation_id:id_str.to_owned(),source_title:title.to_owned(),
            kind:if participants>2{"group"}else{"direct"},participants});
    }
    let more=items.len()>limit;
    items.truncate(limit);
    if items.len()>total{return Err(Error::Archive);}
    let next_after=if more{items.last().map(|r|r.conversation_id.clone())}else{None};
    Ok(Some(Page{state:"last_completed_frozen_roster",generation,pending_generation,
        total,items,next_after}))
}

#[derive(Serialize)]
pub(crate) struct Display {
    platform:&'static str,
    account_id:String,
    conversation_id:String,
    source_title:String,
    kind:&'static str,
    participants:u32,
}

/// Presence requires a current, exact indexed canonical conversation, but
/// this returns only display metadata from the last finalized roster.
pub(super) fn indexed_display(db:&Connection,account:&str,conversation:&str)
    ->super::Result<Option<Display>>
{
    if !id(conversation) {return Err(Error::Format);}
    let Some((_,total,_))=finalized_head(db,account,None)? else{return Ok(None);};
    let mut stmt=db.prepare(
        "SELECT r.title,r.participants FROM native_wecom_frozen_roster_rows_v1 r
         JOIN conversations c ON c.platform COLLATE BINARY='wecom'
           AND c.account_id COLLATE BINARY=r.account_id AND c.id COLLATE BINARY=r.conversation_id
         WHERE r.account_id COLLATE BINARY=?1 AND r.conversation_id COLLATE BINARY=?2
         LIMIT 2").map_err(|_|Error::Archive)?;
    let mut rows=stmt.query(params![account,conversation]).map_err(|_|Error::Archive)?;
    let Some(first)=rows.next().map_err(|_|Error::Archive)? else{return Ok(None);};
    // Bound copied source text before allocating the DTO.
    let title=first.get_ref(0).map_err(|_|Error::Archive)?.as_str().map_err(|_|Error::Archive)?;
    let participants:u32=first.get(1).map_err(|_|Error::Archive)?;
    if total==0 || !label(title) || participants>100_000 {return Err(Error::Archive);}
    let source_title=title.to_owned();
    if rows.next().map_err(|_|Error::Archive)?.is_some(){return Err(Error::Archive);}
    Ok(Some(Display{
        platform:"wecom",account_id:account.to_owned(),conversation_id:conversation.to_owned(),
        source_title,kind:if participants>2{"group"}else{"direct"},participants,
    }))
}

pub(super) fn cli(directory:&std::path::Path,grants:&std::path::Path)
    ->std::result::Result<(),Box<dyn std::error::Error>>{
    use crate::export_schedule_cli::{private_grants,private_pipes,read_frame,reply};
    private_pipes()?;
    let mut input:Input=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(input.token.0.as_str()).filter(|g|g.kind==PluginKind::ScopedProducer
        &&g.permissions.contains(&Permission::Ingest)&&g.permissions.contains(&Permission::SourceJobsManage))
        .ok_or(Error::Scope)?;
    input.token.0.zeroize();
    let account=&grant.account_scope.as_ref().filter(|s|s.platform=="wecom").ok_or(Error::Scope)?.account_id;
    scope(account,&input.lineage)?;
    let limit=input.limit.unwrap_or(8);
    if !(1..=8).contains(&limit)||input.generation==Some(0)
        || input.after.as_deref().is_some_and(|anchor|!id(anchor)||input.generation.is_none()){
        return Err(Error::Format.into());
    }
    let store=super::target(directory)?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let selected=read(&tx,account,&input,limit)?;
    drop(tx);
    store.verify()?;
    if let Some(page)=selected{reply(page,None)?;}
    else{reply(serde_json::json!({"state":"completed_roster_unavailable"}),None)?;}
    Ok(())
}
