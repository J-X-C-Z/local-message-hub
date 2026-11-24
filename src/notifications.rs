//! Local notification preferences and pull-only incoming metadata. No delivery.
use crate::{events::{EventError, EventStore},plugins::AccountScope};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize,Serialize};
use sha2::{Digest,Sha256};
use std::collections::BTreeSet;

const TABLE:&str="CREATE TABLE IF NOT EXISTS native_notification_preferences (
 platform TEXT NOT NULL, account_id TEXT NOT NULL, plugin_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision > 0), preferences TEXT NOT NULL,
 PRIMARY KEY(platform, account_id, plugin_id)) WITHOUT ROWID;";
const MAX_IDS:usize=100;
const MAX_ID_BYTES:usize=256;
pub(crate) const MAX_BATCH:usize=50;
#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid notification input")] Input,
    #[error("notification revision changed")] Conflict,
    #[error("notification storage unavailable")] Storage,
}
impl From<rusqlite::Error> for Error { fn from(_:rusqlite::Error)->Self {Self::Storage} }
impl From<EventError> for Error { fn from(_:EventError)->Self {Self::Storage} }
type Result<T>=std::result::Result<T,Error>;

#[derive(Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub(crate) enum Mode { #[default] All, Allowlist }
#[derive(Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub(crate) enum Style { #[default] Normal, Silent }

#[derive(Clone, Default, PartialEq, Eq, Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Preference {
    pub enabled:bool,
    mode:Mode,
    style:Style,
    muted_conversations:Vec<String>,
    allowed_conversations:Vec<String>,
}
impl Preference {
    fn normalize(&mut self)->Result<()> {
        for ids in [&mut self.muted_conversations,&mut self.allowed_conversations] {
            if ids.len()>MAX_IDS || ids.iter().any(|id| !valid_id(id)) {return Err(Error::Input);}
            let mut seen=BTreeSet::new();
            ids.retain(|id|seen.insert(id.clone()));
        }
        Ok(())
    }
    fn allows(&self,id:&str)->bool {
        self.enabled && !self.muted_conversations.iter().any(|v|v==id)
            && (self.mode==Mode::All || self.allowed_conversations.iter().any(|v|v==id))
    }
}
pub(crate) fn valid_id(id:&str)->bool {
    !id.is_empty() && id.len()<=MAX_ID_BYTES && id.trim()==id
        && !id.chars().any(char::is_control)
}

#[derive(Serialize)]
pub(crate) struct State { pub revision:i64, pub preferences:Preference }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Change { revision:i64, preferences:Preference }

/// Scoped rendering of the Python notification-filter shape. Neither this
/// shape nor its writer can switch the delivery/runtime or enabled flags.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ScopedFilters {
    platforms:Vec<String>,
    mode:Mode,
    style:Style,
    muted_conversations:Vec<String>,
    allowed_conversations:Vec<String>,
}
impl State {
    pub(crate) fn settings_view(&self,scope:&AccountScope)->serde_json::Value {
        let pref=&self.preferences;
        let keys=|ids:&[String]| ids.iter().map(|id|
            serde_json::to_string(&(&scope.platform,&scope.account_id,id))
                .expect("serializing strings cannot fail")).collect();
        serde_json::json!({
            "notification_revision":self.revision,
            "notification_preview_enabled":pref.enabled,
            "notification_filters":ScopedFilters {
                platforms:vec![scope.platform.clone()],mode:pref.mode,style:pref.style,
                muted_conversations:keys(&pref.muted_conversations),
                allowed_conversations:keys(&pref.allowed_conversations),
            },
        })
    }
}
pub(crate) fn put_scoped_filters(store:&EventStore,scope:&AccountScope,plugin:&str,
    revision:i64,filters:ScopedFilters)->Result<State> {
    if filters.platforms.len()!=1 || filters.platforms.first()!=Some(&scope.platform) {
        return Err(Error::Input);
    }
    let ids=|keys:Vec<String>| -> Result<Vec<String>> {
        if keys.len()>MAX_IDS {return Err(Error::Input);}
        keys.into_iter().map(|key| {
            if key.len()>4096 {return Err(Error::Input);}
            let (platform,account,id):(String,String,String)=serde_json::from_str(&key)
                .map_err(|_|Error::Input)?;
            if platform!=scope.platform || account!=scope.account_id || !valid_id(&id) {
                return Err(Error::Input);
            }
            Ok(id)
        }).collect()
    };
    let muted_conversations=ids(filters.muted_conversations)?;
    let allowed_conversations=ids(filters.allowed_conversations)?;
    update(store,scope,plugin,revision,|current|Preference {
        enabled:current.enabled,mode:filters.mode,style:filters.style,
        muted_conversations,allowed_conversations,
    })
}
fn installed(db:&Connection)->Result<bool> {
    Ok(db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='native_notification_preferences')",[],|r|r.get(0))?)
}
pub(crate) fn state(db:&Connection,scope:&AccountScope,plugin:&str)->Result<State> {
    if installed(db)? {
        let found=db.query_row(
            "SELECT revision,preferences FROM native_notification_preferences WHERE platform=? AND account_id=? AND plugin_id=?",
            params![scope.platform,scope.account_id,plugin],
            |r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?)),
        ).optional()?;
        if let Some((revision,value))=found {
            if revision<=0 || value.len()>MAX_IDS*MAX_ID_BYTES*12+256 {return Err(Error::Storage);}
            let mut preferences:Preference=serde_json::from_str(&value).map_err(|_|Error::Storage)?;
            preferences.normalize().map_err(|_|Error::Storage)?;
            return Ok(State{revision,preferences});
        }
    }
    Ok(State{revision:0,preferences:Preference::default()})
}
pub(crate) fn get(store:&EventStore,scope:&AccountScope,plugin:&str)->Result<State> {
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    state(&tx,scope,plugin)
}
pub(crate) fn put(store:&EventStore,scope:&AccountScope,plugin:&str,mut change:Change)->Result<State> {
    change.preferences.normalize()?;
    update(store,scope,plugin,change.revision,|_| change.preferences)
}
fn update(store:&EventStore,scope:&AccountScope,plugin:&str,expected_revision:i64,
    desired:impl FnOnce(&Preference)->Preference)->Result<State> {
    if expected_revision<0 {return Err(Error::Input);}
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let current=state(&tx,scope,plugin)?;
    let mut preferences=desired(&current.preferences);
    preferences.normalize()?;
    if preferences==current.preferences {
        return Ok(current);
    }
    if expected_revision!=current.revision {return Err(Error::Conflict);}
    let revision=current.revision.checked_add(1).ok_or(Error::Storage)?;
    let encoded=serde_json::to_string(&preferences).map_err(|_|Error::Storage)?;
    tx.execute_batch(TABLE)?;
    tx.execute(
        "INSERT INTO native_notification_preferences(platform,account_id,plugin_id,revision,preferences)
         VALUES(?1,?2,?3,?4,?5) ON CONFLICT(platform,account_id,plugin_id)
         DO UPDATE SET revision=excluded.revision, preferences=excluded.preferences",
        params![scope.platform,scope.account_id,plugin,revision,encoded],
    )?;
    tx.commit()?;
    Ok(State{revision,preferences})
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PreviewQuery { cursor:Option<String>, limit:Option<usize> }
#[derive(Serialize)]
pub(crate) struct Match { conversation_id:String, style:Style }
#[derive(Serialize)]
pub(crate) struct Preview {
    preference_revision:i64, cursor:String, has_more:bool, items:Vec<Match>,
}

fn namespace(store:&EventStore,scope:&AccountScope,plugin:&str)->String {
    let input=serde_json::to_vec(&("notification-preview-v1",store.archive_id(),
        &scope.platform,&scope.account_id,plugin)).expect("string tuple is serializable");
    hex::encode(Sha256::digest(input))
}

pub(crate) fn preview(store:&EventStore,scope:&AccountScope,plugin:&str,q:PreviewQuery)->Result<Preview> {
    let limit=q.limit.unwrap_or(30);
    if !(1..=MAX_BATCH).contains(&limit) {return Err(Error::Input);}
    let ns=namespace(store,scope,plugin);
    let after=q.cursor.as_deref().map(|c|{
        let (stamp,offset)=c.split_once(':').ok_or(Error::Input)?;
        if stamp!=ns || offset.is_empty() || offset.len()>19
            || !offset.bytes().all(|b| b.is_ascii_digit()) {return Err(Error::Input);}
        offset.parse::<i64>().map_err(|_|Error::Input)
    }).transpose()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let State{revision,preferences}=state(&tx,scope,plugin)?;
    let checkpoint=checkpoint(&tx,scope)?;
    let Some(after)=after else {
        return Ok(Preview{preference_revision:revision,cursor:format!("{ns}:{checkpoint}"),has_more:false,items:vec![]});
    };
    if after>checkpoint {return Err(Error::Input);}
    let Scanned{through,has_more,items}=scan(&tx,scope,&preferences,after,limit)?;
    Ok(Preview{preference_revision:revision,cursor:format!("{ns}:{through}"),has_more,items})
}

pub(crate) fn checkpoint(db:&Connection,scope:&AccountScope)->Result<i64> {
    Ok(db.query_row(
        "SELECT COALESCE(MAX(incoming_id),0) FROM native_account_incoming
          WHERE platform COLLATE BINARY=?1 AND account_id COLLATE BINARY=?2",
        params![scope.platform,scope.account_id],|r|r.get(0),
    )?)
}

/// Validate a stream start in a verified read transaction. A fresh client
/// receives only the current head; a future checkpoint is not accepted.
pub(crate) fn stream_start(store:&EventStore,scope:&AccountScope,after:Option<i64>)->Result<i64> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    let head=checkpoint(&tx,scope)?;
    if after.is_some_and(|after|after>head){return Err(Error::Input);}
    Ok(after.unwrap_or(head))
}

/// Read current preference and scan the exact account on the same snapshot.
pub(crate) fn stream_scan(store:&EventStore,scope:&AccountScope,plugin:&str,after:i64)
    ->Result<Scanned> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    if after>checkpoint(&tx,scope)?{return Err(Error::Input);}
    let policy=state(&tx,scope,plugin)?;
    scan(&tx,scope,&policy.preferences,after,MAX_BATCH)
}

pub(crate) struct Scanned {pub through:i64,pub has_more:bool,pub items:Vec<Match>}

pub(crate) fn scan(db:&Connection,scope:&AccountScope,preferences:&Preference,after:i64,limit:usize)->Result<Scanned> {
    scan_range(db,scope,preferences,after,i64::MAX,limit)
}

pub(crate) fn attests(
    db:&Connection,scope:&AccountScope,preferences:&Preference,after:i64,through:i64,id:&str,
)->Result<bool> {
    let page=scan_range(db,scope,preferences,after,through,MAX_BATCH)?;
    Ok(!page.has_more && page.through==through && page.items.iter().any(|m|m.conversation_id==id))
}

fn scan_range(
    db:&Connection,scope:&AccountScope,preferences:&Preference,after:i64,through:i64,limit:usize,
)->Result<Scanned> {
    let mut statement=db.prepare(
        "SELECT s.incoming_id,j.conversation_id FROM native_account_incoming s
         JOIN native_event_journal j ON j.event_id=s.event_id
          AND j.platform COLLATE BINARY=s.platform AND j.account_id COLLATE BINARY=s.account_id AND j.kind='created'
         WHERE s.platform COLLATE BINARY=?1 AND s.account_id COLLATE BINARY=?2 AND s.incoming_id>?3
          AND s.incoming_id<=?5
         ORDER BY s.incoming_id LIMIT ?4"
    )?;
    let mut rows=statement.query(params![scope.platform,scope.account_id,after,limit+1,through])?;
    let mut items=Vec::new();
    let mut through=after;
    for _ in 0..limit {
        let Some(row)=rows.next()? else {break;};
        through=row.get(0)?;
        let id:String=row.get(1)?;
        if valid_id(&id) && preferences.allows(&id) {
            items.push(Match{conversation_id:id,style:preferences.style});
        }
    }
    Ok(Scanned{through,has_more:rows.next()?.is_some(),items})
}
