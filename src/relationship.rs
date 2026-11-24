//! On-demand, content-free relationship activity for one exact private conversation.
use crate::{
    annual_report::MAX_ROWS,
    plugins::AccountScope,
    store::{Store, StoreError},
};
use chrono::{Datelike, NaiveDate};
use rusqlite::{Connection, OptionalExtension, params};
use serde::Deserialize;
use serde_json::{Value, json};

const COVERAGE: &str = "local_index_only";
const PRIVATE_KINDS: &str = "('private','direct','friend','single','personal','dm')";
const MAX_PAGE: u32 = 366;
const MAX_OFFSET: u32 = 10_000;
const UTC8: i64 = 28_800;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SummaryQuery {
    pub platform: String,
    pub account_id: String,
    conversation_id: String,
    start: Option<String>,
    end: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TimelineQuery {
    pub platform: String,
    pub account_id: String,
    conversation_id: String,
    start: Option<String>,
    end: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AchievementQuery {
    pub platform: String,
    pub account_id: String,
    conversation_id: String,
}

fn bad() -> StoreError {
    StoreError::BadQuery("invalid relationship analytics request")
}

fn check_scope(
    scope: &AccountScope,
    platform: &str,
    account_id: &str,
    conversation_id: &str,
) -> Result<(), StoreError> {
    if scope.platform != platform || scope.account_id != account_id
        || conversation_id.is_empty() || conversation_id.len() > 1024
        || conversation_id.chars().any(char::is_control)
    {
        return Err(bad());
    }
    Ok(())
}

fn date_bound(value: Option<&str>, end: bool) -> Result<i64, StoreError> {
    let Some(value) = value else { return Ok(if end { i64::MAX } else { i64::MIN }); };
    if value.len() != 10
        || value.bytes().enumerate().any(|(i, b)| match i {
            4 | 7 => b != b'-',
            _ => !b.is_ascii_digit(),
        })
    {
        return Err(bad());
    }
    let date = NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| bad())?;
    let timestamp = date.and_hms_opt(0, 0, 0).ok_or_else(bad)?.and_utc().timestamp();
    Ok(timestamp - UTC8 + if end { 86_399 } else { 0 })
}

#[derive(Clone, Copy)]
enum View {
    Footprint,
    Timeline { limit: u32, offset: u32 },
    Achievements,
}

pub(crate) fn footprint(
    store: Store, scope: AccountScope, query: SummaryQuery,
) -> Result<Option<Value>, StoreError> {
    check_scope(&scope, &query.platform, &query.account_id, &query.conversation_id)?;
    read(store, &scope, &query.conversation_id,
        (query.start.as_deref(),query.end.as_deref()), View::Footprint)
}

pub(crate) fn timeline(
    store: Store, scope: AccountScope, query: TimelineQuery,
) -> Result<Option<Value>, StoreError> {
    check_scope(&scope, &query.platform, &query.account_id, &query.conversation_id)?;
    let limit = query.limit.unwrap_or(MAX_PAGE);
    let offset = query.offset.unwrap_or(0);
    if !(1..=MAX_PAGE).contains(&limit) || offset > MAX_OFFSET {
        return Err(bad());
    }
    read(store, &scope, &query.conversation_id,
        (query.start.as_deref(),query.end.as_deref()), View::Timeline { limit, offset })
}

pub(crate) fn achievements(
    store: Store, scope: AccountScope, query: AchievementQuery,
) -> Result<Option<Value>, StoreError> {
    check_scope(&scope, &query.platform, &query.account_id, &query.conversation_id)?;
    read(store, &scope, &query.conversation_id, (None,None), View::Achievements)
}

fn read(
    store: Store, scope: &AccountScope, id: &str, dates: (Option<&str>, Option<&str>), view: View,
) -> Result<Option<Value>, StoreError> {
    let lower = date_bound(dates.0, false)?;
    let upper = date_bound(dates.1, true)?;
    if lower > upper { return Err(bad()); }
    let mut connection = store.connection()?;
    let db = connection.transaction()?;
    // Eligibility and aggregates must see the same indexed SQLite snapshot.
    let eligible = db.query_row(
        &format!("SELECT 1 FROM conversations WHERE platform COLLATE BINARY=?1
            AND account_id COLLATE BINARY=?2 AND id COLLATE BINARY=?3
            AND LOWER(kind) IN {PRIVATE_KINDS}"),
        params![scope.platform,scope.account_id,id], |_| Ok(())
    ).optional()?.is_some();
    if !eligible { return Ok(None); }
    activity(&db, scope, id, lower, upper, view).map(Some)
}

#[derive(Default)]
struct Activity {
    count: i64,
    sent: i64,
    received: i64,
    images: i64,
    voices: i64,
    videos: i64,
    files: i64,
    calls: i64,
    active_days: i64,
    active_years: i64,
    longest_streak: i64,
    first: Option<NaiveDate>,
    last: Option<NaiveDate>,
    first_timestamp: Option<i64>,
    last_timestamp: Option<i64>,
}

impl Activity {
    fn media(&self) -> i64 { self.images + self.voices + self.videos + self.files }

    fn milestones(&self) -> Value {
        let span = self.last.zip(self.first)
            .map_or(0, |(last, first)| last.signed_duration_since(first).num_days());
        let rows = [
            ("first_message", if self.count > 0 { 1 } else { 0 }, 1),
            ("messages_100", self.count, 100),
            ("messages_1000", self.count, 1000),
            ("messages_10000", self.count, 10000),
            ("messages_50000", self.count, 50000),
            ("active_days_7", self.active_days, 7),
            ("active_days_30", self.active_days, 30),
            ("active_days_100", self.active_days, 100),
            ("active_days_365", self.active_days, 365),
            ("streak_3", self.longest_streak, 3),
            ("streak_7", self.longest_streak, 7),
            ("streak_30", self.longest_streak, 30),
            ("images_100", self.images, 100),
            ("voices_50", self.voices, 50),
            ("videos_20", self.videos, 20),
            ("files_10", self.files, 10),
            ("calls_5", self.calls, 5),
            ("span_365", span, 365),
            ("active_years_2", self.active_years, 2),
            ("active_years_3", self.active_years, 3),
            ("active_years_5", self.active_years, 5),
        ];
        let items: Vec<_> = rows.into_iter().map(|(id,current,threshold)|
            json!({"id":id,"current":current,"threshold":threshold,"unlocked":current>=threshold})
        ).collect();
        json!({"available":true,"coverage":COVERAGE,"items":items})
    }
}

fn activity(
    db: &Connection, scope: &AccountScope, id: &str, lower: i64, upper: i64, view: View,
) -> Result<Value,StoreError> {
    // Materialize only bounded scalar fields after the exact account/conversation
    // filter. Never calculate aggregates on a partial, truncated source dataset.
    let mut stmt = db.prepare(
        "WITH sample AS MATERIALIZED (
            SELECT timestamp, is_self, type,
                   CASE WHEN platform='wechat' THEN raw_type END AS wechat_raw_type
            FROM messages WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2
             AND conversation_id COLLATE BINARY=?3 AND timestamp>=?4 AND timestamp<=?5
            LIMIT ?6
        ), budget AS (SELECT COUNT(*) AS n FROM sample),
        dates AS (
            SELECT date(timestamp,'unixepoch','+8 hours') AS day, COUNT(*) AS n,
                   SUM(CASE WHEN is_self=1 THEN 1 ELSE 0 END) AS sent,
                   SUM(CASE WHEN COALESCE(is_self,0)=0 THEN 1 ELSE 0 END) AS received,
                   SUM(CASE WHEN type='image' AND COALESCE(wechat_raw_type,'') NOT IN ('47','11000') THEN 1 ELSE 0 END) AS images,
                   SUM(CASE WHEN type='voice' THEN 1 ELSE 0 END) AS voices,
                   SUM(CASE WHEN type='video' THEN 1 ELSE 0 END) AS videos,
                   SUM(CASE WHEN type='file' THEN 1 ELSE 0 END) AS files,
                   SUM(CASE WHEN wechat_raw_type='50' THEN 1 ELSE 0 END) AS calls,
                   MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp
            FROM sample WHERE (SELECT n FROM budget)<=?7 GROUP BY day
        )
        SELECT budget.n,dates.* FROM budget LEFT JOIN dates ON 1=1 ORDER BY dates.day DESC"
    )?;
    let mut rows = stmt.query(params![scope.platform,scope.account_id,id,lower,upper,MAX_ROWS+1,MAX_ROWS])?;
    let mut total = Activity::default();
    let mut items = Vec::new();
    let mut streak = 0;
    let mut more = false;
    while let Some(row) = rows.next()? {
        let matched = row.get::<_, i64>(0)?;
        if matched > MAX_ROWS { return Err(StoreError::TooLarge); }
        if matched == 0 { break; }
        let day = NaiveDate::parse_from_str(
            &row.get::<_, Option<String>>(1)?.ok_or_else(bad)?, "%Y-%m-%d",
        ).map_err(|_| bad())?;
        let (count,sent,received) = (row.get::<_,i64>(2)?, row.get::<_,i64>(3)?, row.get::<_,i64>(4)?);
        let (images,voices,videos,files) = (
            row.get::<_,i64>(5)?, row.get::<_,i64>(6)?, row.get::<_,i64>(7)?, row.get::<_,i64>(8)?,
        );
        if let View::Timeline { limit, offset } = view
            && total.active_days >= i64::from(offset)
        {
            if items.len() < limit as usize {
                items.push(json!({"date":day.to_string(),"message_count":count,
                    "sent_count":sent,"received_count":received,"media_count":images+voices+videos+files}));
            } else {
                // Continue validating casted dates before returning the page.
                more = true;
            }
        }
        if total.first.and_then(|previous| previous.pred_opt()) == Some(day) { streak += 1; }
        else { streak = 1; }
        total.longest_streak = total.longest_streak.max(streak);
        if total.first.is_none_or(|previous| previous.year() != day.year()) { total.active_years += 1; }
        total.first = Some(day);
        total.last.get_or_insert(day);
        total.first_timestamp = Some(row.get(10)?);
        if total.last_timestamp.is_none() { total.last_timestamp = Some(row.get(11)?); }
        total.active_days += 1;
        total.count += count;
        total.sent += sent;
        total.received += received;
        total.images += images;
        total.voices += voices;
        total.videos += videos;
        total.files += files;
        total.calls += row.get::<_,i64>(9)?;
    }
    match view {
        View::Footprint => Ok(json!({"coverage":COVERAGE,"conversation_id":id,
            "message_count":total.count,"sent_count":total.sent,"received_count":total.received,
            "active_days":total.active_days,"first_timestamp":total.first_timestamp,
            "last_timestamp":total.last_timestamp,"media_count":total.media()})),
        View::Timeline { limit, offset } => {
            let next_offset = (more && offset + limit <= MAX_OFFSET).then_some(offset+limit);
            Ok(json!({"coverage":COVERAGE,"conversation_id":id,"items":items,"next_offset":next_offset}))
        }
        View::Achievements => Ok(total.milestones()),
    }
}
