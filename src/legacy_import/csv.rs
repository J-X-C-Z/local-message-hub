//! Strict detached text CSV subset of the pinned Hub importer.
use super::{OFFLINE_PRODUCER,preflight::Error};
use crate::{events::{IngestRequest,MAX_BATCH_BYTES,NormalizedMessage},qq_csv_import::{scan_csv,timestamp}};
use serde::Serialize;
use std::collections::BTreeMap;

pub(super) const MODE:&str="lmh-legacy-text-csv-import-v1";

fn canonical(raw:&str)->Option<&'static str> {
    Some(match raw.trim() {
        "timestamp"|"时间"|"time"|"timestamp_text"=>"timestamp",
        "platform"|"平台"=>"platform",
        "account_id"|"账号"|"account"=>"account_id",
        "conversation_id"|"会话ID"|"chat_id"=>"conversation_id",
        "conversation_title"|"会话"|"conversation"|"chat_name"=>"conversation_title",
        "sender_id"|"发送者ID"=>"sender_id",
        "sender_name"|"发送者"|"sender"=>"sender_name",
        "id"|"message_id"|"消息ID"=>"id",
        "type"|"类型"|"message_type"=>"type",
        "text"|"内容"|"content"|"message"=>"text",
        "is_self"|"自己发送"=>"is_self",
        "media_url"|"媒体链接"=>"media_url",
        "file_name"|"文件名"=>"file_name",
        "附件"=>"attachments",
        _=>return None,
    })
}

fn time(raw:&str)->Option<i64> {
    let raw=raw.trim();
    if raw.is_empty(){return Some(0);}
    if raw.bytes().all(|b|b.is_ascii_digit()) {
        let value=raw.parse::<i64>().ok()?;
        // The pinned CSV parser passes numeric text through Python float.
        // Restrict to precisely representable integers before applying its unit rule.
        if value>(1_i64<<53){return None;}
        return Some(if value>=100_000_000_000{value/1000}else{value});
    }
    timestamp(raw)
}

pub(crate) fn decode(body:&[u8],platform:&str,account:&str)->Result<IngestRequest,Error> {
    if body.len()>MAX_BATCH_BYTES{return Err(Error::Limit);}
    let mut rows=scan_csv(body,14,100).map_err(|_|Error::Format)?.into_iter();
    let header=rows.next().ok_or(Error::Format)?;
    let mut fields=BTreeMap::new();
    for (i,name) in header.iter().enumerate() {
        let key=canonical(name).ok_or(Error::Format)?;
        if fields.insert(key,i).is_some(){return Err(Error::Format);}
    }
    for key in ["timestamp","platform","account_id","conversation_id","id","type","text","is_self"] {
        if !fields.contains_key(key){return Err(Error::Format);}
    }
    let rows=rows.collect::<Vec<_>>();
    if rows.is_empty()||rows.iter().any(|r|r.len()!=header.len()){return Err(Error::Format);}
    let platform_col=fields["platform"];
    let account_col=fields["account_id"];
    if rows.iter().any(|r|r[platform_col]!=platform||r[account_col]!=account){return Err(Error::Scope);}
    let mut messages=Vec::with_capacity(rows.len());
    for row in rows {
        let value=|key:&str| fields.get(key).map(|&i|row[i].as_str()).unwrap_or("");
        let some=|key:&str| {
            let text=value(key);
            (!text.is_empty()).then(||text.to_owned())
        };
        if ["conversation_id","id"].into_iter().any(|key| {
            let id=value(key);
            id.is_empty() || id.trim()!=id
        }) || value("type")!="text" || value("text").is_empty()
            || !value("media_url").is_empty() || !value("file_name").is_empty()
            || !matches!(value("attachments"),""|"[]"){return Err(Error::Format);}
        let direction=value("is_self").to_ascii_lowercase();
        let is_self=match direction.as_str() {
            "true"|"yes"|"1"|"是"=>true,
            "false"|"no"|"0"|"否"=>false,
            _=>return Err(Error::Format),
        };
        messages.push(NormalizedMessage{
            platform:platform.to_owned(),account_id:account.to_owned(),
            conversation_id:value("conversation_id").to_owned(),message_id:value("id").to_owned(),
            title:some("conversation_title"),sender_id:some("sender_id"),sender_name:some("sender_name"),
            timestamp:time(value("timestamp")).ok_or(Error::Format)?,is_self,text:some("text"),
        });
    }
    let normalized=serde_json::to_vec(&messages).map_err(|_|Error::Format)?;
    if normalized.len()>MAX_BATCH_BYTES{return Err(Error::Limit);}
    let request=IngestRequest{idempotency_key:"legacy-csv-preflight".into(),messages};
    request.validate(OFFLINE_PRODUCER).map_err(|_|Error::Format)?;
    Ok(request)
}

#[derive(Serialize)]
pub(crate) struct Report<'a> {
    total:usize,valid:usize,invalid:usize,errors:[();0],
    platforms:[&'a str;1],accounts:[&'a str;1],sample:[();0],
}
pub(crate) fn preview<'a>(body:&[u8],platform:&'a str,account:&'a str)->Result<Report<'a>,Error> {
    let total=decode(body,platform,account)?.messages.len();
    Ok(Report{total,valid:total,invalid:0,errors:[],platforms:[platform],accounts:[account],sample:[]})
}
