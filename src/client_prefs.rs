//! Small local UI preferences, isolated from capability and runtime settings.
use crate::{events::{EventError, EventStore}, plugins::AccountScope};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_OWNERS: i64 = 1024;
const MAX_VERSION: i64 = 9_007_199_254_740_991;
const TABLE: &str = "
CREATE TABLE native_client_preferences (
    owner TEXT PRIMARY KEY NOT NULL,
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 9007199254740991),
    list_page_size INTEGER NOT NULL CHECK(list_page_size BETWEEN 10 AND 100),
    list_density TEXT NOT NULL CHECK(list_density IN ('compact','comfortable')),
    preview_lines INTEGER NOT NULL CHECK(preview_lines BETWEEN 0 AND 3),
    show_avatars INTEGER NOT NULL CHECK(show_avatars IN (0,1))
) WITHOUT ROWID;";

#[derive(Debug)]
pub(crate) enum Error { Input, Stale, Quota, Storage }
impl From<EventError> for Error { fn from(_: EventError) -> Self { Self::Storage } }
impl From<rusqlite::Error> for Error { fn from(_: rusqlite::Error) -> Self { Self::Storage } }

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Density { Compact, Comfortable }
impl Density {
    fn name(self) -> &'static str {
        match self { Self::Compact => "compact", Self::Comfortable => "comfortable" }
    }
    fn decode(name: &str) -> rusqlite::Result<Self> {
        match name {
            "compact" => Ok(Self::Compact),
            "comfortable" => Ok(Self::Comfortable),
            _ => Err(rusqlite::Error::InvalidQuery),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Preferences {
    list_page_size: u8,
    list_density: Density,
    preview_lines: u8,
    show_avatars: bool,
}
impl Default for Preferences {
    fn default() -> Self {
        Self { list_page_size: 30, list_density: Density::Comfortable,
            preview_lines: 1, show_avatars: true }
    }
}

#[derive(Default, Serialize)]
pub(crate) struct State {
    version: i64,
    preferences: Preferences,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Set {
    expected_version: i64,
    preferences: Preferences,
}

fn owner(store: &EventStore, plugin: &str, scope: &AccountScope) -> Result<String, Error> {
    let data = serde_json::to_vec(&("lmh-client-preferences-v1",store.archive_id(),plugin,
        &scope.platform,&scope.account_id)).map_err(|_|Error::Storage)?;
    Ok(hex::encode(Sha256::digest(data)))
}

fn installed(db: &Connection) -> Result<bool,Error> {
    let found: Option<(String,String)> = db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name='native_client_preferences' COLLATE NOCASE",
        [], |r|Ok((r.get(0)?,r.get(1)?))).optional()?;
    match found.as_ref().map(|(name,kind)|(name.as_str(),kind.as_str())) {
        None => Ok(false),
        Some(("native_client_preferences","table")) => Ok(true),
        _ => Err(Error::Storage),
    }
}

fn lookup(db: &Connection,key: &str) -> Result<State,Error> {
    Ok(db.query_row(
        "SELECT version,list_page_size,list_density,preview_lines,show_avatars
            FROM native_client_preferences WHERE owner=?", [key], |r|Ok(State {
        version:r.get(0)?, preferences:Preferences { list_page_size:r.get(1)?,
            list_density:Density::decode(r.get_ref(2)?.as_str()?)?, preview_lines:r.get(3)?,
            show_avatars:r.get(4)? },
    })).optional()?.unwrap_or_default())
}

pub(crate) fn read(store: &EventStore,plugin: &str,scope: &AccountScope) -> Result<State,Error> {
    store.require_ordinary()?;
    let key=owner(store,plugin,scope)?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if installed(&tx)? { lookup(&tx,&key) } else { Ok(State::default()) }
}

pub(crate) fn set(store: &EventStore,plugin: &str,scope: &AccountScope,request:Set) -> Result<State,Error> {
    let Set { expected_version,preferences }=request;
    if !(0..=MAX_VERSION).contains(&expected_version)
        || !(10..=100).contains(&preferences.list_page_size) || preferences.preview_lines>3 {
        return Err(Error::Input);
    }
    store.require_ordinary()?;
    let key=owner(store,plugin,scope)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? {
        if expected_version!=0 { return Err(Error::Stale); }
        tx.execute_batch(TABLE)?;
    }
    let current=lookup(&tx,&key)?;
    if current.preferences==preferences
        && (current.version==expected_version || current.version==expected_version+1) {
        return Ok(current);
    }
    if current.version!=expected_version || current.version>=MAX_VERSION { return Err(Error::Stale); }
    if current.version==0 {
        let count:i64=tx.query_row("SELECT count(*) FROM native_client_preferences",[],|r|r.get(0))?;
        if count>=MAX_OWNERS { return Err(Error::Quota); }
    }
    let version=current.version+1;
    let affected=tx.execute(
        "INSERT INTO native_client_preferences
         (owner,version,list_page_size,list_density,preview_lines,show_avatars) VALUES(?,?,?,?,?,?)
         ON CONFLICT(owner) DO UPDATE SET version=excluded.version,list_page_size=excluded.list_page_size,
             list_density=excluded.list_density,preview_lines=excluded.preview_lines,show_avatars=excluded.show_avatars
         WHERE native_client_preferences.version=?", params![key,version,preferences.list_page_size,
             preferences.list_density.name(),preferences.preview_lines,preferences.show_avatars,expected_version])?;
    if affected!=1 { return Err(Error::Stale); }
    tx.commit()?;
    Ok(State {version,preferences})
}
