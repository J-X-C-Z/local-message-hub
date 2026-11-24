//! Read-only article cards for the legacy briefing's explicit all section.
//! The imported archive has civil publication dates, not provider event times.
use super::{article_state,Error,MAX_PROFILE_ITEMS,Result,known_date,ready,slug,state_ready,text};
use crate::{campus_preferences,events::EventStore};
use chrono::{DateTime,FixedOffset,Utc};
use rusqlite::{Connection,params};
use serde::Serialize;
use std::time::{SystemTime,UNIX_EPOCH};

pub(crate) mod shortlist;

#[derive(Serialize)]
pub(crate) struct All {
    mode:&'static str,
    section:&'static str,
    generation:&'static str,
    generated_at:String,
    date:String,
    available_dates:Vec<String>,
    limit:u32,
    offset:u32,
    total:i64,
    counts:Counts,
    items:Vec<Card>,
    ranking_supported:bool,
    grouping_supported:bool,
    refresh_supported:bool,
}

#[derive(Default,Serialize)]
struct Counts {materials:i64,unknown_date:i64,future_date:i64,
    imported_body:i64,summary_only:i64,title_only:i64}

#[derive(Serialize)]
struct Card {
    id:String,representative_id:String,title:String,url:String,summary:String,
    source_name:String,published_at:String,date:String,date_unknown:bool,future_date:bool,
    material_status:&'static str,bookmarked:bool,read:bool,state_revision:i64,reports:[Report;1],
}
#[derive(Serialize)]
struct Report {id:String,title:String,url:String,source_id:String,source_name:String,published_at:String}

fn instant()->Result<(String,String)> {
    let seconds=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|Error::Storage)?.as_secs();
    let utc=DateTime::<Utc>::from_timestamp(i64::try_from(seconds).map_err(|_|Error::Storage)?,0)
        .ok_or(Error::Storage)?;
    let shanghai=FixedOffset::east_opt(8*3600).ok_or(Error::Storage)?;
    let local=utc.with_timezone(&shanghai);
    Ok((local.to_rfc3339(),local.format("%Y-%m-%d").to_string()))
}

pub(crate) fn all(store:&EventStore,profile:&str,date:String,q:String,
    limit:Option<u32>,offset:Option<u32>)->Result<All> {
    let limit=limit.unwrap_or(50);
    let offset=offset.unwrap_or(0);
    if !slug(profile) || !text(&q,512,false) || (!date.is_empty()&&!known_date(&date))
        || !(1..=50).contains(&limit) || offset>MAX_PROFILE_ITEMS as u32 {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let (generated_at,today)=instant()?;
    let mut page=All{mode:"detached-local",section:"all",generation:"imported-article-cards",
        generated_at,date,available_dates:vec![],limit,offset,total:0,counts:Counts::default(),
        items:vec![],ranking_supported:false,grouping_supported:false,refresh_supported:false};
    if !ready(&tx)?{return Ok(page);}
    let visible=visible_sources(&tx)?;
    let base=format!("WHERE a.profile COLLATE BINARY=?1 {visible}");
    let cte=format!("WITH visible AS (
        SELECT a.id,a.url,a.title,a.published_at,a.source_id,a.source_name,
          (length(trim(a.content,char(9,10,13,32)))>0) AS has_body,
          (length(trim(a.summary,char(9,10,13,32)))>0) AS has_summary,
          substr(CASE WHEN length(trim(a.summary,char(9,10,13,32)))>0 THEN a.summary
                      ELSE a.content END,1,240) AS excerpt
        FROM native_campus_articles a {base}
    )");
    page.counts=tx.query_row(&format!("{cte} SELECT count(*),
        count(CASE WHEN published_at='' THEN 1 END),
        count(CASE WHEN published_at COLLATE BINARY>?2 THEN 1 END),
        count(CASE WHEN has_body THEN 1 END),count(CASE WHEN NOT has_body AND has_summary THEN 1 END),
        count(CASE WHEN NOT has_body AND NOT has_summary THEN 1 END) FROM visible"),
        params![profile,today],|r|Ok(Counts{materials:r.get(0)?,unknown_date:r.get(1)?,
        future_date:r.get(2)?,imported_body:r.get(3)?,summary_only:r.get(4)?,title_only:r.get(5)?}))?;
    let mut dates=tx.prepare(&format!("SELECT DISTINCT a.published_at FROM native_campus_articles a {base}
        AND a.published_at<>'' AND a.published_at COLLATE BINARY<=?2
        ORDER BY a.published_at COLLATE BINARY DESC LIMIT ?3"))?;
    page.available_dates=dates.query_map(params![profile,today,MAX_PROFILE_ITEMS],|r|r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    let filter="(?2='' OR instr(lower(v.title || ' ' || v.source_name),lower(?2))>0
                        OR instr(lower(v.excerpt),lower(?2))>0)";
    page.total=tx.query_row(&format!("{cte} SELECT count(*) FROM visible v WHERE {filter}"),
        params![profile,q],|r|r.get(0))?;
    let (bookmarked,read,revision)=card_state_sql(&tx)?;
    let mut rows=tx.prepare(&format!("{cte}
        SELECT v.id,v.url,v.title,v.published_at,v.source_id,v.source_name,v.excerpt,
            v.has_body,v.has_summary,{bookmarked},{read},{revision}
        FROM visible v WHERE {filter}
        ORDER BY v.published_at COLLATE BINARY DESC,v.id COLLATE BINARY DESC LIMIT ?3 OFFSET ?4"))?;
    page.items=rows.query_map(params![profile,q,limit,offset],|r|card(r,&today))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(page)
}


fn visible_sources(tx:&Connection)->Result<&'static str> {
    Ok(if campus_preferences::installed(tx).map_err(|_|Error::Storage)? {
        "AND NOT EXISTS(SELECT 1 FROM native_campus_source_preferences p
          WHERE p.profile COLLATE BINARY=a.profile AND p.source_id COLLATE BINARY=a.source_id
          AND p.visibility='archived')"
    }else{""})
}

fn card_state_sql(tx:&Connection)->Result<(&'static str,&'static str,&'static str)> {
    let bookmarks=state_ready(tx)?;
    let known_read=article_state::ready(tx)?;
    if known_read&&!bookmarks{return Err(Error::Storage);}
    let (bookmarked,revision)=if bookmarks {
        ("EXISTS(SELECT 1 FROM native_campus_bookmarks b
             WHERE b.profile COLLATE BINARY=?1 AND b.id COLLATE BINARY=v.id AND b.bookmarked=1)",
         "COALESCE((SELECT b.revision FROM native_campus_bookmarks b
             WHERE b.profile COLLATE BINARY=?1 AND b.id COLLATE BINARY=v.id),0)")
    }else{("0","0")};
    let read=if known_read {
        "EXISTS(SELECT 1 FROM native_campus_article_read_v1 r
             WHERE r.profile COLLATE BINARY=?1 AND r.id COLLATE BINARY=v.id AND r.read_flag=1)"
    }else{"0"};
    Ok((bookmarked,read,revision))
}

fn card(r:&rusqlite::Row<'_>,today:&str)->rusqlite::Result<Card> {
        let report=Report{id:r.get(0)?,url:r.get(1)?,title:r.get(2)?,published_at:r.get(3)?,
            source_id:r.get(4)?,source_name:r.get(5)?};
        let date_unknown=report.published_at.is_empty();
        let future_date=report.published_at.as_str()>today;
        let body:bool=r.get(7)?;
        let summary:bool=r.get(8)?;
        Ok(Card{id:report.id.clone(),representative_id:report.id.clone(),title:report.title.clone(),
            url:report.url.clone(),summary:r.get(6)?,source_name:report.source_name.clone(),
            published_at:report.published_at.clone(),date:report.published_at.clone(),
            date_unknown,future_date,material_status:if body{"imported_body"}else if summary{"summary_only"}
            else{"title_only"},bookmarked:r.get(9)?,read:r.get(10)?,state_revision:r.get(11)?,reports:[report]})
}
