//! Only operator-attested detached Moments text. No acquisition or association.
use crate::{events::{EventError, EventStore}, legacy_import::read_stable_source, export::{Capped,csv_cell}, store};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, io::Write, path::Path};

pub(crate) mod legacy;

const LIMIT: i64 = 4096;
const MAX_TIME: i64 = 253_402_300_799;
const FORMAT: &str = "wechat-moments-detached-text-v1";
const SCHEMA: &str = "
CREATE TABLE native_detached_moments_v1(
 account TEXT NOT NULL, id TEXT NOT NULL, author_id TEXT NOT NULL,
 created_at INTEGER NOT NULL, text TEXT NOT NULL, folded_text TEXT NOT NULL,
 PRIMARY KEY(account,id)) WITHOUT ROWID;
CREATE INDEX native_detached_moments_order_v1
 ON native_detached_moments_v1(account,created_at DESC,id);
CREATE TABLE native_detached_moments_receipts_v1(
 account TEXT NOT NULL, confirmation TEXT NOT NULL, created INTEGER NOT NULL, unchanged INTEGER NOT NULL,
 PRIMARY KEY(account,confirmation)) WITHOUT ROWID;";

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("invalid detached Moments scope, format or query")] Input,
    #[error("selected detached Moments file unavailable")] Source,
    #[error("target must be an ordinary native archive")] Archive,
    #[error("confirmation does not bind this exact file, archive and account")] Confirmation,
    #[error("detached Moments identity conflicts or catalog capacity reached")] Conflict,
    #[error("detached Moments storage unavailable")] Storage,
    #[error("detached Moments export exceeds its output bounds")] TooLarge,
}
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self { Self::Storage } }
impl From<EventError> for Error { fn from(_:EventError)->Self { Self::Storage } }
type Result<T> = std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope { format:String, account_id:String, posts:Vec<Post> }

#[derive(Deserialize,Serialize,PartialEq,Eq)]
#[serde(deny_unknown_fields)]
struct Post { id:String, author_id:String, created_at:i64, text:String }

fn id(value:&str)->bool {
    !value.is_empty() && value.len()<=256 && value.trim()==value
        && !value.chars().any(char::is_control) && !value.contains(['/','\\'])
}
fn text(value:&str,max:usize)->bool {
    value.len()<=max && !value.chars().any(|c|c.is_control()&&!matches!(c,'\r'|'\n'|'\t'))
}
fn instant(value:i64)->bool { (1..=MAX_TIME).contains(&value) }
fn hex64(value:&str)->bool {
    value.len()==64 && value.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}

fn installed(db:&Connection)->Result<bool> {
    let count:(i64,i64)=db.query_row(
        "SELECT count(*),count(CASE WHEN
         (name IN ('native_detached_moments_v1','native_detached_moments_receipts_v1') AND type='table')
         OR (name='native_detached_moments_order_v1' AND type='index'
             AND tbl_name='native_detached_moments_v1') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN ('native_detached_moments_v1',
          'native_detached_moments_receipts_v1','native_detached_moments_order_v1')",
         [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match count { (0,0)=>Ok(false),(3,3)=>Ok(true),_=>Err(Error::Storage) }
}

struct Plan { confirmation:String, posts:Vec<Post> }
fn plan(file:&Path,store:&EventStore,account:&str)->Result<Plan> {
    if !id(account) { return Err(Error::Input); }
    let bytes=read_stable_source(file,512*1024).map_err(|_|Error::Source)?;
    let value:Envelope=serde_json::from_slice(&bytes).map_err(|_|Error::Input)?;
    if value.format!=FORMAT || value.account_id!=account || !(1..=100).contains(&value.posts.len()) {
        return Err(Error::Input);
    }
    let mut seen=BTreeSet::new();
    for p in &value.posts {
        if !id(&p.id) || !id(&p.author_id) || !instant(p.created_at)
            || !text(&p.text,8192) || p.text.trim().is_empty() || !seen.insert(p.id.as_str()) {
            return Err(Error::Input);
        }
    }
    drop(seen);
    let binding=serde_json::to_vec(&(FORMAT,store.archive_id(),account,hex::encode(Sha256::digest(&bytes))))
        .map_err(|_|Error::Input)?;
    Ok(Plan{confirmation:hex::encode(Sha256::digest(binding)),posts:value.posts})
}

#[derive(Serialize)]
pub struct Preview { confirmation:String, rows:usize }
#[derive(Serialize)]
pub struct Receipt { batch_id:String, created:usize, unchanged:usize }

pub fn preview(file:&Path,directory:&Path,account:&str)->Result<Preview> {
    let store=EventStore::open(directory).map_err(|_|Error::Archive)?;
    let plan=plan(file,&store,account)?;
    Ok(Preview{confirmation:plan.confirmation,rows:plan.posts.len()})
}

pub fn commit(file:&Path,directory:&Path,account:&str,confirmation:&str)->Result<Receipt> {
    if !hex64(confirmation) { return Err(Error::Confirmation); }
    let store=EventStore::open(directory).map_err(|_|Error::Archive)?;
    let plan=plan(file,&store,account)?;
    if plan.confirmation!=confirmation { return Err(Error::Confirmation); }
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { tx.execute_batch(SCHEMA)?; }
    if let Some(prior)=tx.query_row("SELECT created,unchanged FROM native_detached_moments_receipts_v1
        WHERE account=? AND confirmation=?", (account,confirmation),
        |r|Ok(Receipt{batch_id:confirmation.to_owned(),created:r.get(0)?,unchanged:r.get(1)?})).optional()? {
        return Ok(prior);
    }
    let count:i64=tx.query_row("SELECT count(*) FROM native_detached_moments_v1 WHERE account=?",
        [account],|r|r.get(0))?;
    let receipts:i64=tx.query_row("SELECT count(*) FROM native_detached_moments_receipts_v1 WHERE account=?",
        [account],|r|r.get(0))?;
    if receipts>=LIMIT {return Err(Error::Conflict);}
    let mut result=Receipt{batch_id:confirmation.to_owned(),created:0,unchanged:0};
    for post in plan.posts {
        let existing=tx.query_row("SELECT id,author_id,created_at,text FROM native_detached_moments_v1
            WHERE account=? AND id=?", (account,&post.id),row).optional()?;
        if let Some(existing)=existing {
            if existing!=post {return Err(Error::Conflict);}
            result.unchanged+=1;
            continue;
        }
        if count+result.created as i64>=LIMIT {return Err(Error::Conflict);}
        tx.execute("INSERT INTO native_detached_moments_v1(account,id,author_id,created_at,text,folded_text)
            VALUES(?,?,?,?,?,?)",params![account,post.id,post.author_id,post.created_at,post.text,post.text.to_lowercase()])?;
        result.created+=1;
    }
    tx.execute("INSERT INTO native_detached_moments_receipts_v1 VALUES(?,?,?,?)",
        (account,confirmation,result.created,result.unchanged))?;
    tx.commit()?;
    Ok(result)
}

fn row(r:&rusqlite::Row<'_>)->rusqlite::Result<Post> {
    Ok(Post{id:r.get(0)?,author_id:r.get(1)?,created_at:r.get(2)?,text:r.get(3)?})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub account_id:Option<String>,
    author_id:Option<String>,q:Option<String>,start:Option<i64>,end:Option<i64>,limit:Option<u32>,offset:Option<u32>,
}
#[derive(Serialize)]
pub(crate) struct Page {
    mode:&'static str, account_id:String, total:i64, items:Vec<Post>, offset:u32, next_offset:Option<u32>,
}

const WHERE:&str="WHERE account COLLATE BINARY=?1 AND (?2='' OR author_id COLLATE BINARY=?2)
    AND (?3='' OR instr(folded_text,?3)>0) AND created_at>=?4 AND created_at<=?5";
const ORDER:&str="ORDER BY created_at DESC,id COLLATE BINARY";

struct Filter<'a> {
    author:&'a str,
    literal:String,
    start:i64,
    end:i64,
}
fn validate<'a>(
    account:&str,selector:Option<&str>,author:Option<&'a str>,keyword:Option<&str>,
    start:Option<i64>,end:Option<i64>,offset:u32,
)->Result<Filter<'a>>{
    let author=author.unwrap_or("");
    let keyword=keyword.unwrap_or("");
    if !id(account) || selector.is_some_and(|v|v!=account)
        || (!author.is_empty()&&!id(author)) || !text(keyword,256)
        || start.is_some_and(|v| !instant(v)) || end.is_some_and(|v| !instant(v))
        || start.zip(end).is_some_and(|(a,b)|a>b) || offset>LIMIT as u32 {
        return Err(Error::Input);
    }
    Ok(Filter{author,literal:keyword.to_lowercase(),start:start.unwrap_or(1),end:end.unwrap_or(MAX_TIME)})
}

pub(crate) fn read(store:&EventStore,account:&str,q:Query,export:bool)->Result<Page> {
    let (limit,offset)=(q.limit.unwrap_or(25),q.offset.unwrap_or(0));
    if !(1..=50).contains(&limit){return Err(Error::Input);}
    let f=validate(account,q.account_id.as_deref(),q.author_id.as_deref(),q.q.as_deref(),
        q.start,q.end,offset)?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let mut page=Page{mode:if export {"detached-text-export-page"}else{"detached-text-page"},
        account_id:account.to_owned(),total:0,items:Vec::new(),offset,next_offset:None};
    if !installed(&tx)? {return Ok(page);}
    page.total=tx.query_row(&format!("SELECT count(*) FROM native_detached_moments_v1 {WHERE}"),
        params![account,f.author,f.literal,f.start,f.end],|r|r.get(0))?;
    {
        let mut statement=tx.prepare(&format!("SELECT id,author_id,created_at,text
            FROM native_detached_moments_v1 {WHERE} {ORDER} LIMIT ?6 OFFSET ?7"))?;
        page.items=statement.query_map(params![account,f.author,f.literal,f.start,f.end,limit,offset],row)?
            .collect::<rusqlite::Result<_>>()?;
    }
    let next=offset+page.items.len() as u32;
    if i64::from(next)<page.total {page.next_offset=Some(next);}
    Ok(page)
}

#[derive(Clone,Copy,Default,Deserialize)]
#[serde(rename_all="lowercase")]
pub(crate) enum ExportFormat { #[default] Json,Csv }
impl ExportFormat{
    pub(crate) fn content_type(self)->&'static str{
        match self{Self::Json=>"application/json; charset=utf-8",Self::Csv=>"text/csv; charset=utf-8"}
    }
    pub(crate) fn disposition(self)->&'static str{
        match self{Self::Json=>"attachment; filename=hub-moments.json",Self::Csv=>"attachment; filename=hub-moments.csv"}
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ArchiveQuery {
    pub account_id:Option<String>,
    #[serde(alias="username")]
    author_id:Option<String>,
    q:Option<String>,
    start:Option<String>,
    end:Option<String>,
    offset:Option<u32>,
    #[serde(default)]
    pub format:ExportFormat,
}

fn export_bound(raw:Option<&str>,end:bool)->Result<Option<i64>>{
    let Some(raw)=raw else{return Ok(None);};
    if raw.len()>128{return Err(Error::Input);}
    store::parse_bound(raw,end).map_err(|_|Error::Input)
}

// This projection is used only while the SQLite row is live. Match the
// owned Post's field order; page, import and commit still use owned posts.
#[derive(Serialize)]
struct ExportPost<'a> { id:&'a str, author_id:&'a str, created_at:i64, text:&'a str }

fn export_post<'r,'stmt>(row:&'r rusqlite::Row<'stmt>)->Result<ExportPost<'r>> {
    Ok(ExportPost{
        id:row.get_ref(0)?.as_str().map_err(|_|Error::Storage)?,
        author_id:row.get_ref(1)?.as_str().map_err(|_|Error::Storage)?,
        created_at:row.get(2)?,
        text:row.get_ref(3)?.as_str().map_err(|_|Error::Storage)?,
    })
}

pub(crate) fn export_archive(store:&EventStore,account:&str,q:ArchiveQuery)->Result<Vec<u8>>{
    let offset=q.offset.unwrap_or(0);
    let f=validate(account,q.account_id.as_deref(),q.author_id.as_deref(),q.q.as_deref(),
        export_bound(q.start.as_deref(),false)?,export_bound(q.end.as_deref(),true)?,offset)?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;

    // Whole artifact buffered and hard-capped before any response is sent.
    let mut out=Capped::default();
    let write_limit=|_:std::io::Error|Error::TooLarge;
    match q.format{
        ExportFormat::Json=>out.write_all(b"{\"mode\":\"detached-text-export-archive\",\"items\":["),
        ExportFormat::Csv=>out.write_all(b"id,author_id,created_at,text\r\n"),
    }.map_err(write_limit)?;

    let mut count=0_i64;
    if installed(&tx)? {
        let mut statement=tx.prepare(&format!("SELECT id,author_id,created_at,
            CASE WHEN length(CAST(text AS BLOB))<=8192 THEN text END
            FROM native_detached_moments_v1 {WHERE} {ORDER} LIMIT ?6 OFFSET ?7"))?;
        let mut rows=statement.query(params![account,f.author,f.literal,f.start,f.end,LIMIT+1,offset])?;
        while let Some(raw)=rows.next()?{
            if count==LIMIT{return Err(Error::TooLarge);}
            let post=export_post(raw)?;
            if !id(&post.id) || !id(&post.author_id) || !instant(post.created_at)
                || !text(&post.text,8192) || post.text.trim().is_empty(){return Err(Error::Storage);}
            match q.format{
                ExportFormat::Json=>{
                    if count!=0 {out.write_all(b",").map_err(write_limit)?;}
                    serde_json::to_writer(&mut out,&post).map_err(|_|Error::TooLarge)?;
                }
                ExportFormat::Csv=>{
                    let stamp=post.created_at.to_string();
                    for (index,cell) in [post.id,post.author_id,stamp.as_str(),post.text]
                        .into_iter().enumerate()
                    {
                        if index!=0{out.write_all(b",").map_err(write_limit)?;}
                        csv_cell(&mut out,cell).map_err(write_limit)?;
                    }
                    out.write_all(b"\r\n").map_err(write_limit)?;
                }
            }
            count+=1;
        }
    }
    if matches!(q.format,ExportFormat::Json){
        writeln!(&mut out,"],\"rows\":{count},\"offset\":{offset}}}").map_err(write_limit)?;
    }
    drop(tx);
    store.verify()?;
    Ok(out.bytes)
}
