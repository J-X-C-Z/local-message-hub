//! Legacy Moments text projection of one authorized ordinary local catalog.
use super::{Error,EventStore,Filter,LIMIT,Result,WHERE,export_bound,id,installed,instant,row,text,validate};
use crate::export::{Capped,csv_cell};
use chrono::{DateTime,Datelike,FixedOffset,Utc};
use rusqlite::{Connection,params};
use serde::{Deserialize,Serialize};
use std::io::Write;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub(crate) account_id:Option<String>,
    username:Option<String>,
    q:Option<String>,
    start:Option<String>,
    end:Option<String>,
    limit:Option<i64>,
    offset:Option<i64>,
    format:Option<String>,
}

#[derive(Serialize)]
struct Item {
    id:String,
    username:String,
    #[serde(rename="createTime")]
    create_time:i64,
    #[serde(rename="contentDesc")]
    content_desc:String,
}

#[derive(Serialize)]
struct Page<'a> {
    mode:&'static str,
    available:bool,
    catalogued:bool,
    account_id:&'a str,
    accounts:[&'a str;1],
    items:Vec<Item>,
    total:i64,
    limit:i64,
    offset:u32,
}

// None means this exact account has no installed detached catalog, not
// that another account's data exists or that a provider can be contacted.
fn catalog<T>(store:&EventStore,account:&str,q:&Query,
    project:impl FnOnce(&Connection,&Filter<'_>,i64,u32)->Result<T>)->Result<Option<T>>{
    let offset=u32::try_from(q.offset.unwrap_or(0).max(0)).map_err(|_|Error::Input)?;
    let filter=validate(account,q.account_id.as_deref().filter(|v|!v.is_empty()),
        q.username.as_deref(),q.q.as_deref(),export_bound(q.start.as_deref(),false)?,
        export_bound(q.end.as_deref(),true)?,offset)?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let output=if installed(&tx)? {
        // Bound the scope's actual scan before any filtered count or page.
        let count:i64=tx.query_row("SELECT count(*) FROM (SELECT 1 FROM native_detached_moments_v1
            WHERE account COLLATE BINARY=? LIMIT ?)",params![account,LIMIT+1],|r|r.get(0))?;
        match count {
            0=>None,
            1..=LIMIT=>{
                let total:i64=tx.query_row(&format!("SELECT count(*) FROM native_detached_moments_v1 {WHERE}"),
                    params![account,filter.author,filter.literal,filter.start,filter.end],|r|r.get(0))?;
                if !(0..=count).contains(&total){return Err(Error::Storage);}
                Some(project(&tx,&filter,total,offset)?)
            }
            _=>return Err(Error::Storage),
        }
    } else {None};
    drop(tx);
    store.verify()?;
    Ok(output)
}

fn visit(db:&Connection,account:&str,filter:&Filter<'_>,limit:i64,offset:u32,total:i64,
    mut next:impl FnMut(Item)->Result<()>)->Result<()>{
    let mut stmt=db.prepare(&format!("SELECT
        CASE WHEN length(CAST(id AS BLOB))<=256 THEN id END,
        CASE WHEN length(CAST(author_id AS BLOB))<=256 THEN author_id END,
        created_at,CASE WHEN length(CAST(text AS BLOB))<=8192 THEN text END
        FROM native_detached_moments_v1 {WHERE}
        ORDER BY created_at DESC,native_detached_moments_v1.id COLLATE BINARY DESC LIMIT ?6 OFFSET ?7"))?;
    let mut rows=stmt.query(params![account,filter.author,filter.literal,filter.start,filter.end,limit,offset])?;
    let expected=(total-i64::from(offset)).clamp(0,limit);
    let mut actual=0;
    while let Some(raw)=rows.next()?{
        if actual>=expected{return Err(Error::Storage);}
        let post=row(raw)?;
        if !id(&post.id) || !id(&post.author_id) || !instant(post.created_at)
            || !text(&post.text,8192) || post.text.trim().is_empty(){return Err(Error::Storage);}
        next(Item{id:post.id,username:post.author_id,create_time:post.created_at,content_desc:post.text})?;
        actual+=1;
    }
    if actual!=expected{return Err(Error::Storage);}
    Ok(())
}

pub(crate) fn read(store:&EventStore,account:&str,q:Query)->Result<Option<Vec<u8>>>{
    if q.format.is_some(){return Err(Error::Input);}
    let limit=q.limit.unwrap_or(50).clamp(1,200);
    catalog(store,account,&q,|db,filter,total,offset|{
        let mut items=Vec::new();
        visit(db,account,filter,limit,offset,total,|item|{items.push(item);Ok(())})?;
        let mut out=Capped::default();
        serde_json::to_writer(&mut out,&Page{mode:"detached-text-legacy-list",
            available:true,catalogued:true,account_id:account,accounts:[account],
            items,total,limit,offset}).map_err(|_|Error::TooLarge)?;
        Ok(out.bytes)
    })
}

pub(crate) fn export(store:&EventStore,account:&str,q:Query)->Result<Option<(super::ExportFormat,Vec<u8>)>>{
    let format=match q.format.as_deref() {
        None=>super::ExportFormat::Json,
        Some(value) if value.eq_ignore_ascii_case("json")=>super::ExportFormat::Json,
        Some(value) if value.eq_ignore_ascii_case("csv")=>super::ExportFormat::Csv,
        _=>return Err(Error::Input),
    };
    catalog(store,account,&q,|db,filter,total,offset|{
        let mut out=Capped::default();
        let write_limit=|_:std::io::Error|Error::TooLarge;
        let mut first=true;
        match format{
            super::ExportFormat::Json=>{
                // Pinned top-level export keys, without falsely attributing
                // this operator-supplied artifact to an upstream database.
                out.write_all(b"{\"available\":true,\"catalogued\":true,\"limit\":50,\"account_id\":")
                    .map_err(write_limit)?;
                serde_json::to_writer(&mut out,account).map_err(|_|Error::TooLarge)?;
                write!(&mut out,",\"total\":{total},\"offset\":{offset},\"source\":{{\"platform\":\"wechat\",\"scope\":\"detached_text\"}},\"items\":[")
                    .map_err(write_limit)?;
            }
            super::ExportFormat::Csv=>out.write_all(
                "来源平台,来源数据库,范围,时间,微信号,昵称,正文,类型,地点,媒体数,点赞,评论\r\n".as_bytes()).map_err(write_limit)?,
        }
        visit(db,account,filter,LIMIT,offset,total,|item|{
            match format{
                super::ExportFormat::Json=>{
                    if !first{out.write_all(b",").map_err(write_limit)?;}
                    first=false;
                    serde_json::to_writer(&mut out,&item).map_err(|_|Error::TooLarge)?;
                }
                super::ExportFormat::Csv=>{
                    let utc8=FixedOffset::east_opt(28_800).ok_or(Error::Storage)?;
                    let stamp=DateTime::<Utc>::from_timestamp(item.create_time,0)
                        .map(|value|value.with_timezone(&utc8))
                        .filter(|value|value.year()<=9999)
                        .map(|value|value.format("%Y-%m-%d %H:%M:%S").to_string())
                        .unwrap_or_default();
                    // Unknown imported attributes remain blank, not claimed
                    // to be absent upstream (e.g. zero media or no comments).
                    let cells=["wechat","","detached_text",stamp.as_str(),item.username.as_str(),"",
                        item.content_desc.as_str(),"","","","",""];
                    for (index,cell) in cells.into_iter().enumerate(){
                        if index!=0{out.write_all(b",").map_err(write_limit)?;}
                        csv_cell(&mut out,cell).map_err(write_limit)?;
                    }
                    out.write_all(b"\r\n").map_err(write_limit)?;
                }
            }
            Ok(())
        })?;
        if matches!(format,super::ExportFormat::Json){out.write_all(b"]}").map_err(write_limit)?;}
        Ok((format,out.bytes))
    })
}
