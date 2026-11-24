//! Exact-account draft staging and local review requests, never delivery.
use super::{Client as Loopback, Credential, Error as LoopbackError, hex64};
use crate::{desktop_sdk::{Connection, Mode}, draft_review_http, drafts_http, plugins::AccountScope};
use serde::{Deserialize, Deserializer, Serialize};
use std::sync::Arc;
use zeroize::Zeroizing;

const BODY_BYTES: usize = 16 * 1024;
const DRAFT_WIRE_BYTES: usize = 104 * 1024;
const SMALL_REPLY: usize = 1024;
const MAX_REVISION: i64 = 9_007_199_254_740_991;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] LoopbackError),
    #[error("draft or review is unavailable for this exact local selector")]
    Missing,
    #[error("draft revision or retained request key conflicts")]
    SaveConflict,
    #[error("draft changed or the local review operator is not provisioned")]
    ReviewConflict,
    #[error("draft or review state belongs to a different desktop session")]
    WrongSession,
    #[error("local write outcome is unknown; reconcile before another write")]
    OutcomeUnknown,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftOffer {
    url: String,
    can_write: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReviewOffer {
    path: String,
    requests_only: bool,
    delivery: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DraftWire {
    revision: i64,
    #[serde(deserialize_with = "required_nullable")]
    body: Option<String>,
}

fn required_nullable<'de, D: Deserializer<'de>>(input: D) -> Result<Option<String>, D::Error> {
    Option::<String>::deserialize(input)
}

/// Private draft text from the selected indexed conversation. The reply wire
/// omits scope, so this handle binds the authenticated current request's
/// exact conversation and session instead.
pub struct Draft {
    session: Arc<()>,
    conversation_id: String,
    revision: i64,
    body: Option<Zeroizing<String>>,
}

impl Draft {
    pub fn conversation_id(&self) -> &str { &self.conversation_id }
    pub fn revision(&self) -> i64 { self.revision }
    pub fn body(&self) -> Option<&str> { self.body.as_ref().map(|value| value.as_str()) }
}

pub enum Change<'a> { Set(&'a str), Clear }

/// Preserve this key with the exact intended target, revision and body in
/// private caller state. Never reuse it for a different operation.
pub struct RequestId(String);
impl RequestId {
    pub fn new(value: &str) -> Result<Self, Error> {
        if !(1..=128).contains(&value.len())
            || !value.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        {
            return Err(LoopbackError::Input.into());
        }
        Ok(Self(value.to_owned()))
    }
}

/// A receipt for the selected request, not evidence of the current draft
/// if another writer has since changed it.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveReceipt {
    pub revision: i64,
    pub present: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ReviewState { Pending, AcceptedForReview, Rejected, Expired, DraftChanged }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReviewWire { review_id: String, revision: i64, state: ReviewState }

/// This ID only selects an existing local review owned by the grant.
pub struct Review {
    session: Arc<()>,
    record: ReviewWire,
}
impl Review {
    pub fn review_id(&self) -> &str { &self.record.review_id }
    pub fn revision(&self) -> i64 { self.record.revision }
    pub fn state(&self) -> ReviewState { self.record.state }
}

pub struct Client<'a> {
    wire: Loopback<'a>,
    can_write: bool,
    can_review: bool,
}

impl<'a> Client<'a> {
    pub fn connect(
        connection: Connection,
        credential: Zeroizing<String>,
        grant_id: String,
        scope: AccountScope,
        is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        if connection.mode() != Mode::NativeLive { return Err(LoopbackError::Unsupported.into()); }
        if !hex64(&credential) { return Err(LoopbackError::Input.into()); }
        let (wire, manifest) = Loopback::connect_exact(
            connection, Credential::Installed(credential), grant_id, scope, is_current,
        )?;
        let permit = |permission: &str| manifest.plugin.permissions.iter().any(|p| p == permission);
        if !permit("conversations.read") || !permit("drafts.read") {
            return Err(LoopbackError::Unsupported.into());
        }
        let offer: DraftOffer = serde_json::from_value(
            manifest.local_drafts.ok_or(LoopbackError::Unsupported)?,
        ).map_err(|_| LoopbackError::Protocol)?;
        if offer.url != drafts_http::PATH { return Err(LoopbackError::Unsupported.into()); }
        let can_write = offer.can_write && permit("drafts.write");
        let can_review = if cfg!(unix) && permit("drafts.review.queue") {
            manifest.local_draft_review
                .map(serde_json::from_value::<ReviewOffer>)
                .transpose().map_err(|_| LoopbackError::Protocol)?
                .is_some_and(|offer| offer.path == draft_review_http::PATH && offer.requests_only && !offer.delivery)
        } else { false };
        Ok(Self { wire, can_write, can_review })
    }

    pub fn supports_save(&self) -> bool { self.wire.active && self.can_write }
    pub fn supports_review(&self) -> bool { self.wire.active && self.can_review }

    pub fn read(&mut self, conversation_id: &str) -> Result<Draft, Error> {
        if !valid_conversation(conversation_id) { return Err(LoopbackError::Input.into()); }
        let target = self.wire.target(drafts_http::PATH, &[("conversation_id", conversation_id)])?;
        let row: DraftWire = self.wire.call(&target, true, DRAFT_WIRE_BYTES).map_err(read_error)?;
        if !(0..=MAX_REVISION).contains(&row.revision)
            || row.body.as_deref().is_some_and(|body| row.revision == 0 || !valid_body(body))
        {
            return self.invalidate(LoopbackError::Protocol.into());
        }
        Ok(Draft {
            session: Arc::clone(&self.wire.session),
            conversation_id: conversation_id.to_owned(),
            revision: row.revision,
            body: row.body.map(Zeroizing::new),
        })
    }

    /// Save only this observed revision. Set and clear are separate explicit
    /// choices. Never rereads, changes the expected revision, or retries.
    pub fn save(&mut self, current: &Draft, request: &RequestId, change: Change<'_>) -> Result<SaveReceipt, Error> {
        if !self.can_write { return Err(LoopbackError::Unsupported.into()); }
        self.same_session(&current.session)?;
        let body = match change {
            Change::Set(value) if valid_body(value) => Some(value),
            Change::Set(_) => return Err(LoopbackError::Input.into()),
            Change::Clear => None,
        };
        let expected = current.revision + if body == current.body() { 0 } else { 1 };
        if expected > MAX_REVISION { return Err(LoopbackError::Input.into()); }
        #[derive(Serialize)]
        struct Target<'b> { conversation_id: &'b str, platform: &'b str, account_id: &'b str }
        #[derive(Serialize)]
        struct Input<'b> {
            target: Target<'b>, expected_revision: i64, request_id: &'b str, body: Option<&'b str>,
        }
        let input = Input {
            target: Target {
                conversation_id: &current.conversation_id,
                platform: &self.wire.scope.platform, account_id: &self.wire.scope.account_id,
            },
            expected_revision: current.revision, request_id: &request.0, body,
        };
        let encoded = encode(&input, DRAFT_WIRE_BYTES)?;
        let receipt: SaveReceipt = self.wire.call_json(
            drafts_http::PATH, true, Some(encoded.as_slice()), SMALL_REPLY,
        ).map_err(|error| write_error(error, Error::SaveConflict))?;
        if receipt.revision != expected || receipt.present != body.is_some() {
            return self.invalidate(Error::OutcomeUnknown);
        }
        Ok(receipt)
    }

    /// Queue only the exact observed, present draft revision for local review.
    /// This neither changes the draft nor authorizes any external action.
    pub fn queue_review(&mut self, current: &Draft) -> Result<Review, Error> {
        if !self.can_review { return Err(LoopbackError::Unsupported.into()); }
        self.same_session(&current.session)?;
        if current.body.is_none() || current.revision == 0 { return Err(LoopbackError::Input.into()); }
        #[derive(Serialize)]
        struct Input<'b> { conversation_id: &'b str, expected_revision: i64 }
        let encoded = encode(&Input {
            conversation_id: &current.conversation_id, expected_revision: current.revision,
        }, 16 * 1024)?;
        let record: ReviewWire = self.wire.call_json(
            draft_review_http::PATH, true, Some(encoded.as_slice()), SMALL_REPLY,
        ).map_err(|error| write_error(error, Error::ReviewConflict))?;
        if !valid_review_id(&record.review_id) || record.revision != current.revision {
            return self.invalidate(Error::OutcomeUnknown);
        }
        Ok(self.review(record))
    }

    pub fn review_status(&mut self, known: &Review) -> Result<Review, Error> {
        self.same_session(&known.session)?;
        self.restore_review(known.review_id(), known.revision())
    }

    /// Explicit read-only recovery for an already known ID and revision.
    /// The backend independently checks the current grant's ownership.
    pub fn restore_review(&mut self, review_id: &str, revision: i64) -> Result<Review, Error> {
        if !self.can_review { return Err(LoopbackError::Unsupported.into()); }
        if !valid_review_id(review_id) || !(1..=MAX_REVISION).contains(&revision) {
            return Err(LoopbackError::Input.into());
        }
        // Review status only accepts its literal lowercase-hex selector.
        let target = format!("{}?review_id={review_id}", draft_review_http::PATH);
        let record: ReviewWire = self.wire.call(&target, true, SMALL_REPLY).map_err(|error| {
            match error { LoopbackError::Status(409) => Error::ReviewConflict, other => read_error(other) }
        })?;
        if record.review_id != review_id || record.revision != revision {
            return self.invalidate(LoopbackError::Protocol.into());
        }
        Ok(self.review(record))
    }

    fn review(&self, record: ReviewWire) -> Review {
        Review { session: Arc::clone(&self.wire.session), record }
    }

    fn same_session(&self, session: &Arc<()>) -> Result<(), Error> {
        if Arc::ptr_eq(session, &self.wire.session) { Ok(()) } else { Err(Error::WrongSession) }
    }

    fn invalidate<T>(&mut self, error: Error) -> Result<T, Error> {
        self.wire.active = false;
        Err(error)
    }
}

fn valid_conversation(value: &str) -> bool {
    !value.trim().is_empty() && value.len() <= 2048 && value.chars().count() <= 512
        && !value.chars().any(char::is_control)
}
fn valid_body(value: &str) -> bool {
    value.len() <= BODY_BYTES && !value.trim().is_empty()
        && !value.chars().any(|c| c.is_control() && !matches!(c, '\r' | '\n' | '\t'))
}
fn valid_review_id(value: &str) -> bool {
    value.len() == 48 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn encode(input: &impl Serialize, max: usize) -> Result<Zeroizing<Vec<u8>>, Error> {
    let encoded = Zeroizing::new(serde_json::to_vec(input).map_err(|_| LoopbackError::Input)?);
    if encoded.len() > max { return Err(LoopbackError::Input.into()); }
    Ok(encoded)
}
fn read_error(error: LoopbackError) -> Error {
    match error { LoopbackError::Status(404) => Error::Missing, other => Error::Session(other) }
}
fn write_error(error: LoopbackError, conflict: Error) -> Error {
    match error {
        LoopbackError::Status(409) => conflict,
        LoopbackError::Status(code @ 400..=499) => read_error(LoopbackError::Status(code)),
        _ => Error::OutcomeUnknown,
    }
}
