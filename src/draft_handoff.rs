//! Owner-approved, local export only. No transport, sender, or completion callback.
use crate::{draft_handoff_receipt::{self, Binding, Export}, draft_review, events::EventStore,
    plugins::AccountScope};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use zeroize::{Zeroize, Zeroizing};

const TTL: i64 = 120;
const MAX_ROWS: i64 = 4_096;
const MAX_REVISION: i64 = 9_007_199_254_740_991;
const TABLE: &str = "native_draft_handoff_v1";
const SCHEMA: &str = "
CREATE TABLE native_draft_handoff_v1(
    target_key BLOB PRIMARY KEY NOT NULL CHECK(length(target_key)=32),
    review_id TEXT NOT NULL CHECK(length(review_id)=48),
    body_digest BLOB NOT NULL CHECK(length(body_digest)=32),
    challenge_digest BLOB CHECK(challenge_digest IS NULL OR length(challenge_digest)=32),
    issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at>issued_at),
    consumed_at INTEGER,
    CHECK((challenge_digest IS NULL)=(consumed_at IS NOT NULL)),
    CHECK(consumed_at IS NULL OR (consumed_at>=issued_at AND consumed_at<expires_at))
) WITHOUT ROWID;";

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid local handoff input")] Input,
    #[error("current approved draft unavailable to the local operator")] Review,
    #[error("handoff confirmation changed, expired or was already consumed")] Stale,
    #[error("local handoff ledger capacity reached")] Capacity,
    #[error("local handoff storage unavailable")] Storage,
    #[error(transparent)] Receipt(#[from] draft_handoff_receipt::Error),
}

impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}
impl From<crate::events::EventError> for Error {
    fn from(_: crate::events::EventError) -> Self { Self::Storage }
}

type Result<T> = std::result::Result<T, Error>;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Target {
    scope: AccountScope,
    review_id: String,
    conversation_id: String,
    revision: i64,
}

impl Target {
    pub(crate) fn review_id(&self) -> &str { &self.review_id }

    pub(crate) fn key(&self) -> Result<[u8; 32]> {
        let nonempty = |s: &str, max: usize| {
            (1..=max).contains(&s.len()) && s.trim() == s && !s.chars().any(char::is_control)
        };
        if !crate::store::PLATFORMS.contains(&self.scope.platform.as_str()) || self.scope.platform == "all"
            || !nonempty(&self.scope.account_id, 256) || !nonempty(&self.conversation_id, 2_048)
            || self.conversation_id.chars().count() > 512 || !(1..=MAX_REVISION).contains(&self.revision)
            || !hex_string(&self.review_id, 48)
        {
            return Err(Error::Input);
        }
        // Independent of review/grant ID: duplicate reviews cannot mint a
        // second packet for the exact same account, destination and revision.
        let raw = serde_json::to_vec(&("hub-local-handoff-v1", &self.scope.platform,
            &self.scope.account_id, &self.conversation_id, self.revision)).map_err(|_| Error::Input)?;
        Ok(Sha256::digest(raw).into())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PreviewInput {
    passphrase: String,
    target: Target,
    receipt_public_key: Option<String>,
}
impl Drop for PreviewInput {
    fn drop(&mut self) { self.passphrase.zeroize(); }
}

#[derive(Clone, Copy, Deserialize)]
enum Action {
    #[serde(rename = "approve_exact_local_export")]
    ApproveExactLocalExport,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PullInput {
    passphrase: String,
    target: Target,
    challenge: String,
    body_sha256: String,
    action: Action,
    receipt_public_key: Option<String>,
}
impl Drop for PullInput {
    fn drop(&mut self) { self.passphrase.zeroize(); self.challenge.zeroize(); }
}

#[derive(Serialize)]
pub(crate) struct Preview<'a> {
    mode: &'static str,
    target: &'a Target,
    body: String,
    body_sha256: String,
    challenge: String,
    expires_at: i64,
    delivery_performed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_key_sha256: Option<String>,
}

#[derive(Serialize)]
pub(crate) struct Packet<'a> {
    schema: &'static str,
    target: &'a Target,
    body: String,
    body_sha256: String,
    expires_at: i64,
    delivery_performed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_binding: Option<Binding>,
}

fn installed(db: &Connection) -> Result<bool> {
    let object: Option<(String, String)> = db.query_row(
        "SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
        [TABLE], |row| Ok((row.get(0)?, row.get(1)?)),
    ).optional()?;
    match object {
        None => Ok(false),
        Some((name, kind)) if name == TABLE && kind == "table" => Ok(true),
        _ => Err(Error::Storage),
    }
}

fn hex_string(text: &str, size: usize) -> bool {
    text.len() == size && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn digest_token(text: &str) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"hub-local-handoff-challenge-v1\0");
    digest.update(text.as_bytes());
    digest.finalize().into()
}

fn now(db: &Connection) -> Result<i64> {
    let time: i64 = db.query_row("SELECT unixepoch()", [], |r| r.get(0))?;
    if !(1..=(MAX_REVISION - TTL)).contains(&time) { return Err(Error::Storage); }
    Ok(time)
}

fn authenticate(store: &EventStore, passphrase: &str) -> Result<()> {
    draft_review::authenticate(store, passphrase).map_err(|_| Error::Review)
}

fn current(db: &Connection, target: &Target) -> Result<(String, [u8; 32])> {
    draft_review::checked_handoff_body(db, &target.scope, &target.review_id, &target.conversation_id,
        target.revision).map_err(|_| Error::Review)
}

pub(crate) fn preview<'a>(store: &EventStore, input: &'a PreviewInput) -> Result<Preview<'a>> {
    let target = &input.target;
    let key = target.key()?;
    let receipt_key = draft_handoff_receipt::parse_key(input.receipt_public_key.as_deref())?;
    authenticate(store, &input.passphrase)?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    let (body, body_hash) = current(&tx, target)?;
    let time = now(&tx)?;
    if !installed(&tx)? { tx.execute_batch(SCHEMA)?; }
    tx.execute("DELETE FROM native_draft_handoff_v1 WHERE consumed_at IS NULL AND expires_at<=?",
        [time])?;
    let exists: bool = tx.query_row(
        "SELECT EXISTS(SELECT 1 FROM native_draft_handoff_v1 WHERE target_key=?)",
        [key.as_slice()], |row| row.get(0))?;
    if !exists {
        let count: i64 = tx.query_row("SELECT count(*) FROM native_draft_handoff_v1", [], |row| row.get(0))?;
        if count >= MAX_ROWS { return Err(Error::Capacity); }
    }
    let mut entropy = Zeroizing::new([0_u8; 32]);
    getrandom::fill(entropy.as_mut_slice()).map_err(|_| Error::Storage)?;
    let challenge = hex::encode(entropy.as_slice());
    let challenge_hash = digest_token(&challenge);
    let expires_at = time + TTL;
    if tx.execute("INSERT INTO native_draft_handoff_v1
        (target_key,review_id,body_digest,challenge_digest,issued_at,expires_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(target_key) DO UPDATE SET review_id=excluded.review_id,
          body_digest=excluded.body_digest,challenge_digest=excluded.challenge_digest,
          issued_at=excluded.issued_at,expires_at=excluded.expires_at
        WHERE consumed_at IS NULL",
        params![key.as_slice(), target.review_id, body_hash.as_slice(),
                challenge_hash.as_slice(), time, expires_at])? != 1
    {
        return Err(Error::Stale);
    }
    draft_handoff_receipt::prepare(&tx, &key, receipt_key.as_ref())?;
    tx.commit()?;
    Ok(Preview { mode: "local-export-preview-only", target, body,
        body_sha256: hex::encode(body_hash), challenge, expires_at, delivery_performed: false,
        receipt_key_sha256: receipt_key.as_ref().map(draft_handoff_receipt::key_fingerprint) })
}

struct Pending {
    review_id: String,
    body_digest: [u8; 32],
    challenge_digest: Option<[u8; 32]>,
    issued_at: i64,
    expires_at: i64,
}

pub(crate) fn pull<'a>(store: &EventStore, input: &'a PullInput) -> Result<Packet<'a>> {
    let target = &input.target;
    let key = target.key()?;
    let Action::ApproveExactLocalExport = input.action;
    let receipt_key = draft_handoff_receipt::parse_key(input.receipt_public_key.as_deref())?;
    if !hex_string(&input.challenge, 64) || !hex_string(&input.body_sha256, 64) {
        return Err(Error::Input);
    }
    authenticate(store, &input.passphrase)?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Stale); }
    let pending = tx.query_row("SELECT review_id,body_digest,challenge_digest,issued_at,expires_at
        FROM native_draft_handoff_v1 WHERE target_key=? AND consumed_at IS NULL",
        [key.as_slice()], |row| Ok(Pending { review_id: row.get(0)?, body_digest: row.get(1)?,
            challenge_digest: row.get(2)?, issued_at: row.get(3)?, expires_at: row.get(4)? }))
        .optional()?.ok_or(Error::Stale)?;
    let time = now(&tx)?;
    let (body, digest) = current(&tx, target)?;
    let expected_body = hex::encode(digest);
    let token = digest_token(&input.challenge);
    if pending.review_id != target.review_id || time < pending.issued_at || time >= pending.expires_at
        || pending.expires_at.checked_sub(pending.issued_at) != Some(TTL)
        || input.body_sha256 != expected_body
        || !bool::from(digest.as_slice().ct_eq(pending.body_digest.as_slice()))
        || !pending.challenge_digest.is_some_and(|stored| bool::from(token.as_slice().ct_eq(stored.as_slice())))
    {
        return Err(Error::Stale);
    }
    let affected = tx.execute("UPDATE native_draft_handoff_v1
         SET challenge_digest=NULL,consumed_at=? WHERE target_key=? AND consumed_at IS NULL",
        params![time, key.as_slice()])?;
    if affected != 1 { return Err(Error::Stale); }
    let receipt_binding = draft_handoff_receipt::bind(&tx, Export {
        archive: store.archive_id(), target_key: &key, review_id: &target.review_id,
        body_hash: &digest, consumed_at: time, expires_at: pending.expires_at,
    }, receipt_key.as_ref())?;
    // Commit before caller can write body to the private output pipe. If its
    // reader fails, burn the token rather than risk emitting a second packet.
    tx.commit()?;
    Ok(Packet { schema: "hub-local-handoff-v1", target, body,
        body_sha256: expected_body, expires_at: pending.expires_at, delivery_performed: false, receipt_binding })
}
