//! Explicit bounded JSON replay of a managed live account journal.
use super::{Client as Loopback, Credential, Error, hex64};
use crate::{desktop_sdk::{Connection, Mode}, events::EventKind, plugins::AccountScope};
use serde::Deserialize;
use std::sync::Arc;
use zeroize::Zeroizing;

mod sse;

const STREAM: &str = "/api/v1/events/stream";
const CHECKPOINT: &str = "/api/v1/events/checkpoint";
const REPLAY: &str = "/api/v1/events";
const MAX_PAGE: usize = 100;
const MAX_REPLY: usize = 256 * 1024;

/// Opaque position within one authorized account journal. Intentionally not
/// Debug or Deserialize. Restore a saved position against a fresh checkpoint.
pub struct Cursor {
    encoded: String,
    ordinal: i64,
    session: Arc<()>,
}

impl Cursor {
    /// Persist privately only after the corresponding local refresh was applied.
    pub fn as_str(&self) -> &str { &self.encoded }
}

pub struct Checkpoint {
    cursor: Cursor,
}

impl Checkpoint {
    pub fn cursor(&self) -> &Cursor { &self.cursor }
}

/// Scoped metadata only. Bodies and sender, media, and provider data are
/// not part of this API. The IDs are normalized archive identifiers.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Event {
    pub event_id: i64,
    pub kind: EventKind,
    platform: String,
    account_id: String,
    pub conversation_id: String,
    pub message_id: String,
    pub timestamp: i64,
}

pub enum Wait {
    /// Fresh metadata; persist this cursor only after applying the local update.
    Item { event: Event, next_cursor: Cursor },
    /// Wait elapsed cleanly. The caller's initial cursor is unchanged.
    Idle,
}

pub struct Page {
    pub items: Vec<Event>,
    pub next_cursor: Cursor,
    pub has_more: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireCheckpoint {
    checkpoint: i64,
    cursor: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WirePage {
    items: Vec<Event>,
    next_after: i64,
    next_cursor: String,
    has_more: bool,
    checkpoint: i64,
}

fn position(encoded: &str) -> Option<(&str, i64)> {
    let (namespace, raw) = encoded.split_once(':')?;
    let digest = namespace.strip_prefix("a-")?;
    if digest.len() != 32 || !digest.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !(1..=19).contains(&raw.len()) || !raw.bytes().all(|b| b.is_ascii_digit())
        || (raw.len() > 1 && raw.starts_with('0'))
    {
        return None;
    }
    Some((namespace, raw.parse().ok()?))
}

fn archive_id(value: &str) -> bool {
    (1..=256).contains(&value.len()) && !value.chars().any(char::is_control)
}

/// Independent read-only client; does not need message-body permissions.
/// Construction checks the managed backend's exact identity and fixed routes.
pub struct Client<'a> {
    wire: Loopback<'a>,
    stream: bool,
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
            return Err(Error::Unsupported);
        }
        if !hex64(&credential) { return Err(Error::Input); }
        let (wire, manifest) = Loopback::connect_exact(
            connection, Credential::Installed(credential), grant_id, scope, is_current,
        )?;
        if !manifest.plugin.permissions.iter().any(|p| p == "events.read")
            || manifest.resources.event_checkpoint.as_deref() != Some(CHECKPOINT)
            || manifest.resources.events.as_deref() != Some(REPLAY)
        {
            return Err(Error::Unsupported);
        }
        Ok(Self { wire, stream: manifest.resources.event_stream.as_deref() == Some(STREAM) })
    }

    pub fn checkpoint(&mut self) -> Result<Checkpoint, Error> {
        let row: WireCheckpoint = self.wire.call(CHECKPOINT, true, 1024)?;
        if position(&row.cursor).is_none_or(|(_, n)| n != row.checkpoint) {
            return self.invalidate(Error::Protocol);
        }
        Ok(Checkpoint { cursor: self.cursor(row.cursor, row.checkpoint) })
    }

    /// Explicitly restore the last applied position after a new connection.
    /// A foreign archive/grant, malformed position, or future ordinal is
    /// rejected locally. This neither advances to the head nor sends a request.
    pub fn restore_cursor(&self, current: &Checkpoint, previously_applied: &str) -> Result<Cursor, Error> {
        if !self.wire.active { return Err(Error::Stale); }
        if !Arc::ptr_eq(&current.cursor.session, &self.wire.session) { return Err(Error::Input); }
        let (namespace, ordinal) = position(previously_applied).ok_or(Error::Input)?;
        let (actual, head) = position(current.cursor.as_str()).ok_or(Error::Input)?;
        if namespace != actual || ordinal > head { return Err(Error::Input); }
        Ok(self.cursor(previously_applied.to_owned(), ordinal))
    }

    /// Replay one finite page after a checkpoint or last locally applied
    /// position. No state is marked applied or automatically retried.
    pub fn replay(&mut self, after: &Cursor, limit: usize) -> Result<Page, Error> {
        if !Arc::ptr_eq(&after.session, &self.wire.session) || !(1..=MAX_PAGE).contains(&limit) {
            return Err(Error::Input);
        }
        let (namespace, _) = position(after.as_str()).ok_or(Error::Input)?;
        // The replay route accepts only these two parameters. Account and
        // platform are obtained from the authenticated grant, not selectors.
        let mut query = url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("cursor", after.as_str()).append_pair("limit", &limit.to_string());
        let query = query.finish();
        let page: WirePage = self.wire.call(&format!("{REPLAY}?{query}"), true, MAX_REPLY)?;
        if page.items.len() > limit || page.checkpoint < after.ordinal {
            return self.invalidate(Error::Protocol);
        }
        let mut expected = after.ordinal;
        for event in &page.items {
            if event.platform != self.wire.scope.platform || event.account_id != self.wire.scope.account_id {
                return self.invalidate(Error::Identity);
            }
            if expected.checked_add(1) != Some(event.event_id)
                || !archive_id(&event.conversation_id) || !archive_id(&event.message_id)
                || event.timestamp < 0
            {
                return self.invalidate(Error::Protocol);
            }
            expected = event.event_id;
        }
        if page.next_after != expected
            || position(&page.next_cursor) != Some((namespace, expected))
            || expected > page.checkpoint
            || page.has_more != (expected < page.checkpoint)
            || (page.has_more && page.items.len() != limit)
        {
            return self.invalidate(Error::Protocol);
        }
        Ok(Page {
            next_cursor: self.cursor(page.next_cursor, expected),
            items: page.items,
            has_more: page.has_more,
        })
    }

    /// Observe at most one metadata event after a current session cursor.
    /// The socket closes after this call. No acknowledgments, cursor updates,
    /// retries or reconnects are performed by the SDK.
    pub fn wait_one(&mut self, after: &Cursor) -> Result<Wait, Error> {
        if !self.stream { return Err(Error::Unsupported); }
        if !Arc::ptr_eq(&after.session, &self.wire.session) { return Err(Error::Input); }
        let (namespace, ordinal) = position(after.as_str()).ok_or(Error::Input)?;
        if ordinal != after.ordinal { return Err(Error::Input); }
        let result = sse::wait_one(&mut self.wire, after.as_str(), namespace, ordinal);
        match result {
            Ok(Some((event, encoded))) => {
                let ordinal = event.event_id;
                Ok(Wait::Item { event, next_cursor: self.cursor(encoded, ordinal) })
            }
            Ok(None) => Ok(Wait::Idle),
            Err(error @ Error::Status(_)) => Err(error),
            Err(error) => self.invalidate(error),
        }
    }

    fn cursor(&self, encoded: String, ordinal: i64) -> Cursor {
        Cursor { encoded, ordinal, session: Arc::clone(&self.wire.session) }
    }

    fn invalidate<T>(&mut self, error: Error) -> Result<T, Error> {
        self.wire.active = false;
        Err(error)
    }
}
