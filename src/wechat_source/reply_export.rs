//! The detached stock export's rendered reply display, never a target locator.
use super::{Error,LegacyRow,normalize_export_rows};
use crate::{events::{EventError,MAX_BATCH_BYTES,NormalizedMessage},wechat_detached};
use rusqlite::{Connection,OptionalExtension,params};
use serde::{Deserialize,Serialize,de::IgnoredAny};
use std::collections::BTreeMap;

use crate::wechat_detached::CLAIM_REPLY_PRODUCER;
const TABLE:&str="native_wechat_reply_display_v1";
const SCHEMA:&str="
CREATE TABLE native_wechat_reply_display_v1 (
    account_id TEXT NOT NULL,conversation_id TEXT NOT NULL,message_id TEXT NOT NULL,
    sender TEXT NOT NULL,content TEXT NOT NULL,
    PRIMARY KEY(account_id,conversation_id,message_id)
) WITHOUT ROWID;";

#[derive(Deserialize,Serialize,PartialEq,Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Display {pub sender:String,pub content:String}

#[derive(Deserialize)]
struct Extras {
    wechat_quote:Option<Display>,
}

#[derive(Deserialize)]
pub(super) struct ExportRow {
    #[serde(flatten)]
    row:LegacyRow,
    raw_type:Option<String>,
    media_url:Option<String>,
    #[serde(default)]
    resources:Vec<IgnoredAny>,
    extra:Option<Extras>,
}

fn plain(text:&str,multiline:bool)->bool {
    if text.contains('<')||text.contains('>')||text.chars().any(|c|
        c.is_control() && !(multiline&&c=='\n')) {return false;}
    let lower=text.to_ascii_lowercase();
    !lower.contains("://")&&!lower.contains("www.")&&!lower.contains("javascript:")
        &&!lower.contains("data:")&&!lower.contains("file:")
}

fn valid_display(d:&Display)->bool {
    !d.content.trim().is_empty()&&d.content.chars().count()<=512&&d.sender.chars().count()<=128
        &&plain(&d.sender,false)&&plain(&d.content,false)
}

pub(crate) type Displays=BTreeMap<String,Display>;

pub(crate) fn decode(bytes:&[u8],account:&str)
    ->Result<(Vec<NormalizedMessage>,Displays),Error> {
    if bytes.len()>MAX_BATCH_BYTES{return Err(Error::LegacyExport);}
    let rows:Vec<ExportRow>=serde_json::from_slice(bytes).map_err(|_|Error::LegacyExport)?;
    normalize(rows,account,100)
}

pub(super) fn normalize(rows:Vec<ExportRow>,account:&str,limit:usize)
    ->Result<(Vec<NormalizedMessage>,Displays),Error> {
    if !(1..=limit).contains(&rows.len()){return Err(Error::LegacyExport);}
    let mut display=Displays::new();
    let mut text_rows=Vec::with_capacity(rows.len());
    for ExportRow{mut row,raw_type,media_url,resources,extra} in rows {
        if row.platform!="wechat"||row.account_id!=account{return Err(Error::Scope);}
        if !resources.is_empty(){return Err(Error::LegacyExport);}
        let quote=extra.and_then(|extra|extra.wechat_quote);
        match (row.kind.as_str(),quote) {
            ("link",Some(quote)) if raw_type.as_deref()==Some("49")
                && media_url.as_deref().is_none_or(str::is_empty)
                && wechat_detached::text(&row.text)&&plain(&row.text,true)&&valid_display(&quote) => {
                if display.insert(row.id.clone(),quote).is_some(){return Err(Error::LegacyExport);}
                row.kind="text".into();
            }
            ("text",None) if media_url.as_deref().is_none_or(str::is_empty)=>{},
            _=>return Err(Error::LegacyExport),
        }
        text_rows.push(row);
    }
    if display.is_empty(){return Err(Error::LegacyExport);}
    Ok((normalize_export_rows(text_rows,account,limit)?,display))
}

pub(crate) fn installed(db:&Connection)->rusqlite::Result<bool>{
    let found=db.query_row("SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
        [TABLE],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?))).optional()?;
    match found.as_ref().map(|(name,kind)|(name.as_str(),kind.as_str())) {
        None=>Ok(false),
        Some((TABLE,"table"))=>Ok(true),
        _=>Err(rusqlite::Error::InvalidQuery),
    }
}

const SELECT_DISPLAY: &str =
    "SELECT q.sender,q.content FROM native_wechat_reply_display_v1 q
     JOIN native_owners o ON o.platform='wechat' AND o.account_id=q.account_id AND o.message_id=q.message_id
     WHERE q.account_id=? AND q.conversation_id=? AND q.message_id=? AND o.producer=?
       AND typeof(q.sender)='text' AND length(CAST(q.sender AS BLOB))<=512
       AND typeof(q.content)='text' AND length(CAST(q.content AS BLOB))<=2048";

fn decode_display(row: &rusqlite::Row<'_>) -> rusqlite::Result<Display> {
    Ok(Display { sender: row.get(0)?, content: row.get(1)? })
}

pub(crate) fn get(db:&Connection,account:&str,conversation:&str,id:&str)
    ->rusqlite::Result<Option<Display>> {
    let value=db.query_row(SELECT_DISPLAY, params![account,conversation,id,CLAIM_REPLY_PRODUCER],
        decode_display).optional()?;
    Ok(value.filter(valid_display))
}

/// Page readers use a fresh connection per request. Reuse its statement
/// between emitted rows, not prepared state or display data across requests.
pub(crate) fn get_for_page(db: &Connection, account: &str, conversation: &str, id: &str)
    -> rusqlite::Result<Option<Display>> {
    let value = db.prepare_cached(SELECT_DISPLAY)?.query_row(
        params![account, conversation, id, CLAIM_REPLY_PRODUCER], decode_display).optional()?;
    Ok(value.filter(valid_display))
}

pub(crate) fn record(db:&Connection,message:&NormalizedMessage,display:&Display,created:bool)
    ->Result<(),EventError> {
    if !installed(db)?{db.execute_batch(SCHEMA)?;}
    if !created {
        return if get(db,&message.account_id,&message.conversation_id,&message.message_id)?.as_ref()==Some(display){
            Ok(())
        }else{Err(EventError::Conflict)};
    }
    db.execute("INSERT INTO native_wechat_reply_display_v1(account_id,conversation_id,message_id,sender,content)
        VALUES(?,?,?,?,?)",params![message.account_id,message.conversation_id,message.message_id,display.sender,display.content])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_lookup_keeps_scope_owner_and_display_validation() {
        let db = Connection::open_in_memory().unwrap();
        db.execute_batch(SCHEMA).unwrap();
        db.execute_batch("CREATE TABLE native_owners(
             platform TEXT,account_id TEXT,message_id TEXT,producer TEXT,
             PRIMARY KEY(platform,account_id,message_id));").unwrap();
        for (id, producer, text) in [
            ("valid", CLAIM_REPLY_PRODUCER, "引用"),
            ("markup", CLAIM_REPLY_PRODUCER, "<not plain>"),
            ("foreign-owner", "other-producer", "not owned"),
        ] {
            db.execute("INSERT INTO native_owners VALUES('wechat','a',?,?)", params![id,producer]).unwrap();
            db.execute("INSERT INTO native_wechat_reply_display_v1 VALUES('a','room',?,'name',?)",
                params![id,text]).unwrap();
        }
        let tx = db.unchecked_transaction().unwrap();
        for (account, room, id, expected) in [
            ("a", "room", "valid", Some("引用")),
            ("a", "room", "markup", None),
            ("a", "room", "foreign-owner", None),
            ("b", "room", "valid", None),
            ("a", "other-room", "valid", None),
            ("a", "room", "missing", None),
            ("a", "room", "valid", Some("引用")),
        ] {
            let direct = get(&tx, account, room, id).unwrap();
            let cached = get_for_page(&tx, account, room, id).unwrap();
            assert!(direct == cached);
            assert_eq!(cached.as_ref().map(|display| display.content.as_str()), expected);
        }
        tx.rollback().unwrap();
    }
}
