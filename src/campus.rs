//! Detached public-campus text index; there is intentionally no collector.
use crate::{campus_wechat,events::EventStore,legacy_import::read_stable_source};
use chrono::{Datelike,NaiveDate};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Deserialize,Serialize};
use sha2::{Digest as _,Sha256};
use std::{collections::BTreeSet,path::Path};

pub(crate) mod briefing;
pub(crate) mod article_state;
pub mod enrichment;

const MAX_BYTES:usize=512*1024;
const MAX_PROFILE_ITEMS:i64=4_096;
const MAX_BATCH:usize=100;
const SCHEMA:&str="
 CREATE TABLE native_campus_articles (
 profile TEXT NOT NULL, id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
 published_at TEXT NOT NULL, summary TEXT NOT NULL, source_id TEXT NOT NULL,
 source_name TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
 search_text TEXT NOT NULL, digest BLOB NOT NULL, PRIMARY KEY(profile,id)
 ) WITHOUT ROWID;
 CREATE INDEX native_campus_date ON native_campus_articles(profile,published_at DESC,id);
 CREATE TABLE native_campus_receipts (
 profile TEXT NOT NULL, token TEXT NOT NULL, created INTEGER NOT NULL, unchanged INTEGER NOT NULL,
 PRIMARY KEY(profile,token)
 ) WITHOUT ROWID;
";

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("detached campus file unavailable or too large")] Source,
    #[error("invalid bounded detached campus input")] Input,
    #[error("campus profile or article unavailable")] Missing,
    #[error("target is not an initialized ordinary Rust archive")] Archive,
    #[error("confirmation does not match the exact campus preview")] Confirmation,
    #[error("campus input conflicts with an existing article or profile capacity")] Conflict,
    #[error("campus bookmark revision conflict")] StaleBookmark,
    #[error("source is disabled for new detached imports in this profile")] SourceBlocked,
    #[error("campus archive unavailable")] Storage,
}
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self { Self::Storage } }
impl From<crate::events::EventError> for Error { fn from(_:crate::events::EventError)->Self { Self::Storage } }
type Result<T>=std::result::Result<T,Error>;

pub(crate) fn slug(value:&str)->bool {
    (1..=64).contains(&value.len()) && value.as_bytes()[0].is_ascii_lowercase()
        && value.bytes().all(|b|b.is_ascii_lowercase() || b.is_ascii_digit() || b"-_".contains(&b))
}

fn text(value:&str,limit:usize,required:bool)->bool {
    value.len()<=limit && (!required || !value.trim().is_empty())
        && !value.chars().any(|c|c.is_control() && !matches!(c,'\r'|'\n'|'\t'))
}

fn article_url(value:&str)->Option<String> {
    if value.len()>1_024 || value.trim()!=value || value.chars().any(char::is_control)
        || !value.get(..8).is_some_and(|p|p.eq_ignore_ascii_case("https://"))
        || value.contains(['\\','?','@']) { return None; }
    let mut url=url::Url::parse(value).ok()?;
    let host=url.host_str()?;
    if url.scheme()!="https" || !(host=="sysu.edu.cn" || host.ends_with(".sysu.edu.cn"))
        || host.split('.').any(str::is_empty)
        || !url.username().is_empty() || url.password().is_some() || url.port().is_some()
        || url.query().is_some() || url.path()=="/" { return None; }
    url.set_fragment(None);
    Some(url.into())
}

fn known_date(value:&str)->bool {
    value.len()==10 && NaiveDate::parse_from_str(value,"%Y-%m-%d").ok().is_some_and(|d|
        d.year()>0 && d.format("%Y-%m-%d").to_string()==value)
}

fn sha(bytes:impl AsRef<[u8]>)->String { hex::encode(Sha256::digest(bytes)) }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document { version:u8,profile:String,articles:Vec<Detached> }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Detached {
    url:String,title:String,
    #[serde(default)] published_at:String,
    #[serde(default)] summary:String,
    source_id:String,source_name:String,category:String,
    #[serde(default)] content:String,
}

#[derive(Debug,Serialize)]
pub(crate) struct Article {
    id:String,url:String,title:String,published_at:String,summary:String,
    source_id:String,source_name:String,category:String,
    #[serde(skip_serializing_if="Option::is_none")] content:Option<String>,
}
impl Detached {
    fn validated(self)->Result<Article> {
        let (id,url)=if let Some(url)=article_url(&self.url) {
            if self.source_id==campus_wechat::SOURCE_ID { return Err(Error::Input); }
            (sha(&url),url)
        } else {
            let article=campus_wechat::normalize(&self.url).ok_or(Error::Input)?;
            if self.source_id!=campus_wechat::SOURCE_ID || self.source_name!=campus_wechat::SOURCE_NAME
                || self.category!=campus_wechat::CATEGORY { return Err(Error::Input); }
            (sha(article.key),article.url)
        };
        if !self.published_at.is_empty() && !known_date(&self.published_at) { return Err(Error::Input); }
        if !slug(&self.source_id) || !slug(&self.category) || !text(&self.title,512,true)
            || !text(&self.source_name,256,true) || !text(&self.summary,2_048,false)
            || !text(&self.content,16*1024,false) { return Err(Error::Input); }
        Ok(Article{id,url,title:self.title,published_at:self.published_at,summary:self.summary,
            source_id:self.source_id,source_name:self.source_name,category:self.category,content:Some(self.content)})
    }
}

#[derive(Debug,Serialize)]
pub struct Preview { confirmation:String, rows:usize }
#[derive(Debug,Serialize)]
pub struct Receipt { batch_id:String,created:usize,unchanged:usize }

fn prepare(store:&EventStore,source:&Path,profile:&str)->Result<(String,Vec<Article>)> {
    if !slug(profile) { return Err(Error::Input); }
    let bytes=read_stable_source(source,MAX_BYTES).map_err(|_|Error::Source)?;
    let document:Document=serde_json::from_slice(&bytes).map_err(|_|Error::Input)?;
    if document.version!=1 || document.profile!=profile || !(1..=MAX_BATCH).contains(&document.articles.len()) {
        return Err(Error::Input);
    }
    let mut found=BTreeSet::new();
    let mut list=Vec::with_capacity(document.articles.len());
    for raw in document.articles {
        let item=raw.validated()?;
        if !found.insert(item.id.clone()) { return Err(Error::Input); }
        list.push(item);
    }
    let binding=serde_json::to_vec(&("lmh-campus-import-v1",store.archive_id(),profile,sha(bytes))).map_err(|_|Error::Input)?;
    Ok((sha(binding),list))
}
pub(crate) fn ready(sql:&Connection)->Result<bool> {
    let (objects,matching):(i64,i64)=sql.query_row(
        "SELECT count(*),count(CASE WHEN (name IN ('native_campus_articles','native_campus_receipts') AND type='table')
         OR (name='native_campus_date' AND type='index') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
         ('native_campus_articles','native_campus_receipts','native_campus_date')",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match(objects,matching){(0,0)=>Ok(false),(3,3)=>Ok(true),_=>Err(Error::Storage)}
}
pub fn preview(source:&Path,archive:&Path,profile:&str)->Result<Preview> {
    let store=EventStore::open(archive).map_err(|_|Error::Archive)?;
    let (confirmation,list)=prepare(&store,source,profile)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    crate::campus_registration::admit(&tx,profile,list.iter().map(|a|a.source_id.as_str()))?;
    Ok(Preview{confirmation,rows:list.len()})
}

fn commit_items(store:&EventStore,profile:&str,batch_id:String,list:Vec<Article>)->Result<Receipt> {
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !ready(&tx)? { tx.execute_batch(SCHEMA)?; }
    if let Some((created,unchanged))=tx.query_row(
        "SELECT created,unchanged FROM native_campus_receipts WHERE profile=? AND token=?",
        (profile,&batch_id),|r|Ok((r.get(0)?,r.get(1)?))).optional()? {
        return Ok(Receipt{batch_id,created,unchanged});
    }
    crate::campus_registration::admit(&tx,profile,list.iter().map(|a|a.source_id.as_str()))?;
    let count:i64=tx.query_row("SELECT count(*) FROM native_campus_articles WHERE profile=?",[profile],|r|r.get(0))?;
    let mut created=0;
    let mut unchanged=0;
    for a in list {
        let encoded=serde_json::to_vec(&a).map_err(|_|Error::Input)?;
        let digest=Sha256::digest(encoded);
        if let Some(old)=tx.query_row("SELECT digest FROM native_campus_articles WHERE profile=? AND id=?",
            (profile,&a.id),|r|r.get::<_,[u8;32]>(0)).optional()? {
            if old!=digest[..] { return Err(Error::Conflict); }
            unchanged+=1;continue;
        }
        if count+created as i64>=MAX_PROFILE_ITEMS { return Err(Error::Conflict); }
        let mut search=format!("{} {} {} {}",a.title,a.summary,a.content.as_deref().unwrap_or_default(),a.source_name);
        // Only detached institutional text with an entirely ASCII search
        // projection can reuse its buffer. Preserve provider and Unicode paths.
        if a.source_id != campus_wechat::SOURCE_ID && search.is_ascii() {
            search.make_ascii_lowercase();
        } else {
            search=search.to_lowercase();
        }
        tx.execute("INSERT INTO native_campus_articles
          (profile,id,url,title,published_at,summary,source_id,source_name,category,content,search_text,digest)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          params![profile,a.id,a.url,a.title,a.published_at,a.summary,a.source_id,a.source_name,a.category,
              a.content.unwrap_or_default(),search,digest.as_slice()])?;
        created+=1;
    }
    tx.execute("INSERT INTO native_campus_receipts VALUES(?,?,?,?)",(profile,&batch_id,created,unchanged))?;
    tx.commit()?;
    Ok(Receipt{batch_id,created,unchanged})
}
pub fn commit(source:&Path,archive:&Path,profile:&str,confirmation:&str)->Result<Receipt> {
    let store=EventStore::open(archive).map_err(|_|Error::Archive)?;
    let (batch_id,list)=prepare(&store,source,profile)?;
    if batch_id!=confirmation { return Err(Error::Confirmation); }
    commit_items(&store,profile,batch_id,list)
}

#[derive(Default,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub profile:Option<String>,
    q:Option<String>,category:Option<String>,source_id:Option<String>,limit:Option<u32>,offset:Option<u32>,
}
impl Query {
    fn validated(self)->Result<(String,String,String,u32,u32)> {
        let q=self.q.unwrap_or_default();
        let category=self.category.unwrap_or_default();
        let source=self.source_id.unwrap_or_default();
        let limit=self.limit.unwrap_or(25);
        let offset=self.offset.unwrap_or(0);
        if !text(&q,512,false) || (!category.is_empty() && !slug(&category))
            || (!source.is_empty() && !slug(&source)) || !(1..=50).contains(&limit)
            || offset>MAX_PROFILE_ITEMS as u32 { return Err(Error::Input); }
        Ok((q.to_lowercase(),category,source,limit,offset))
    }
}
#[derive(Default,Serialize)]
pub(crate) struct Page { items:Vec<Article>,total:i64 }

fn row(r:&rusqlite::Row<'_>,detail:bool)->rusqlite::Result<Article> {
    Ok(Article{id:r.get(0)?,url:r.get(1)?,title:r.get(2)?,published_at:r.get(3)?,summary:r.get(4)?,
        source_id:r.get(5)?,source_name:r.get(6)?,category:r.get(7)?,content:if detail {Some(r.get(8)?)} else {None}})
}
pub(crate) fn list(store:&EventStore,profile:&str,query:Query)->Result<Page> {
    list_inner(store,profile,query,false)
}

pub(crate) fn list_visible(store:&EventStore,profile:&str,query:Query)->Result<Page> {
    list_inner(store,profile,query,true)
}

fn list_inner(store:&EventStore,profile:&str,query:Query,visible_only:bool)->Result<Page> {
    if visible_only && (!slug(profile) || query.profile.as_deref().is_some_and(|requested|requested!=profile)) {
        return Err(Error::Missing);
    }
    let filters=query.validated()?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    list_in(&tx,profile,filters,visible_only)
}

// The display feed loads its saved defaults and visible page in one snapshot.
pub(crate) fn list_display_visible(db:&Connection,profile:&str,category:Option<&str>,
    limit:u32,offset:u32)->Result<Page> {
    if !slug(profile) {return Err(Error::Missing);}
    let filters=Query{profile:None,q:None,category:category.map(str::to_owned),
        source_id:None,limit:Some(limit),offset:Some(offset)}.validated()?;
    list_in(db,profile,filters,true)
}

fn list_in(tx:&Connection,profile:&str,(q,category,source,limit,offset):(String,String,String,u32,u32),
    visible_only:bool)->Result<Page> {
    if !ready(tx)? { return Ok(Page::default()); }
    let visibility=if visible_only && crate::campus_preferences::installed(tx).map_err(|_|Error::Storage)? {
        "AND NOT EXISTS(SELECT 1 FROM native_campus_source_preferences p
            WHERE p.profile COLLATE BINARY=a.profile AND p.source_id COLLATE BINARY=a.source_id
            AND p.visibility='archived')"
    } else { "" };
    let filters=format!("WHERE a.profile=?1 AND (?2='' OR a.category=?2) AND (?3='' OR a.source_id=?3)
        AND (?4='' OR instr(a.search_text,?4)>0) {visibility}");
    let total=tx.query_row(&format!("SELECT count(*) FROM native_campus_articles a {filters}"),
        (&profile,&category,&source,&q),|r|r.get(0))?;
    let mut sql=tx.prepare(&format!("SELECT a.id,a.url,a.title,a.published_at,a.summary,a.source_id,a.source_name,a.category
        FROM native_campus_articles a {filters} ORDER BY a.published_at DESC,a.id LIMIT ?5 OFFSET ?6"))?;
    let items=sql.query_map((&profile,&category,&source,&q,limit,offset),|r|row(r,false))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(Page{items,total})
}
pub(crate) fn get(store:&EventStore,profile:&str,id:&str)->Result<Article> {
    if !item_id(id) { return Err(Error::Missing); }
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !ready(&tx)? { return Err(Error::Missing); }
    tx.query_row("SELECT id,url,title,published_at,summary,source_id,source_name,category,content
         FROM native_campus_articles WHERE profile=? AND id=?",(profile,id),|r|row(r,true)).optional()?.ok_or(Error::Missing)
}


const STATE_SCHEMA:&str="
 CREATE TABLE native_campus_bookmarks (
    profile TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    bookmarked INTEGER NOT NULL CHECK(bookmarked IN (0,1)),PRIMARY KEY(profile,id),
    FOREIGN KEY(profile,id) REFERENCES native_campus_articles(profile,id)
 ) WITHOUT ROWID;
 CREATE TABLE native_campus_bookmark_audit (
    profile TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,
    bookmarked INTEGER NOT NULL CHECK(bookmarked IN (0,1)),changed_at INTEGER NOT NULL,
    PRIMARY KEY(profile,id,revision),FOREIGN KEY(profile,id) REFERENCES native_campus_articles(profile,id)
 ) WITHOUT ROWID;
";
const MAX_REVISION:i64=9_007_199_254_740_991;

fn state_ready(sql:&Connection)->Result<bool> {
    let (count,tables):(i64,i64)=sql.query_row(
        "SELECT count(*),count(CASE WHEN type='table' AND name IN
         ('native_campus_bookmarks','native_campus_bookmark_audit') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
         ('native_campus_bookmarks','native_campus_bookmark_audit')",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match(count,tables){(0,0)=>Ok(false),(2,2)=>Ok(true),_=>Err(Error::Storage)}
}

pub(crate) fn item_id(id:&str)->bool {
    id.len()==64 && id.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn require_item(db:&Connection,profile:&str,id:&str)->Result<()> {
    if !item_id(id) || !ready(db)? { return Err(Error::Missing); }
    let exists:bool=db.query_row(
        "SELECT EXISTS(SELECT 1 FROM native_campus_articles WHERE profile=? AND id=?)",
        (profile,id),|r|r.get(0))?;
    if !exists { return Err(Error::Missing); }
    Ok(())
}

#[derive(Debug,Default,PartialEq,Eq,Serialize)]
pub(crate) struct Bookmark { bookmarked:bool,revision:i64 }
fn load_bookmark(sql:&Connection,profile:&str,id:&str)->Result<Bookmark> {
    Ok(sql.query_row("SELECT bookmarked,revision FROM native_campus_bookmarks WHERE profile=? AND id=?",
        (profile,id), |r|Ok(Bookmark{bookmarked:r.get(0)?,revision:r.get(1)?})).optional()?.unwrap_or_default())
}
pub(crate) fn bookmark(store:&EventStore,profile:&str,id:&str)->Result<Bookmark> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    require_item(&tx,profile,id)?;
    if state_ready(&tx)? { load_bookmark(&tx,profile,id) } else { Ok(Bookmark::default()) }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BookmarkChange {
    pub profile:Option<String>,expected_revision:i64,bookmarked:bool,
}
pub(crate) fn set_bookmark(store:&EventStore,profile:&str,id:&str,change:BookmarkChange)->Result<Bookmark> {
    if !(0..=MAX_REVISION).contains(&change.expected_revision) { return Err(Error::Input); }
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    require_item(&tx,profile,id)?;
    let installed=state_ready(&tx)?;
    let old=if installed {load_bookmark(&tx,profile,id)?}else{Bookmark::default()};
    if change.expected_revision!=old.revision { return Err(Error::StaleBookmark); }
    if change.bookmarked==old.bookmarked { return Ok(old); }
    if !installed { tx.execute_batch(STATE_SCHEMA)?; }
    let revision=old.revision.checked_add(1).filter(|v|*v<=MAX_REVISION).ok_or(Error::Storage)?;
    tx.execute("INSERT INTO native_campus_bookmarks(profile,id,revision,bookmarked) VALUES(?,?,?,?)
        ON CONFLICT(profile,id) DO UPDATE SET revision=excluded.revision,bookmarked=excluded.bookmarked",
        (profile,id,revision,change.bookmarked))?;
    tx.execute("INSERT INTO native_campus_bookmark_audit VALUES(?,?,?,?,unixepoch())",
        (profile,id,revision,change.bookmarked))?;
    tx.commit()?;
    Ok(Bookmark{revision,bookmarked:change.bookmarked})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BookmarkQuery {
    pub profile:Option<String>,limit:Option<u32>,offset:Option<u32>,
}
#[derive(Serialize)]
struct BookmarkedArticle {
    #[serde(flatten)] article:Article,
    revision:i64,
}
#[derive(Default,Serialize)]
pub(crate) struct Bookmarks { items:Vec<BookmarkedArticle>,total:i64 }

pub(crate) fn bookmarks(store:&EventStore,profile:&str,query:BookmarkQuery)->Result<Bookmarks> {
    let limit=query.limit.unwrap_or(25);
    let offset=query.offset.unwrap_or(0);
    if !(1..=50).contains(&limit) || offset>MAX_PROFILE_ITEMS as u32 { return Err(Error::Input); }
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !ready(&tx)? || !state_ready(&tx)? { return Ok(Bookmarks::default()); }
    let total=tx.query_row("SELECT count(*) FROM native_campus_bookmarks WHERE profile=? AND bookmarked=1",
        [profile],|r|r.get(0))?;
    let mut stmt=tx.prepare("SELECT a.id,a.url,a.title,a.published_at,a.summary,a.source_id,a.source_name,a.category,b.revision
        FROM native_campus_articles a JOIN native_campus_bookmarks b ON a.profile=b.profile AND a.id=b.id
        WHERE a.profile=? AND b.bookmarked=1 ORDER BY a.published_at DESC,a.id LIMIT ? OFFSET ?")?;
    let items=stmt.query_map((profile,limit,offset),|r|Ok(BookmarkedArticle{article:row(r,false)?,revision:r.get(8)?}))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(Bookmarks{items,total})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DayQuery { pub profile:Option<String>,pub date:String,pub limit:Option<u32> }
#[derive(Serialize)]
struct CategoryCount { category:String,count:i64 }
#[derive(Serialize)]
pub(crate) struct Digest { date:String,total:i64,items:Vec<Article>,category_counts:Vec<CategoryCount> }

pub(crate) fn digest(store:&EventStore,profile:&str,query:DayQuery)->Result<Digest> {
    let date=query.date;
    let limit=query.limit.unwrap_or(25);
    if !known_date(&date) || !(1..=50).contains(&limit) { return Err(Error::Input); }
    let mut result=Digest{date,total:0,items:vec![],category_counts:vec![]};
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if !ready(&tx)? { return Ok(result); }
    let mut categories=tx.prepare("SELECT category,count(*) FROM native_campus_articles
        WHERE profile=? AND published_at=? GROUP BY category ORDER BY category")?;
    result.category_counts=categories.query_map((profile,&result.date),
        |r|Ok(CategoryCount{category:r.get(0)?,count:r.get(1)?}))?.collect::<rusqlite::Result<_>>()?;
    result.total=result.category_counts.iter().map(|category|category.count).sum();
    let mut articles=tx.prepare("SELECT id,url,title,published_at,summary,source_id,source_name,category
         FROM native_campus_articles WHERE profile=? AND published_at=? ORDER BY id LIMIT ?")?;
    result.items=articles.query_map((profile,&result.date,limit),|r|row(r,false))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn example(url:&str,date:&str,title:&str)->Detached {
        serde_json::from_value(json!({"url":url,"title":title,"published_at":date,
            "source_id":"synthetic","source_name":"Archive","category":"teaching","content":"private detail"})).unwrap()
    }

    #[test]
    fn official_https_path_only_canonicalization_and_no_fabricated_date() {
        let item=example("HTTPS://JWB.SYSU.EDU.CN/info/99.htm#part","","标题").validated().unwrap();
        assert_eq!(item.url,"https://jwb.sysu.edu.cn/info/99.htm");
        assert_eq!(item.id,sha(&item.url));
        assert_eq!(item.published_at,"");
        for invalid in ["https://sysu.edu.cn.evil.invalid/info/1","https:/sysu.edu.cn/info/1",
            "https://.sysu.edu.cn/info/1","http://jwb.sysu.edu.cn/info/1","https://user@sysu.edu.cn/info/1",
            "https://sysu.edu.cn:89/info/1","https://sysu.edu.cn/info/1?ticket=x","https://sysu.edu.cn/"] {
            assert!(article_url(invalid).is_none(),"{invalid}");
        }
        assert!(example("https://www.sysu.edu.cn/info/1","2025-02-29","x").validated().is_err());
    }

    #[test]
    fn immutable_conflict_rolls_back_before_own_or_other_profile_reads() {
        let dir=tempfile::tempdir().unwrap();
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(dir.path(),std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let store=EventStore::init(dir.path()).unwrap();
        let make=|id:&str,title|example(&format!("https://www.sysu.edu.cn/info/{id}"),"2025-01-02",title).validated().unwrap();
        let first=make("1","First");
        let id=first.id.clone();
        commit_items(&store,"local","key1".into(),vec![first]).unwrap();
        assert!(matches!(commit_items(&store,"local","conflict".into(),
            vec![make("2","not persisted"),make("1","conflicting")]),Err(Error::Conflict)));
        let page=list(&store,"local",Query::default()).unwrap();
        assert_eq!(page.total,1);
        assert!(page.items[0].content.is_none());
        assert_eq!(get(&store,"local",&id).unwrap().content.as_deref(),Some("private detail"));
        assert!(matches!(get(&store,"other",&id),Err(Error::Missing)));
    }

    fn data_fixture()->(tempfile::TempDir,EventStore,String) {
        let dir=tempfile::tempdir().unwrap();
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(dir.path(),std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let store=EventStore::init(dir.path()).unwrap();
        let item=example("https://www.sysu.edu.cn/info/1","2025-01-02","one").validated().unwrap();
        let id=item.id.clone();
        let other=example("https://www.sysu.edu.cn/info/1","2025-01-02","foreign").validated().unwrap();
        let undated=example("https://www.sysu.edu.cn/info/2","","undated").validated().unwrap();
        commit_items(&store,"local","one".into(),vec![item,undated]).unwrap();
        commit_items(&store,"peer","two".into(),vec![other]).unwrap();
        (dir,store,id)
    }
    fn change(expected_revision:i64,bookmarked:bool)->BookmarkChange {
        BookmarkChange{profile:None,expected_revision,bookmarked}
    }

    #[test]
    fn bookmark_cas_noop_migration_scope_and_tombstone() {
        let (dir,store,id)=data_fixture();
        assert_eq!(bookmark(&store,"local",&id).unwrap(),Bookmark::default());
        assert_eq!(set_bookmark(&store,"local",&id,change(0,false)).unwrap(),Bookmark::default());
        assert!(!state_ready(&store.read_connection().unwrap()).unwrap());
        let first=set_bookmark(&store,"local",&id,change(0,true)).unwrap();
        assert_eq!(first,Bookmark{bookmarked:true,revision:1});
        assert_eq!(bookmark(&store,"peer",&id).unwrap(),Bookmark::default());
        assert_eq!(set_bookmark(&store,"local",&id,change(1,true)).unwrap(),first);
        let cleared=set_bookmark(&store,"local",&id,change(1,false)).unwrap();
        assert_eq!(cleared,Bookmark{bookmarked:false,revision:2});
        let reopened=EventStore::open(dir.path()).unwrap();
        assert_eq!(bookmark(&reopened,"local",&id).unwrap(),cleared);
        assert!(matches!(set_bookmark(&reopened,"local",&id,change(0,true)),Err(Error::StaleBookmark)));
    }

    #[test]
    fn late_bookmark_audit_failure_is_atomic() {
        let (_dir,store,id)=data_fixture();
        set_bookmark(&store,"local",&id,change(0,true)).unwrap();
        let db=store.write_connection().unwrap();
        db.execute_batch("CREATE TRIGGER fail_local_audit BEFORE INSERT ON native_campus_bookmark_audit
            BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
        assert!(matches!(set_bookmark(&store,"local",&id,change(1,false)),Err(Error::Storage)));
        assert_eq!(bookmark(&store,"local",&id).unwrap(),Bookmark{bookmarked:true,revision:1});
        let count:i64=db.query_row("SELECT count(*) FROM native_campus_bookmark_audit",[],|r|r.get(0)).unwrap();
        assert_eq!(count,1);
    }

    #[test]
    fn digest_is_exact_day_bounded_and_does_not_use_body_as_summary() {
        let (_dir,store,_)=data_fixture();
        let query=|date:&str|DayQuery{profile:None,date:date.into(),limit:Some(1)};
        let result=digest(&store,"local",query("2025-01-02")).unwrap();
        assert_eq!(result.total,1);
        assert_eq!(result.items[0].title,"one");
        assert_eq!(result.items[0].summary,"");
        assert!(result.items[0].content.is_none());
        assert_eq!(digest(&store,"local",query("2025-01-03")).unwrap().total,0);
        assert!(matches!(digest(&store,"local",query("")),Err(Error::Input)));
    }

    #[test]
    fn no_profile_or_unknown_nested_input_accepted() {
        for invalid in ["","Profile","scope/escape","测试","p q","../scope"] { assert!(!slug(invalid)); }
        assert!(serde_json::from_value::<Document>(json!({"version":1,"profile":"allowed","articles":[],
             "private_feed":"disabled"})).is_err());
        assert!(serde_json::from_value::<Detached>(json!({"url":"https://sysu.edu.cn/1","title":"t",
             "source_id":"source","source_name":"name","category":"test","fetch":true})).is_err());
    }
}
