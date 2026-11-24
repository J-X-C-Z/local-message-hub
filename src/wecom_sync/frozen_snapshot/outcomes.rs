//! Durable, exact active-generation accounting; never backfills old imports.
use super::{InspectInput,Inventory,MAX_BYTES,Position,Step,head,hex,map_apply,
    read_stable_source,scope,table_installed,validated_head_for_roster};
use super::super::{Advance,Error,EventError,MAX_ROWS};
use crate::{
    export_schedule_cli::{private_grants,private_pipes,read_frame,reply},
    legacy_import::OFFLINE_PRODUCER,
    plugins::{Permission,PluginKind},
};
use rusqlite::{Connection,OptionalExtension,params};
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::path::Path;
use zeroize::Zeroizing;

const TABLE:&str="native_wecom_frozen_outcome_pages_v1";
const SCHEMA:&str="
CREATE TABLE native_wecom_frozen_outcome_pages_v1(
    account_id TEXT NOT NULL,source_id TEXT NOT NULL CHECK(length(source_id)=32),
    generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 4294967295),
    file_hash TEXT NOT NULL CHECK(length(file_hash)=64),
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 127),
    batch TEXT NOT NULL UNIQUE CHECK(length(batch)=64),
    skipped INTEGER NOT NULL CHECK(skipped BETWEEN 0 AND 100),
    PRIMARY KEY(account_id,ordinal)
) WITHOUT ROWID;";

pub(super) fn record(db:&Connection,advance:&Advance,step:&Step)->Result<(),EventError>{
    let claim=advance.claim.as_ref().ok_or(EventError::Conflict)?;
    if step.index==0{
        if !table_installed(db,TABLE)?{db.execute_batch(SCHEMA)?;}
        // The prior page receipts are immutable; retiring this optional
        // active-only index does not touch them or any archive rows.
        db.execute("DELETE FROM native_wecom_frozen_outcome_pages_v1
            WHERE account_id COLLATE BINARY=?",[&advance.account])?;
    }else{
        if !table_installed(db,TABLE)?{return Ok(());}
        let first:Option<(String,u32,String)>=db.query_row(
            "SELECT source_id,generation,file_hash FROM native_wecom_frozen_outcome_pages_v1
             WHERE account_id COLLATE BINARY=? AND ordinal=0",
            [&advance.account],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
        let Some((source,generation,hash))=first else{
            // A pre-upgrade active generation does not have a page-zero
            // witness. Do not create or infer a historical rollup.
            return Ok(());
        };
        if source!=advance.source||generation!=advance.next.generation||hash!=step.hash{
            return Err(EventError::Conflict);
        }
        let prefix:usize=db.query_row(
            "SELECT COUNT(*) FROM native_wecom_frozen_outcome_pages_v1 WHERE account_id COLLATE BINARY=?",
            [&advance.account],|r|r.get(0))?;
        if prefix!=step.index{return Err(EventError::Storage);}
    }
    if advance.skipped>MAX_ROWS{return Err(EventError::Storage);}
    db.execute(
        "INSERT INTO native_wecom_frozen_outcome_pages_v1(account_id,source_id,generation,file_hash,ordinal,batch,skipped)
         VALUES(?,?,?,?,?,?,?)",params![advance.account,advance.source,advance.next.generation,
            step.hash,step.index,claim.batch,advance.skipped])?;
    Ok(())
}

#[derive(Serialize)]
struct Outcome {
    state:&'static str,generation:u32,pages:usize,projected_messages:usize,
    created:usize,unchanged:usize,skipped_non_text:usize,
}

fn read(db:&Connection,inventory:&Inventory<'_>,store:&super::EventStore,account:&str,lineage:&str,lane:&str)
    ->super::Result<Option<Outcome>>{
    let Some((_,active))=validated_head_for_roster(db,account,Some(lineage))? else{return Ok(None);};
    let Some(marker)=head(db,account).map_err(map_apply)? else{return Ok(None);};
    if !active.complete||marker.hash!=inventory.hash||marker.pages!=inventory.pages.len()
        || !table_installed(db,TABLE).map_err(map_apply)?
        || !table_installed(db,super::super::CLAIM_TABLE).map_err(map_apply)?{
        return Ok(None);
    }
    let mut stmt=db.prepare(
        "SELECT a.ordinal,a.skipped,r.digest,r.created,r.revised,r.unchanged,c.checkpoint
         FROM native_wecom_frozen_outcome_pages_v1 a
         JOIN native_ingest_receipts r ON r.producer=?1 AND r.idempotency_key=a.batch
         JOIN native_wecom_claim_receipts_v1 c ON c.batch=a.batch
           AND c.account_id COLLATE BINARY=a.account_id AND c.lineage COLLATE BINARY=a.source_id
         WHERE a.account_id COLLATE BINARY=?2 AND a.source_id COLLATE BINARY=?3
           AND a.generation=?4 AND a.file_hash COLLATE BINARY=?5 AND c.lane COLLATE BINARY=?6
         ORDER BY a.ordinal LIMIT ?7").map_err(|_|Error::Archive)?;
    let mut rows=stmt.query(params![OFFLINE_PRODUCER,account,lineage,active.generation,
        marker.hash,lane,marker.pages+1]).map_err(|_|Error::Archive)?;
    let mut result=Outcome{state:"finalized_local_generation_outcome",generation:active.generation,
        pages:marker.pages,projected_messages:0,created:0,unchanged:0,skipped_non_text:0};
    let neutral=Position{generation:1,previous_checkpoint:"initial".into()};
    for index in 0..marker.pages{
        let Some(row)=rows.next().map_err(|_|Error::Archive)? else{return Ok(None);};
        let ordinal:usize=row.get(0).map_err(|_|Error::Archive)?;
        let skipped:usize=row.get(1).map_err(|_|Error::Archive)?;
        let digest_value=row.get_ref(2).map_err(|_|Error::Archive)?;
        let digest=digest_value.as_blob().map_err(|_|Error::Archive)?;
        let created:usize=row.get(3).map_err(|_|Error::Archive)?;
        let revised:usize=row.get(4).map_err(|_|Error::Archive)?;
        let unchanged:usize=row.get(5).map_err(|_|Error::Archive)?;
        let checkpoint_value=row.get_ref(6).map_err(|_|Error::Archive)?;
        let checkpoint=checkpoint_value.as_str().map_err(|_|Error::Archive)?;
        let page=inventory.project(index,store,account,lineage,&neutral,None)?;
        let serialized=serde_json::to_vec(&page.request.messages).map_err(|_|Error::Format)?;
        if ordinal!=index || revised!=0 || skipped!=page.skipped || created>MAX_ROWS || unchanged>MAX_ROWS
            || created+unchanged!=page.request.messages.len() || digest!=Sha256::digest(&serialized).as_slice()
            || !hex(checkpoint,64) || (index+1==marker.pages && checkpoint!=active.checkpoint.as_str()){
            return Ok(None);
        }
        result.projected_messages+=created+unchanged;
        result.created+=created;
        result.unchanged+=unchanged;
        result.skipped_non_text+=skipped;
    }
    if rows.next().map_err(|_|Error::Archive)?.is_some()
        || result.projected_messages+result.skipped_non_text!=inventory.messages{
        return Ok(None);
    }
    Ok(Some(result))
}

pub(in crate::wecom_sync) fn cli(source:&Path,directory:&Path,grants:&Path)->Result<(),Box<dyn std::error::Error>>{
    private_pipes()?;
    let InspectInput{token,lineage,file_sha256}=read_frame()?;
    let registry=private_grants(grants)?;
    let grant=registry.authenticate_token(token.0.as_str()).filter(|g|g.kind==PluginKind::ScopedProducer
        &&g.permissions.contains(&Permission::Ingest)&&g.permissions.contains(&Permission::SourceJobsManage))
        .ok_or(Error::Scope)?;
    drop(token);
    let grant_scope=grant.account_scope.as_ref().filter(|s|s.platform=="wecom").ok_or(Error::Scope)?;
    let account=&grant_scope.account_id;
    scope(account,&lineage)?;
    if !hex(&file_sha256,64){return Err(Error::Confirmation.into());}
    let store=super::super::target(directory)?;
    store.require_ordinary()?;
    let lane=crate::source_jobs::lane(&store,&grant.id,grant_scope).map_err(|_|Error::Scope)?;
    let bytes=Zeroizing::new(read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?);
    let inventory=Inventory::parse(bytes.as_slice(),account)?;
    if inventory.hash!=file_sha256{return Err(Error::Confirmation.into());}
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let outcome=read(&tx,&inventory,&store,account,&lineage,&lane)?;
    drop(tx);
    store.verify()?;
    if let Some(outcome)=outcome{reply(outcome,None)?;}
    else{reply(serde_json::json!({"state":"finalized_outcome_unavailable"}),None)?;}
    Ok(())
}
