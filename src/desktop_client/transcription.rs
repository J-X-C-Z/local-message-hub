//! Explicit manual text annotations on one archived voice; no audio operation.
use super::{Client as Loopback, Credential, Error as LoopbackError, MAX_TARGET, hex64};
use crate::{
    desktop_sdk::{Connection, Mode},
    plugins::AccountScope,
    transcription::{MAX_REVISION, language, valid_id, valid_text},
    transcription_http::PATH,
};
use serde::{Deserialize, Deserializer, Serialize};
use std::sync::Arc;
use zeroize::Zeroizing;

const MAX_RECORD: usize = 128 * 1024;
const MAX_RECEIPT: usize = 512;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Session(#[from] LoopbackError),
    #[error("the exact indexed voice message was not found")]
    MissingVoice,
    #[error("the manual annotation revision changed; read and review before editing")]
    Conflict,
    #[error("annotation state belongs to another desktop session")]
    WrongSession,
    #[error("annotation write outcome is unknown; reconnect and read the exact voice before editing")]
    OutcomeUnknown,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status { NotRecorded, Manual, Cleared }

fn present_string<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Option<String>, D::Error> {
    String::deserialize(deserializer).map(Some)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    revision: i64,
    status: Status,
    #[serde(default, deserialize_with = "present_string")]
    text: Option<String>,
    #[serde(default, deserialize_with = "present_string")]
    language: Option<String>,
}

impl Record {
    fn valid(&self) -> bool {
        match self.status {
            Status::NotRecorded => self.revision == 0 && self.text.is_none() && self.language.is_none(),
            Status::Cleared => (1..=MAX_REVISION).contains(&self.revision) && self.text.is_none() && self.language.is_none(),
            Status::Manual => (1..=MAX_REVISION).contains(&self.revision)
                && self.text.as_deref().is_some_and(valid_text)
                && self.language.as_deref().is_none_or(language),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt { revision: i64, status: Status }

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Capability {
    path: String,
    can_write: bool,
    automatic: bool,
    legacy_manual_status: Option<String>,
    legacy_manual_lookup: Option<String>,
}

/// The exact reviewed target and revision, usable only by its original SDK
/// session. No identifiers or text from a response can change the target.
pub struct Annotation {
    session: Arc<()>,
    conversation_id: String,
    message_id: String,
    revision: i64,
    status: Status,
    text: Option<Zeroizing<String>>,
    language: Option<String>,
}

impl Annotation {
    pub fn conversation_id(&self) -> &str { &self.conversation_id }
    pub fn message_id(&self) -> &str { &self.message_id }
    pub fn revision(&self) -> i64 { self.revision }
    pub fn status(&self) -> Status { self.status }
    pub fn text(&self) -> Option<&str> { self.text.as_deref().map(String::as_str) }
    pub fn language(&self) -> Option<&str> { self.language.as_deref() }
}

pub struct Client<'a> {
    wire: Loopback<'a>,
    can_write: bool,
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
        let allows = |needed| manifest.plugin.permissions.iter().any(|p| p == needed);
        if !allows("messages.read") || !allows("voice.transcription.read") {
            return Err(LoopbackError::Unsupported.into());
        }
        let cap: Capability = serde_json::from_value(
            manifest.local_transcription.ok_or(LoopbackError::Unsupported)?,
        ).map_err(|_| LoopbackError::Protocol)?;
        if cap.path != PATH || cap.automatic || cap.can_write != allows("voice.transcription.write")
            || cap.legacy_manual_status.as_deref().is_some_and(|path| path != crate::transcription_http::LEGACY_STATUS)
            || cap.legacy_manual_lookup.as_deref().is_some_and(|path| path != crate::transcription_http::LEGACY_TRANSCRIBE)
        {
            return Err(LoopbackError::Unsupported.into());
        }
        Ok(Self { wire, can_write: cap.can_write })
    }

    pub fn can_write(&self) -> bool { self.can_write }

    pub fn get(&mut self, conversation_id: &str, message_id: &str) -> Result<Annotation, Error> {
        if !valid_id(conversation_id) || !valid_id(message_id) {
            return Err(LoopbackError::Input.into());
        }
        let mut query = url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("conversation_id", conversation_id).append_pair("message_id", message_id);
        let target = format!("{PATH}?{}", query.finish());
        if target.len() > MAX_TARGET { return Err(LoopbackError::Input.into()); }
        let reply: Record = self.wire.call(&target, true, MAX_RECORD).map_err(read_error)?;
        if !reply.valid() {
            self.wire.active = false;
            return Err(LoopbackError::Protocol.into());
        }
        Ok(self.annotation(conversation_id, message_id, reply))
    }

    /// An explicit CAS. No audio is opened and there are no automatic retries.
    pub fn set(&mut self, current: &Annotation, text: &str, tag: Option<&str>) -> Result<Annotation, Error> {
        if !valid_text(text) || !tag.is_none_or(language) { return Err(LoopbackError::Input.into()); }
        self.write(current, Some(text), tag)
    }

    /// Remove current text, preserving the tombstone revision. Clearing an
    /// already absent annotation is an unchanged success on the same revision.
    pub fn clear(&mut self, current: &Annotation) -> Result<Annotation, Error> {
        self.write(current, None, None)
    }

    fn write(&mut self, current: &Annotation, text: Option<&str>, language: Option<&str>) -> Result<Annotation, Error> {
        if !Arc::ptr_eq(&current.session, &self.wire.session) { return Err(Error::WrongSession); }
        if !self.can_write { return Err(LoopbackError::Unsupported.into()); }
        let changed = current.text() != text || current.language() != language;
        let revision = if changed {
            current.revision.checked_add(1).filter(|n| *n <= MAX_REVISION).ok_or(Error::Conflict)?
        } else {
            current.revision
        };
        let status = if text.is_some() { Status::Manual }
            else if revision == 0 { Status::NotRecorded } else { Status::Cleared };
        #[derive(Serialize)]
        struct Request<'b> {
            operation: &'static str,
            conversation_id: &'b str,
            message_id: &'b str,
            expected_revision: i64,
            #[serde(skip_serializing_if = "Option::is_none")]
            text: Option<&'b str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            language: Option<&'b str>,
        }
        let request = Request { operation: if text.is_some() { "set" } else { "clear" },
            conversation_id: &current.conversation_id, message_id: &current.message_id,
            expected_revision: current.revision, text, language };
        let body = Zeroizing::new(serde_json::to_vec(&request).map_err(|_| LoopbackError::Input)?);
        self.wire.live()?; // An already-stale client has not sent a request.
        let reply: Receipt = self.wire.call_json(PATH, true, Some(body.as_slice()), MAX_RECEIPT)
            .map_err(write_error)?;
        if reply.revision != revision || reply.status != status {
            self.wire.active = false;
            return Err(Error::OutcomeUnknown);
        }
        let record = Record { revision, status, text: text.map(str::to_owned), language: language.map(str::to_owned) };
        Ok(self.annotation(&current.conversation_id, &current.message_id, record))
    }

    fn annotation(&self, conversation_id: &str, message_id: &str, record: Record) -> Annotation {
        Annotation { session: Arc::clone(&self.wire.session), conversation_id: conversation_id.to_owned(),
            message_id: message_id.to_owned(), revision: record.revision, status: record.status,
            text: record.text.map(Zeroizing::new), language: record.language }
    }
}

fn read_error(error: LoopbackError) -> Error {
    match error {
        LoopbackError::Status(404) => Error::MissingVoice,
        other => Error::Session(other),
    }
}

fn write_error(error: LoopbackError) -> Error {
    match error {
        LoopbackError::Status(409) => Error::Conflict,
        LoopbackError::Status(code @ 400..=499) => read_error(LoopbackError::Status(code)),
        _ => Error::OutcomeUnknown,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wire_rejects_inconsistent_status_and_extra_private_fields() {
        for invalid in [
            r#"{"revision":0,"status":"manual","text":"annotation"}"#,
            r#"{"revision":1,"status":"manual"}"#,
            r#"{"revision":0,"status":"cleared"}"#,
            r#"{"revision":1,"status":"cleared","text":"unexpected"}"#,
            r#"{"revision":1,"status":"cleared","text":null}"#,
            r#"{"revision":1,"status":"manual","text":"x","language":"EN"}"#,
            r#"{"revision":1,"status":"manual","text":"x","media_url":"/private"}"#,
            r#"{"revision":9007199254740992,"status":"cleared"}"#,
        ] {
            assert!(!serde_json::from_str::<Record>(invalid).is_ok_and(|record| record.valid()));
        }
        let good: Record = serde_json::from_str(r#"{"revision":1,"status":"manual","text":"x","language":"en"}"#).unwrap();
        assert!(good.valid());
        assert!(serde_json::from_str::<Receipt>(r#"{"revision":1,"status":"manual","text":"x"}"#).is_err());
        assert!(matches!(write_error(LoopbackError::Transport), Error::OutcomeUnknown));
    }
}
