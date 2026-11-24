//! Exact-profile admission of detached imports, independent of display visibility.
use crate::{campus,events::{EventError,EventStore}};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};

const MAX_REV:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_campus_registration_v1 (
 profile TEXT NOT NULL, source_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
 PRIMARY KEY(profile,source_id)
) WITHOUT ROWID;";
#[derive(Clone,Copy)]
struct Entry{ id:&'static str, label:&'static str, category:&'static str }
// Public feed subset and manual article lane from pinned integrations/campus/catalog.py.
// Navigation, sign-in, and other portal entries are not import registrations.
const ENTRIES:&[Entry]=&[
    Entry{id:"sysu",label:"中山大学官网公告",category:"university"},
    Entry{id:"sysu-news",label:"中山大学新闻网",category:"university"},
    Entry{id:"teaching",label:"教务部通知通告",category:"teaching"},
    Entry{id:"seit-undergraduate",label:"电子与信息工程学院本科教务",category:"teaching"},
    Entry{id:"seit-student",label:"电子与信息工程学院学生通知与奖助",category:"student"},
    Entry{id:"seit-activities",label:"电子与信息工程学院学生活动",category:"competition"},
    Entry{id:"student-affairs",label:"党委学生工作部",category:"student"},
    Entry{id:"youth",label:"校团委",category:"competition"},
    Entry{id:"graduate",label:"研究生院",category:"graduate"},
    Entry{id:"library",label:"图书馆公告",category:"library"},
    Entry{id:"career",label:"就业信息网",category:"career"},
    Entry{id:"exchange",label:"国际合作与交流处",category:"exchange"},
    Entry{id:"logistics",label:"总务部",category:"services"},
    Entry{id:"security",label:"保卫处",category:"services"},
    Entry{id:"zhuhai",label:"珠海校区管委会",category:"campus"},
    Entry{id:"shenzhen",label:"深圳校区",category:"campus"},
    Entry{id:"wechat-manual",label:"微信公众号公开文章（按链接导入）",category:"wechat"},
];

#[derive(Debug)]
pub(crate) enum Error { Input,Missing,Conflict,Storage }
impl From<rusqlite::Error> for Error {fn from(_:rusqlite::Error)->Self{Self::Storage}}
impl From<EventError> for Error {fn from(_:EventError)->Self{Self::Storage}}
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scope{pub profile:Option<String>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change{pub profile:Option<String>,expected_revision:i64,enabled:bool}
#[derive(Serialize)]
pub(crate) struct Status{
    source_id:&'static str,label:&'static str,category:&'static str,
    revision:i64,enabled:Option<bool>,admits_detached_import:bool,fetch_supported:bool,
}
#[derive(Serialize)]
pub(crate) struct Catalog{items:Vec<Status>,mode:&'static str,fetch_supported:bool}

fn entry(id:&str)->Option<Entry>{ENTRIES.iter().copied().find(|e|e.id==id)}
fn validate_scope(profile:&str,requested:Option<&str>)->Result<()> {
    if campus::slug(profile)&&requested.is_none_or(|r|r==profile){Ok(())}else{Err(Error::Missing)}
}
fn installed(db:&Connection)->Result<bool>{
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name='native_campus_registration_v1' COLLATE NOCASE",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object{None=>Ok(false),Some((name,kind))if name=="native_campus_registration_v1"&&kind=="table"=>Ok(true),
        _=>Err(Error::Storage)}
}
fn state(db:&Connection,profile:&str,id:&str,ready:bool)->Result<Option<(bool,i64)>>{
    if !ready{return Ok(None);}
    let row:Option<(i64,i64)>=db.query_row("SELECT enabled,revision FROM native_campus_registration_v1
        WHERE profile COLLATE BINARY=? AND source_id COLLATE BINARY=?", (profile,id),
        |r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match row{
        None=>Ok(None),
        Some((enabled,revision)) if matches!(enabled,0|1)&&(1..=MAX_REV).contains(&revision)=>Ok(Some((enabled==1,revision))),
        _=>Err(Error::Storage),
    }
}
fn status(entry:Entry,setting:Option<(bool,i64)>)->Status{
    Status{source_id:entry.id,label:entry.label,category:entry.category,
        revision:setting.map_or(0,|(_,r)|r),enabled:setting.map(|(enabled,_)|enabled),
        admits_detached_import:setting.is_none_or(|(enabled,_)|enabled),fetch_supported:false}
}
pub(crate) fn list(store:&EventStore,profile:&str,q:Scope)->Result<Catalog>{
    validate_scope(profile,q.profile.as_deref())?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let ready=installed(&tx)?;
    let items=ENTRIES.iter().copied().map(|e|Ok(status(e,state(&tx,profile,e.id,ready)?)))
        .collect::<Result<Vec<_>>>()?;
    Ok(Catalog{items,mode:"detached-import-admission-only",fetch_supported:false})
}
pub(crate) fn set(store:&EventStore,profile:&str,id:&str,q:Change)->Result<Status>{
    validate_scope(profile,q.profile.as_deref())?;
    let e=entry(id).ok_or(Error::Missing)?;
    if !(0..=MAX_REV).contains(&q.expected_revision){return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let ready=installed(&tx)?;
    let old=state(&tx,profile,id,ready)?;
    let revision=old.map_or(0,|(_,v)|v);
    if q.expected_revision!=revision{return Err(Error::Conflict);}
    if old.is_some_and(|(enabled,_)|enabled==q.enabled){return Ok(status(e,old));}
    let next=revision.checked_add(1).filter(|r|*r<=MAX_REV).ok_or(Error::Conflict)?;
    if !ready{tx.execute_batch(SCHEMA)?;}
    tx.execute("INSERT INTO native_campus_registration_v1(profile,source_id,enabled,revision) VALUES(?,?,?,?)
        ON CONFLICT(profile,source_id) DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision",
        params![profile,id,q.enabled,next])?;
    tx.commit()?;
    Ok(status(e,Some((q.enabled,next))))
}

pub(crate) fn admit<'a>(db:&Connection,profile:&str,ids:impl IntoIterator<Item=&'a str>)
    ->std::result::Result<(),campus::Error>{
    let ready=installed(db).map_err(|_|campus::Error::Storage)?;
    if !ready{return Ok(());}
    for id in ids {
        if entry(id).is_some() && state(db,profile,id,true).map_err(|_|campus::Error::Storage)?
            .is_some_and(|(enabled,_)|!enabled){
            return Err(campus::Error::SourceBlocked);
        }
    }
    Ok(())
}
