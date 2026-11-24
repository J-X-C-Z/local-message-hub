//! Preserve observed original positions only; the source SDK receipt is not a
//! provider cursor. No provider access or persistent cursor ownership here.
use super::{Committed,Error,LegacyRow,Pending,Session,normalize_legacy_rows};
use crate::{events::MAX_BATCH_BYTES,wechat_detached::source_id};
use serde::{Deserialize,Serialize};
use std::collections::BTreeSet;

const FORMAT:&str="wechat-observed-ordered-text-v1";

/// Exact v1 source position copied from an authorized source page. No export
/// timestamps, ordering by display ID, connector marks or guessed defaults.
#[derive(Clone,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Position {
    pub version:u8,
    pub session_id:String,
    pub sort_seq:i64,
    pub shard:String,
    pub local_id:u64,
}
impl Position {
    fn valid(&self,session:&str)->bool {
        self.version==1 && self.session_id==session && self.sort_seq>=0
            && source_id(&self.shard) && self.local_id!=0
    }
}

/// Adapter-owned durable record. Persist only after a valid acknowledgement,
/// or explicit reconciliation of the exact submitted page with Hub sequence.
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Checkpoint {
    pub hub_sequence:u64,
    pub account_id:String,
    pub source_namespace:String,
    pub position:Position,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservedPage {
    format:String,
    account_id:String,
    session_id:String,
    #[serde(deserialize_with="explicit_after")]
    after:Option<Position>,
    next_cursor:Position,
    rows:Vec<ObservedRow>,
}
// Explicit reset (null) must be present rather than an omitted predecessor.
fn explicit_after<'de,D:serde::Deserializer<'de>>(deserializer:D)->Result<Option<Position>,D::Error>{
    Option::<Position>::deserialize(deserializer)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservedRow { cursor:Position, message:LegacyRow }

pub struct OrderedPending { batch:Pending,next:Checkpoint }
impl OrderedPending {
    pub fn frame(&self)->&[u8] {self.batch.frame()}
    /// Retain the opaque pending-page mapping once any bytes may have been
    /// written. A missing acknowledgement never grants permission to retry.
    pub fn acknowledge(self,reply:&[u8])->Result<(Committed,Checkpoint),Error> {
        let committed=self.batch.acknowledge(reply)?;
        Ok((committed,self.next))
    }
}

/// Prepare one single-session ordered page from a trusted adapter. The adapter
/// must already have obtained the tuples and order legally; the Hub does not
/// call the original provider, reorder rows, or infer omitted positions.
pub fn prepare(session:Session<'_>,sdk_predecessor:u64,session_id:&str,
    saved:Option<&Checkpoint>,page:&[u8])->Result<OrderedPending,Error> {
    let sequence=session.successor(sdk_predecessor)?;
    if !source_id(session_id)||page.is_empty()||page.len()>MAX_BATCH_BYTES {
        return Err(Error::LegacyExport);
    }
    let decoded:ObservedPage=serde_json::from_slice(page).map_err(|_|Error::LegacyExport)?;
    if decoded.format!=FORMAT || decoded.session_id!=session_id || !(1..=100).contains(&decoded.rows.len()) {
        return Err(Error::LegacyExport);
    }
    if decoded.account_id!=session.account{return Err(Error::Scope);}
    if let Some(saved)=saved {
        if saved.account_id!=session.account || saved.source_namespace!=session.namespace {
            return Err(Error::Scope);
        }
        // Other conversations may have advanced the account-wide Hub sequence.
        if !(1..=sdk_predecessor).contains(&saved.hub_sequence)
            || !saved.position.valid(session_id) {return Err(Error::Sequence);}
    }
    let saved_position=saved.map(|s|&s.position);
    if decoded.after.as_ref()!=saved_position {return Err(Error::Sequence);}
    if !decoded.next_cursor.valid(session_id) {return Err(Error::LegacyExport);}
    let mut previous_sort=saved_position.map(|c|c.sort_seq);
    let mut seen=BTreeSet::new();
    if let Some(saved)=saved_position { seen.insert((saved.shard.clone(),saved.local_id)); }
    let mut messages=Vec::with_capacity(decoded.rows.len());
    let mut last=None;
    for row in decoded.rows {
        let cursor=row.cursor;
        if !cursor.valid(session_id) || row.message.conversation_id!=session_id
            || row.message.id!=format!("{session_id}:{}:{}",cursor.shard,cursor.local_id)
            || previous_sort.is_some_and(|old|cursor.sort_seq<old)
            || !seen.insert((cursor.shard.clone(),cursor.local_id)) {
            return Err(Error::LegacyExport);
        }
        previous_sort=Some(cursor.sort_seq);
        last=Some(cursor);
        messages.push(row.message);
    }
    if last.as_ref()!=Some(&decoded.next_cursor) {return Err(Error::LegacyExport);}
    let normalized=normalize_legacy_rows(messages,session.account)?;
    let next=Checkpoint {hub_sequence:sequence,account_id:session.account.to_owned(),
        source_namespace:session.namespace.to_owned(),position:decoded.next_cursor};
    let batch=session.pack(sequence,normalized,Error::LegacyExport)?;
    Ok(OrderedPending {batch,next})
}
