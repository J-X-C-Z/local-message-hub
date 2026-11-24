//! Explicit retrieval of verified local media from the managed Linux archive.
use super::{hex64, valid_identifier, Client, Error, ResponseKind, MEDIA};
use crate::desktop_sdk::Mode;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::sync::{Arc, Weak};
use zeroize::Zeroizing;

// Match the managed ImportedMedia server's complete-object bound.
const MAX_BYTES: usize = 8 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Selector {
    platform: String,
    account_id: String,
    conversation_id: String,
    message_id: String,
    attachment_id: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Response {
    source: String,
    local_available: bool,
    available: bool,
    mime_type: String,
    size: usize,
    sha256: String,
    selector: Selector,
    download_route: String,
    requires_bearer: bool,
    provider_path: bool,
    blob_verified: bool,
    persistent_url: bool,
}

/// A verified selector pinned to this exact authenticated client session.
/// Not a URL, bearer, or persistent handle; no public construction or cloning.
pub struct Reference {
    session: Weak<()>,
    selector: Selector,
    size: usize,
    digest: [u8; 32],
}

impl Reference {
    pub fn size(&self) -> usize {
        self.size
    }
}

impl Client<'_> {
    /// Verify one exact local attachment of the specified archive message.
    /// This request returns metadata only. Download requires a separate call.
    pub fn media_reference(
        &mut self,
        conversation_id: &str,
        message_id: &str,
        attachment_id: &str,
    ) -> Result<Reference, Error> {
        if !self.media || self.connection.mode() != Mode::ImportedMedia {
            return Err(Error::Unsupported);
        }
        if !valid_identifier(conversation_id, 512)
            || !valid_identifier(message_id, 512)
            || !hex64(attachment_id)
        {
            return Err(Error::Input);
        }
        let path = format!("{MEDIA}/{attachment_id}");
        let target = self.target(
            &format!("{path}/reference"),
            &[("conversation_id", conversation_id), ("message_id", message_id)],
        )?;
        let result: Response = self.call(&target, true, 8192)?;
        let expected_selector = result.selector.platform == self.scope.platform
            && result.selector.account_id == self.scope.account_id
            && result.selector.conversation_id == conversation_id
            && result.selector.message_id == message_id
            && result.selector.attachment_id == attachment_id;
        if !expected_selector {
            self.active = false;
            return Err(Error::Identity);
        }
        if result.source != "imported_bundle"
            || !result.local_available
            || !result.available
            || result.mime_type != "application/octet-stream"
            || result.size > MAX_BYTES
            || !hex64(&result.sha256)
            || result.download_route != path
            || !result.requires_bearer
            || result.provider_path
            || !result.blob_verified
            || result.persistent_url
        {
            self.active = false;
            return Err(Error::Protocol);
        }
        let mut digest = [0; 32];
        if hex::decode_to_slice(&result.sha256, &mut digest).is_err() {
            self.active = false;
            return Err(Error::Protocol);
        }
        self.live()?;
        Ok(Reference {
            session: Arc::downgrade(&self.session),
            selector: result.selector,
            size: result.size,
            digest,
        })
    }

    /// Retrieve exactly one full, previously verified local object. Consumes the
    /// reference; a later attempt requires explicitly verifying a new reference.
    /// No bytes are released on error. This API never opens or writes a file.
    pub fn download_media(&mut self, reference: Reference) -> Result<Zeroizing<Vec<u8>>, Error> {
        if !self.media || self.connection.mode() != Mode::ImportedMedia {
            return Err(Error::Unsupported);
        }
        if !reference.session.ptr_eq(&Arc::downgrade(&self.session))
            || reference.selector.platform != self.scope.platform
            || reference.selector.account_id != self.scope.account_id
        {
            return Err(Error::Identity);
        }
        let target = self.target(
            &format!("{MEDIA}/{}", reference.selector.attachment_id),
            &[
                ("conversation_id", &reference.selector.conversation_id),
                ("message_id", &reference.selector.message_id),
            ],
        )?;
        self.call_wire(
            &target,
            true,
            None,
            None,
            200,
            MAX_BYTES,
            ResponseKind::Octet { exact: reference.size },
            |bytes| {
                let digest: [u8; 32] = Sha256::digest(bytes.as_slice()).into();
                if digest != reference.digest {
                    return Err(Error::Protocol);
                }
                Ok(bytes)
            },
        )
    }
}
