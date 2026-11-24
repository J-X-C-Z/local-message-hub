//! Signed acknowledgement of an exact local export, not evidence of delivery.
use crate::{draft_handoff::Target, draft_review, events::EventStore};
use ring::signature::{ECDSA_P256_SHA256_FIXED, UnparsedPublicKey};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use zeroize::Zeroize;

const CONTRACT: &str = "hub-local-draft-consumer-receipt-v1";
const TABLE: &str = "native_draft_handoff_receipt_v1";
const REPORT_WINDOW: i64 = 86_400;
const SCHEMA: &str = "
CREATE TABLE native_draft_handoff_receipt_v1(
    target_key BLOB PRIMARY KEY NOT NULL REFERENCES native_draft_handoff_v1(target_key) ON DELETE CASCADE,
    public_key BLOB NOT NULL CHECK(length(public_key)=65),
    exported_nonce BLOB CHECK(exported_nonce IS NULL OR length(exported_nonce)=32),
    disposition TEXT CHECK(disposition IS NULL OR disposition IN ('accepted','declined','unknown')),
    signature_digest BLOB CHECK(signature_digest IS NULL OR length(signature_digest)=32),
    recorded_at INTEGER,
    CHECK((disposition IS NULL)=(signature_digest IS NULL)),
    CHECK((disposition IS NULL)=(recorded_at IS NULL)),
    CHECK(disposition IS NULL OR exported_nonce IS NOT NULL)
) WITHOUT ROWID;";

#[derive(Debug, thiserror::Error)]
pub(crate) enum Error {
    #[error("invalid signed handoff receipt input")] Input,
    #[error("local operator access unavailable")] Operator,
    #[error("handoff receipt does not match the exact consumed export or selected key")] Binding,
    #[error("handoff receipt signature is not from the preselected key")] Signature,
    #[error("handoff receipt submission window ended")] Expired,
    #[error("handoff already has a different immutable acknowledgement")] Conflict,
    #[error("local handoff receipt storage unavailable")] Storage,
}
impl From<rusqlite::Error> for Error {
    fn from(_: rusqlite::Error) -> Self { Self::Storage }
}
impl From<crate::events::EventError> for Error {
    fn from(_: crate::events::EventError) -> Self { Self::Storage }
}
type Result<T> = std::result::Result<T, Error>;

fn canonical_hex<const N: usize>(value: &str) -> Result<[u8; N]> {
    if value.len() != N * 2 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err(Error::Input);
    }
    let mut raw = [0_u8; N];
    hex::decode_to_slice(value, &mut raw).map_err(|_| Error::Input)?;
    Ok(raw)
}

pub(crate) fn parse_key(public_key: Option<&str>) -> Result<Option<[u8; 65]>> {
    public_key.map(|value| {
        let raw = canonical_hex::<65>(value)?;
        // Only uncompressed SEC1 P-256 points. Ring checks curve validity at verification.
        if raw[0] != 4 || raw[1..].iter().all(|&b| b == 0) { return Err(Error::Input); }
        Ok(raw)
    }).transpose()
}

pub(crate) fn key_fingerprint(public_key: &[u8; 65]) -> String {
    hex::encode(Sha256::digest(public_key))
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

/// Replacing an unconsumed preview also replaces its selected public key.
pub(crate) fn prepare(db: &Connection, target: &[u8; 32], public_key: Option<&[u8; 65]>) -> Result<()> {
    if !installed(db)? {
        if public_key.is_none() { return Ok(()); }
        db.execute_batch(SCHEMA)?;
    }
    let exported = db.query_row(
        "SELECT exported_nonce IS NOT NULL FROM native_draft_handoff_receipt_v1 WHERE target_key=?",
        [target.as_slice()], |row| row.get::<_, bool>(0),
    ).optional()?;
    if exported == Some(true) { return Err(Error::Binding); }
    db.execute("DELETE FROM native_draft_handoff_receipt_v1 WHERE target_key=?", [target.as_slice()])?;
    if let Some(key) = public_key {
        db.execute("INSERT INTO native_draft_handoff_receipt_v1 (target_key,public_key) VALUES(?,?)",
            (target.as_slice(), key.as_slice()))?;
    }
    Ok(())
}

#[derive(Serialize)]
pub(crate) struct Binding {
    protocol: &'static str,
    handoff_id: String,
    recipient_key_sha256: String,
}

pub(crate) struct Export<'a> {
    pub archive: &'a str,
    pub target_key: &'a [u8; 32],
    pub review_id: &'a str,
    pub body_hash: &'a [u8; 32],
    pub consumed_at: i64,
    pub expires_at: i64,
}

impl Export<'_> {
    fn identifier(&self, key: &[u8; 65], nonce: &[u8; 32]) -> String {
        let mut hash = Sha256::new();
        hash.update(b"hub-local-draft-export-binding-v1\0");
        let consumed_at = self.consumed_at.to_be_bytes();
        let expires_at = self.expires_at.to_be_bytes();
        for part in [self.archive.as_bytes(), self.target_key.as_slice(), self.review_id.as_bytes(),
                     self.body_hash.as_slice(), key.as_slice(), nonce.as_slice(),
                     consumed_at.as_slice(), expires_at.as_slice()] {
            hash.update((part.len() as u32).to_be_bytes());
            hash.update(part);
        }
        hex::encode(hash.finalize())
    }
}

/// Called in the same transaction that consumes the original handoff challenge.
pub(crate) fn bind(db: &Connection, export: Export<'_>, expected_key: Option<&[u8; 65]>)
    -> Result<Option<Binding>>
{
    let pending: Option<(Vec<u8>, Option<[u8; 32]>)> = if installed(db)? {
        db.query_row("SELECT public_key,exported_nonce FROM native_draft_handoff_receipt_v1
            WHERE target_key=?", [export.target_key.as_slice()], |row| Ok((row.get(0)?, row.get(1)?))).optional()?
    } else { None };
    let key = match (pending, expected_key) {
        (None, None) => return Ok(None),
        (Some((key, None)), Some(expected)) if key.as_slice() == expected.as_slice() => *expected,
        _ => return Err(Error::Binding),
    };
    let mut nonce = [0_u8; 32];
    getrandom::fill(&mut nonce).map_err(|_| Error::Storage)?;
    if db.execute("UPDATE native_draft_handoff_receipt_v1 SET exported_nonce=?
        WHERE target_key=? AND exported_nonce IS NULL", (nonce.as_slice(), export.target_key.as_slice()))? != 1
    {
        return Err(Error::Binding);
    }
    Ok(Some(Binding { protocol: CONTRACT, handoff_id: export.identifier(&key, &nonce),
        recipient_key_sha256: key_fingerprint(&key) }))
}

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
enum Disposition { Accepted, Declined, Unknown }
impl Disposition {
    fn wire(self) -> &'static str {
        match self { Self::Accepted => "accepted", Self::Declined => "declined", Self::Unknown => "unknown" }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SignedReceipt {
    handoff_id: String,
    body_sha256: String,
    disposition: Disposition,
    signature: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Input {
    passphrase: String,
    target: Target,
    receipt: SignedReceipt,
}
impl Drop for Input {
    fn drop(&mut self) { self.passphrase.zeroize(); }
}

#[derive(Serialize)]
pub(crate) struct Outcome<'a> {
    protocol: &'static str,
    handoff_id: &'a str,
    consumer_acknowledgement: Disposition,
    recorded_at: i64,
    signature_verified: bool,
    delivery_verified: bool,
}

struct Bound {
    review: String,
    body_hash: [u8; 32],
    consumed_at: i64,
    expires_at: i64,
    public_key: Vec<u8>,
    nonce: [u8; 32],
    disposition: Option<String>,
    signature_hash: Option<[u8; 32]>,
    recorded_at: Option<i64>,
}

pub(crate) fn reconcile<'a>(store: &EventStore, input: &'a Input) -> Result<Outcome<'a>> {
    let target_key = input.target.key().map_err(|_| Error::Input)?;
    let receipt = &input.receipt;
    canonical_hex::<32>(&receipt.handoff_id)?;
    let candidate_body = canonical_hex::<32>(&receipt.body_sha256)?;
    let signature = canonical_hex::<64>(&receipt.signature)?;
    draft_review::authenticate(store, &input.passphrase).map_err(|_| Error::Operator)?;
    let mut db = store.write_connection()?;
    let tx = db.transaction_with_behavior(TransactionBehavior::Immediate)?;
    store.verify_connection(&tx)?;
    if !installed(&tx)? { return Err(Error::Binding); }
    let link = tx.query_row(
        "SELECT h.review_id,h.body_digest,h.consumed_at,h.expires_at,r.public_key,r.exported_nonce,
                r.disposition,r.signature_digest,r.recorded_at
         FROM native_draft_handoff_v1 h JOIN native_draft_handoff_receipt_v1 r USING(target_key)
         WHERE h.target_key=? AND h.consumed_at IS NOT NULL AND r.exported_nonce IS NOT NULL",
        [target_key.as_slice()], |row| Ok(Bound {
            review: row.get(0)?, body_hash: row.get(1)?, consumed_at: row.get(2)?,
            expires_at: row.get(3)?, public_key: row.get(4)?, nonce: row.get(5)?,
            disposition: row.get(6)?, signature_hash: row.get(7)?, recorded_at: row.get(8)?,
        }),
    ).optional()?.ok_or(Error::Binding)?;
    let binding = Export { archive: store.archive_id(), target_key: &target_key, review_id: &link.review,
        body_hash: &link.body_hash, consumed_at: link.consumed_at, expires_at: link.expires_at };
    let public_key: [u8; 65] = link.public_key.try_into().map_err(|_| Error::Storage)?;
    if public_key[0] != 4 || link.review != input.target.review_id() || link.consumed_at <= 0 || link.expires_at <= link.consumed_at
        || binding.identifier(&public_key, &link.nonce) != receipt.handoff_id
        || !bool::from(candidate_body.as_slice().ct_eq(link.body_hash.as_slice()))
    {
        return Err(Error::Binding);
    }

    let signed = format!("{CONTRACT}\n{}\n{}\n{}\n",
        receipt.handoff_id, receipt.body_sha256, receipt.disposition.wire());
    UnparsedPublicKey::new(&ECDSA_P256_SHA256_FIXED, public_key).verify(signed.as_bytes(), &signature)
        .map_err(|_| Error::Signature)?;
    let signature_hash = Sha256::digest(signature);
    let now: i64 = tx.query_row("SELECT unixepoch()", [], |row| row.get(0))?;
    let stop = link.consumed_at.checked_add(REPORT_WINDOW).ok_or(Error::Storage)?;
    if now < link.consumed_at { return Err(Error::Expired); }

    let recorded_at = match (link.disposition, link.signature_hash, link.recorded_at) {
        (Some(state), Some(previous), Some(recorded))
            if state == receipt.disposition.wire() && recorded >= link.consumed_at && recorded < stop
                && bool::from(previous.as_slice().ct_eq(signature_hash.as_slice())) => recorded,
        (Some(_), Some(_), Some(_)) => return Err(Error::Conflict),
        (None, None, None) => {
            if now >= stop { return Err(Error::Expired); }
            if tx.execute("UPDATE native_draft_handoff_receipt_v1
                SET disposition=?,signature_digest=?,recorded_at=?
                WHERE target_key=? AND disposition IS NULL",
                params![receipt.disposition.wire(), signature_hash.as_slice(), now, target_key.as_slice()])? != 1
            {
                return Err(Error::Conflict);
            }
            now
        }
        _ => return Err(Error::Storage),
    };
    tx.commit()?;
    Ok(Outcome { protocol: CONTRACT, handoff_id: &receipt.handoff_id,
        consumer_acknowledgement: receipt.disposition, recorded_at,
        signature_verified: true, delivery_verified: false })
}
