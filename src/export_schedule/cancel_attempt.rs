//! Explicit cancellation of one fenced local export attempt, not its schedule.
use super::{Error,MAX_ATTEMPTS,MAX_TIME,Principal,Result,begin,principal,valid_id,valid_now,writer};
use crate::{events::EventStore,plugins::AccountScope};
use rusqlite::{Connection,OptionalExtension,params};
use serde::{Deserialize,Serialize};

#[derive(Deserialize,Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Attempt {
    pub schedule_id:String,
    pub due_at:i64,
    pub attempt:i64,
    pub lease_until:i64,
}
impl Attempt {
    fn validate(&self,now:i64)->Result<()> {
        if !valid_id(&self.schedule_id)||!valid_now(now)
            ||!(0..=MAX_TIME).contains(&self.due_at)
            ||!(1..=MAX_ATTEMPTS).contains(&self.attempt)
            ||!(0..=MAX_TIME).contains(&self.lease_until) ||self.lease_until<=self.due_at {
            return Err(Error::Invalid);
        }
        if self.lease_until<=now || self.due_at>now {return Err(Error::StaleLease);}
        Ok(())
    }
}

/// Holds no title, preset data, token, path, or artifact. See the schedule
/// inspection action for the exact tuple an operator may choose to cancel.
fn require_active(db:&Connection,key:&Principal,target:&Attempt,now:i64)->Result<()> {
    target.validate(now)?;
    let objects:(i64,i64)=db.query_row(
        "SELECT count(*),count(CASE WHEN type='table' AND name IN
            ('native_export_schedule_v1','native_export_run_v1','native_export_clock_v1') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
            ('native_export_schedule_v1','native_export_run_v1','native_export_clock_v1')",
        [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match objects {(0,0)=>return Err(Error::StaleLease),(3,3)=>{},_=>return Err(Error::Storage)}
    let active:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM native_export_run_v1 r
         JOIN native_export_schedule_v1 s ON s.owner=r.owner AND s.account=r.account AND s.id=r.schedule_id
         WHERE r.owner=?1 AND r.account=?2 AND r.schedule_id=?3 AND r.due_at=?4
          AND r.state='leased' AND r.attempt=?5 AND r.lease_until=?6 AND r.lease_until>?7
          AND s.enabled=1)",
        params![key.owner,key.account,target.schedule_id,target.due_at,target.attempt,target.lease_until,now],
        |r|r.get(0))?;
    if !active{return Err(Error::StaleLease);}
    let clock:Option<i64>=db.query_row("SELECT observed_at FROM native_export_clock_v1 WHERE account=?1",
        [&key.account],|r|r.get(0)).optional()?;
    if !clock.is_some_and(|clock|(0..=now).contains(&clock)){return Err(Error::StaleLease);}
    Ok(())
}

pub(crate) fn preflight(store:&EventStore,plugin:&str,scope:&AccountScope,target:&Attempt,now:i64)
    ->Result<()> {
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    require_active(&tx,&key,target,now)
}

pub(crate) fn cancel(store:&EventStore,plugin:&str,scope:&AccountScope,target:&Attempt,now:i64)
    ->Result<()> {
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    require_active(&tx,&key,target,now)?;
    // The publisher must pass its exact lease check inside its own writer
    // transaction before spending recurring authority or inserting an artifact.
    let changed=tx.execute("UPDATE native_export_run_v1 SET state='cancelled',lease_until=0
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND due_at=?4
          AND state='leased' AND attempt=?5 AND lease_until=?6 AND lease_until>?7",
        params![key.owner,key.account,target.schedule_id,target.due_at,target.attempt,target.lease_until,now])?;
    if changed!=1{return Err(Error::StaleLease);}
    tx.commit()?;
    Ok(())
}
