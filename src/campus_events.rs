//! Explicit, local grouping of already-indexed Campus articles.
use crate::{campus,events::{EventError,EventStore}};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};

const MAX_ARTICLES:usize=16;
const MAX_EVENTS:i64=1024;
const MAX_REVISION:i64=9_007_199_254_740_991;
const SCHEMA:&str="
CREATE TABLE native_campus_events (
  profile TEXT NOT NULL, id TEXT NOT NULL,
  article_count INTEGER NOT NULL CHECK(article_count BETWEEN 1 AND 16),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision BETWEEN 0 AND 9007199254740991),
  bookmarked INTEGER NOT NULL DEFAULT 0 CHECK(bookmarked IN (0,1)),
  read INTEGER NOT NULL DEFAULT 0 CHECK(read IN (0,1)),
  PRIMARY KEY(profile,id)
) WITHOUT ROWID;
CREATE TABLE native_campus_event_articles (
  profile TEXT NOT NULL,event_id TEXT NOT NULL,article_id TEXT NOT NULL,
  PRIMARY KEY(profile,event_id,article_id),
  FOREIGN KEY(profile,event_id) REFERENCES native_campus_events(profile,id),
  FOREIGN KEY(profile,article_id) REFERENCES native_campus_articles(profile,id)
) WITHOUT ROWID;
";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Stale, Quota, Locked, Storage }
impl From<rusqlite::Error> for Error {fn from(_:rusqlite::Error)->Self{Self::Storage}}
impl From<EventError> for Error {
    fn from(value:EventError)->Self{
        if matches!(value,EventError::ProfileLocked){Self::Locked}else{Self::Storage}
    }
}
impl From<campus::Error> for Error {fn from(_:campus::Error)->Self{Self::Storage}}
type Result<T>=std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Create {
    pub profile:Option<String>,
    article_ids:Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub profile:Option<String>,
    expected_revision:i64,
    bookmarked:Option<bool>,
    read:Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub profile:Option<String>,
    after:Option<String>,
    limit:Option<u32>,
    bookmarked:Option<bool>,
}

#[derive(Serialize)]
pub(crate) struct Summary {
    id:String,article_count:usize,revision:i64,bookmarked:bool,read:bool,
}

#[derive(Serialize)]
pub(crate) struct Event {
    #[serde(flatten)]
    current:Summary,
    article_ids:Vec<String>,
}

#[derive(Default,Serialize)]
pub(crate) struct Page {
    items:Vec<Summary>,
    next_after:Option<String>,
}

fn installed(db:&Connection)->Result<bool>{
    let (count,tables):(i64,i64)=db.query_row(
        "SELECT COUNT(*),COUNT(CASE WHEN type='table' AND name IN
        ('native_campus_events','native_campus_event_articles') THEN 1 END)
        FROM sqlite_schema WHERE name COLLATE NOCASE IN
        ('native_campus_events','native_campus_event_articles')",
        [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match(count,tables){(0,0)=>Ok(false),(2,2)=>Ok(true),_=>Err(Error::Storage)}
}

fn scope(profile:&str,selected:Option<&str>)->Result<()> {
    if !campus::slug(profile) || selected.is_some_and(|wanted|wanted!=profile) {return Err(Error::Missing);}
    Ok(())
}

fn row(r:&rusqlite::Row<'_>)->rusqlite::Result<Summary>{
    let result=Summary{id:r.get(0)?,article_count:r.get(1)?,revision:r.get(2)?,
        bookmarked:r.get(3)?,read:r.get(4)?};
    if !campus::item_id(&result.id) || !(1..=MAX_ARTICLES).contains(&result.article_count)
        || !(0..=MAX_REVISION).contains(&result.revision)
    {return Err(rusqlite::Error::InvalidQuery);}
    Ok(result)
}

fn find(db:&Connection,profile:&str,id:&str)->Result<Option<Summary>>{
    Ok(db.query_row(
        "SELECT id,article_count,revision,bookmarked,read FROM native_campus_events
         WHERE profile COLLATE BINARY=?1 AND id COLLATE BINARY=?2",(profile,id),row
    ).optional()?)
}

fn detail(db:&Connection,profile:&str,current:Summary)->Result<Event>{
    let mut stmt=db.prepare(
        "SELECT m.article_id FROM native_campus_event_articles m
         JOIN native_campus_articles a ON a.profile COLLATE BINARY=m.profile AND a.id COLLATE BINARY=m.article_id
         WHERE m.profile COLLATE BINARY=?1 AND m.event_id COLLATE BINARY=?2
         ORDER BY m.article_id COLLATE BINARY LIMIT ?3")?;
    let article_ids:Vec<String>=stmt.query_map((profile,&current.id,MAX_ARTICLES as i64+1),|row|row.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    if article_ids.len()!=current.article_count || article_ids.iter().any(|id|!campus::item_id(id)) {
        return Err(Error::Storage);
    }
    Ok(Event{current,article_ids})
}

fn id_for(profile:&str,ids:&[String])->String{
    // All member IDs have the same fixed hex width; the profile is explicitly
    // delimited. Request order is not part of the immutable set's identity.
    let mut sha=Sha256::new();
    sha.update(b"lmh-campus-manual-event-v1\0");
    sha.update(profile.as_bytes());
    sha.update([0_u8]);
    for id in ids {sha.update(id.as_bytes());}
    hex::encode(sha.finalize())
}

pub(crate) fn create(store:&EventStore,profile:&str,mut q:Create)->Result<Event>{
    scope(profile,q.profile.as_deref())?;
    if !(1..=MAX_ARTICLES).contains(&q.article_ids.len()) ||
        q.article_ids.iter().any(|id|!campus::item_id(id))
    {return Err(Error::Input);}
    q.article_ids.sort_unstable();
    if q.article_ids.windows(2).any(|pair|pair[0]==pair[1]){return Err(Error::Input);}
    let id=id_for(profile,&q.article_ids);
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !campus::ready(&tx)? {return Err(Error::Missing);}
    if !installed(&tx)? {tx.execute_batch(SCHEMA)?;}
    if let Some(existing)=find(&tx,profile,&id)? {
        let output=detail(&tx,profile,existing)?;
        if output.article_ids!=q.article_ids {return Err(Error::Storage);}
        return Ok(output);
    }
    let count:i64=tx.query_row(
        "SELECT COUNT(*) FROM native_campus_events WHERE profile COLLATE BINARY=?1",
        [profile],|r|r.get(0))?;
    if count>=MAX_EVENTS {return Err(Error::Quota);}
    {
        let mut eligible=tx.prepare(
            "SELECT EXISTS(SELECT 1 FROM native_campus_articles
             WHERE profile COLLATE BINARY=?1 AND id COLLATE BINARY=?2)")?;
        for member in &q.article_ids {
            if !eligible.query_row((profile,member),|r|r.get::<_,bool>(0))? {return Err(Error::Missing);}
        }
    }
    let article_count=q.article_ids.len();
    tx.execute("INSERT INTO native_campus_events(profile,id,article_count) VALUES(?1,?2,?3)",
        params![profile,id,article_count as i64])?;
    {
        let mut bind=tx.prepare("INSERT INTO native_campus_event_articles VALUES(?1,?2,?3)")?;
        for member in &q.article_ids {bind.execute((profile,&id,member))?;}
    }
    tx.commit()?;
    Ok(Event{current:Summary{id,article_count,revision:0,bookmarked:false,read:false},
        article_ids:q.article_ids})
}

pub(crate) fn list(store:&EventStore,profile:&str,q:Query)->Result<Page>{
    scope(profile,q.profile.as_deref())?;
    let limit=q.limit.unwrap_or(25);
    if !(1..=50).contains(&limit) || q.after.as_deref().is_some_and(|id|!campus::item_id(id)) {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !campus::ready(&tx)? || !installed(&tx)? {return Ok(Page::default());}
    let mut stmt=tx.prepare(
        "SELECT id,article_count,revision,bookmarked,read FROM native_campus_events
         WHERE profile COLLATE BINARY=?1 AND id COLLATE BINARY>?2
         AND (?3 IS NULL OR bookmarked=?3)
         ORDER BY id COLLATE BINARY LIMIT ?4")?;
    let mut items:Vec<_>=stmt.query_map(
        params![profile,q.after.as_deref().unwrap_or(""),q.bookmarked,limit+1],row
    )?.collect::<rusqlite::Result<_>>()?;
    let next_after=if items.len()>limit as usize {
        items.truncate(limit as usize);
        items.last().map(|item|item.id.clone())
    }else{None};
    Ok(Page{items,next_after})
}

pub(crate) fn get(store:&EventStore,profile:&str,id:&str)->Result<Event>{
    scope(profile,None)?;
    if !campus::item_id(id){return Err(Error::Missing);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !campus::ready(&tx)? || !installed(&tx)? {return Err(Error::Missing);}
    let current=find(&tx,profile,id)?.ok_or(Error::Missing)?;
    detail(&tx,profile,current)
}

pub(crate) fn set_state(store:&EventStore,profile:&str,id:&str,q:Change)->Result<Summary>{
    scope(profile,q.profile.as_deref())?;
    if !campus::item_id(id){return Err(Error::Missing);}
    if !(0..=MAX_REVISION).contains(&q.expected_revision) || (q.bookmarked.is_none() && q.read.is_none()) {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !campus::ready(&tx)? || !installed(&tx)? {return Err(Error::Missing);}
    let mut current=find(&tx,profile,id)?.ok_or(Error::Missing)?;
    if current.revision!=q.expected_revision{return Err(Error::Stale);}
    let bookmarked=q.bookmarked.unwrap_or(current.bookmarked);
    let read=q.read.unwrap_or(current.read);
    if current.bookmarked==bookmarked && current.read==read{return Ok(current);}
    let revision=current.revision.checked_add(1).filter(|r|*r<=MAX_REVISION).ok_or(Error::Stale)?;
    let n=tx.execute("UPDATE native_campus_events SET bookmarked=?3,read=?4,revision=?5
        WHERE profile COLLATE BINARY=?1 AND id COLLATE BINARY=?2 AND revision=?6",
        params![profile,id,bookmarked,read,revision,current.revision])?;
    if n!=1{return Err(Error::Stale);}
    tx.commit()?;
    current.bookmarked=bookmarked;
    current.read=read;
    current.revision=revision;
    Ok(current)
}
