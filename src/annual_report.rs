//! Bounded account-only annual activity calculations. No message bodies or identities are read.
use crate::{plugins::AccountScope,store::{Store,StoreError}};
use chrono::{Datelike,NaiveDate};
use rusqlite::Connection;
use serde::Deserialize;
use serde_json::{Value,json};
use std::{collections::BTreeMap,fmt::Write};

pub(crate) const MAX_ROWS: i64=200_000;
const UTC8:i64=28_800;

// The pinned web UI's normalized type vocabulary, plus one fallback. Do not
// propagate arbitrary message type strings or inspect raw_type/extra.
const TYPE_LABELS:[&str;10]=["text","image","sticker","video","voice","file","link","location","system","unknown"];
const UNKNOWN_TYPE:usize=TYPE_LABELS.len()-1;

#[derive(Clone,Copy,Eq,PartialEq)]
pub(crate) enum Detail { Activity, LegacyTypes }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub platform:Option<String>,
    pub account_id:Option<String>,
    pub conversation_id:Option<String>,
    pub year:i32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LegacyQuery {
    pub platform:Option<String>,
    pub account_id:Option<String>,
    pub conversation_id:Option<String>,
    pub year:Option<i32>,
}
impl From<Query> for LegacyQuery {
    fn from(q:Query)->Self {
        Self {platform:q.platform,account_id:q.account_id,conversation_id:q.conversation_id,
            year:Some(q.year)}
    }
}

fn bad() -> StoreError {StoreError::BadQuery("invalid annual report request")}

pub(crate) fn report(store:Store,scope:AccountScope,q:LegacyQuery,detail:Detail)->Result<Value,StoreError> {
    if q.year.is_some_and(|year| !(1970..=9999).contains(&year))
        || q.conversation_id.as_ref().is_some_and(|id|
            id.is_empty() || id.len()>1024 || id.chars().any(char::is_control))
    {return Err(bad());}
    let mut db=store.connection()?;
    let tx=db.transaction()?;
    annual(&tx,&scope,q.year,q.conversation_id.as_deref(),MAX_ROWS,detail)
}

pub(crate) fn boundary(year:i32) -> Result<i64,StoreError> {
    NaiveDate::from_ymd_opt(year,1,1).and_then(|day|day.and_hms_opt(0,0,0))
        .map(|midnight|midnight.and_utc().timestamp()-UTC8).ok_or_else(bad)
}

pub(crate) fn period_bounds(year:Option<i32>)->Result<(Option<i64>,Option<i64>),StoreError> {
    match year {
        None=>Ok((None,None)),
        Some(year) if (1970..=9999).contains(&year)=>Ok((Some(boundary(year)?),Some(boundary(year+1)?))),
        Some(_)=>Err(bad()),
    }
}

pub(crate) fn annual(db:&Connection,scope:&AccountScope,year:impl Into<Option<i32>>,
    conversation:Option<&str>,row_cap:i64,detail:Detail)->Result<Value,StoreError> {
    let year=year.into();
    let (lower,upper)=period_bounds(year)?;
    let date_condition=if year.is_some() {"AND timestamp>=?3 AND timestamp<?4"} else {""};
    let condition=if conversation.is_some() {"AND conversation_id COLLATE BINARY=?5"} else {""};
    // Bound type cardinality before grouping. The query never selects raw
    // label strings even when the detached index contains unexpected values.
    let (sample_type,bucket_column)=if detail==Detail::LegacyTypes {
        let mut case=String::from(", CASE type COLLATE BINARY");
        for (bucket,label) in TYPE_LABELS[..UNKNOWN_TYPE].iter().enumerate() {
            write!(&mut case," WHEN '{label}' THEN {bucket}").map_err(|_|bad())?;
        }
        write!(&mut case," ELSE {UNKNOWN_TYPE} END AS bucket").map_err(|_|bad())?;
        (case,",bucket")
    } else {
        (String::new(),"")
    };
    let sql=format!("WITH sample AS (
        SELECT timestamp,is_self{sample_type} FROM messages
        WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2 {date_condition} {condition} LIMIT ?6
        ) SELECT date(timestamp,'unixepoch','+8 hours') AS day,
                CAST(strftime('%H',timestamp,'unixepoch','+8 hours') AS INTEGER) AS hour,
                COUNT(*),COALESCE(SUM(CASE WHEN is_self=1 THEN 1 ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN is_self=0 THEN 1 ELSE 0 END),0),MIN(timestamp),MAX(timestamp){bucket_column}
          FROM sample GROUP BY day,hour{bucket_column} ORDER BY day,hour");
    // Omitted constraints use unused numbered placeholders; no OR/COALESCE
    // date predicate obscures the index on the year-filtered path. LIMIT reads
    // one overflow row so a full-history result is never silently truncated.
    // The store also imposes its per-connection execution deadline.
    let mut stmt=db.prepare(&sql)?;
    let mut rows=stmt.query((&scope.platform,&scope.account_id,lower,upper,conversation,row_cap+1))?;
    let mut days=BTreeMap::<NaiveDate,i64>::new();
    let mut months=[0_i64;12];
    let mut hours=[0_i64;24];
    let mut heatmap=[[0_i64;24];7];
    let mut type_counts=[0_i64;TYPE_LABELS.len()];
    let (mut sent,mut received,mut total)=(0_i64,0_i64,0_i64);
    let (mut first,mut last):(Option<i64>,Option<i64>)=(None,None);
    while let Some(row)=rows.next()? {
        let day=NaiveDate::parse_from_str(&row.get::<_,String>(0)?,"%Y-%m-%d").map_err(|_|bad())?;
        let hour:usize=row.get(1)?;
        if hour>=24 || year.is_some_and(|year|day.year()!=year) {return Err(bad());}
        let count:i64=row.get(2)?;
        total+=count;
        if total>row_cap {return Err(StoreError::TooLarge);}
        if detail==Detail::LegacyTypes {
            let bucket=type_counts.get_mut(row.get::<_,usize>(7)?).ok_or_else(bad)?;
            *bucket+=count;
        }
        sent+=row.get::<_,i64>(3)?;
        received+=row.get::<_,i64>(4)?;
        let min=row.get::<_,i64>(5)?;
        let max=row.get::<_,i64>(6)?;
        first=Some(first.map_or(min,|old|old.min(min)));
        last=Some(last.map_or(max,|old|old.max(max)));
        months[day.month0() as usize]+=count;
        hours[hour]+=count;
        heatmap[day.weekday().num_days_from_sunday() as usize][hour]+=count;
        *days.entry(day).or_default()+=count;
    }

    // Tie policy matches the pinned report: oldest date, earliest hour,
    // and first occurrence of the longest consecutive active-day streak.
    let busiest_day=days.iter().max_by_key(|(day,count)|(**count,std::cmp::Reverse(**day)))
        .map(|(day,count)|json!({"date":day.to_string(),"count":count}));
    let busiest_hour=hours.iter().enumerate().filter(|(_,count)|**count>0)
        .max_by_key(|(hour,count)|(**count,std::cmp::Reverse(*hour)))
        .map(|(hour,count)|json!({"hour":hour,"count":count}));
    let mut streak:Option<(i64,NaiveDate,NaiveDate)>=None;
    let (mut previous,mut start,mut streak_days)=(None::<NaiveDate>,None,0);
    for &day in days.keys() {
        if previous.and_then(|prev|prev.succ_opt()) != Some(day) {
            start=Some(day);
            streak_days=0;
        }
        streak_days+=1;
        previous=Some(day);
        if streak.is_none_or(|(n,_,_)|streak_days>n) {
            streak=Some((streak_days,start.expect("first date sets start"),day));
        }
    }
    // Stock all-history reports fold the same month of different years into
    // one calendar-month bin; days and the streak retain full UTC+08 dates.
    let monthly:Vec<_>=months.iter().enumerate().map(|(i,n)|json!({"month":i+1,"count":n})).collect();
    let hourly:Vec<_>=hours.iter().enumerate().filter(|(_,n)|**n>0).map(|(i,n)|json!({"hour":i,"count":n})).collect();
    let ratio=if total==0 {0.0} else {
        // Format the f64 directly to avoid extra rounding from a pre-scale multiply.
        format!("{:.6}",sent as f64 / total as f64).parse::<f64>().map_err(|_|bad())?
    };
    let mut result=json!({"platform":scope.platform,"account_id":scope.account_id,"year":year,"conversation_id":conversation,
        "total_messages":total,"sent_messages":sent,"received_messages":received,"sent_ratio":ratio,
        "active_days":days.len(),"first_timestamp":first,"last_timestamp":last,"monthly":monthly,"hourly":hourly,
        "busiest_day":busiest_day,"busiest_hour":busiest_hour,"activity_heatmap":heatmap,
        "longest_streak":streak.map(|(n,start,end)|json!({"days":n,"start_date":start.to_string(),"end_date":end.to_string()}))
    });
    if detail==Detail::LegacyTypes {
        let mut entries:Vec<_>=TYPE_LABELS.into_iter().zip(type_counts).filter(|(_,n)|*n>0).collect();
        entries.sort_unstable_by_key(|&(label,n)|(std::cmp::Reverse(n),label));
        result["message_types"]=Value::Array(entries.into_iter()
            .map(|(label,n)|json!({"type":label,"count":n})).collect());
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_select_never_returns_a_partial_report() {
        let db=Connection::open_in_memory().unwrap();
        db.execute_batch("CREATE TABLE messages(platform TEXT COLLATE NOCASE,account_id TEXT COLLATE NOCASE,conversation_id TEXT COLLATE NOCASE,timestamp INTEGER,is_self INTEGER);").unwrap();
        let scope=AccountScope{platform:"qq".into(),account_id:"account".into()};
        for owner in ["account","account","ACCOUNT"] {
            db.execute("INSERT INTO messages VALUES ('qq',?,'room',?,0)",(owner,boundary(2026).unwrap())).unwrap();
        }
        assert!(matches!(annual(&db,&scope,2026,None,1,Detail::Activity),Err(StoreError::TooLarge)));
        let report=annual(&db,&scope,2026,Some("room"),2,Detail::Activity).unwrap();
        assert_eq!(report["total_messages"],2);
        assert_eq!(report["busiest_day"]["date"],"2026-01-01");
        assert_eq!(report["busiest_hour"]["hour"],0);
        assert_eq!(annual(&db,&scope,2026,Some("none"),1,Detail::Activity).unwrap()["total_messages"],0);
    }
}
