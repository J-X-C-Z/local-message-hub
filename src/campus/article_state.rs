//! Revisioned flags on one imported article, never on inferred or manual groups.
use super::{Bookmark,Error,MAX_REVISION,Result,STATE_SCHEMA,load_bookmark,require_item,slug,state_ready};
use crate::events::EventStore;
use rusqlite::{Connection,OptionalExtension,TransactionBehavior};
use serde::{Deserialize,Deserializer,Serialize};

const SCHEMA:&str="
CREATE TABLE native_campus_article_read_v1 (
    profile TEXT NOT NULL,id TEXT NOT NULL,read_flag INTEGER NOT NULL CHECK(read_flag IN (0,1)),
    PRIMARY KEY(profile,id),
    FOREIGN KEY(profile,id) REFERENCES native_campus_bookmarks(profile,id)
) WITHOUT ROWID;";

/// Backward-compatible sparse state table. Existing bookmark revisions remain
/// the sole compare-and-set counter, including for a combined mutation.
pub(super) fn ready(db:&Connection)->Result<bool> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE='native_campus_article_read_v1'",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object.as_ref().map(|(n,t)|(n.as_str(),t.as_str())) {
        None=>Ok(false),Some(("native_campus_article_read_v1","table"))=>Ok(true),_=>Err(Error::Storage),
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub profile:Option<String>,
    expected_revision:i64,
    #[serde(default,deserialize_with="explicit_bool")]
    bookmarked:Option<bool>,
    #[serde(default,deserialize_with="explicit_bool")]
    pub read:Option<bool>,
}
fn explicit_bool<'de,D:Deserializer<'de>>(value:D)->std::result::Result<Option<bool>,D::Error>{
    bool::deserialize(value).map(Some)
}

#[derive(Serialize)]
pub(crate) struct ArticleState {
    pub bookmarked:bool,
    pub revision:i64,
    read:bool,
}
impl ArticleState {
    pub(crate) fn bookmark_only(&self)->Bookmark {
        Bookmark{bookmarked:self.bookmarked,revision:self.revision}
    }
}

fn load(db:&Connection,profile:&str,id:&str,bookmarks:bool,read:bool)->Result<ArticleState>{
    if read&&!bookmarks{return Err(Error::Storage);}
    let bookmark=if bookmarks{load_bookmark(db,profile,id)?}else{Bookmark::default()};
    let read_flag=if read {
        db.query_row("SELECT read_flag FROM native_campus_article_read_v1
            WHERE profile COLLATE BINARY=? AND id COLLATE BINARY=?",
            (profile,id),|r|r.get::<_,i64>(0)).optional()?
    }else{None};
    if read_flag.is_some_and(|v|!matches!(v,0|1)||bookmark.revision==0){return Err(Error::Storage);}
    Ok(ArticleState{bookmarked:bookmark.bookmarked,revision:bookmark.revision,read:read_flag==Some(1)})
}

pub(crate) fn get(store:&EventStore,profile:&str,id:&str)->Result<ArticleState>{
    if !slug(profile){return Err(Error::Missing);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    require_item(&tx,profile,id)?;
    load(&tx,profile,id,state_ready(&tx)?,ready(&tx)?)
}

pub(crate) fn set(store:&EventStore,profile:&str,id:&str,q:Change)->Result<ArticleState>{
    if !slug(profile)||q.profile.as_deref().is_some_and(|p|p!=profile){return Err(Error::Missing);}
    if !(0..=MAX_REVISION).contains(&q.expected_revision)
        ||(q.bookmarked.is_none()&&q.read.is_none()){return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    require_item(&tx,profile,id)?;
    let bookmarks=state_ready(&tx)?;
    let read_installed=ready(&tx)?;
    let old=load(&tx,profile,id,bookmarks,read_installed)?;
    if q.expected_revision!=old.revision{return Err(Error::StaleBookmark);}
    let bookmarked=q.bookmarked.unwrap_or(old.bookmarked);
    let read=q.read.unwrap_or(old.read);
    if bookmarked==old.bookmarked&&read==old.read{return Ok(old);}
    let revision=old.revision.checked_add(1).filter(|v|*v<=MAX_REVISION).ok_or(Error::Storage)?;
    if !bookmarks{tx.execute_batch(STATE_SCHEMA)?;}
    tx.execute("INSERT INTO native_campus_bookmarks(profile,id,revision,bookmarked) VALUES(?,?,?,?)
        ON CONFLICT(profile,id) DO UPDATE SET revision=excluded.revision,bookmarked=excluded.bookmarked",
        (profile,id,revision,bookmarked))?;
    if bookmarked!=old.bookmarked {
        tx.execute("INSERT INTO native_campus_bookmark_audit VALUES(?,?,?,?,unixepoch())",
            (profile,id,revision,bookmarked))?;
    }
    if read!=old.read {
        if !read_installed{tx.execute_batch(SCHEMA)?;}
        tx.execute("INSERT INTO native_campus_article_read_v1(profile,id,read_flag) VALUES(?,?,?)
           ON CONFLICT(profile,id) DO UPDATE SET read_flag=excluded.read_flag",
            (profile,id,read))?;
    }
    tx.commit()?;
    Ok(ArticleState{bookmarked,revision,read})
}
