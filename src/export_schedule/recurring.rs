//! A separately approved, expiring authority to publish to the local BLOB
//! ledger only. This never creates a manual content-confirmation receipt.
use super::*;

const MAX_DURATION:i64=30*86_400;
const MAX_RUNS:i64=32;
const TABLE:&str="
CREATE TABLE IF NOT EXISTS native_export_recurring_v1(
    owner TEXT NOT NULL, account TEXT NOT NULL, schedule_id TEXT NOT NULL,
    generation TEXT NOT NULL CHECK(length(generation)=64),
    authority TEXT NOT NULL CHECK(length(authority)=64),
    preset_id TEXT NOT NULL, preset_revision INTEGER NOT NULL,
    first_slot INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at>first_slot),
    remaining INTEGER NOT NULL CHECK(remaining BETWEEN 0 AND 32),
    PRIMARY KEY(owner,schedule_id)
) WITHOUT ROWID;";

#[derive(Debug,Serialize)]
pub(crate) struct Offer {
    schedule_id:String,
    preset_id:String,
    preset_revision:i64,
    interval_seconds:i64,
    first_slot:i64,
    expires_at:i64,
    max_artifacts:i64,
    output_format:&'static str,
    conversation_ids:Vec<String>,
    start:Option<i64>,
    end:Option<i64>,
    mode:&'static str,
    future_content:bool,
    maximum_artifact_bytes:usize,
    destination:&'static str,
    auto_retrieve:bool,
}

#[derive(Debug,Serialize)]
pub(crate) struct State {
    pub first_slot:i64,
    pub expires_at:i64,
    pub remaining:i64,
}

/// Only this module can build a permit from an authorized, durable rule.
pub(crate) struct Permit {
    pub state:State,
    generation:String,
    authority:String,
    schedule_id:String,
}
impl Permit {
    pub(super) fn generation(&self)->&str { &self.generation }
}

fn available(db:&Connection)->Result<bool> {
    let kind:Option<String>=db.query_row(
        "SELECT type FROM sqlite_schema WHERE name='native_export_recurring_v1' COLLATE NOCASE",
        [],|r|r.get(0)).optional()?;
    match kind.as_deref(){None=>Ok(false),Some("table")=>Ok(true),_=>Err(Error::Storage)}
}

fn validate_terms(now:i64,expires_at:i64,max_artifacts:i64)->Result<()> {
    if !valid_now(now)||!(now+1..=(now+MAX_DURATION).min(MAX_TIME)).contains(&expires_at)
        || !(1..=MAX_RUNS).contains(&max_artifacts) {return Err(Error::Invalid);}
    Ok(())
}
fn read_clock(db:&Connection,key:&Principal,now:i64)->Result<()> {
    let clock:i64=db.query_row("SELECT observed_at FROM native_export_clock_v1 WHERE account=?1",
        [&key.account],|r|r.get(0))?;
    if now<clock {return Err(Error::ClockRegression);}
    Ok(())
}

pub(crate) fn preview(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,
    now:i64,expires_at:i64,max_artifacts:i64)->Result<Offer> {
    if !valid_id(id){return Err(Error::Invalid);}
    validate_terms(now,expires_at,max_artifacts)?;
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    let schedule=get(&tx,&key.owner,id)?;
    read_clock(&tx,&key,now)?;
    if !schedule.enabled||schedule.next_due<=now||schedule.next_due>=expires_at {
        return Err(Error::Invalid);
    }
    let spec=export_presets::render_spec_in(&tx,store,plugin,scope,&schedule.preset_id,schedule.preset_revision)
        .map_err(preset_error)?;
    let output_format=artifact_format(spec.format)?;
    tx.commit()?;
    Ok(Offer{schedule_id:id.to_owned(),preset_id:schedule.preset_id,preset_revision:schedule.preset_revision,
        interval_seconds:schedule.interval_seconds,first_slot:schedule.next_due,expires_at,max_artifacts,
        output_format,conversation_ids:spec.conversation_ids,start:spec.start,end:spec.end,
        mode:"permit_recurring_publication_without_per_artifact_confirmation",future_content:true,
        maximum_artifact_bytes:ARTIFACT_MAX,destination:"this_private_ordinary_archive_only",auto_retrieve:false})
}

/// Caller must have obtained explicit informed approval of this exact Offer.
pub(crate) fn enable(store:&EventStore,plugin:&str,scope:&AccountScope,offer:Offer,
    authority:&str,now:i64)->Result<State> {
    validate_terms(now,offer.expires_at,offer.max_artifacts)?;
    if !valid_id(authority){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    let schedule=get(&tx,&key.owner,&offer.schedule_id)?;
    if !schedule.enabled||schedule.preset_id!=offer.preset_id||schedule.preset_revision!=offer.preset_revision
        ||schedule.next_due!=offer.first_slot||schedule.interval_seconds!=offer.interval_seconds
        ||offer.first_slot<=now {return Err(Error::RecurringDenied);}
    require_preset(&tx,store,plugin,scope,&offer.preset_id,offer.preset_revision)?;
    let running:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM native_export_run_v1
        WHERE owner=?1 AND schedule_id=?2 AND state='leased')",params![key.owner,offer.schedule_id],|r|r.get(0))?;
    if running {return Err(Error::Busy);}
    advance_clock(&tx,&key.account,now)?;
    tx.execute_batch(TABLE)?;
    let mut random=[0;32];
    getrandom::fill(&mut random).map_err(|_|Error::Storage)?;
    let generation=hex::encode(random);
    if tx.execute("INSERT INTO native_export_recurring_v1(owner,account,schedule_id,generation,authority,
        preset_id,preset_revision,first_slot,expires_at,remaining)
        VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)
        ON CONFLICT(owner,schedule_id) DO UPDATE SET generation=excluded.generation,authority=excluded.authority,
        preset_id=excluded.preset_id,preset_revision=excluded.preset_revision,first_slot=excluded.first_slot,
        expires_at=excluded.expires_at,remaining=excluded.remaining
        WHERE native_export_recurring_v1.expires_at<=?11 OR native_export_recurring_v1.remaining=0",
        params![key.owner,key.account,offer.schedule_id,generation,authority,offer.preset_id,offer.preset_revision,
            offer.first_slot,offer.expires_at,offer.max_artifacts,now])?!=1 {return Err(Error::RecurringDenied);}
    tx.commit()?;
    Ok(State{first_slot:offer.first_slot,expires_at:offer.expires_at,remaining:offer.max_artifacts})
}

pub(crate) fn revoke(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str)->Result<()> {
    if !valid_id(id){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    let mut db=writer(store)?;
    let tx=begin(store,&mut db)?;
    if !available(&tx)? {return Err(Error::RecurringDenied);}
    if tx.execute("DELETE FROM native_export_recurring_v1 WHERE owner=?1 AND account=?2 AND schedule_id=?3",
        params![key.owner,key.account,id])?!=1 {return Err(Error::RecurringDenied);}
    tx.commit()?;
    Ok(())
}

pub(crate) fn current(store:&EventStore,plugin:&str,scope:&AccountScope,id:&str,
    authority:&str,now:i64)->Result<Permit> {
    if !valid_id(id)||!valid_id(authority)||!valid_now(now){return Err(Error::Invalid);}
    let key=principal(store,plugin,scope)?;
    store.require_ordinary().map_err(|_|Error::Storage)?;
    let mut db=store.read_connection().map_err(|_|Error::Storage)?;
    let tx=db.transaction()?;
    store.verify_connection(&tx).map_err(|_|Error::Storage)?;
    if !available(&tx)? {return Err(Error::RecurringDenied);}
    let schedule=get(&tx,&key.owner,id)?;
    require_preset(&tx,store,plugin,scope,&schedule.preset_id,schedule.preset_revision)?;
    read_clock(&tx,&key,now)?;
    let permit=tx.query_row("SELECT generation,first_slot,expires_at,remaining FROM native_export_recurring_v1
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND authority=?4",
        params![key.owner,key.account,id,authority],|r|Ok(Permit{generation:r.get(0)?,authority:authority.to_owned(),
            schedule_id:id.to_owned(),state:State{first_slot:r.get(1)?,expires_at:r.get(2)?,remaining:r.get(3)?}}))
        .optional()?.ok_or(Error::RecurringDenied)?;
    check(&tx,&key,&schedule,&permit,None,now)?;
    tx.commit()?;
    Ok(permit)
}

pub(super) fn check(db:&Connection,key:&Principal,schedule:&Schedule,permit:&Permit,
    slot:Option<i64>,now:i64)->Result<()> {
    if !schedule.enabled||schedule.id!=permit.schedule_id||permit.state.expires_at<=now
        ||slot.is_some_and(|due|due<permit.state.first_slot) {return Err(Error::RecurringDenied);}
    let active:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM native_export_recurring_v1
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND authority=?4 AND generation=?5
        AND preset_id=?6 AND preset_revision=?7 AND first_slot=?8 AND expires_at=?9
        AND expires_at>?10 AND remaining>0)",
        params![key.owner,key.account,schedule.id,permit.authority,permit.generation,
            schedule.preset_id,schedule.preset_revision,permit.state.first_slot,permit.state.expires_at,now],
        |r|r.get(0))?;
    if !active{return Err(Error::RecurringDenied);}
    Ok(())
}

pub(super) fn spend(tx:&Transaction<'_>,key:&Principal,schedule:&Schedule,permit:&Permit,
    slot:i64,now:i64)->Result<()> {
    check(tx,key,schedule,permit,Some(slot),now)?;
    if tx.execute("UPDATE native_export_recurring_v1 SET remaining=remaining-1
        WHERE owner=?1 AND account=?2 AND schedule_id=?3 AND generation=?4 AND remaining>0",
        params![key.owner,key.account,schedule.id,permit.generation])?!=1 {return Err(Error::RecurringDenied);}
    Ok(())
}
