//! Public topic eligibility subset of the pinned provider-off briefing rules.
use super::{Card,card,card_state_sql,instant,visible_sources};
use crate::{campus::{self,Error,Result},events::EventStore};
use regex::RegexSet;
use rusqlite::params;
use serde::{Deserialize,Serialize};
use std::sync::OnceLock;

const MAX_ITEMS:i64=campus::MAX_PROFILE_ITEMS;
const POLICY:&str="pinned-public-topic-subset-v1";
// Pinned hub/campus_briefing.py TOPICS entries used by the original _rank
// public topic predicate. These are literal substrings, not inferred categories.
const KEYWORDS:[&str;3]=[
    "奖学金|奖助|助学金|困难补助|国家励志",
    "竞赛|大赛|挑战杯|创新创业|比赛|科创",
    "选课|课程|补退选|教务|考试|学分|教学",
];
static MATCHERS:OnceLock<Option<RegexSet>>=OnceLock::new();

#[derive(Clone,Copy,PartialEq,Eq,Deserialize,Serialize)]
#[serde(rename_all="snake_case")]
enum Topic {Scholarships,Competitions,Courses}
const TOPICS:[Topic;3]=[Topic::Scholarships,Topic::Competitions,Topic::Courses];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Query {
    pub profile:Option<String>,
    topic:Option<Topic>,
    limit:Option<u32>,
    offset:Option<u32>,
}

#[derive(Serialize)]
pub(crate) struct Page {
    policy:&'static str,
    generated_at:String,
    topic:Option<Topic>,
    limit:u32,offset:u32,total:usize,
    items:Vec<Item>,
    personalized:bool,ranking_supported:bool,grouping_supported:bool,
}
#[derive(Serialize)]
struct Item {
    #[serde(flatten)]
    card:Card,
    matching_public_topics:Vec<Topic>,
}

// Intentionally stronger than stock material presence: report no candidate
// where both payload fields are blank or merely repeat the article headline.
fn substantive(field:&str,title:&str)->bool {
    !field.trim().is_empty() && !field.trim().eq_ignore_ascii_case(title.trim())
}

pub(crate) fn list(store:&EventStore,profile:&str,q:Query)->Result<Page> {
    let limit=q.limit.unwrap_or(50);
    let offset=q.offset.unwrap_or(0);
    if !campus::slug(profile)||q.profile.as_deref().is_some_and(|selected|selected!=profile) {
        return Err(Error::Missing);
    }
    if !(1..=50).contains(&limit)||i64::from(offset)>MAX_ITEMS {return Err(Error::Input);}
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let (generated_at,today)=instant()?;
    let mut page=Page{policy:POLICY,generated_at,topic:q.topic,limit,offset,total:0,
        items:Vec::new(),personalized:false,ranking_supported:false,grouping_supported:false};
    if !campus::ready(&tx)? {return Ok(page);}
    let visible=visible_sources(&tx)?;
    let (bookmarked,read,revision)=card_state_sql(&tx)?;
    let matcher=MATCHERS.get_or_init(||RegexSet::new(KEYWORDS).ok()).as_ref().ok_or(Error::Storage)?;
    // Filter before pagination; cap work even if the configured profile budget
    // has been violated. Stream older candidates, never buffer full bodies.
    let mut sql=tx.prepare(&format!("WITH visible AS (
         SELECT a.id,a.url,a.title,a.published_at,a.source_id,a.source_name,a.content,a.summary
         FROM native_campus_articles a
         WHERE a.profile COLLATE BINARY=?1
             AND (a.published_at='' OR a.published_at COLLATE BINARY<=?2) {visible}
      )
      SELECT v.id,v.url,v.title,v.published_at,v.source_id,v.source_name,
          substr(CASE WHEN length(trim(v.summary,char(9,10,13,32)))>0 THEN v.summary
                      ELSE v.content END,1,240),
          length(trim(v.content,char(9,10,13,32)))>0,length(trim(v.summary,char(9,10,13,32)))>0,
          {bookmarked},{read},{revision},v.summary,v.content
      FROM visible v ORDER BY v.published_at COLLATE BINARY DESC,v.id COLLATE BINARY DESC LIMIT ?3"))?;
    let mut rows=sql.query(params![profile,today,MAX_ITEMS+1])?;
    let mut visited=0_i64;
    while let Some(row)=rows.next()? {
        visited+=1;
        if visited>MAX_ITEMS{return Err(Error::Storage);}
        let (title_ref,date_ref,summary_ref,body_ref)=
            (row.get_ref(2)?,row.get_ref(3)?,row.get_ref(12)?,row.get_ref(13)?);
        let title=title_ref.as_str().map_err(|_|Error::Storage)?;
        let date=date_ref.as_str().map_err(|_|Error::Storage)?;
        let summary=summary_ref.as_str().map_err(|_|Error::Storage)?;
        let body=body_ref.as_str().map_err(|_|Error::Storage)?;
        if !date.is_empty()&&!campus::known_date(date){return Err(Error::Storage);}
        if title.len()>512||summary.len()>2_048||body.len()>16*1024{return Err(Error::Storage);}
        if !substantive(summary,title)&&!substantive(body,title){continue;}
        let in_title=matcher.matches(title);
        let in_summary=matcher.matches(summary);
        let in_body=matcher.matches(body);
        let topics:Vec<_>=TOPICS.into_iter().enumerate()
            .filter_map(|(i,t)|(in_title.matched(i)||in_summary.matched(i)||in_body.matched(i)).then_some(t))
            .collect();
        if topics.is_empty()||q.topic.is_some_and(|w|!topics.contains(&w)){continue;}
        if page.total>=offset as usize&&page.items.len()<limit as usize {
            page.items.push(Item{card:card(row,&today)?,matching_public_topics:topics});
        }
        page.total+=1;
    }
    Ok(page)
}
