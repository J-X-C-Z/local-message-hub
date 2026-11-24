//! Desktop chat activity indexes over one exact archived account.
use crate::{
    annual_report::MAX_ROWS,
    plugins::AccountScope,
    store::{Store, StoreError},
};
use chrono::{Days, NaiveDate};
use rusqlite::{Connection, params};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;

const PRIVATE: &str = "LOWER(c.kind) IN ('private','direct','friend','single','personal','dm')";
const GROUP: &str = "LOWER(c.kind) = 'group'";
const MAX_LIMIT: u32 = 200;
const MAX_OFFSET: u32 = 10_000;
const UTC8: i64 = 28_800;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ProfileQuery {
    pub platform: Option<String>,
    pub account_id: Option<String>,
    pub start: Option<String>,
    pub end: Option<String>,
    pub conversation_id: Option<String>,
    pub limit: Option<u32>,
    pub offset: Option<u32>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DailyQuery {
    pub platform: Option<String>,
    pub account_id: Option<String>,
    start: Option<String>,
    end: Option<String>,
}

#[derive(Clone, Copy)]
pub(crate) enum ProfileKind {
    Private,
    Group,
}

fn bad() -> StoreError {
    StoreError::BadQuery("invalid chat analytics request")
}

fn check_scope(
    scope: &AccountScope,
    platform: Option<&str>,
    account_id: Option<&str>,
) -> Result<(), StoreError> {
    if platform.is_some_and(|v| v != scope.platform)
        || account_id.is_some_and(|v| v != scope.account_id)
    {
        return Err(bad());
    }
    Ok(())
}

fn day_bound(date: &str, end: bool) -> Result<i64, StoreError> {
    if date.len() != 10 || date.as_bytes()[4] != b'-' || date.as_bytes()[7] != b'-'
        || date.bytes().enumerate().any(|(i, b)| i != 4 && i != 7 && !b.is_ascii_digit())
    {
        return Err(bad());
    }
    let day = NaiveDate::parse_from_str(date, "%Y-%m-%d").map_err(|_| bad())?;
    let second = day.and_hms_opt(0, 0, 0).ok_or_else(bad)?.and_utc().timestamp();
    Ok(second - UTC8 + if end { 86_399 } else { 0 })
}

fn bounds(start: Option<&str>, end: Option<&str>) -> Result<(i64, i64), StoreError> {
    let lower = start.map(|s| day_bound(s, false)).transpose()?.unwrap_or(i64::MIN);
    let upper = end.map(|s| day_bound(s, true)).transpose()?.unwrap_or(i64::MAX);
    if lower > upper {
        return Err(bad());
    }
    Ok((lower, upper))
}

pub(crate) fn profiles(
    store: Store,
    scope: AccountScope,
    query: ProfileQuery,
    kind: ProfileKind,
) -> Result<Value, StoreError> {
    check_scope(&scope, query.platform.as_deref(), query.account_id.as_deref())?;
    let (lower, upper) = bounds(query.start.as_deref(), query.end.as_deref())?;
    let limit = query.limit.unwrap_or(50);
    let offset = query.offset.unwrap_or(0);
    if limit == 0 || limit > MAX_LIMIT || offset > MAX_OFFSET
        || query.conversation_id.as_ref().is_some_and(|id|
            id.is_empty() || id.len() > 1024 || id.chars().any(char::is_control))
    {
        return Err(bad());
    }

    let kind_sql = match kind {
        ProfileKind::Private => PRIVATE,
        ProfileKind::Group => GROUP,
    };
    let conversation_sql = if query.conversation_id.is_some() {
        "AND m.conversation_id COLLATE BINARY = ?5"
    } else {
        ""
    };
    let mut db = store.connection()?;
    let snapshot = db.transaction()?;
    // Before grouping or paging, cap input and fail closed on overflow. No sender,
    // message body, or conversation title is even selected by this query.
    let sql = format!(
        "WITH sample AS MATERIALIZED (
             SELECT m.conversation_id, m.timestamp, m.is_self, c.member_count,
                    (m.type IN ('image','video','voice','file') AND NOT (
                     m.platform='wechat' AND COALESCE(m.raw_type,'') IN ('47','11000'))) AS media
             FROM messages m JOIN conversations c
               ON c.platform COLLATE BINARY=m.platform AND c.account_id COLLATE BINARY=m.account_id
              AND c.id COLLATE BINARY=m.conversation_id
             WHERE m.platform COLLATE BINARY=?1 AND m.account_id COLLATE BINARY=?2
               AND m.timestamp>=?3 AND m.timestamp<=?4 AND {kind_sql} {conversation_sql}
             LIMIT ?6
         ), budget AS (SELECT COUNT(*) n FROM sample),
         page AS (
             SELECT conversation_id AS id, COUNT(*) AS n,
                    SUM(CASE WHEN is_self=1 THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN is_self=0 THEN 1 ELSE 0 END) AS received,
                    MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp,
                    SUM(CASE WHEN media THEN 1 ELSE 0 END) AS canonical_media,
                    MAX(member_count) AS member_count
             FROM sample WHERE (SELECT n FROM budget) <= ?7
             GROUP BY conversation_id COLLATE BINARY
             ORDER BY id COLLATE BINARY LIMIT ?8 OFFSET ?9
         )
         SELECT budget.n,page.* FROM budget LEFT JOIN page ON 1=1 ORDER BY page.id COLLATE BINARY"
    );
    let mut stmt = snapshot.prepare(&sql)?;
    let mut rows = stmt.query(params![
        scope.platform, scope.account_id, lower, upper, query.conversation_id, MAX_ROWS+1,
        MAX_ROWS, limit+1, offset,
    ])?;
    let mut items = Vec::with_capacity(limit as usize);
    let mut next_offset = None;
    while let Some(row) = rows.next()? {
        if row.get::<_, i64>(0)? > MAX_ROWS {
            return Err(StoreError::TooLarge);
        }
        let Some(id) = row.get::<_, Option<String>>(1)? else { break };
        if id.is_empty() || id.len() > 1024 || id.chars().any(char::is_control) {
            return Err(bad());
        }
        if items.len() == limit as usize {
            next_offset = offset.checked_add(limit).filter(|v| *v <= MAX_OFFSET);
            break;
        }
        let count = row.get::<_, i64>(2)?;
        let sent = row.get::<_, i64>(3)?;
        let ratio = format!("{:.6}", sent as f64 / count as f64)
            .parse::<f64>().map_err(|_| bad())?;
        let mut item = json!({
            "conversation_id": id, "message_count":count, "sent_count":sent,
            "received_count": row.get::<_, i64>(4)?, "sent_ratio":ratio,
            "first_timestamp": row.get::<_, i64>(5)?, "last_timestamp": row.get::<_, i64>(6)?,
        });
        match kind {
            ProfileKind::Private => item["canonical_media_count"] = json!(row.get::<_, i64>(7)?),
            ProfileKind::Group => item["roster_member_count"] = json!(row.get::<_, Option<i64>>(8)?),
        }
        items.push(item);
    }
    Ok(json!({"items":items, "next_offset":next_offset}))
}

pub(crate) fn self_sent_daily(
    store: Store,
    scope: AccountScope,
    query: DailyQuery,
) -> Result<Value, StoreError> {
    check_scope(&scope, query.platform.as_deref(), query.account_id.as_deref())?;
    let (lower, upper) = bounds(query.start.as_deref(), query.end.as_deref())?;
    let db = store.connection()?;
    daily(&db, &scope, lower, upper)
}

fn daily(db: &Connection, scope: &AccountScope, lower: i64, upper: i64) -> Result<Value, StoreError> {
    let sql = format!(
        "WITH sample AS MATERIALIZED (
             SELECT date(m.timestamp,'unixepoch','+8 hours') AS day
             FROM messages m JOIN conversations c
               ON c.platform COLLATE BINARY=m.platform AND c.account_id COLLATE BINARY=m.account_id
              AND c.id COLLATE BINARY=m.conversation_id
             WHERE m.platform COLLATE BINARY=?1 AND m.account_id COLLATE BINARY=?2
               AND m.timestamp>=?3 AND m.timestamp<=?4 AND m.is_self=1 AND {PRIVATE}
             LIMIT ?5
         ), summary AS (
             SELECT COUNT(*) n, COUNT(day) valid, MIN(day) first_day, MAX(day) last_day FROM sample
         ), daily AS (
             SELECT day, COUNT(*) amount FROM sample
             WHERE (SELECT n FROM summary) <= ?6
               AND day >= date((SELECT last_day FROM summary),'-36 days')
             GROUP BY day
         )
         SELECT n,valid,first_day,last_day,daily.day,daily.amount
         FROM summary LEFT JOIN daily ON 1=1 ORDER BY daily.day"
    );
    let mut stmt = db.prepare(&sql)?;
    let mut rows = stmt.query(params![scope.platform, scope.account_id, lower, upper, MAX_ROWS+1, MAX_ROWS])?;
    let Some(first_row) = rows.next()? else { return Err(bad()); };
    let total = first_row.get::<_, i64>(0)?;
    if total > MAX_ROWS {
        return Err(StoreError::TooLarge);
    }
    if total == 0 {
        return Ok(json!({"platform":scope.platform,"account_id":scope.account_id,
            "total_messages":0,"first_day":null,"last_day":null,"baseline":0.0,"items":[]}));
    }
    if first_row.get::<_, i64>(1)? != total {
        return Err(bad());
    }
    let parse_date = |idx| -> Result<NaiveDate, StoreError> {
        NaiveDate::parse_from_str(&first_row.get::<_, String>(idx)?, "%Y-%m-%d").map_err(|_| bad())
    };
    let (first, last) = (parse_date(2)?, parse_date(3)?);
    let mut counts = BTreeMap::new();
    let mut row = Some(first_row);
    while let Some(current) = row {
        if let Some(day) = current.get::<_, Option<String>>(4)? {
            counts.insert(day, current.get::<_, i64>(5)?);
        }
        row = rows.next()?;
    }
    let mut day = last.checked_sub_days(Days::new(36)).ok_or_else(bad)?.max(first);
    let mut items = Vec::with_capacity(37);
    loop {
        let date = day.to_string();
        items.push(json!({"date":date, "count":counts.get(&date).copied().unwrap_or(0)}));
        if day == last { break; }
        day = day.succ_opt().ok_or_else(bad)?;
    }
    let span = last.signed_duration_since(first).num_days() + 1;
    Ok(json!({"platform":scope.platform,"account_id":scope.account_id,
        "total_messages":total,"first_day":first.to_string(),"last_day":last.to_string(),
        "baseline":total as f64 / span as f64,"items":items}))
}
