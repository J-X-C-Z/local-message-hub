//! Safe metadata projections for one explicit indexed account; no provider lookup.
use crate::{plugins::AccountScope,store::{Store,Result,StoreError}};
use rusqlite::{Connection,params};
use serde::Deserialize;
use serde_json::{Value,json};
use std::collections::BTreeMap;

const ROW_CAP:i64=200_000;
const DAY_CAP:i64=3_660;

#[derive(Default,Deserialize)]
#[serde(default,deny_unknown_fields)]
pub(crate) struct Selector {
    pub platform:String,
    pub account_id:String,
}

pub(crate) fn accounts(store:Store,scope:AccountScope, contacts_allowed:bool) -> Result<Value> {
    let db=store.connection()?;
    let present:bool=db.query_row(
        "SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=?1 AND account_id=?2)
         OR EXISTS(SELECT 1 FROM messages WHERE platform=?1 AND account_id=?2)
         OR (?3 AND EXISTS(SELECT 1 FROM contacts WHERE platform=?1 AND account_id=?2))",
        params![scope.platform,scope.account_id,contacts_allowed],|r|r.get(0))?;
    Ok(json!({"items":if present {vec![json!({"platform":scope.platform,"account_id":scope.account_id})]} else {vec![]}}))
}

fn bounded_count(db:&Connection,scope:&AccountScope,table:&'static str) -> Result<i64> {
    let sql=format!("SELECT count(*) FROM
         (SELECT 1 FROM {table} WHERE platform=?1 AND account_id=?2 LIMIT ?3)");
    let n:i64=db.query_row(&sql,params![scope.platform,scope.account_id,ROW_CAP+1],|r|r.get(0))?;
    if n>ROW_CAP {Err(StoreError::TooLarge)} else {Ok(n)}
}

pub(crate) fn stats(store:Store,scope:AccountScope) -> Result<Value> {
    let mut db=store.connection()?;
    let tx=db.transaction()?;
    let message_count=bounded_count(&tx,&scope,"messages")?;
    let conversation_count=bounded_count(&tx,&scope,"conversations")?;
    let contact_count=bounded_count(&tx,&scope,"contacts")?;
    let mut types=tx.prepare(
        "SELECT CASE WHEN platform='wechat' AND raw_type IN ('47','11000') THEN 'sticker'
          WHEN type IN ('text','image','video','voice','file','emoji','link','location','system','sticker') THEN type
          ELSE 'unknown' END AS safe_kind, count(*)
          FROM messages WHERE platform=?1 AND account_id=?2 GROUP BY safe_kind")?;
    let mut rows=types.query(params![scope.platform,scope.account_id])?;
    let mut per_type=BTreeMap::new();
    while let Some(row)=rows.next()? {
        per_type.insert(row.get::<_,String>(0)?,row.get::<_,i64>(1)?);
    }
    let media_count=["image","video","voice","file"].iter().filter_map(|k|per_type.get(*k)).sum::<i64>();
    let mut daily=tx.prepare(
        "SELECT date(timestamp,'unixepoch','+8 hours') AS day,count(*)
         FROM messages WHERE platform=?1 AND account_id=?2 AND day IS NOT NULL
         GROUP BY day ORDER BY day ASC LIMIT ?3")?;
    let mut rows=daily.query(params![scope.platform,scope.account_id,DAY_CAP+1])?;
    let mut days=Vec::new();
    while let Some(row)=rows.next()? {
        if days.len()==DAY_CAP as usize {return Err(StoreError::TooLarge);}
        days.push(json!({"date":row.get::<_,String>(0)?,"count":row.get::<_,i64>(1)?}));
    }
    let mut per_platform=serde_json::Map::new();
    if message_count+conversation_count+contact_count>0 {
        per_platform.insert(scope.platform,json!({"messages":message_count,
            "conversations":conversation_count,"contacts":contact_count}));
    }
    Ok(json!({"message_count":message_count,"conversation_count":conversation_count,
        "contact_count":contact_count,"media_count":media_count,"per_type":per_type,
        "per_platform":per_platform,"daily":days,"scope":"indexed_account",
        "media_count_basis":"normalized_message_kind_only"}))
}
