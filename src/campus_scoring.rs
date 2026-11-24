//! Opt-in deterministic ranking over local public categories and substantive imported summaries.
//! Not the old model pipeline, scheduler, or student-profile evaluator.
use crate::{campus, campus_preferences, events::{EventError, EventStore}};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use sha2::{Digest,Sha256};

const MAX_REVISION: i64 = 9_007_199_254_740_991;
const MAX_PRIORITIES: usize = 16;
const POLICY: &str = "local-topic-weight-v2";
const SCHEMA: &str = "
CREATE TABLE native_campus_scoring_v1(
    profile TEXT PRIMARY KEY NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    priorities TEXT NOT NULL CHECK(length(CAST(priorities AS BLOB)) BETWEEN 2 AND 2048),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991)
) WITHOUT ROWID;";

#[derive(Debug)]
pub(crate) enum Error { Input, Missing, Conflict, Storage }
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self { Self::Storage } }
impl From<EventError> for Error { fn from(_:EventError)->Self { Self::Storage } }
impl From<campus::Error> for Error { fn from(_:campus::Error)->Self { Self::Storage } }
impl From<campus_preferences::Error> for Error {
    fn from(_:campus_preferences::Error)->Self { Self::Storage }
}
type Result<T> = std::result::Result<T,Error>;

#[derive(Clone,PartialEq,Eq,Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Priority { category:String, weight:u8 }

#[derive(Default,Serialize)]
struct Config { enabled:bool, priorities:Vec<Priority>, revision:i64 }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scope { pub profile:Option<String> }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change {
    pub profile:Option<String>,
    expected_revision:i64,
    enabled:bool,
    priorities:Vec<Priority>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub profile:Option<String>,
    source_id:Option<String>,
    limit:Option<u32>,
    offset:Option<u32>,
}

#[derive(Default,Serialize)]
struct Counts {
    total_visible:i64,
    scored:i64,
    unprioritized:i64,
    insufficient_summary:i64,
    disabled:i64,
}

#[derive(Serialize)]
pub(crate) struct Status {
    profile:String,
    policy:&'static str,
    settings:Config,
    counts:Counts,
    remote_models:bool,
    running:bool,
}

#[derive(Serialize)]
struct Item {
    id:String,
    category:String,
    source_id:String,
    title:String,
    published_at:String,
    score:u8,
    reason:&'static str,
}

#[derive(Serialize)]
pub(crate) struct Page {
    profile:String,
    policy:&'static str,
    settings_revision:i64,
    items:Vec<Item>,
    total:i64,
}

fn scope(profile:&str,requested:Option<&str>)->Result<()> {
    if !campus::slug(profile) || requested.is_some_and(|value|value!=profile) { return Err(Error::Missing); }
    Ok(())
}

fn canonicalize(enabled:bool,values:&mut [Priority])->Result<()> {
    if values.len()>MAX_PRIORITIES || (enabled && values.is_empty()) { return Err(Error::Input); }
    let mut unique=BTreeSet::new();
    for item in values.iter() {
        if !campus::slug(&item.category) || !(1..=5).contains(&item.weight)
            || !unique.insert(item.category.as_str()) { return Err(Error::Input); }
    }
    drop(unique);
    values.sort_unstable_by(|a,b|a.category.cmp(&b.category));
    Ok(())
}

fn installed(db:&Connection)->Result<bool> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name COLLATE NOCASE='native_campus_scoring_v1'",[],
        |r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object {
        None=>Ok(false),
        Some((name,kind)) if name=="native_campus_scoring_v1" && kind=="table"=>Ok(true),
        _=>Err(Error::Storage),
    }
}

fn config(db:&Connection,profile:&str)->Result<Config> {
    if !installed(db)? { return Ok(Config::default()); }
    let saved:Option<(i64,String,i64)>=db.query_row(
        "SELECT enabled,priorities,revision FROM native_campus_scoring_v1 WHERE profile COLLATE BINARY=?",
        [profile],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
    let Some((enabled,data,revision))=saved else { return Ok(Config::default()); };
    if !matches!(enabled,0|1) || !(1..=MAX_REVISION).contains(&revision) || data.len()>2048 {
        return Err(Error::Storage);
    }
    let enabled=enabled==1;
    let mut priorities:Vec<Priority>=serde_json::from_str(&data).map_err(|_|Error::Storage)?;
    canonicalize(enabled,&mut priorities).map_err(|_|Error::Storage)?;
    Ok(Config{enabled,priorities,revision})
}

fn topics(values:&[Priority])->Result<String> { serde_json::to_string(values).map_err(|_|Error::Storage) }

fn visible(db:&Connection)->Result<&'static str> {
    if campus_preferences::installed(db)? {
        Ok("AND NOT EXISTS(SELECT 1 FROM native_campus_source_preferences p
              WHERE p.profile COLLATE BINARY=a.profile AND p.source_id COLLATE BINARY=a.source_id
              AND p.visibility='archived')")
    } else { Ok("") }
}

fn rows(visible:&str)->String {
    // Caller binds profile, canonical JSON priorities, and an optional exact source.
    format!("WITH candidates AS (
        SELECT a.id,a.category,a.source_id,a.title,a.published_at,
               COALESCE(CAST(json_extract(topic.value,'$.weight') AS INTEGER),0) AS weight,
               (length(trim(a.summary,char(9,10,13,32)))>0
                  AND trim(a.summary,char(9,10,13,32)) COLLATE NOCASE
                       <> trim(a.title,char(9,10,13,32))) AS has_summary
        FROM native_campus_articles a LEFT JOIN json_each(?2) topic
          ON json_extract(topic.value,'$.category') COLLATE BINARY=a.category
        WHERE a.profile COLLATE BINARY=?1 AND (?3='' OR a.source_id COLLATE BINARY=?3) {visible}
    )")
}

fn observed_status(db:&Connection,profile:&str,settings:Config)->Result<Status> {
    let mut out=Status { profile:profile.to_owned(),policy:POLICY,settings,
        counts:Counts::default(),remote_models:false,running:false };
    if !campus::ready(db)? { return Ok(out); }
    let visible=visible(db)?;
    if !out.settings.enabled {
        let n=db.query_row(&format!("SELECT count(*) FROM native_campus_articles a
                                    WHERE a.profile COLLATE BINARY=?1 {visible}"),
            [profile],|r|r.get(0))?;
        out.counts=Counts{total_visible:n,disabled:n,..Counts::default()};
        return Ok(out);
    }
    let json=topics(&out.settings.priorities)?;
    let query=format!("{} SELECT count(*),count(CASE WHEN weight>0 AND has_summary THEN 1 END),
        count(CASE WHEN weight=0 THEN 1 END),
        count(CASE WHEN weight>0 AND NOT has_summary THEN 1 END) FROM candidates",rows(visible));
    out.counts=db.query_row(&query,params![profile,json,""],|r|Ok(Counts{
        total_visible:r.get(0)?,scored:r.get(1)?,unprioritized:r.get(2)?,
        insufficient_summary:r.get(3)?,disabled:0
    }))?;
    Ok(out)
}

pub(crate) fn status(store:&EventStore,profile:&str,q:Scope)->Result<Status> {
    scope(profile,q.profile.as_deref())?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    observed_status(&tx,profile,config(&tx,profile)?)
}

pub(crate) fn configure(store:&EventStore,profile:&str,mut q:Change)->Result<Status> {
    scope(profile,q.profile.as_deref())?;
    if !(0..=MAX_REVISION).contains(&q.expected_revision) { return Err(Error::Input); }
    canonicalize(q.enabled,&mut q.priorities)?;
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let previous=config(&tx,profile)?;
    if previous.revision!=q.expected_revision { return Err(Error::Conflict); }
    if previous.enabled==q.enabled && previous.priorities==q.priorities {
        return observed_status(&tx,profile,previous);
    }
    if !q.priorities.is_empty() {
        if !campus::ready(&tx)? { return Err(Error::Missing); }
        for item in &q.priorities {
            let known:bool=tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM native_campus_articles WHERE profile COLLATE BINARY=?1
                                AND category COLLATE BINARY=?2)",
                (profile,&item.category),|r|r.get(0))?;
            if !known { return Err(Error::Missing); }
        }
    }
    let revision=previous.revision.checked_add(1).filter(|v|*v<=MAX_REVISION).ok_or(Error::Conflict)?;
    if !installed(&tx)? { tx.execute_batch(SCHEMA)?; }
    tx.execute("INSERT INTO native_campus_scoring_v1(profile,enabled,priorities,revision) VALUES(?,?,?,?)
        ON CONFLICT(profile) DO UPDATE SET enabled=excluded.enabled,priorities=excluded.priorities,revision=excluded.revision",
        params![profile,q.enabled,topics(&q.priorities)?,revision])?;
    let status=observed_status(&tx,profile,Config{enabled:q.enabled,priorities:q.priorities,revision})?;
    tx.commit()?;
    Ok(status)
}

pub(crate) fn items(store:&EventStore,profile:&str,q:Query)->Result<Page> {
    scope(profile,q.profile.as_deref())?;
    let limit=q.limit.unwrap_or(25);
    let offset=q.offset.unwrap_or(0);
    if !(1..=50).contains(&limit) || offset>4096
        || q.source_id.as_deref().is_some_and(|source|!campus::slug(source)) { return Err(Error::Input); }
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let settings=config(&tx,profile)?;
    ranked(&tx,profile,&settings,q.source_id.as_deref().unwrap_or(""),limit,offset)
}

fn ranked(db:&Connection,profile:&str,settings:&Config,source:&str,limit:u32,offset:u32)->Result<Page> {
    let mut out=Page { profile:profile.to_owned(),policy:POLICY,settings_revision:settings.revision,
        items:vec![],total:0 };
    if !settings.enabled || !campus::ready(db)? { return Ok(out); }
    let prefix=rows(visible(db)?);
    let topics=topics(&settings.priorities)?;
    out.total=db.query_row(&format!("{prefix} SELECT count(*) FROM candidates WHERE weight>0 AND has_summary"),
        (profile,&topics,source),|r|r.get(0))?;
    let mut stmt=db.prepare(&format!("{prefix}
        SELECT id,category,source_id,title,published_at,weight FROM candidates WHERE weight>0 AND has_summary
        ORDER BY weight DESC,published_at COLLATE BINARY DESC,id COLLATE BINARY LIMIT ?4 OFFSET ?5"))?;
    out.items=stmt.query_map((profile,&topics,source,limit,offset),|r|Ok(Item{
        id:r.get(0)?,category:r.get(1)?,source_id:r.get(2)?,title:r.get(3)?,
        published_at:r.get(4)?,score:r.get(5)?,reason:"prioritized-category-with-summary",
    }))?.collect::<rusqlite::Result<_>>()?;
    Ok(out)
}

const RUN_SCHEMA:&str="
CREATE TABLE native_campus_scoring_run_v1(
 profile TEXT PRIMARY KEY NOT NULL,
 rank_digest TEXT NOT NULL CHECK(length(rank_digest)=64),
 result TEXT NOT NULL CHECK(length(CAST(result AS BLOB)) BETWEEN 2 AND 8192),
 settings_revision INTEGER NOT NULL CHECK(settings_revision BETWEEN 1 AND 9007199254740991),
 materialized_at INTEGER NOT NULL CHECK(materialized_at>0)
) WITHOUT ROWID;";
const RUN_LIMIT:u32=50;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Run {
    pub profile:Option<String>,
    expected_settings_revision:i64,
}
#[derive(Serialize)]
pub(crate) struct Material {
    state:&'static str,
    policy:&'static str,
    settings_revision:i64,
    result:Option<Rank>,
    materialized_at:Option<i64>,
    running:bool,
    remote_models:bool,
}
#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Rank { total:i64, items:Vec<Score> }
#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
struct Score { id:String, score:u8 }

struct Stored { digest:String, result:String, revision:i64, at:i64 }

fn run_ready(db:&Connection)->Result<bool> {
    let object:Option<(String,String)>=db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name='native_campus_scoring_run_v1' COLLATE NOCASE",
        [],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match object {
        None=>Ok(false),
        Some((n,t)) if n=="native_campus_scoring_run_v1"&&t=="table"=>Ok(true),
        _=>Err(Error::Storage),
    }
}
fn stored(db:&Connection,profile:&str)->Result<Option<Stored>> {
    if !run_ready(db)? {return Ok(None);}
    Ok(db.query_row("SELECT rank_digest,result,settings_revision,materialized_at
       FROM native_campus_scoring_run_v1 WHERE profile COLLATE BINARY=?", [profile],
       |r|Ok(Stored{digest:r.get(0)?,result:r.get(1)?,revision:r.get(2)?,at:r.get(3)?})).optional()?)
}
fn rank_digest(json:&str)->String {
    let mut hash=Sha256::new();
    // Never serve an earlier materialization as though it used this policy,
    // even when the new policy happens to produce the same IDs and scores.
    hash.update(b"lmh-campus-local-rank\0");
    hash.update(POLICY.as_bytes());
    hash.update(b"\0");
    hash.update(json.as_bytes());
    hex::encode(hash.finalize())
}
fn material(db:&Connection,profile:&str,settings:&Config)->Result<(Rank,String,String)> {
    let page=ranked(db,profile,settings,"",RUN_LIMIT,0)?;
    let rank=Rank{total:page.total,items:page.items.into_iter()
        .map(|r|Score{id:r.id,score:r.score}).collect()};
    let json=serde_json::to_string(&rank).map_err(|_|Error::Storage)?;
    if json.len()>8192 {return Err(Error::Storage);}
    let digest=rank_digest(&json);
    Ok((rank,json,digest))
}
fn report(state:&'static str,revision:i64,published:Option<(Rank,i64)>)->Material {
    let (result,materialized_at)=published.map_or((None,None),|(rank,at)|(Some(rank),Some(at)));
    Material{state,policy:POLICY,settings_revision:revision,result,materialized_at,running:false,remote_models:false}
}

pub(crate) fn material_status(store:&EventStore,profile:&str,q:Scope)->Result<Material> {
    scope(profile,q.profile.as_deref())?;
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let settings=config(&tx,profile)?;
    if !settings.enabled {return Ok(report("disabled",settings.revision,None));}
    let Some(saved)=stored(&tx,profile)? else {return Ok(report("not_run",settings.revision,None));};
    if saved.revision!=settings.revision {return Ok(report("stale",settings.revision,None));}
    let (_,canonical,digest)=material(&tx,profile,&settings)?;
    if saved.digest!=digest {return Ok(report("stale",settings.revision,None));}
    if saved.result!=canonical || saved.at<=0 {return Err(Error::Storage);}
    let rank=serde_json::from_str(&saved.result).map_err(|_|Error::Storage)?;
    Ok(report("ready",settings.revision,Some((rank,saved.at))))
}

pub(crate) fn materialize(store:&EventStore,profile:&str,q:Run)->Result<Material> {
    scope(profile,q.profile.as_deref())?;
    if !(1..=MAX_REVISION).contains(&q.expected_settings_revision) {return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let settings=config(&tx,profile)?;
    if !settings.enabled || settings.revision!=q.expected_settings_revision {return Err(Error::Conflict);}
    let (rank,json,digest)=material(&tx,profile,&settings)?;
    if let Some(prior)=stored(&tx,profile)? {
        if prior.revision==settings.revision && prior.digest==digest {
            if prior.result!=json || prior.at<=0 {return Err(Error::Storage);}
            return Ok(report("ready",settings.revision,Some((rank,prior.at))));
        }
    }
    let now:i64=tx.query_row("SELECT unixepoch()",[],|r|r.get(0))?;
    if now<=0 {return Err(Error::Storage);}
    if !run_ready(&tx)? {tx.execute_batch(RUN_SCHEMA)?;}
    tx.execute("INSERT INTO native_campus_scoring_run_v1
        (profile,rank_digest,result,settings_revision,materialized_at) VALUES(?,?,?,?,?)
        ON CONFLICT(profile) DO UPDATE SET rank_digest=excluded.rank_digest,result=excluded.result,
        settings_revision=excluded.settings_revision,materialized_at=excluded.materialized_at",
        params![profile,digest,json,settings.revision,now])?;
    tx.commit()?;
    Ok(report("ready",settings.revision,Some((rank,now))))
}
