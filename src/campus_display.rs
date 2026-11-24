//! Opt-in display defaults for one exact Campus scope; no personal profile.
use crate::{campus, events::{EventError, EventStore}};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};

const MAX_REVISION:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_campus_display_v1 (
 profile TEXT PRIMARY KEY NOT NULL,
 category TEXT CHECK(category IS NULL OR length(category) BETWEEN 1 AND 64),
 page_size INTEGER NOT NULL CHECK(page_size IN (10,25,50)),
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991)
) WITHOUT ROWID;";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Conflict, Storage }
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self{Self::Storage} }
impl From<EventError> for Error { fn from(_:EventError)->Self{Self::Storage} }
impl From<campus::Error> for Error {
    fn from(e:campus::Error)->Self {match e {
        campus::Error::Input=>Self::Input,campus::Error::Missing=>Self::Missing,_=>Self::Storage
    }}
}
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scope { pub profile:Option<String> }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub profile:Option<String>,
    expected_revision:i64,
    default_category:Option<String>,
    page_size:u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FeedQuery {
    pub profile:Option<String>,
    offset:Option<u32>,
}

#[derive(Serialize)]
pub(crate) struct Display {
    profile:String,
    revision:i64,
    default_category:Option<String>,
    page_size:u32,
    fetch_supported:bool,
    models_supported:bool,
    scheduled_updates:bool,
}

#[derive(Serialize)]
pub(crate) struct Feed {
    display:Display,
    #[serde(flatten)]
    page:campus::Page,
}

fn scope(profile:&str,wanted:Option<&str>)->Result<()> {
    if !campus::slug(profile) || wanted.is_some_and(|value|value!=profile) {return Err(Error::Missing);}
    Ok(())
}
fn values(category:Option<&str>,limit:u32)->bool {
    category.is_none_or(campus::slug) && matches!(limit,10|25|50)
}
fn installed(db:&Connection)->Result<bool> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE='native_campus_display_v1'",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object{
        None=>Ok(false),
        Some((name,kind)) if name=="native_campus_display_v1"&&kind=="table"=>Ok(true),
        _=>Err(Error::Storage),
    }
}
fn display(profile:&str,revision:i64,default_category:Option<String>,page_size:u32)->Display {
    Display{profile:profile.to_owned(),revision,default_category,page_size,
        fetch_supported:false,models_supported:false,scheduled_updates:false}
}
fn load(db:&Connection,profile:&str)->Result<Display> {
    let row:Option<(i64,Option<String>,u32)>=if installed(db)? {
        db.query_row("SELECT revision,category,page_size FROM native_campus_display_v1
                      WHERE profile COLLATE BINARY=?", [profile],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?)))
            .optional()?
    } else { None };
    match row{
        None=>Ok(display(profile,0,None,25)),
        Some((revision,category,limit)) if (1..=MAX_REVISION).contains(&revision)
            && values(category.as_deref(),limit)=>Ok(display(profile,revision,category,limit)),
        _=>Err(Error::Storage),
    }
}

pub(crate) fn get(store:&EventStore,profile:&str,q:Scope)->Result<Display> {
    scope(profile,q.profile.as_deref())?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    load(&tx,profile)
}

pub(crate) fn put(store:&EventStore,profile:&str,q:Change)->Result<Display> {
    scope(profile,q.profile.as_deref())?;
    if !values(q.default_category.as_deref(),q.page_size) || !(0..=MAX_REVISION).contains(&q.expected_revision) {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let old=load(&tx,profile)?;
    if old.revision!=q.expected_revision {return Err(Error::Conflict);}
    if old.default_category==q.default_category && old.page_size==q.page_size {return Ok(old);}
    if let Some(category)=q.default_category.as_deref() {
        if !campus::ready(&tx)? {return Err(Error::Missing);}
        let known:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM native_campus_articles
            WHERE profile COLLATE BINARY=?1 AND category COLLATE BINARY=?2)",
            (profile,category),|r|r.get(0))?;
        if !known {return Err(Error::Missing);}
    }
    let revision=old.revision.checked_add(1).filter(|v|*v<=MAX_REVISION).ok_or(Error::Conflict)?;
    if !installed(&tx)? {tx.execute_batch(SCHEMA)?;}
    tx.execute("INSERT INTO native_campus_display_v1(profile,category,page_size,revision) VALUES(?,?,?,?)
        ON CONFLICT(profile) DO UPDATE SET category=excluded.category,
           page_size=excluded.page_size,revision=excluded.revision",
        params![profile,q.default_category,q.page_size,revision])?;
    tx.commit()?;
    Ok(display(profile,revision,q.default_category,q.page_size))
}

pub(crate) fn feed(store:&EventStore,profile:&str,q:FeedQuery)->Result<Feed> {
    scope(profile,q.profile.as_deref())?;
    let offset=q.offset.unwrap_or(0);
    if offset>4096 {return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let display=load(&tx,profile)?;
    let page=campus::list_display_visible(&tx,profile,display.default_category.as_deref(),display.page_size,offset)?;
    Ok(Feed{display,page})
}
