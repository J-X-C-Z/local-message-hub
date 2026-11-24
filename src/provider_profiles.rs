//! Local operator intent, deliberately independent of every provider execution path.
use crate::events::{EventError, EventStore};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::Serialize;
use std::path::Path;

const LIMIT: i64 = 256;
const MAX_REVISION: i64 = 9_007_199_254_740_991;
const TABLE: &str = "native_provider_profiles_v1";
const SCHEMA: &str = "
CREATE TABLE native_provider_profiles_v1(
    profile_id TEXT NOT NULL PRIMARY KEY CHECK(length(profile_id) BETWEEN 1 AND 64),
    adapter TEXT NOT NULL CHECK(adapter IN (
        'wecom-rendered','wechat-provider-page','qq-hub-csv','telegram-desktop-json',
        'mail-eml','mail-imap-fixture')),
    account_alias TEXT NOT NULL CHECK(length(account_alias) BETWEEN 1 AND 64),
    enabled_intent INTEGER NOT NULL CHECK(enabled_intent IN (0,1)),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991)
) WITHOUT ROWID;";

const RETIREMENT_LIMIT: i64 = 4_096;
const RETIREMENT_SCHEMA: &str = "
CREATE TABLE native_provider_profile_retired_v1(
    profile_id TEXT PRIMARY KEY NOT NULL CHECK(length(profile_id) BETWEEN 1 AND 64),
    adapter TEXT NOT NULL,
    account_alias TEXT NOT NULL CHECK(length(account_alias) BETWEEN 1 AND 64),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 2 AND 9007199254740991)
) WITHOUT ROWID;
CREATE TRIGGER native_provider_profile_retired_insert_v1
BEFORE INSERT ON native_provider_profiles_v1
WHEN EXISTS(SELECT 1 FROM native_provider_profile_retired_v1 WHERE profile_id=NEW.profile_id)
BEGIN SELECT RAISE(ABORT, 'retired provider profile ID'); END;
CREATE TRIGGER native_provider_profile_retired_rename_v1
BEFORE UPDATE OF profile_id ON native_provider_profiles_v1
WHEN EXISTS(SELECT 1 FROM native_provider_profile_retired_v1 WHERE profile_id=NEW.profile_id)
BEGIN SELECT RAISE(ABORT, 'retired provider profile ID'); END;
";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Adapter {
    WecomRendered,
    WechatProviderPage,
    QqHubCsv,
    TelegramDesktopJson,
    MailEml,
    MailImapFixture,
}

impl Adapter {
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "wecom-rendered" => Ok(Self::WecomRendered),
            "wechat-provider-page" => Ok(Self::WechatProviderPage),
            "qq-hub-csv" => Ok(Self::QqHubCsv),
            "telegram-desktop-json" => Ok(Self::TelegramDesktopJson),
            "mail-eml" => Ok(Self::MailEml),
            "mail-imap-fixture" => Ok(Self::MailImapFixture),
            _ => Err(ProfileError::Input),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::WecomRendered => "wecom-rendered",
            Self::WechatProviderPage => "wechat-provider-page",
            Self::QqHubCsv => "qq-hub-csv",
            Self::TelegramDesktopJson => "telegram-desktop-json",
            Self::MailEml => "mail-eml",
            Self::MailImapFixture => "mail-imap-fixture",
        }
    }

    fn capabilities(self) -> Capabilities {
        let standalone_adapter = match self {
            Self::WechatProviderPage => "detached-preview-only",
            Self::MailImapFixture => "explicit-loopback-fixture-only",
            _ => "detached-preview-and-confirmed-import",
        };
        Capabilities {
            standalone_adapter,
            profile_executes_adapter: false,
            live_provider_supported: false,
            provider_connected: false,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ProfileError {
    #[error("invalid provider profile metadata")]
    Input,
    #[error("provider profile not found for the selected scope")]
    Missing,
    #[error("provider profile ID or revision conflict")]
    Conflict,
    #[error("provider profile limit reached")]
    Limit,
    #[error("provider profile is permanently retired")]
    Retired,
    #[error("disable the provider profile before retiring it")]
    Enabled,
    #[error("provider profile retirement ledger limit reached")]
    RetirementLimit,
    #[error("ordinary native provider profile storage unavailable")]
    Storage,
}

impl From<EventError> for ProfileError {
    fn from(_: EventError) -> Self { Self::Storage }
}
impl From<rusqlite::Error> for ProfileError {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}

type Result<T> = std::result::Result<T, ProfileError>;

#[derive(Debug, Serialize)]
struct Capabilities {
    standalone_adapter: &'static str,
    profile_executes_adapter: bool,
    live_provider_supported: bool,
    provider_connected: bool,
}

#[derive(Debug, Serialize)]
pub struct Profile {
    profile_id: String,
    adapter: Adapter,
    account_alias: String,
    enabled_intent: bool,
    revision: i64,
    capabilities: Capabilities,
}

#[derive(Debug, Serialize)]
pub struct ProfileList<'a> {
    adapter: Adapter,
    account_alias: &'a str,
    capabilities: Capabilities,
    profiles: Vec<Profile>,
}

fn identifier(value: &str) -> bool {
    let mut bytes = value.bytes();
    (1..=64).contains(&value.len())
        && bytes.next().is_some_and(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        && bytes.all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'-' | b'_'))
}

fn installed(db: &Connection) -> Result<bool> {
    let entry: Option<(String, String)> = db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
        [TABLE], |row| Ok((row.get(0)?, row.get(1)?)),
    ).optional()?;
    match entry {
        None => Ok(false),
        Some((name, kind)) if name == TABLE && kind == "table" => Ok(true),
        _ => Err(ProfileError::Storage),
    }
}

fn retirement_installed(db: &Connection) -> Result<bool> {
    let (objects, expected): (i64, i64) = db.query_row(
        "SELECT count(*),count(CASE
            WHEN name='native_provider_profile_retired_v1' AND type='table' THEN 1
            WHEN name IN ('native_provider_profile_retired_insert_v1','native_provider_profile_retired_rename_v1')
                 AND type='trigger' AND tbl_name='native_provider_profiles_v1' THEN 1
         END) FROM sqlite_schema WHERE name COLLATE NOCASE IN
         ('native_provider_profile_retired_v1','native_provider_profile_retired_insert_v1',
          'native_provider_profile_retired_rename_v1')",
        [], |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    match (objects, expected) {
        (0, 0) => Ok(false),
        (3, 3) => Ok(true),
        _ => Err(ProfileError::Storage),
    }
}

#[derive(Debug, Serialize)]
pub struct Retirement {
    profile_id: String,
    adapter: Adapter,
    account_alias: String,
    revision: i64,
    retired: bool,
}

fn retired(db: &Connection, id: &str) -> Result<Option<Retirement>> {
    if !retirement_installed(db)? { return Ok(None); }
    let row: Option<(String, String, i64)> = db.query_row(
        "SELECT adapter,account_alias,revision FROM native_provider_profile_retired_v1 WHERE profile_id=?",
        [id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    ).optional()?;
    if let Some((adapter, account_alias, revision)) = row {
        let adapter = Adapter::parse(&adapter).map_err(|_| ProfileError::Storage)?;
        if !identifier(&account_alias) || !(2..=MAX_REVISION).contains(&revision) {
            return Err(ProfileError::Storage);
        }
        return Ok(Some(Retirement { profile_id: id.to_owned(), adapter, account_alias, revision, retired: true }));
    }
    Ok(None)
}

fn raw(row: &rusqlite::Row<'_>) -> rusqlite::Result<(String, String, String, i64, i64)> {
    Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?))
}

fn decode((profile_id, name, account_alias, enabled, revision): (String, String, String, i64, i64))
    -> Result<Profile>
{
    let adapter = Adapter::parse(&name).map_err(|_| ProfileError::Storage)?;
    if !identifier(&profile_id) || !identifier(&account_alias)
        || !matches!(enabled, 0 | 1) || !(1..=MAX_REVISION).contains(&revision)
    {
        return Err(ProfileError::Storage);
    }
    Ok(Profile { profile_id, adapter, account_alias, enabled_intent: enabled == 1, revision,
        capabilities: adapter.capabilities() })
}

fn find(db: &Connection, id: &str) -> Result<Option<Profile>> {
    db.query_row(
        "SELECT profile_id,adapter,account_alias,enabled_intent,revision
         FROM native_provider_profiles_v1 WHERE profile_id=?",
        [id], raw,
    ).optional()?.map(decode).transpose()
}

pub fn list<'a>(directory: &Path, adapter: Adapter, account: &'a str) -> Result<ProfileList<'a>> {
    if !identifier(account) { return Err(ProfileError::Input); }
    let archive = EventStore::open(directory)?;
    let mut db = archive.read_connection()?;
    let tx = db.transaction()?;
    archive.verify_connection(&tx)?;
    let mut profiles = Vec::new();
    if installed(&tx)? {
        let mut stmt = tx.prepare(
            "SELECT profile_id,adapter,account_alias,enabled_intent,revision
             FROM native_provider_profiles_v1 WHERE adapter=? AND account_alias=?
             ORDER BY profile_id LIMIT ?",
        )?;
        let mut rows = stmt.query(params![adapter.name(), account, LIMIT + 1])?;
        while let Some(row) = rows.next()? {
            if profiles.len() == LIMIT as usize { return Err(ProfileError::Storage); }
            profiles.push(decode(raw(row)?)?);
        }
    }
    tx.commit()?;
    Ok(ProfileList { adapter, account_alias: account, capabilities: adapter.capabilities(), profiles })
}

/// First explicit creation installs only this additive schema. An identical retry
/// returns the existing record without changing intent or revision.
pub fn create(directory: &Path, adapter: Adapter, account: &str, id: &str) -> Result<Profile> {
    if !identifier(account) || !identifier(id) { return Err(ProfileError::Input); }
    let archive = EventStore::open(directory)?;
    let mut db = archive.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    archive.verify_connection(&tx)?;
    if !installed(&tx)? { tx.execute_batch(SCHEMA)?; }
    if let Some(prior) = retired(&tx, id)? {
        return Err(if prior.adapter == adapter && prior.account_alias == account {
            ProfileError::Retired
        } else { ProfileError::Conflict });
    }
    let record = match find(&tx, id)? {
        Some(record) if record.adapter == adapter && record.account_alias == account => record,
        Some(_) => return Err(ProfileError::Conflict),
        None => {
            let total: i64 = tx.query_row("SELECT count(*) FROM native_provider_profiles_v1", [], |row| row.get(0))?;
            if total >= LIMIT { return Err(ProfileError::Limit); }
            tx.execute("INSERT INTO native_provider_profiles_v1
                (profile_id,adapter,account_alias,enabled_intent,revision) VALUES(?,?,?,0,1)",
                params![id, adapter.name(), account])?;
            find(&tx, id)?.ok_or(ProfileError::Storage)?
        }
    };
    tx.commit()?;
    Ok(record)
}

pub fn set_intent(directory: &Path, adapter: Adapter, account: &str, id: &str,
                  expected: i64, enabled: bool) -> Result<Profile> {
    if !identifier(account) || !identifier(id) || !(1..=MAX_REVISION).contains(&expected) {
        return Err(ProfileError::Input);
    }
    let archive = EventStore::open(directory)?;
    let mut db = archive.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    archive.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(ProfileError::Missing); }
    if let Some(prior) = retired(&tx, id)? {
        return Err(if prior.adapter == adapter && prior.account_alias == account {
            ProfileError::Retired
        } else { ProfileError::Missing });
    }
    let mut record = find(&tx, id)?
        .filter(|record| record.adapter == adapter && record.account_alias == account)
        .ok_or(ProfileError::Missing)?;
    if record.revision != expected { return Err(ProfileError::Conflict); }
    if record.enabled_intent != enabled {
        if expected == MAX_REVISION { return Err(ProfileError::Conflict); }
        let changed = tx.execute("UPDATE native_provider_profiles_v1
             SET enabled_intent=?,revision=revision+1
             WHERE profile_id=? AND adapter=? AND account_alias=? AND revision=?",
            params![enabled, id, adapter.name(), account, expected])?;
        if changed != 1 { return Err(ProfileError::Conflict); }
        record.enabled_intent = enabled;
        record.revision += 1;
    }
    tx.commit()?;
    Ok(record)
}

/// Remove the disabled entry but retain identity and revision in an immutable
/// tombstone. Old-core inserts also hit the SQLite identity guard.
pub fn retire(directory: &Path, adapter: Adapter, account: &str, id: &str,
              expected: i64) -> Result<Retirement> {
    if !identifier(account) || !identifier(id) || !(1..MAX_REVISION).contains(&expected) {
        return Err(ProfileError::Input);
    }
    let archive = EventStore::open(directory)?;
    let mut db = archive.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    archive.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(ProfileError::Missing); }
    if let Some(prior) = retired(&tx, id)? {
        if prior.adapter != adapter || prior.account_alias != account { return Err(ProfileError::Missing); }
        if prior.revision != expected + 1 { return Err(ProfileError::Conflict); }
        tx.commit()?;
        return Ok(prior);
    }
    let record = find(&tx, id)?
        .filter(|record| record.adapter == adapter && record.account_alias == account)
        .ok_or(ProfileError::Missing)?;
    if record.revision != expected { return Err(ProfileError::Conflict); }
    if record.enabled_intent { return Err(ProfileError::Enabled); }
    if !retirement_installed(&tx)? { tx.execute_batch(RETIREMENT_SCHEMA)?; }
    let count: i64 = tx.query_row(
        "SELECT count(*) FROM native_provider_profile_retired_v1", [], |row| row.get(0))?;
    if count >= RETIREMENT_LIMIT { return Err(ProfileError::RetirementLimit); }
    if tx.execute("DELETE FROM native_provider_profiles_v1
        WHERE profile_id=? AND adapter=? AND account_alias=? AND revision=? AND enabled_intent=0",
        params![id, adapter.name(), account, expected])? != 1
    {
        return Err(ProfileError::Conflict);
    }
    tx.execute("INSERT INTO native_provider_profile_retired_v1
        (profile_id,adapter,account_alias,revision) VALUES(?,?,?,?)",
        params![id, adapter.name(), account, expected + 1])?;
    tx.commit()?;
    Ok(Retirement { profile_id: id.to_owned(), adapter, account_alias: account.to_owned(),
        revision: expected + 1, retired: true })
}
