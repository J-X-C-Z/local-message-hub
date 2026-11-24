//! Read-only observation of one owned scheduler ledger, not a worker heartbeat.
use super::{Error,LEASE,MAX_ATTEMPTS,MAX_INTERVAL,MAX_TIME,MIN_INTERVAL,Result,decode_schedule,
    principal,require_preset,retention,valid_id,valid_now};
use crate::{events::EventStore,plugins::AccountScope};
use rusqlite::{OptionalExtension,params};
use serde::Serialize;

#[derive(Serialize)]
pub(crate) struct Inspection {
    schedule_id:String,
    observed_at:i64,
    enabled:bool,
    preset_matches:bool,
    interval_seconds:i64,
    next_unclaimed_slot:i64,
    ledger_status:&'static str,
    lease:Option<Lease>,
    latest_terminal_slot:Option<Terminal>,
}

#[derive(Serialize)]
struct Lease {due_at:i64,attempt:i64,lease_until:i64,expired:bool}

#[derive(Serialize)]
struct Terminal {due_at:i64,attempt:i64,state:String}

// No calls to begin/write_connection/clock advancement, claim, render or
// publish are permitted here, even when an expired lease is observed.
pub(crate) fn schedule(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,now:i64)
    ->Result<Inspection> {
    if !valid_id(id)||!valid_now(now){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    let objects:(i64,i64)=tx.query_row(
        "SELECT count(*),count(CASE WHEN type='table' AND
          name IN ('native_export_schedule_v1','native_export_run_v1') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN ('native_export_schedule_v1','native_export_run_v1')",
        [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match objects {
        (0,0)=>return Err(Error::Missing),
        (2,2)=>{},
        _=>return Err(Error::Storage),
    }
    let schedule=tx.query_row(
        "SELECT id,preset_id,preset_revision,interval_seconds,next_due,enabled
         FROM native_export_schedule_v1 WHERE owner=?1 AND account=?2 AND id=?3",
        params![key.owner,key.account,id],decode_schedule).optional()?.ok_or(Error::Missing)?;
    if !(MIN_INTERVAL..=MAX_INTERVAL).contains(&schedule.interval_seconds)
        || !(0..=MAX_TIME).contains(&schedule.next_due) {return Err(Error::Storage);}

    let preset_matches=match require_preset(&tx,store,plugin,scope,&schedule.preset_id,schedule.preset_revision) {
        Ok(())=>true,
        Err(Error::StalePreset)=>false,
        Err(e)=>return Err(e),
    };
    let mut live=tx.prepare("SELECT due_at,attempt,lease_until FROM native_export_run_v1
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND state='leased' LIMIT 2")?;
    let active=live.query_map(params![key.owner,key.account,id],|r|Ok(Lease {
        due_at:r.get(0)?,attempt:r.get(1)?,lease_until:r.get(2)?,expired:false,
    }))?.collect::<rusqlite::Result<Vec<_>>>()?;
    let lease=match active.as_slice() {
        []=>None,
        [one] if (0..schedule.next_due).contains(&one.due_at)
            && (1..=MAX_ATTEMPTS).contains(&one.attempt)
            && ((one.due_at+LEASE)..=MAX_TIME).contains(&one.lease_until) =>
            Some(Lease {due_at:one.due_at,attempt:one.attempt,lease_until:one.lease_until,
                expired:one.lease_until<=now}),
        _=>return Err(Error::Storage),
    };
    let latest_terminal_slot:Option<Terminal>=tx.query_row(
        "SELECT due_at,attempt,state FROM native_export_run_v1
         WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND state<>'leased'
         ORDER BY due_at DESC LIMIT 1",
        params![key.owner,key.account,id],|r|Ok(Terminal{due_at:r.get(0)?,attempt:r.get(1)?,state:r.get(2)?}))
        .optional()?;
    if latest_terminal_slot.as_ref().is_some_and(|slot|
        !(0..schedule.next_due).contains(&slot.due_at)||!(1..=MAX_ATTEMPTS).contains(&slot.attempt)
        ||!matches!(slot.state.as_str(),"cancelled"|"expired"|"rendered"|"failed")) {
        return Err(Error::Storage);
    }
    let ledger_status=if !schedule.enabled {"disabled"}
        else if !preset_matches {"preset_changed"}
        else if let Some(held)=&lease {
            if !held.expired {"leased"}
            else if held.attempt<MAX_ATTEMPTS {"lease_expired_retry_available"}
            else {"lease_expired_attempts_exhausted"}
        } else if schedule.next_due<=now {"slot_due"}
        else {"waiting_for_slot"};
    Ok(Inspection{schedule_id:id.to_owned(),observed_at:now,enabled:schedule.enabled,preset_matches,
        interval_seconds:schedule.interval_seconds,next_unclaimed_slot:schedule.next_due,
        ledger_status,lease,latest_terminal_slot})
}

#[derive(Serialize)]
pub(crate) struct RunInspection<'a> {
    #[serde(flatten)]
    slot:&'a retention::Slot,
    observed_at:i64,
    state:&'static str,
    attempt:Option<i64>,
    lease_until:Option<i64>,
    artifact_state:Option<&'static str>,
}

/// Reconcile a known due slot after losing a claim/publication response.
/// No writes or clock advancement, even when reading an elapsed lease.
pub(crate) fn run<'a>(store:&EventStore,plugin:&str,scope:&AccountScope,slot:&'a retention::Slot,now:i64)
    ->Result<RunInspection<'a>> {
    if !slot.valid()||!valid_now(now){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    let objects:(i64,i64)=tx.query_row("SELECT count(*),count(CASE WHEN type='table' AND
        name IN ('native_export_schedule_v1','native_export_run_v1') THEN 1 END)
        FROM sqlite_schema WHERE name COLLATE NOCASE IN ('native_export_schedule_v1','native_export_run_v1')",
        [],|r|Ok((r.get(0)?,r.get(1)?)))?;
    match objects {
        (0,0)=>return Err(Error::Missing),
        (2,2)=>(),
        _=>return Err(Error::Storage),
    }
    let next_due:i64=tx.query_row("SELECT next_due FROM native_export_schedule_v1
        WHERE owner=?1 AND account=?2 AND id=?3",
        params![key.owner,key.account,slot.schedule_id],|r|r.get(0)).optional()?.ok_or(Error::Missing)?;
    if !(0..=MAX_TIME).contains(&next_due) {return Err(Error::Storage);}
    let row:Option<(String,i64,i64)>=tx.query_row(
        "SELECT state,attempt,lease_until FROM native_export_run_v1
         WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND due_at=?4",
        params![key.owner,key.account,slot.schedule_id,slot.due_at],
        |r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
    let mut record=RunInspection{slot,observed_at:now,state:"not_recorded",
        attempt:None,lease_until:None,artifact_state:None};
    if let Some((state,attempt,lease))=row {
        if !(1..=MAX_ATTEMPTS).contains(&attempt)||slot.due_at>=next_due {
            return Err(Error::Storage);
        }
        record.attempt=Some(attempt);
        record.state=match (state.as_str(),lease) {
            ("leased",value) if ((slot.due_at+LEASE)..=MAX_TIME).contains(&value)=>{
                record.lease_until=Some(value);
                if value<=now {"lease_elapsed"} else {"leased"}
            }
            ("cancelled",0)=>"cancelled",
            ("expired",0)=>"expired",
            ("failed",0)=>"failed",
            ("rendered",0)=>{
                record.artifact_state=Some(retention::artifact_state_in(&tx,&key,slot)?);
                "published"
            }
            _=>return Err(Error::Storage)
        };
    }
    tx.commit()?;
    Ok(record)
}
