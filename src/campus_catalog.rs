//! Scoped summaries of detached campus articles; never a collector or student profile.
use crate::{campus::{self, Error}, events::EventStore};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};

type Result<T> = std::result::Result<T, Error>;

fn read<T>(store: &EventStore, profile: &str, empty: T,
           query: impl FnOnce(&Connection, T) -> Result<T>) -> Result<T> {
    if !campus::slug(profile) { return Err(Error::Input); }
    store.require_ordinary()?;
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.verify_connection(&tx)?;
    let result = if campus::ready(&tx)? { query(&tx, empty)? } else { empty };
    tx.commit()?;
    Ok(result)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Scope {
    pub profile: Option<String>,
}

#[derive(Serialize)]
pub(crate) struct Profile {
    profile: String,
    mode: &'static str,
    article_count: i64,
    source_count: i64,
    preferences_supported: bool,
}

pub(crate) fn profile(store: &EventStore, profile: &str) -> Result<Profile> {
    read(store, profile, Profile {
        profile: profile.to_owned(), mode: "detached-local",
        article_count: 0, source_count: 0, preferences_supported: false,
    }, |db, mut info| {
        (info.article_count, info.source_count) = db.query_row(
            "SELECT count(*), count(DISTINCT source_id) FROM native_campus_articles WHERE profile=?",
            [profile], |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        Ok(info)
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SourceQuery {
    pub profile: Option<String>,
    limit: Option<u32>,
    offset: Option<u32>,
}

#[derive(Serialize)]
struct Source {
    id: String,
    item_count: i64,
    latest_published_date: Option<String>,
}

#[derive(Serialize)]
pub(crate) struct Sources {
    profile: String,
    source_kind: &'static str,
    items: Vec<Source>,
    total: i64,
}

pub(crate) fn sources(store: &EventStore, profile: &str, filters: SourceQuery) -> Result<Sources> {
    let limit = filters.limit.unwrap_or(25);
    let offset = filters.offset.unwrap_or(0);
    if !(1..=50).contains(&limit) || offset > 4_096 { return Err(Error::Input); }
    read(store, profile, Sources {
        profile: profile.to_owned(), source_kind: "observed-detached", items: Vec::new(), total: 0,
    }, |db, mut page| {
        page.total = db.query_row(
            "SELECT count(DISTINCT source_id) FROM native_campus_articles WHERE profile=?",
            [profile], |row| row.get(0),
        )?;
        let mut stmt = db.prepare(
            "SELECT source_id, count(*), max(nullif(published_at,''))
             FROM native_campus_articles WHERE profile=? GROUP BY source_id
             ORDER BY source_id COLLATE BINARY LIMIT ? OFFSET ?",
        )?;
        let rows = stmt.query_map((profile, limit, offset), |row| Ok(Source {
            id: row.get(0)?, item_count: row.get(1)?, latest_published_date: row.get(2)?,
        }))?;
        for row in rows {
            let item = row?;
            if !campus::slug(&item.id) || item.item_count < 1 { return Err(Error::Storage); }
            page.items.push(item);
        }
        Ok(page)
    })
}

#[derive(Serialize)]
struct ContentCounts {
    imported_text: i64,
    summary_only: i64,
    no_text: i64,
}

#[derive(Serialize)]
pub(crate) struct Updates {
    profile: String,
    auto_updates_enabled: bool,
    running: bool,
    fetch_supported: bool,
    latest_published_date: Option<String>,
    content_counts: ContentCounts,
}

pub(crate) fn updates(store: &EventStore, profile: &str) -> Result<Updates> {
    read(store, profile, Updates {
        profile: profile.to_owned(), auto_updates_enabled: false, running: false,
        fetch_supported: false, latest_published_date: None,
        content_counts: ContentCounts { imported_text: 0, summary_only: 0, no_text: 0 },
    }, |db, mut info| {
        let (total, text, summary, latest): (i64, i64, i64, Option<String>) = db.query_row(
            "WITH visible AS (
                SELECT length(trim(content, char(9,10,13,32))) > 0 AS imported_text,
                       length(trim(summary, char(9,10,13,32))) > 0 AS summary,
                       nullif(published_at,'') AS published
                FROM native_campus_articles WHERE profile=?
             )
             SELECT count(*), count(CASE WHEN imported_text THEN 1 END),
                    count(CASE WHEN NOT imported_text AND summary THEN 1 END), max(published)
             FROM visible",
            [profile], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )?;
        info.content_counts = ContentCounts { imported_text: text, summary_only: summary, no_text: total - text - summary };
        info.latest_published_date = latest;
        Ok(info)
    })
}
