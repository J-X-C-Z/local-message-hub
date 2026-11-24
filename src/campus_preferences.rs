//! Offline visibility intent for observed Campus sources, not source configuration.
use crate::{campus,events::{EventError,EventStore}};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};

const MAX_REVISION:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_campus_source_preferences (
    profile TEXT NOT NULL, source_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('visible','archived')),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    PRIMARY KEY(profile,source_id)
) WITHOUT ROWID;
";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Conflict, Locked, Storage }
impl From<rusqlite::Error> for Error {fn from(_:rusqlite::Error)->Self{Self::Storage}}
impl From<EventError> for Error {
    fn from(e:EventError)->Self {
        if matches!(e,EventError::ProfileLocked){Self::Locked}else{Self::Storage}
    }
}
impl From<campus::Error> for Error {
    fn from(e:campus::Error)->Self {
        match e {
            campus::Error::Input=>Self::Input,
            campus::Error::Missing=>Self::Missing,
            _=>Self::Storage,
        }
    }
}
type Result<T>=std::result::Result<T,Error>;

#[derive(Clone,Copy,PartialEq,Eq,Deserialize,Serialize)]
#[serde(rename_all="snake_case")]
enum Visibility { Visible, Archived }
impl Visibility {
    fn wire(self)->&'static str {
        match self {Self::Visible=>"visible",Self::Archived=>"archived"}
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scope {pub profile:Option<String>}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub profile:Option<String>,
    limit:Option<u32>,
    offset:Option<u32>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub profile:Option<String>,
    expected_revision:i64,
    visibility:Visibility,
}

#[derive(Serialize)]
pub(crate) struct Preference {
    source_id:String,
    revision:i64,
    visibility:Visibility,
}
#[derive(Default,Serialize)]
pub(crate) struct Page {items:Vec<Preference>,total:i64}

pub(crate) fn installed(db:&Connection)->Result<bool> {
    let found:Option<String>=db.query_row(
        "SELECT type FROM sqlite_schema WHERE name COLLATE NOCASE='native_campus_source_preferences'",
        [],|r|r.get(0)).optional()?;
    match found.as_deref(){None=>Ok(false),Some("table")=>Ok(true),_=>Err(Error::Storage)}
}

fn check(profile:&str,selected:Option<&str>)->Result<()> {
    if !campus::slug(profile) || selected.is_some_and(|wanted| wanted!=profile){return Err(Error::Missing);}
    Ok(())
}

fn require_observed(db:&Connection,profile:&str,source_id:&str)->Result<()> {
    if !campus::ready(db)? {return Err(Error::Missing);}
    let observed:bool=db.query_row(
        "SELECT EXISTS(SELECT 1 FROM native_campus_articles
           WHERE profile COLLATE BINARY=?1 AND source_id COLLATE BINARY=?2)",
        (profile,source_id),|r|r.get(0))?;
    if observed{Ok(())}else{Err(Error::Missing)}
}

fn row(r:&rusqlite::Row<'_>)->rusqlite::Result<Preference> {
    let source_id:String=r.get(0)?;
    let revision:i64=r.get(1)?;
    let visibility=match (revision,r.get_ref(2)?.as_str()?) {
        (0,"visible")=>Visibility::Visible,
        (1..=MAX_REVISION,"visible")=>Visibility::Visible,
        (1..=MAX_REVISION,"archived")=>Visibility::Archived,
        _=>return Err(rusqlite::Error::InvalidQuery),
    };
    if !campus::slug(&source_id){return Err(rusqlite::Error::InvalidQuery);}
    Ok(Preference{source_id,revision,visibility})
}
fn default_for(source_id:&str)->Preference {
    Preference{source_id:source_id.to_owned(),revision:0,visibility:Visibility::Visible}
}
fn current(db:&Connection,profile:&str,source_id:&str)->Result<Preference> {
    Ok(db.query_row(
        "SELECT source_id,revision,visibility FROM native_campus_source_preferences
          WHERE profile COLLATE BINARY=?1 AND source_id COLLATE BINARY=?2",
        (profile,source_id),row).optional()?.unwrap_or_else(||default_for(source_id)))
}

pub(crate) fn get(store:&EventStore,profile:&str,source_id:&str,query:Scope)->Result<Preference> {
    check(profile,query.profile.as_deref())?;
    if !campus::slug(source_id){return Err(Error::Missing);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    require_observed(&tx,profile,source_id)?;
    if installed(&tx)?{current(&tx,profile,source_id)}else{Ok(default_for(source_id))}
}

pub(crate) fn list(store:&EventStore,profile:&str,q:Query)->Result<Page> {
    check(profile,q.profile.as_deref())?;
    let limit=q.limit.unwrap_or(25);
    let offset=q.offset.unwrap_or(0);
    if !(1..=50).contains(&limit) || offset>4096{return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !campus::ready(&tx)?{return Ok(Page::default());}
    let total=tx.query_row(
        "SELECT COUNT(DISTINCT source_id COLLATE BINARY) FROM native_campus_articles
            WHERE profile COLLATE BINARY=?1",
        [profile],|r|r.get(0))?;
    let select=if installed(&tx)? {
        "SELECT seen.source_id,COALESCE(p.revision,0),COALESCE(p.visibility,'visible')
         FROM seen LEFT JOIN native_campus_source_preferences p
           ON p.profile COLLATE BINARY=?1 AND p.source_id COLLATE BINARY=seen.source_id"
    } else {
        "SELECT seen.source_id,0,'visible' FROM seen"
    };
    let mut stmt=tx.prepare(&format!(
        "WITH seen AS (
            SELECT DISTINCT source_id COLLATE BINARY AS source_id FROM native_campus_articles
            WHERE profile COLLATE BINARY=?1
            ORDER BY source_id COLLATE BINARY LIMIT ?2 OFFSET ?3
         ) {select} ORDER BY seen.source_id COLLATE BINARY"
    ))?;
    let items=stmt.query_map((profile,limit,offset),row)?.collect::<rusqlite::Result<_>>()?;
    Ok(Page{items,total})
}

pub(crate) fn put(store:&EventStore,profile:&str,source_id:&str,change:Change)->Result<Preference> {
    check(profile,change.profile.as_deref())?;
    if !campus::slug(source_id){return Err(Error::Missing);}
    if !(0..=MAX_REVISION).contains(&change.expected_revision){return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    require_observed(&tx,profile,source_id)?;
    let table=installed(&tx)?;
    let old=if table{current(&tx,profile,source_id)?}else{default_for(source_id)};
    if old.revision!=change.expected_revision{return Err(Error::Conflict);}
    if old.visibility==change.visibility{return Ok(old);}
    let revision=old.revision.checked_add(1).filter(|r|*r<=MAX_REVISION).ok_or(Error::Conflict)?;
    if !table{tx.execute_batch(SCHEMA)?;}
    tx.execute(
        "INSERT INTO native_campus_source_preferences(profile,source_id,visibility,revision)
            VALUES(?1,?2,?3,?4) ON CONFLICT(profile,source_id) DO UPDATE SET
             visibility=excluded.visibility,revision=excluded.revision",
        params![profile,source_id,change.visibility.wire(),revision])?;
    tx.commit()?;
    Ok(Preference{source_id:source_id.to_owned(),revision,visibility:change.visibility})
}
