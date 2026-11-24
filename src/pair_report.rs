//! Private-conversation activity; no message bodies, sender identities, or titles.
use crate::{annual_report::{Detail,MAX_ROWS,annual_for_pair,period_bounds},plugins::AccountScope,store::{Store,StoreError}};
use rusqlite::{Connection,OptionalExtension};
use serde::Deserialize;
use serde_json::{Value,json};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub platform:String,
    pub account_id:String,
    pub conversation_id:String,
    pub year:i32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LegacyQuery {
    pub platform:String,
    pub account_id:String,
    pub conversation_id:String,
    pub year:Option<i32>,
}
impl From<Query> for LegacyQuery {
    fn from(q:Query)->Self {
        Self {platform:q.platform,account_id:q.account_id,conversation_id:q.conversation_id,
            year:Some(q.year)}
    }
}

fn bad() -> StoreError {StoreError::BadQuery("invalid private conversation activity request")}

pub(crate) fn report(store:Store,scope:AccountScope,q:LegacyQuery,detail:Detail) -> Result<Option<Value>,StoreError> {
    if q.year.is_some_and(|year| !(1970..=9999).contains(&year))
        || q.conversation_id.is_empty() || q.conversation_id.len()>1024
        || q.conversation_id.chars().any(char::is_control)
    {return Err(bad());}
    let mut db=store.connection()?;
    let tx=db.transaction()?;
    pair(&tx,&scope,q.year,&q.conversation_id,MAX_ROWS,detail)
}

fn pair(db:&Connection,scope:&AccountScope,year:impl Into<Option<i32>>,conversation:&str,cap:i64,detail:Detail) -> Result<Option<Value>,StoreError> {
    let year=year.into();
    let (lower,upper)=period_bounds(year)?;
    let kind:Option<String>=db.query_row(
        "SELECT LOWER(COALESCE(kind,'private')) FROM conversations
         WHERE platform COLLATE BINARY=? AND account_id COLLATE BINARY=? AND id COLLATE BINARY=?",
         (&scope.platform,&scope.account_id,conversation),|r|r.get(0)).optional()?;
    let Some(kind)=kind else {return Ok(None);};
    if !matches!(kind.as_str(),"private"|"direct"|"friend"|"single"|"personal"|"dm") {
        return Err(StoreError::BadQuery("pair reports require a private conversation"));
    }
    let mut summary=annual_for_pair(db,scope,year,conversation,cap,detail)?;
    let fields=summary.as_object_mut().ok_or_else(bad)?;
    fields.retain(|key,_|matches!(key.as_str(),"platform"|"account_id"|"conversation_id"|"year"
        |"total_messages"|"sent_messages"|"received_messages"|"sent_ratio"|"active_days"
        |"first_timestamp"|"last_timestamp"|"hourly"|"message_types"));
    let date_condition=if year.is_some() {"AND timestamp>=?4 AND timestamp<?5"} else {""};
    let mut stmt=db.prepare(&format!(
        "SELECT timestamp,COALESCE(is_self,0) FROM messages
         WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2 AND conversation_id COLLATE BINARY=?3
         {date_condition} ORDER BY timestamp,rowid LIMIT ?6"))?;
    let mut rows=stmt.query((&scope.platform,&scope.account_id,conversation,lower,upper,cap+1))?;
    let (mut n,mut count,mut sum)=(0_i64,0_i64,0_i64);
    let mut fastest:Option<i64>=None;
    let mut previous:Option<(i64,i64)>=None;
    while let Some(row)=rows.next()? {
        n+=1;
        if n>cap {return Err(StoreError::TooLarge);}
        let ts:i64=row.get(0)?;
        let role:i64=row.get(1)?;
        if role==1
            && let Some((before,0))=previous
            && let Some(seconds)=ts.checked_sub(before).filter(|s|(0..=3600).contains(s))
        {
            count+=1;
            sum+=seconds;
            fastest=Some(fastest.map_or(seconds,|old|old.min(seconds)));
        }
        previous=Some((ts,role));
    }
    fields.insert("response_basis".into(),json!("adjacent_incoming_to_self"));
    fields.insert("response_count".into(),json!(count));
    fields.insert("response_avg_seconds".into(),json!((count>0).then(||
        (sum as f64 / count as f64).round_ties_even() as i64)));
    fields.insert("response_fastest_seconds".into(),json!(fastest));
    Ok(Some(summary))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::annual_report::boundary;
    #[test]
    fn scopes_before_role_transitions_and_refuses_partial_over_budget() {
        let db=Connection::open_in_memory().unwrap();
        db.execute_batch("
          CREATE TABLE conversations(platform TEXT,account_id TEXT COLLATE NOCASE,id TEXT,kind TEXT);
          CREATE TABLE messages(platform TEXT,account_id TEXT COLLATE NOCASE,conversation_id TEXT,timestamp INTEGER,is_self INTEGER);
          INSERT INTO conversations VALUES('qq','account','room','direct'),('qq','other','room','direct');
        ").unwrap();
        let t=boundary(2026).unwrap();
        for (account,ts,own) in [("account",t,0),("ACCOUNT",t+1,1),("account",t+3,1),("account",t+4,1),
            ("account",t+10,0),("account",t+10,1),("account",t+11,0),("account",t+3700,1)] {
            db.execute("INSERT INTO messages VALUES ('qq',?,'room',?,?)",(account,ts,own)).unwrap();
        }
        let scope=AccountScope{platform:"qq".into(),account_id:"account".into()};
        let report=pair(&db,&scope,2026,"room",7,Detail::Activity).unwrap().unwrap();
        assert_eq!(report["total_messages"],7);
        assert_eq!(report["response_count"],2);
        assert_eq!(report["response_fastest_seconds"],0);
        assert_eq!(report["response_avg_seconds"],2);
        assert!(matches!(pair(&db,&scope,2026,"room",6,Detail::Activity),Err(StoreError::TooLarge)));
        assert!(pair(&db,&scope,2026,"unknown",7,Detail::Activity).unwrap().is_none());
    }
}
