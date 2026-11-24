//! Exact-account contact selection and explicit local identity organization.
use super::{Client as Loopback, Credential, Error as SessionError, hex64};
use crate::{
    desktop_sdk::{Connection, Mode},
    metadata::limited_text,
    metadata_http::{CONTACT, MAX_BODY},
    plugins::AccountScope,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, sync::Arc};
use zeroize::Zeroizing;

const LIST: &str = "/api/v1/contacts";
const MAX_PAGE: u16 = 100;
const MAX_OFFSET: u32 = 100_000;
const LIST_RESPONSE: usize = 4 * 1024 * 1024 + 4096;
const IDENTITY_RESPONSE: usize = 4096;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] SessionError),
    #[error("contact does not exist under the exact grant")]
    Missing,
    #[error("contact selection or identity state belongs to another desktop session")]
    WrongSession,
    #[error("identity revision changed; read and review before an explicit edit")]
    Conflict,
    #[error("identity edit outcome is unknown; reconcile the same contact before another edit")]
    OutcomeUnknown,
}

pub struct Query<'a> {
    pub search: Option<&'a str>,
    pub kind: Option<&'a str>,
    pub limit: u16,
    pub offset: u32,
}

/// Only safe contact fields leave this client. Names and kinds are private
/// untrusted text; avatar, provider extra fields and URL fields are omitted.
pub struct Contact {
    session: Arc<()>,
    id: String,
    name: Option<String>,
    kind: Option<String>,
}

impl Contact {
    pub fn id(&self) -> &str { &self.id }
    pub fn name(&self) -> Option<&str> { self.name.as_deref() }
    pub fn kind(&self) -> Option<&str> { self.kind.as_deref() }
}

pub struct Page {
    pub items: Vec<Contact>,
    pub total: u64,
}

#[derive(Deserialize)]
struct ContactWire {
    platform: String,
    account_id: String,
    id: String,
    name: Option<String>,
    kind: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContactsWire {
    items: Vec<ContactWire>,
    total: u64,
    accounts: Vec<AccountScope>,
}

/// Exact server-observed state for the selected contact and live session.
pub struct Identity {
    session: Arc<()>,
    contact_id: String,
    revision: i64,
    value: IdentityWire,
}

impl Identity {
    pub fn contact_id(&self) -> &str { &self.contact_id }
    pub fn revision(&self) -> i64 { self.revision }
    pub fn identity_id(&self) -> Option<&str> { self.value.as_deref() }
}

pub enum Update {
    Changed(Identity),
    Unchanged(Identity),
}

// Non-optional field at the envelope level: null is unlinked; a missing
// field is a malformed reply, never an observed unlink.
#[derive(Deserialize)]
#[serde(untagged)]
enum IdentityWire {
    Linked(String),
    Unlinked(()),
}

impl IdentityWire {
    fn as_deref(&self) -> Option<&str> {
        match self {
            Self::Linked(value) => Some(value.as_str()),
            Self::Unlinked(()) => None,
        }
    }

    fn valid(&self) -> bool {
        self.as_deref().is_none_or(|value|
            !value.is_empty() && value.trim() == value && limited_text(value, 256, false))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StatusWire {
    revision: i64,
    identity_id: IdentityWire,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UpdateWire {
    changed: bool,
    revision: i64,
    identity_id: IdentityWire,
}

/// Listing and editing are negotiated against separate existing permissions
/// and fixed manifest paths. Never follows manifest-supplied URLs.
pub struct Client<'a> {
    wire: Loopback<'a>,
    session: Arc<()>,
    can_edit: bool,
}

impl<'a> Client<'a> {
    pub fn connect(
        connection: Connection,
        credential: Zeroizing<String>,
        grant_id: String,
        scope: AccountScope,
        is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        if connection.mode() != Mode::NativeLive { return Err(SessionError::Unsupported.into()); }
        if !hex64(&credential) { return Err(SessionError::Input.into()); }
        let (wire, manifest) = Loopback::connect_exact(
            connection, Credential::Installed(credential), grant_id, scope, is_current,
        )?;
        let has = |value: &str| manifest.plugin.permissions.iter().any(|p| p == value);
        if !has("contacts.read") || manifest.resources.contacts.as_deref() != Some(LIST) {
            return Err(SessionError::Unsupported.into());
        }
        let can_edit = has("contacts.identity.write")
            && manifest.resources.contact_identity.as_deref() == Some(CONTACT);
        Ok(Self { wire, session: Arc::new(()), can_edit })
    }

    /// Negotiated edit support only, not backend liveness or metadata readiness.
    pub fn supports_identity(&self) -> bool { self.wire.active && self.can_edit }

    /// New accepted readiness invalidates all former selection and edit state.
    pub fn reconnect(
        self, new_connection: Connection, is_current: impl FnMut(u32) -> bool + 'a,
    ) -> Result<Self, Error> {
        let Loopback { grant, grant_id, scope, .. } = self.wire;
        let Credential::Installed(credential) = grant else {
            return Err(SessionError::Identity.into());
        };
        Self::connect(new_connection, credential, grant_id, scope, is_current)
    }

    pub fn list(&mut self, request: Query<'_>) -> Result<Page, Error> {
        if !(1..=MAX_PAGE).contains(&request.limit) || request.offset > MAX_OFFSET
            || request.search.is_some_and(|q| !filter(q, 4096))
            || request.kind.is_some_and(|q| !filter(q, 128))
        {
            return Err(SessionError::Input.into());
        }
        let limit = request.limit.to_string();
        let offset = request.offset.to_string();
        let mut pairs = vec![("limit", limit.as_str()), ("offset", offset.as_str())];
        if let Some(search) = request.search { pairs.push(("q", search)); }
        if let Some(kind) = request.kind { pairs.push(("kind", kind)); }
        let target = self.wire.target(LIST, &pairs)?;
        let reply: ContactsWire = self.wire.call(&target, true, LIST_RESPONSE).map_err(read_error)?;
        let in_scope = |platform: &str, account: &str| {
            platform == self.wire.scope.platform.as_str() && account == self.wire.scope.account_id.as_str()
        };
        if reply.accounts.len() > 1
            || reply.accounts.iter().any(|scope| !in_scope(&scope.platform, &scope.account_id))
            || reply.items.iter().any(|row| !in_scope(&row.platform, &row.account_id))
        {
            self.wire.active = false;
            return Err(SessionError::Identity.into());
        }
        let expected = reply.total.saturating_sub(u64::from(request.offset))
            .min(u64::from(request.limit));
        let mut seen = BTreeSet::new();
        if reply.items.len() as u64 != expected || (reply.total > 0 && reply.accounts.is_empty())
            || reply.items.iter().any(|row| {
                !valid_target(&row.id) || !seen.insert(row.id.as_str())
                    || row.name.as_ref().is_some_and(|name| name.len() > 4096)
                    || row.kind.as_ref().is_some_and(|kind| kind.len() > 512)
            })
        {
            self.wire.active = false;
            return Err(SessionError::Protocol.into());
        }
        drop(seen);
        let items = reply.items.into_iter().map(|row| Contact {
            session: self.session.clone(), id: row.id, name: row.name, kind: row.kind,
        }).collect();
        Ok(Page { items, total: reply.total })
    }

    /// Load the exact selected contact's actual revision, not a list snapshot.
    pub fn identity(&mut self, selected: &Contact) -> Result<Identity, Error> {
        if !self.can_edit { return Err(SessionError::Unsupported.into()); }
        if !Arc::ptr_eq(&self.session, &selected.session) { return Err(Error::WrongSession); }
        let target = self.wire.target(CONTACT, &[("id", &selected.id)])?;
        let info: StatusWire = self.wire.call(&target, true, IDENTITY_RESPONSE).map_err(read_error)?;
        if info.revision < 0 || !info.identity_id.valid() {
            self.wire.active = false;
            return Err(SessionError::Protocol.into());
        }
        Ok(self.observed(&selected.id, info.revision, info.identity_id))
    }

    /// Explicitly attach an operator-entered local label to one contact.
    pub fn link(&mut self, current: &Identity, identity_id: &str) -> Result<Update, Error> {
        if identity_id.len() > 4096 { return Err(SessionError::Input.into()); }
        let value = identity_id.trim();
        if value.is_empty() || !limited_text(value, 256, false) {
            return Err(SessionError::Input.into());
        }
        self.update(current, Some(value))
    }

    /// Explicitly clear only the mapping of the current contact.
    pub fn unlink(&mut self, current: &Identity) -> Result<Update, Error> {
        self.update(current, None)
    }

    fn observed(&self, contact_id: &str, revision: i64, value: IdentityWire) -> Identity {
        Identity { session: self.session.clone(), contact_id: contact_id.to_owned(), revision, value }
    }

    fn update(&mut self, current: &Identity, desired: Option<&str>) -> Result<Update, Error> {
        if !self.can_edit { return Err(SessionError::Unsupported.into()); }
        if !Arc::ptr_eq(&self.session, &current.session) { return Err(Error::WrongSession); }
        #[derive(Serialize)]
        struct Target<'b> { id: &'b str, platform: &'b str, account_id: &'b str }
        #[derive(Serialize)]
        struct Edit<'b> {
            target: Target<'b>,
            expected_revision: i64,
            identity_id: Option<&'b str>, // None must be sent as explicit null.
        }
        let edit = Edit {
            target: Target {
                id: &current.contact_id,
                platform: &self.wire.scope.platform,
                account_id: &self.wire.scope.account_id,
            },
            expected_revision: current.revision,
            identity_id: desired,
        };
        let body = Zeroizing::new(serde_json::to_vec(&edit).map_err(|_| SessionError::Input)?);
        if body.len() > MAX_BODY { return Err(SessionError::Input.into()); }
        let new: UpdateWire = self.wire.call_json(
            CONTACT, true, Some(body.as_slice()), IDENTITY_RESPONSE,
        ).map_err(write_error)?;
        let was_desired = current.identity_id() == desired;
        let valid_revision = if new.changed {
            !was_desired && current.revision.checked_add(1) == Some(new.revision)
        } else {
            // A stale request can be a successful no-op only at the newer
            // current revision, if another editor already made this change.
            new.revision > current.revision || (new.revision == current.revision && was_desired)
        };
        if !valid_revision || !new.identity_id.valid() || new.identity_id.as_deref() != desired {
            self.wire.active = false;
            return Err(Error::OutcomeUnknown);
        }
        let result = self.observed(&current.contact_id, new.revision, new.identity_id);
        Ok(if new.changed { Update::Changed(result) } else { Update::Unchanged(result) })
    }
}

fn filter(value: &str, max: usize) -> bool {
    value.len() <= max && !value.chars().any(char::is_control)
}

fn valid_target(id: &str) -> bool {
    !id.trim().is_empty() && filter(id, 2048) && id.chars().count() <= 512
}

fn read_error(error: SessionError) -> Error {
    match error {
        SessionError::Status(404) => Error::Missing,
        other => Error::Session(other),
    }
}

fn write_error(error: SessionError) -> Error {
    match error {
        SessionError::Status(409) => Error::Conflict,
        SessionError::Status(code @ 400..=499) if code != 408 => read_error(SessionError::Status(code)),
        _ => Error::OutcomeUnknown,
    }
}
