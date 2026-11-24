//! Exact selected-message projection from the same verified imported snapshot
//! backing the media catalog. No provider queries or path fields on the wire.
use crate::{
    asset_jobs::{self,Artifact,Create,Error,Selection,Work},
    media_bundle::{Bundle,MessageKey},
    plugins::AccountScope,
    store::{Query,Store,StoreError},
};
use rusqlite::{OptionalExtension,params};
use serde::{Deserialize,Serialize};
use std::{collections::BTreeSet,io::{self,Write}};

const MAX_SELECTED:usize=16;
const MAX_TEXT_BYTES:usize=64*1024;
const MAX_TEXT_JSON:usize=768*1024;
const TEXT_END:&[u8]=b"]\n";

#[derive(Default)]
struct MessageJson { bytes: Vec<u8> }

impl MessageJson {
    fn append(&mut self, bytes:&[u8],limit:usize)->io::Result<()> {
        if bytes.len()>limit-self.bytes.len() {
            return Err(io::Error::other("selected message JSON limit"));
        }
        let needed=self.bytes.len()+bytes.len();
        let cap=self.bytes.capacity();
        if cap<needed {
            let next=cap.saturating_mul(2).max(needed).max(8).min(MAX_TEXT_JSON);
            self.bytes.reserve_exact(next-self.bytes.len());
        }
        self.bytes.extend_from_slice(bytes);
        Ok(())
    }

    fn finish(mut self)->Result<Vec<u8>,Error> {
        self.append(TEXT_END,MAX_TEXT_JSON).map_err(|_|Error::Limit)?;
        Ok(self.bytes)
    }
}

impl Write for MessageJson {
    fn write(&mut self,bytes:&[u8])->io::Result<usize> {
        // Reserve the exact closing bytes while encoding each row, even
        // when JSON escaping expands the source message.
        self.append(bytes,MAX_TEXT_JSON-TEXT_END.len())?;
        Ok(bytes.len())
    }

    fn flush(&mut self)->io::Result<()> { Ok(()) }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    conversation_id:String,
    message_ids:Vec<String>,
    attachments:Vec<SelectedAttachment>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectedAttachment {message_id:String,attachment_id:String,sha256:Option<String>}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FilteredRequest {
    query:Filters,
    #[serde(default)]
    allow_text_only:bool,
    #[serde(default)]
    pub deferred:bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Filters {
    platform:String,
    account_id:String,
    conversation_id:String,
    #[serde(default)]
    q:String,
    #[serde(default,rename="type")]
    kind_type:String,
    #[serde(default)]
    start:String,
    #[serde(default)]
    end:String,
    format:Option<String>,
}

#[derive(Serialize)]
struct Row<'a> {
    message_id:&'a str,
    timestamp:Option<i64>,
    is_self:Option<bool>,
    text:Option<String>,
}

fn valid(id:&str)->bool {
    (1..=512).contains(&id.len())&&!id.chars().any(char::is_control)
}

pub(crate) fn create(store:Store,bundle:&Bundle,scope:&AccountScope,req:Request)
    ->Result<Artifact,Error> {
    create_inner(store,bundle,scope,req,false,false,None)
}

pub(crate) fn validate_filtered_scope(req:&FilteredRequest,scope:&AccountScope)->Result<(),Error>{
    let query=&req.query;
    if query.platform!=scope.platform || query.account_id!=scope.account_id{return Err(Error::Missing);}
    if !matches!(scope.platform.as_str(),"qq"|"wechat") || !valid(&query.conversation_id)
        || query.format.as_deref().is_some_and(|value|value!="json")
    {return Err(Error::Invalid);}
    Ok(())
}
pub(crate) fn create_filtered(
    store:Store,bundle:&Bundle,scope:&AccountScope,req:FilteredRequest,work:Option<&Work>,
)->Result<Artifact,Error> {
    validate_filtered_scope(&req,scope)?;
    let FilteredRequest{query,allow_text_only,..}=req;
    work.map_or(Ok(()),Work::checkpoint)?;
    let q=Query{platform:query.platform,account_id:query.account_id,conversation_id:query.conversation_id,
        q:query.q,kind_type:query.kind_type,start:query.start,end:query.end,account_scoped:true,
        ..Query::default()};
    let message_ids=store.filtered_bundle_ids(&q,MAX_SELECTED).map_err(|e|match e {
        StoreError::BadQuery(_)=>Error::Invalid,
        StoreError::TooLarge=>Error::Limit,
        _=>Error::Unavailable,
    })?;
    if message_ids.is_empty(){return Err(Error::Missing);}
    let mut attachments=Vec::new();
    for message_id in &message_ids {
        work.map_or(Ok(()),Work::checkpoint)?;
        let message=MessageKey{platform:scope.platform.clone(),account_id:scope.account_id.clone(),
            conversation_id:q.conversation_id.clone(),message_id:message_id.clone()};
        if !message.valid(){return Err(Error::Unavailable);}
        let ordered=bundle.list(&message);
        if ordered.len()>MAX_SELECTED-attachments.len(){return Err(Error::Limit);}
        let mut ordered:Vec<_>=ordered.iter().collect();
        ordered.sort_unstable_by(|a,b|a.id.cmp(&b.id));
        attachments.extend(ordered.into_iter().map(|item|SelectedAttachment{
            message_id:message_id.clone(),attachment_id:item.id.clone(),sha256:None,
        }));
    }
    if attachments.is_empty() && !allow_text_only{return Err(Error::Missing);}
    create_inner(store,bundle,scope,Request{conversation_id:q.conversation_id,message_ids,attachments},
        allow_text_only,true,work)
}

fn create_inner(store:Store,bundle:&Bundle,scope:&AccountScope,req:Request,allow_text_only:bool,exact_filtered:bool,work:Option<&Work>)
    ->Result<Artifact,Error> {
    if !valid(&req.conversation_id) || !(1..=MAX_SELECTED).contains(&req.message_ids.len())
        || req.attachments.len()>MAX_SELECTED || (req.attachments.is_empty()&&!allow_text_only) {
        return Err(Error::Invalid);
    }
    let mut selected=BTreeSet::new();
    if req.message_ids.iter().any(|id| !valid(id)||!selected.insert(id.as_str()))
        || req.attachments.iter().any(|a|!selected.contains(a.message_id.as_str())) {
        return Err(Error::Invalid);
    }
    let mut db=store.connection().map_err(|_|Error::Unavailable)?;
    let tx=db.transaction().map_err(|_|Error::Unavailable)?;
    let mut lookup=tx.prepare(if exact_filtered {
        "SELECT timestamp,is_self,
            CASE WHEN length(CAST(text AS BLOB))<=?5 THEN text END,
            coalesce(length(CAST(text AS BLOB))>?5,0)
         FROM messages WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
            AND conversation_id COLLATE BINARY=?3 AND id COLLATE BINARY=?4 LIMIT 2"
    } else {
        "SELECT timestamp,is_self,
            CASE WHEN length(CAST(text AS BLOB))<=?5 THEN text END,
            coalesce(length(CAST(text AS BLOB))>?5,0)
         FROM messages WHERE platform=?1 AND account_id=?2 AND conversation_id=?3 AND id=?4"
    }).map_err(|_|Error::Unavailable)?;
    let mut text=MessageJson::default();
    text.write_all(b"[").map_err(|_|Error::Limit)?;
    for (order,id) in req.message_ids.iter().enumerate() {
        work.map_or(Ok(()),Work::checkpoint)?;
        let read=|row:&rusqlite::Row<'_>| -> rusqlite::Result<_> {
            Ok((row.get::<_,Option<i64>>(0)?,row.get::<_,Option<bool>>(1)?,
                row.get::<_,Option<String>>(2)?,row.get::<_,bool>(3)?))
        };
        let result=if exact_filtered {
            let mut rows=lookup.query(
                params![scope.platform,scope.account_id,req.conversation_id,id,MAX_TEXT_BYTES as i64],
            ).map_err(|_|Error::Unavailable)?;
            let found=rows.next().map_err(|_|Error::Unavailable)?.map(read)
                .transpose().map_err(|_|Error::Unavailable)?;
            if rows.next().map_err(|_|Error::Unavailable)?.is_some() {
                return Err(Error::Unavailable);
            }
            found
        } else {
            lookup.query_row(
                params![scope.platform,scope.account_id,req.conversation_id,id,MAX_TEXT_BYTES as i64],
                read,
            ).optional().map_err(|_|Error::Unavailable)?
        };
        let Some((timestamp,is_self,body,too_big))=result else {return Err(Error::Missing);};
        if too_big{return Err(Error::Limit);}
        if order!=0 {text.write_all(b",").map_err(|_|Error::Limit)?;}
        serde_json::to_writer(&mut text,&Row{message_id:id,timestamp,is_self,text:body})
            .map_err(|_|Error::Limit)?;
    }
    let text=text.finish()?;
    drop(lookup);
    drop(tx);
    let empty_media=req.attachments.is_empty();
    let items=req.attachments.into_iter().map(|item|Selection{
        conversation_id:req.conversation_id.clone(),message_id:item.message_id,
        attachment_id:item.attachment_id,sha256:item.sha256,
    }).collect();
    if allow_text_only && empty_media {
        return asset_jobs::create_filtered_text_only(bundle,scope,text,req.message_ids.len(),work);
    }
    asset_jobs::create_with_messages(bundle,scope,Create{items},text,req.message_ids.len(),work)
}

#[cfg(test)]
mod json_limit_tests {
    use super::*;

    fn write_rows(text:&mut impl Write,last:usize)->io::Result<()> {
        text.write_all(b"[")?;
        for i in 0..12 {
            if i>0 {text.write_all(b",")?;}
            let prefix=if i==0 {"\0\"\\多"} else {""};
            let padding=if i==11 {last} else {MAX_TEXT_BYTES-prefix.len()};
            let body=format!("{prefix}{}","x".repeat(padding));
            assert!(body.len()<=MAX_TEXT_BYTES);
            serde_json::to_writer(&mut *text,&Row{message_id:"row",timestamp:Some(i),
                is_self:None,text:Some(body)}).map_err(io::Error::other)?;
        }
        Ok(())
    }

    #[test]
    fn selected_json_boundary_includes_punctuation_escapes_and_closure() {
        let mut oracle=Vec::new();
        write_rows(&mut oracle,0).unwrap();
        let last=MAX_TEXT_JSON-TEXT_END.len()-oracle.len();
        assert!((1..MAX_TEXT_BYTES).contains(&last));
        for pad in [last-1,last] {
            let mut oracle=Vec::new();
            write_rows(&mut oracle,pad).unwrap();
            oracle.extend_from_slice(TEXT_END);
            let mut bounded=MessageJson::default();
            write_rows(&mut bounded,pad).unwrap();
            assert_eq!(bounded.finish().unwrap(),oracle);
        }
        assert!(write_rows(&mut MessageJson::default(),last+1).is_err());
    }
}
