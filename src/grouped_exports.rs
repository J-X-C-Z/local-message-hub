//! Scoped, bounded local export discovery and read projections. No jobs or files.
use crate::{
    export::{Capped, MAX_OUTPUT, MAX_ROWS, csv_cell, json_value},
    plugins::AccountScope,
    store::{Query, Result, Store, StoreError},
};
use rusqlite::params;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{collections::BTreeSet, io::Write};

const MAX_SELECTION: usize = 20;
const MAX_OBSERVED_MESSAGES: usize = 25_000;
const MAX_LABEL: usize = 8 * 1024;

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all="lowercase")]
pub(crate) enum TabularFormat {
    #[default]
    Csv,
    Json,
}
impl TabularFormat {
    pub(crate) fn mime(self) -> &'static str {
        match self {
            Self::Csv => "text/csv; charset=utf-8",
            Self::Json => "application/json; charset=utf-8",
        }
    }
    pub(crate) fn disposition(self, contacts:bool) -> &'static str {
        match (self, contacts) {
            (Self::Csv, true) => "attachment; filename=\"contacts.csv\"",
            (Self::Json, true) => "attachment; filename=\"contacts.json\"",
            (Self::Csv, false) => "attachment; filename=\"observed-participants.csv\"",
            (Self::Json, false) => "attachment; filename=\"observed-participants.json\"",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ContactExport {
    #[serde(default)] pub platform:String,
    #[serde(default)] pub account_id:String,
    #[serde(default)] pub kind:String,
    #[serde(default)] pub q:String,
    #[serde(default)] pub format:TabularFormat,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Observed {
    #[serde(default)] pub platform:String,
    #[serde(default)] pub account_id:String,
    pub conversation_id:String,
    #[serde(default)] pub q:String,
    /// Legacy aliases must explicitly request the detached observed record,
    /// not an impersonation of an authoritative live provider roster.
    pub source:Option<String>,
    #[serde(default)] pub format:TabularFormat,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Selection {
    #[serde(default)] pub platform:String,
    #[serde(default)] pub account_id:String,
    /// JSON array of IDs. Supports comma/percent in identifiers, no wildcard.
    pub conversation_ids:String,
    pub start:Option<i64>,
    pub end:Option<i64>,
}

fn bounded_id(id:&str) -> bool {
    (1..=256).contains(&id.len()) && id.trim()==id && !id.chars().any(char::is_control)
}
fn match_text(s:&str) -> Result<String> {
    if s.len()>256 || s.chars().any(char::is_control) {
        return Err(StoreError::BadQuery("invalid local text filter"));
    }
    let escaped=s.replace('\\',"\\\\").replace('%',"\\%").replace('_',"\\_");
    Ok(format!("%{escaped}%"))
}
fn capped_label(value:Option<String>) -> Result<Option<String>> {
    if value.as_ref().is_some_and(|s|s.len()>MAX_LABEL) {return Err(StoreError::TooLarge);}
    Ok(value)
}

pub(crate) fn contact_records(store:Store,scope:AccountScope,query:ContactExport) -> Result<Vec<Value>> {
    match_text(&query.q)?;
    if query.kind.len()>128 || query.kind.chars().any(char::is_control) {
        return Err(StoreError::BadQuery("invalid contact category"));
    }
    let page=store.projected_contacts(&Query{
        platform:scope.platform,account_id:scope.account_id,kind:query.kind,q:query.q,
        limit:Some(MAX_ROWS as i64),account_scoped:true,..Default::default()
    })?;
    if page["total"].as_u64().unwrap_or(u64::MAX)>MAX_ROWS as u64 {return Err(StoreError::TooLarge);}
    let mut records=Vec::new();
    for row in page["items"].as_array().ok_or(StoreError::Schema)? {
        let name=capped_label(row["name"].as_str().map(str::to_owned))?;
        // Only projected local fields. No alias, account linkage, avatar or opaque extra.
        records.push(json!({"id":row["id"],"platform":row["platform"],"account_id":row["account_id"],
            "name":name,"kind":row["kind"]}));
    }
    Ok(records)
}

pub(crate) fn observed_records(store:Store,scope:AccountScope,query:Observed) -> Result<Option<Vec<Value>>> {
    if !bounded_id(&query.conversation_id) || query.source.as_deref().is_some_and(|s|s!="observed") {
        return Err(StoreError::BadQuery("invalid observed participant selection"));
    }
    let text=match_text(&query.q)?;
    let mut conn=store.connection()?;
    let tx=conn.transaction()?;
    let group:bool=tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=?1 AND account_id=?2 AND id=?3 AND lower(kind)='group')",
        params![scope.platform,scope.account_id,query.conversation_id],|r|r.get(0))?;
    if !group {return Ok(None);}
    let messages:usize=tx.query_row(
        "SELECT count(*) FROM (SELECT 1 FROM messages
         WHERE platform=?1 AND account_id=?2 AND conversation_id=?3 LIMIT ?4)",
        params![scope.platform,scope.account_id,query.conversation_id,MAX_OBSERVED_MESSAGES+1],|r|r.get(0))?;
    if messages>MAX_OBSERVED_MESSAGES {return Err(StoreError::TooLarge);}
    let mut stmt=tx.prepare(
        "WITH ranked AS (
            SELECT sender_id,substr(sender_name,1,?4) AS sender_name,
              min(timestamp) OVER by_sender AS first_seen,max(timestamp) OVER by_sender AS last_seen,
              count(*) OVER by_sender AS messages,
              row_number() OVER (PARTITION BY sender_id COLLATE BINARY ORDER BY timestamp DESC,id COLLATE BINARY DESC) AS rank
            FROM messages WHERE platform=?1 AND account_id=?2 AND conversation_id=?3
              AND sender_id IS NOT NULL AND sender_id<>''
            WINDOW by_sender AS (PARTITION BY sender_id COLLATE BINARY)
         )
         SELECT substr(sender_id,1,257),sender_name,first_seen,last_seen,messages FROM ranked
         WHERE rank=1 AND (sender_name LIKE ?5 ESCAPE '\\' OR sender_id LIKE ?5 ESCAPE '\\')
         ORDER BY sender_id COLLATE BINARY LIMIT ?6")?;
    let mut rows=stmt.query(params![scope.platform,scope.account_id,query.conversation_id,MAX_LABEL+1,text,MAX_ROWS+1])?;
    let mut items=Vec::new();
    let mut aggregate=0usize;
    while let Some(r)=rows.next()? {
        if items.len()==MAX_ROWS {return Err(StoreError::TooLarge);}
        let id:String=r.get(0)?;
        let name=capped_label(r.get(1)?)?;
        if !bounded_id(&id) {return Err(StoreError::Schema);}
        aggregate=aggregate.saturating_add(id.len()+name.as_ref().map_or(0,String::len)+512);
        if aggregate>MAX_OUTPUT {return Err(StoreError::TooLarge);}
        items.push(json!({"source":"observed","id":id,"name":name,
            "platform":scope.platform,"account_id":scope.account_id,"conversation_id":query.conversation_id,
            "first_seen":r.get::<_,i64>(2)?,"last_seen":r.get::<_,i64>(3)?,"message_count":r.get::<_,i64>(4)?}));
    }
    Ok(Some(items))
}

pub(crate) fn describe(store:Store,scope:AccountScope,query:Selection) -> Result<Option<Value>> {
    if query.conversation_ids.len()>7*1024 || matches!((query.start,query.end),(Some(a),Some(b)) if a>b) {
        return Err(StoreError::BadQuery("invalid export selection"));
    }
    let ids=serde_json::from_str::<Vec<String>>(&query.conversation_ids).map_err(|_|StoreError::BadQuery("invalid export selection"))?;
    let mut seen=BTreeSet::new();
    if ids.is_empty() || ids.len()>MAX_SELECTION || ids.iter().any(|id|!bounded_id(id)||!seen.insert(id)) {
        return Err(StoreError::BadQuery("invalid export selection"));
    }
    let mut conn=store.connection()?;
    let tx=conn.transaction()?;
    let mut exists=tx.prepare("SELECT EXISTS(SELECT 1 FROM conversations WHERE platform=?1 AND account_id=?2 AND id=?3)")?;
    let mut count=tx.prepare("SELECT count(*) FROM (SELECT 1 FROM messages
      WHERE platform=?1 AND account_id=?2 AND conversation_id=?3
        AND timestamp BETWEEN ?4 AND ?5 LIMIT ?6)")?;
    let mut plans=Vec::with_capacity(ids.len());
    for id in &ids {
        if !exists.query_row(params![scope.platform,scope.account_id,id],|r|r.get::<_,bool>(0))? {
            return Ok(None);
        }
        let n:usize=count.query_row(params![scope.platform,scope.account_id,id,
            query.start.unwrap_or(i64::MIN),query.end.unwrap_or(i64::MAX),MAX_ROWS+1],|r|r.get(0))?;
        plans.push(json!({"conversation_id":id,"rows_at_most_limit":n.min(MAX_ROWS),"over_row_limit":n>MAX_ROWS}));
    }
    Ok(Some(json!({"platform":scope.platform,"account_id":scope.account_id,"conversations":plans,
        "start":query.start,"end":query.end,"download_route":"/api/v1/messages/export",
        "merged_file":false,"background":false,"snapshot_bound":false})))
}

const OBSERVED_CSV_LABELS: &[(&str,&str)] = &[
    ("source","来源"),("platform","平台"),("account_id","账号"),("conversation_id","会话ID"),
    ("id","发送者ID"),("name","最后一次可见名称"),("first_seen","首次出现Unix秒"),
    ("last_seen","最后出现Unix秒"),("message_count","档案消息数"),
];

fn observed_csv(records: &[Value]) -> Result<Vec<u8>> {
    let mut out = Capped::default();
    let limit = |_| StoreError::TooLarge;
    for (i, (_, label)) in OBSERVED_CSV_LABELS.iter().enumerate() {
        if i != 0 { out.write_all(b",").map_err(limit)?; }
        csv_cell(&mut out, label).map_err(limit)?;
    }
    out.write_all(b"\r\n").map_err(limit)?;
    for row in records {
        for (i, (key, _)) in OBSERVED_CSV_LABELS.iter().enumerate() {
            if i != 0 { out.write_all(b",").map_err(limit)?; }
            match &row[*key] {
                Value::String(value) => csv_cell(&mut out, value),
                Value::Number(value) => csv_cell(&mut out, &value.to_string()),
                _ => csv_cell(&mut out, ""),
            }.map_err(limit)?;
        }
        out.write_all(b"\r\n").map_err(limit)?;
    }
    Ok(out.bytes)
}

pub(crate) fn download(records:Vec<Value>,format:TabularFormat,contacts:bool) -> Result<Vec<u8>> {
    if !contacts && matches!(format,TabularFormat::Csv) {
        return observed_csv(&records);
    }
    let mut out=Capped::default();
    if matches!(format,TabularFormat::Json) {
        json_value(&mut out,&Value::Array(records))?;
    } else {
        let labels:&[(&str,&str)]=
            &[("platform","平台"),("account_id","账号"),("id","联系人ID"),("name","名称"),("kind","类型")];
        let io=||StoreError::TooLarge;
        let mut write_line=|values:&[&str]| -> Result<()> {
            for (i,cell) in values.iter().enumerate() {
                if i>0 {out.write_all(b",").map_err(|_|io())?;}
                csv_cell(&mut out,cell).map_err(|_|io())?;
            }
            out.write_all(b"\r\n").map_err(|_|io())?;
            Ok(())
        };
        write_line(&labels.iter().map(|(_,label)|*label).collect::<Vec<_>>())?;
        for r in records {
            let values=labels.iter().map(|(key,_)| match &r[*key] {
                Value::String(s)=>s.clone(),
                Value::Number(n)=>n.to_string(),
                _=>String::new(),
            }).collect::<Vec<_>>();
            write_line(&values.iter().map(String::as_str).collect::<Vec<_>>())?;
        }
    }
    Ok(out.bytes)
}

#[cfg(test)]
mod observed_csv_tests {
    use super::*;

    #[test]
    fn observed_csv_keeps_exact_columns_formula_guard_and_nulls() {
        let input = vec![
            json!({"source":"observed","platform":"wechat","account_id":"account",
                "conversation_id":"room","id":"one","name":"  =雪,\"李\"\r\n",
                "first_seen":-1,"last_seen":1700000000,"message_count":9}),
            json!({"source":"observed","platform":"qq","account_id":"other",
                "conversation_id":"room","id":"two","name":null,"first_seen":0,
                "last_seen":1,"message_count":12,"extra":"not exported"}),
        ];
        let header = "来源,平台,账号,会话ID,发送者ID,最后一次可见名称,首次出现Unix秒,最后出现Unix秒,档案消息数\r\n";
        let expected = format!("{header}observed,wechat,account,room,one,\"'  =雪,\"\"李\"\"\r\n\",'-1,1700000000,9\r\nobserved,qq,other,room,two,,0,1,12\r\n");
        assert_eq!(download(input,TabularFormat::Csv,false).unwrap(), expected.as_bytes());
        assert_eq!(download(vec![],TabularFormat::Csv,false).unwrap(), header.as_bytes());
    }

    #[test]
    fn observed_csv_keeps_total_byte_cap() {
        let row=json!({"name":"界".repeat(MAX_OUTPUT/3),"message_count":1});
        assert!(matches!(download(vec![Value::Null,row],TabularFormat::Csv,false),Err(StoreError::TooLarge)));
    }
}
