//! Manual local annotations only. This module has no dispatch or provider dependency.
use crate::{events::{EventError, EventStore}, plugins::AccountScope, profile_lock::Verifier};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const REQUEST_TTL: i64 = 86_400;
const CHALLENGE_TTL: i64 = 300;
const RETAIN: i64 = 30 * 86_400;
const MAX_REVISION: i64 = 9_007_199_254_740_991;

const SCHEMA: &str = "
CREATE TABLE native_review_operator (
 one INTEGER PRIMARY KEY CHECK(one=1), salt BLOB NOT NULL CHECK(length(salt)=16),
 digest BLOB NOT NULL CHECK(length(digest)=32)
);
CREATE TABLE native_draft_review (
 id TEXT PRIMARY KEY NOT NULL, grant_id TEXT NOT NULL, platform TEXT NOT NULL,
 account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
 body_digest BLOB NOT NULL CHECK(length(body_digest)=32),
 created INTEGER NOT NULL, expires INTEGER NOT NULL CHECK(expires>created),
 state TEXT NOT NULL CHECK(state IN ('pending','accepted_for_review','rejected')),
 challenge BLOB CHECK(challenge IS NULL OR length(challenge)=32), challenge_until INTEGER,
 CHECK((challenge IS NULL)=(challenge_until IS NULL)),
 CHECK(state='pending' OR challenge IS NULL),
 UNIQUE(grant_id,platform,account_id,conversation_id,revision)
) WITHOUT ROWID;
";

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid review input")] Input,
    #[error("review access denied")] Permission,
    #[error("review operator is not provisioned")] Disabled,
    #[error("review not found")] Missing,
    #[error("review is no longer current")] Stale,
    #[error("local review quota reached")] Quota,
    #[error("review store unavailable")] Storage,
}

impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}
impl From<EventError> for Error {
    fn from(value: EventError) -> Self {
        if matches!(value, EventError::ProfileLocked) { Self::Permission } else { Self::Storage }
    }
}
type Result<T> = std::result::Result<T,Error>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prepare {
    conversation_id: String,
    expected_revision: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    pub review_id: String,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all="snake_case")]
pub(crate) enum Decision { AcceptForReview, Reject }
impl Decision {
    fn state(self) -> &'static str {
        match self { Self::AcceptForReview=>"accepted_for_review", Self::Reject=>"rejected" }
    }
}

#[derive(Serialize)]
pub(crate) struct Status {
    review_id: String,
    revision: i64,
    state: &'static str,
}

#[derive(Serialize)]
pub(crate) struct Inspection {
    review_id: String,
    platform: String,
    account_id: String,
    conversation_id: String,
    revision: i64,
    body: String,
    #[serde(skip_serializing_if="Option::is_none")]
    challenge: Option<String>,
    #[serde(skip_serializing_if="Option::is_none")]
    challenge_expires_at: Option<i64>,
}

fn installed(db: &Connection) -> Result<bool> {
    let (count,tables):(i64,i64) = db.query_row(
        "SELECT COUNT(*),COUNT(CASE WHEN type='table' AND name IN
         ('native_review_operator','native_draft_review') THEN 1 END)
         FROM sqlite_schema WHERE name COLLATE NOCASE IN
         ('native_review_operator','native_draft_review')", [],
        |r|Ok((r.get(0)?,r.get(1)?))
    )?;
    match (count,tables) { (0,0)=>Ok(false), (2,2)=>Ok(true), _=>Err(Error::Storage) }
}

pub(crate) fn provision(store: &EventStore, passphrase: &str) -> Result<()> {
    store.require_ordinary()?;
    let verifier = Verifier::new(passphrase).ok_or(Error::Input)?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if installed(&tx)? { return Err(Error::Stale); }
    tx.execute_batch(SCHEMA)?;
    tx.execute("INSERT INTO native_review_operator VALUES (1,?1,?2)",
        params![verifier.salt.as_slice(),verifier.digest.as_slice()])?;
    tx.commit()?;
    Ok(())
}

fn require_installed(db: &Connection) -> Result<()> {
    if installed(db)? { Ok(()) } else { Err(Error::Disabled) }
}

pub(crate) fn authenticate(store: &EventStore, passphrase: &str) -> Result<()> {
    store.require_ordinary()?;
    let db = store.read_connection()?;
    require_installed(&db)?;
    let verifier = db.query_row("SELECT salt,digest FROM native_review_operator WHERE one=1",
        [], |r|Ok(Verifier{salt:r.get(0)?,digest:r.get(1)?}))?;
    if !verifier.verify(passphrase) { return Err(Error::Permission); }
    Ok(())
}

fn random_hex(bytes: usize) -> Result<String> {
    let mut buffer = Zeroizing::new(vec![0_u8;bytes]);
    getrandom::fill(buffer.as_mut_slice()).map_err(|_|Error::Storage)?;
    Ok(hex::encode(&*buffer))
}
fn hex_token(value:&str, length:usize) -> bool {
    value.len()==length && value.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b))
}
fn now(db:&Connection) -> Result<i64> {
    let n:i64 = db.query_row("SELECT unixepoch()",[],|r|r.get(0))?;
    if !(1..=MAX_REVISION).contains(&n) { return Err(Error::Storage); }
    Ok(n)
}

struct Draft { body:String, digest:[u8;32] }
fn draft(
    db:&Connection, scope:&AccountScope, conversation:&str, revision:i64,
) -> Result<Option<Draft>> {
    let has_table: bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE name='native_drafts' AND type='table')",
        [],|r|r.get(0))?;
    if !has_table { return Ok(None); }
    let body: Option<String> = db.query_row(
        "SELECT d.body FROM native_drafts d JOIN conversations c
          ON c.platform COLLATE BINARY=d.platform AND c.account_id COLLATE BINARY=d.account_id
         AND c.id COLLATE BINARY=d.conversation_id
         WHERE d.platform COLLATE BINARY=?1 AND d.account_id COLLATE BINARY=?2
          AND d.conversation_id COLLATE BINARY=?3 AND d.revision=?4
          AND length(CAST(d.body AS BLOB)) BETWEEN 1 AND 16384",
        params![scope.platform,scope.account_id,conversation,revision],|r|r.get(0)
    ).optional()?;
    Ok(body.map(|body|Draft{digest:Sha256::digest(body.as_bytes()).into(),body}))
}

#[derive(Debug)]
struct Review {
    id:String, grant:String, scope:AccountScope, conversation:String,
    revision:i64, body_digest:[u8;32], created:i64, expires:i64, state:String,
    challenge:Option<[u8;32]>, challenge_until:Option<i64>,
}

fn by_id(
    db:&Connection, id:&str, scope:&AccountScope, grant:Option<&str>,
) -> Result<Review> {
    if !hex_token(id,48) { return Err(Error::Input); }
    let (private_fields,clause) = if grant.is_some() {
        ("NULL,NULL"," AND grant_id COLLATE BINARY=?4")
    } else { ("challenge,challenge_until","") };
    let sql = format!("SELECT id,grant_id,platform,account_id,conversation_id,revision,body_digest,
        created,expires,state,{private_fields} FROM native_draft_review WHERE id COLLATE BINARY=?1
        AND platform COLLATE BINARY=?2 AND account_id COLLATE BINARY=?3{clause}");
    let row = |r:&rusqlite::Row<'_>|Ok(Review{
        id:r.get(0)?,grant:r.get(1)?,scope:AccountScope{platform:r.get(2)?,account_id:r.get(3)?},
        conversation:r.get(4)?,revision:r.get(5)?,body_digest:r.get(6)?,created:r.get(7)?,
        expires:r.get(8)?,state:r.get(9)?,challenge:r.get(10)?,challenge_until:r.get(11)?,
    });
    let found=if let Some(grant)=grant {
        db.query_row(&sql,params![id,scope.platform,scope.account_id,grant],row).optional()?
    } else {
        db.query_row(&sql,params![id,scope.platform,scope.account_id],row).optional()?
    };
    found.ok_or(Error::Missing)
}

fn current(db:&Connection, item:&Review) -> Result<Draft> {
    draft(db,&item.scope,&item.conversation,item.revision)?
        .filter(|d|bool::from(d.digest.as_slice().ct_eq(item.body_digest.as_slice())))
        .ok_or(Error::Stale)
}

/// Check the prerequisite annotation and exact current draft within the
/// caller's transaction. This function grants no export or delivery right.
#[cfg(any(target_os="linux",target_os="macos"))]
pub(crate) fn checked_handoff_body(
    db:&Connection, scope:&AccountScope, review_id:&str, conversation_id:&str, revision:i64,
) -> Result<(String,[u8;32])> {
    require_installed(db)?;
    let item=by_id(db,review_id,scope,None)?;
    if item.state!="accepted_for_review" || item.conversation!=conversation_id || item.revision!=revision {
        return Err(Error::Stale);
    }
    let draft=current(db,&item)?;
    Ok((draft.body,draft.digest))
}

fn still_pending(item:&Review, time:i64) -> bool {
    item.state=="pending" && item.created<=time && time<item.expires
}

fn status_of(db:&Connection, item:Review, time:i64) -> Result<Status> {
    let state=match item.state.as_str() {
        "accepted_for_review"=>"accepted_for_review",
        "rejected"=>"rejected",
        "pending" if !still_pending(&item,time)=>"expired",
        "pending"=>match current(db,&item) {
            Ok(_)=>"pending",
            Err(Error::Stale)=>"draft_changed",
            Err(e)=>return Err(e),
        },
        _=>return Err(Error::Storage),
    };
    Ok(Status{review_id:item.id,revision:item.revision,state})
}

pub(crate) fn prepare(store:&EventStore, grant:&str, scope:&AccountScope, q:Prepare) -> Result<Status> {
    let id=&q.conversation_id;
    if id.trim().is_empty() || id.len()>2048 || id.chars().count()>512 || id.chars().any(char::is_control)
        || !(1..=MAX_REVISION).contains(&q.expected_revision)
    { return Err(Error::Input); }
    store.require_ordinary()?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    require_installed(&tx)?;
    let time=now(&tx)?;
    let revision=q.expected_revision;
    let candidate=draft(&tx,scope,id,revision)?.ok_or(Error::Stale)?;
    let existing: Option<String> = tx.query_row(
        "SELECT id FROM native_draft_review WHERE grant_id COLLATE BINARY=?1 AND platform COLLATE BINARY=?2
         AND account_id COLLATE BINARY=?3 AND conversation_id COLLATE BINARY=?4 AND revision=?5",
        params![grant,scope.platform,scope.account_id,id,revision], |r|r.get(0)).optional()?;
    if let Some(review_id)=existing {
        return status_of(&tx,by_id(&tx,&review_id,scope,Some(grant))?,time);
    }
    tx.execute("DELETE FROM native_draft_review WHERE created<?1",[time-RETAIN])?;
    let total:i64=tx.query_row("SELECT COUNT(*) FROM native_draft_review",[],|r|r.get(0))?;
    let own_pending:i64=tx.query_row(
        "SELECT COUNT(*) FROM native_draft_review WHERE grant_id COLLATE BINARY=?1 AND platform COLLATE BINARY=?2
         AND account_id COLLATE BINARY=?3 AND state='pending' AND created<=?4 AND expires>?4",
        params![grant,scope.platform,scope.account_id,time],|r|r.get(0))?;
    if total>=4096 || own_pending>=8 { return Err(Error::Quota); }
    let review_id=random_hex(24)?;
    tx.execute("INSERT INTO native_draft_review
         (id,grant_id,platform,account_id,conversation_id,revision,body_digest,created,expires,state)
         VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,'pending')",
         params![review_id,grant,scope.platform,scope.account_id,id,revision,candidate.digest.as_slice(),
            time,time+REQUEST_TTL])?;
    tx.commit()?;
    Ok(Status{review_id,revision,state:"pending"})
}

pub(crate) fn status(store:&EventStore, grant:&str, scope:&AccountScope, id:&str) -> Result<Status> {
    store.require_ordinary()?;
    let mut db=store.read_connection()?;
    let tx=db.transaction()?;
    store.verify_connection(&tx)?;
    require_installed(&tx)?;
    let item=by_id(&tx,id,scope,Some(grant))?;
    status_of(&tx,item,now(&tx)?)
}

fn challenge_hash(store:&EventStore,item:&Review,until:i64,token:&str) -> Result<[u8;32]> {
    let bytes=Zeroizing::new(serde_json::to_vec(&("draft-review-v1",store.archive_id(),
        &item.id,&item.grant,&item.scope.platform,&item.scope.account_id,&item.conversation,
        item.revision,item.body_digest,item.created,item.expires,until,token)).map_err(|_|Error::Storage)?);
    Ok(Sha256::digest(bytes.as_slice()).into())
}

pub(crate) fn inspect(store:&EventStore, passphrase:&str, scope:&AccountScope, id:&str) -> Result<Inspection> {
    authenticate(store,passphrase)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let item=by_id(&tx,id,scope,None)?;
    let time=now(&tx)?;
    if !still_pending(&item,time) { return Err(Error::Stale); }
    let candidate=current(&tx,&item)?;
    let token=random_hex(32)?;
    let until=(time+CHALLENGE_TTL).min(item.expires);
    let tag=challenge_hash(store,&item,until,&token)?;
    tx.execute("UPDATE native_draft_review SET challenge=?2,challenge_until=?3 WHERE id=?1",
        params![id,tag.as_slice(),until])?;
    tx.commit()?;
    Ok(Inspection{review_id:item.id,platform:item.scope.platform,account_id:item.scope.account_id,
        conversation_id:item.conversation,revision:item.revision,body:candidate.body,
        challenge:Some(token),challenge_expires_at:Some(until)})
}

pub(crate) fn decide(
    store:&EventStore, passphrase:&str, scope:&AccountScope, id:&str, token:&str, decision:Decision,
) -> Result<Status> {
    if !hex_token(token,64) { return Err(Error::Input); }
    authenticate(store,passphrase)?;
    let mut db=store.write_connection()?;
    let tx=db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let item=by_id(&tx,id,scope,None)?;
    let time=now(&tx)?;
    if !still_pending(&item,time) { return Err(Error::Stale); }
    let (saved,until)=item.challenge.zip(item.challenge_until).ok_or(Error::Stale)?;
    if until<=time || until>time+CHALLENGE_TTL || until>item.expires
        || !bool::from(saved.as_slice().ct_eq(challenge_hash(store,&item,until,token)?.as_slice()))
    { return Err(Error::Stale); }
    current(&tx,&item)?;
    let state=decision.state();
    let changed=tx.execute("UPDATE native_draft_review SET state=?2,challenge=NULL,challenge_until=NULL
        WHERE id=?1 AND state='pending'",params![id,state])?;
    if changed!=1 { return Err(Error::Stale); }
    tx.commit()?;
    Ok(Status{review_id:item.id,revision:item.revision,state})
}
