//! Owner-operated, local fill of missing text. Never fetches the article URL.
use super::{Article, Error, Result, item_id, read_stable_source, ready, row, sha, slug, text};
use crate::{campus_registration, events::EventStore};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::Path;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    version: u8,
    profile: String,
    article_id: String,
    content: String,
}

#[derive(Serialize)]
pub struct Preview {
    article_id: String,
    text_bytes: usize,
    unchanged: bool,
    confirmation: String,
}

#[derive(Serialize)]
pub struct Outcome {
    article_id: String,
    text_bytes: usize,
    unchanged: bool,
}

fn load(store: &EventStore, source: &Path, profile: &str) -> Result<(Document, String)> {
    if !slug(profile) { return Err(Error::Input); }
    let bytes = read_stable_source(source, 128 * 1024).map_err(|_| Error::Source)?;
    let doc: Document = serde_json::from_slice(&bytes).map_err(|_| Error::Input)?;
    if doc.version != 1 || profile != doc.profile || !item_id(&doc.article_id)
        || !text(&doc.content, 16 * 1024, true) {
        return Err(Error::Input);
    }
    let bind = serde_json::to_vec(&("lmh-campus-enrich-content-v1", store.archive_id(), profile, sha(bytes)))
        .map_err(|_| Error::Input)?;
    Ok((doc, sha(bind)))
}

fn inspect(db: &Connection, doc: &Document) -> Result<(Article, [u8; 32], bool)> {
    if !ready(db)? { return Err(Error::Missing); }
    let (article, digest): (Article, [u8; 32]) = db.query_row(
        "SELECT id,url,title,published_at,summary,source_id,source_name,category,content,digest
         FROM native_campus_articles WHERE profile=?1 AND id=?2",
        (&doc.profile, &doc.article_id), |r| Ok((row(r, true)?, r.get(9)?)),
    ).optional()?.ok_or(Error::Missing)?;
    let previous = article.content.as_deref().ok_or(Error::Storage)?;
    let body = serde_json::to_vec(&article).map_err(|_| Error::Storage)?;
    let stored: [u8; 32] = Sha256::digest(body).into();
    if digest != stored { return Err(Error::Storage); }
    let unchanged = previous == doc.content;
    if !unchanged {
        if !previous.trim_matches([' ', '\t', '\r', '\n']).is_empty() {
            return Err(Error::Conflict);
        }
        campus_registration::admit(db, &doc.profile, [article.source_id.as_str()].into_iter())?;
    }
    Ok((article, digest, unchanged))
}

pub fn preview(source: &Path, archive: &Path, profile: &str) -> Result<Preview> {
    let store = EventStore::open(archive).map_err(|_| Error::Archive)?;
    store.require_ordinary()?;
    let (doc, confirmation) = load(&store, source, profile)?;
    let mut db = store.read_connection()?;
    let tx = db.transaction()?;
    store.verify_connection(&tx)?;
    let (_, _, unchanged) = inspect(&tx, &doc)?;
    Ok(Preview {
        text_bytes: doc.content.len(), article_id: doc.article_id, unchanged, confirmation,
    })
}

pub fn apply(source: &Path, archive: &Path, profile: &str, confirmation: &str) -> Result<Outcome> {
    let store = EventStore::open(archive).map_err(|_| Error::Archive)?;
    store.require_ordinary()?;
    let (doc, expected) = load(&store, source, profile)?;
    if confirmation != expected { return Err(Error::Confirmation); }
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let (mut article, old_digest, unchanged) = inspect(&tx, &doc)?;
    let text_bytes = doc.content.len();
    if !unchanged {
        let search = format!("{} {} {} {}", article.title, article.summary, doc.content, article.source_name)
            .to_lowercase();
        article.content = Some(doc.content);
        let updated: [u8; 32] = Sha256::digest(serde_json::to_vec(&article).map_err(|_| Error::Storage)?).into();
        let changed = tx.execute(
            "UPDATE native_campus_articles SET content=?1,search_text=?2,digest=?3
             WHERE profile=?4 AND id=?5 AND digest=?6",
            params![article.content, search, updated.as_slice(), doc.profile, doc.article_id, old_digest.as_slice()],
        )?;
        if changed != 1 { return Err(Error::Conflict); }
    }
    tx.commit()?;
    Ok(Outcome { article_id: doc.article_id, text_bytes, unchanged })
}
