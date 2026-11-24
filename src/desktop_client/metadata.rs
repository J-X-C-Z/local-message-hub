//! Exact-account local conversation organization on managed native-live Hub.
use super::{Client as Loopback, Credential, Error as LoopbackError, hex64};
use crate::{
    desktop_sdk::{Connection, Mode},
    metadata::{limited_text, normalize_tags},
    metadata_http::{CONVERSATION, MAX_BODY},
    plugins::AccountScope,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use zeroize::Zeroizing;

const PERMISSION: &str = "conversations.metadata.write";
const MAX_RESPONSE: usize = 64 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] LoopbackError),
    #[error("conversation does not exist under the exact account")]
    Missing,
    #[error("conversation metadata revision changed; read and review before an explicit save")]
    Conflict,
    #[error("metadata state belongs to another desktop session")]
    WrongSession,
    #[error("save outcome is unknown; read the current metadata before making another change")]
    OutcomeUnknown,
}

#[derive(Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Metadata {
    note: String,
    tags: Vec<String>,
    archived: bool,
}

impl Metadata {
    fn valid(&self) -> bool {
        limited_text(&self.note, 4096, true) && self.tags.len() <= 50
            && self.tags.iter().enumerate().all(|(index, tag)| {
                !tag.is_empty() && tag.trim() == tag && limited_text(tag, 64, false)
                    && !self.tags[..index].contains(tag)
            })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StateWire {
    revision: i64,
    metadata: Metadata,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SaveWire {
    changed: bool,
    revision: i64,
    metadata: Metadata,
}

/// A private, session-bound revision and exact conversation target. The
/// endpoint's response omits scope; this state is bound to the validated
/// session and the fixed request's target, not to a server-supplied selector.
pub struct State {
    session: Arc<()>,
    id: String,
    revision: i64,
    metadata: Metadata,
}

impl State {
    pub fn conversation_id(&self) -> &str { &self.id }
    pub fn revision(&self) -> i64 { self.revision }
    pub fn note(&self) -> &str { &self.metadata.note }
    pub fn tags(&self) -> &[String] { &self.metadata.tags }
    pub fn archived(&self) -> bool { self.metadata.archived }
}

/// Omitted fields are preserved. Supply an empty note or tag list to clear
/// that field. The server's trim/dedup rules apply to explicitly supplied tags.
#[derive(Default)]
pub struct Patch<'a> {
    pub note: Option<&'a str>,
    pub tags: Option<&'a [String]>,
    pub archived: Option<bool>,
}

pub enum Saved {
    Changed(State),
    Unchanged(State),
}

/// Does not open paths, modify grants, or query provider state. Every
/// operation is an explicit call against a current numeric loopback session.
pub struct Client<'a> {
    wire: Loopback<'a>,
    session: Arc<()>,
}

impl<'a> Client<'a> {
    pub fn connect(
        connection: Connection,
        credential: Zeroizing<String>,
        grant_id: String,
        scope: AccountScope,
        is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        if connection.mode() != Mode::NativeLive {
            return Err(LoopbackError::Unsupported.into());
        }
        if !hex64(&credential) {
            return Err(LoopbackError::Input.into());
        }
        let (wire, manifest) = Loopback::connect_exact(
            connection, Credential::Installed(credential), grant_id, scope, is_current,
        )?;
        if !manifest.plugin.permissions.iter().any(|p| p == PERMISSION)
            || manifest.resources.conversation_metadata.as_deref() != Some(CONVERSATION)
        {
            return Err(LoopbackError::Unsupported.into());
        }
        Ok(Self { wire, session: Arc::new(()) })
    }

    /// Re-negotiate on a fresh supervisor readiness frame. All earlier
    /// state handles become invalid for saves, even for the same conversation.
    pub fn reconnect(
        self, new_connection: Connection, is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        let Loopback { grant, grant_id, scope, .. } = self.wire;
        let Credential::Installed(credential) = grant else {
            return Err(LoopbackError::Identity.into());
        };
        Self::connect(new_connection, credential, grant_id, scope, is_current)
    }

    pub fn conversation(&mut self, id: &str) -> Result<State, Error> {
        if id.trim().is_empty() || id.len() > 2048 || id.chars().count() > 512
            || id.chars().any(char::is_control)
        {
            return Err(LoopbackError::Input.into());
        }
        let target = self.wire.target(CONVERSATION, &[("id", id)])?;
        let received: StateWire = self.wire.call(&target, true, MAX_RESPONSE).map_err(read_error)?;
        if received.revision < 0 || !received.metadata.valid() {
            self.wire.active = false;
            return Err(LoopbackError::Protocol.into());
        }
        Ok(self.state(id, received.revision, received.metadata))
    }

    /// Apply an explicit patch using the revision from the exact current
    /// session and target. Never reload, change the revision or retry a write.
    pub fn save(&mut self, current: &State, patch: Patch<'_>) -> Result<Saved, Error> {
        if !Arc::ptr_eq(&self.session, &current.session) {
            return Err(Error::WrongSession);
        }
        if (patch.note.is_none() && patch.tags.is_none() && patch.archived.is_none())
            || patch.note.is_some_and(|value| !limited_text(value, 4096, true))
            // Limit the raw caller-supplied strings before cloning/normalizing.
            || patch.tags.is_some_and(|tags| tags.len() > 50 || tags.iter().any(|tag| tag.len() > 1024))
        {
            return Err(LoopbackError::Input.into());
        }
        let tags = patch.tags.map(|tags| normalize_tags(tags.to_vec())).transpose()
            .map_err(|_| LoopbackError::Input)?;
        #[derive(Serialize)]
        struct Target<'b> {
            id: &'b str,
            platform: &'b str,
            account_id: &'b str,
        }
        #[derive(Serialize)]
        struct Input<'b> {
            target: Target<'b>,
            expected_revision: i64,
            #[serde(skip_serializing_if = "Option::is_none")]
            note: Option<&'b str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            tags: Option<&'b [String]>,
            #[serde(skip_serializing_if = "Option::is_none")]
            archived: Option<bool>,
        }
        let request = Input {
            target: Target {
                id: &current.id,
                platform: &self.wire.scope.platform,
                account_id: &self.wire.scope.account_id,
            },
            expected_revision: current.revision,
            note: patch.note,
            tags: tags.as_deref(),
            archived: patch.archived,
        };
        let body = Zeroizing::new(serde_json::to_vec(&request).map_err(|_| LoopbackError::Input)?);
        if body.len() > MAX_BODY {
            return Err(LoopbackError::Input.into());
        }
        let next: SaveWire = self.wire.call_json(
            CONVERSATION, true, Some(body.as_slice()), MAX_RESPONSE,
        ).map_err(save_error)?;
        let desired = (
            patch.note.unwrap_or(&current.metadata.note),
            tags.as_deref().unwrap_or(&current.metadata.tags),
            patch.archived.unwrap_or(current.metadata.archived),
        );
        let changed_before = desired.0 != current.metadata.note.as_str()
            || desired.1 != current.metadata.tags.as_slice() || desired.2 != current.metadata.archived;
        if !next.metadata.valid() || next.revision < current.revision
            || patch.note.is_some_and(|note| note != next.metadata.note.as_str())
            || tags.as_deref().is_some_and(|tags| tags != next.metadata.tags.as_slice())
            || patch.archived.is_some_and(|archived| archived != next.metadata.archived)
            || (next.changed && (!changed_before || Some(next.revision) != current.revision.checked_add(1)
                || desired.0 != next.metadata.note.as_str()
                || desired.1 != next.metadata.tags.as_slice() || desired.2 != next.metadata.archived))
            || (!next.changed && next.revision == current.revision && next.metadata != current.metadata)
        {
            self.wire.active = false;
            return Err(Error::OutcomeUnknown);
        }
        let state = self.state(&current.id, next.revision, next.metadata);
        Ok(if next.changed { Saved::Changed(state) } else { Saved::Unchanged(state) })
    }

    fn state(&self, id: &str, revision: i64, metadata: Metadata) -> State {
        State { session: self.session.clone(), id: id.to_owned(), revision, metadata }
    }
}

fn read_error(error: LoopbackError) -> Error {
    match error {
        LoopbackError::Status(404) => Error::Missing,
        other => Error::Session(other),
    }
}

fn save_error(error: LoopbackError) -> Error {
    match error {
        LoopbackError::Status(409) => Error::Conflict,
        LoopbackError::Status(code @ 400..=499) => read_error(LoopbackError::Status(code)),
        _ => Error::OutcomeUnknown,
    }
}
