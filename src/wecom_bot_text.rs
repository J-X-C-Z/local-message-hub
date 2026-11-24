//! Disconnected protocol core for one explicitly reviewed WeCom smart-bot text send.
//! The caller owns operator authentication, TLS, and durable send-attempt policy.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::time::{Duration, Instant};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

pub const OFFICIAL_ENDPOINT: &str = "wss://openws.work.weixin.qq.com/";
pub const MAX_TEXT_BYTES: usize = 20_480;
/// Limit an assembled WebSocket message at the transport too, before allocating it.
pub const MAX_FRAME_BYTES: usize = 1024 * 1024;
const MAX_INBOUND_BYTES: usize = 4 * MAX_FRAME_BYTES;
const MAX_INBOUND_FRAMES: usize = 64;
const MAX_ID_BYTES: usize = 256;
const MAX_SECRET_BYTES: usize = 1024;
const REVIEW_WINDOW: Duration = Duration::from_secs(60);
const SEND_WINDOW: Duration = Duration::from_secs(8);
const SUBSCRIBE: &str = "aibot_subscribe";
const SEND: &str = "aibot_send_msg";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RecipientKind {
    Private,
    Group,
}

#[derive(Debug, thiserror::Error, Eq, PartialEq)]
pub enum Error {
    #[error("invalid bot or recipient identity")]
    Identity,
    #[error("text is empty, exceeds the UTF-8 byte limit, or contains unsupported controls")]
    Text,
    #[error("secure randomness unavailable")]
    Random,
    #[error("review expired or did not match this exact proposal")]
    Review,
    #[error("credential does not match the approved bot or is invalid")]
    Credential,
    #[error("protocol frame encoding failed")]
    Encoding,
    #[error("send frame is unavailable in the current phase")]
    Phase,
}

/// This opaque, non-cloneable proposal is not a permission grant. Only a trusted local
/// operator interface should present it and return its challenge after explicit approval.
pub struct Proposal {
    bot: String,
    recipient: String,
    kind: RecipientKind,
    text: Zeroizing<String>,
    challenge: [u8; 32],
    review_until: Instant,
}

impl Proposal {
    pub fn new(bot: &str, recipient: &str, kind: RecipientKind, text: &str) -> Result<Self, Error> {
        if !valid_identity(bot) || !valid_identity(recipient) {
            return Err(Error::Identity);
        }
        if text.len() > MAX_TEXT_BYTES
            || text.trim().is_empty()
            || text
                .chars()
                .any(|c| c.is_control() && !matches!(c, '\n' | '\r' | '\t'))
        {
            return Err(Error::Text);
        }
        let mut nonce = [0u8; 32];
        getrandom::fill(&mut nonce).map_err(|_| Error::Random)?;
        let mut hash = Sha256::new();
        hash.update(b"lmh-wecom-bot-text-review-v1\0");
        hash.update(nonce);
        hash.update([match kind {
            RecipientKind::Private => 0,
            RecipientKind::Group => 1,
        }]);
        for field in [bot, recipient, text] {
            hash.update((field.len() as u64).to_be_bytes());
            hash.update(field.as_bytes());
        }
        Ok(Self {
            bot: bot.to_owned(),
            recipient: recipient.to_owned(),
            kind,
            text: Zeroizing::new(text.to_owned()),
            challenge: hash.finalize().into(),
            review_until: Instant::now() + REVIEW_WINDOW,
        })
    }

    /// Exact, untrimmed bytes as previewed; no formatting or payload mutation follows.
    pub fn review(&self) -> (&str, &str, RecipientKind, &str) {
        (&self.bot, &self.recipient, self.kind, self.text.as_str())
    }

    pub fn challenge(&self) -> [u8; 32] {
        self.challenge
    }

    /// Consumes the proposal even on failure. Returning an automatically echoed
    /// challenge does not constitute operator authorization.
    pub fn approve(self, operator_challenge: &[u8; 32]) -> Result<Approved, Error> {
        if Instant::now() >= self.review_until
            || !bool::from(self.challenge.ct_eq(operator_challenge))
        {
            return Err(Error::Review);
        }
        Ok(Approved(self))
    }
}

pub struct Approved(Proposal);

/// One outgoing JSON message for the already-verified fixed endpoint. Deliberately
/// neither Clone nor Debug. A transport adapter must attempt it at most once, then
/// drop the buffer and never redial or queue it for replay.
#[must_use]
pub struct Frame(Zeroizing<Vec<u8>>);

impl Frame {
    pub fn into_bytes(self) -> Zeroizing<Vec<u8>> {
        self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NotAttempted {
    AuthorizationExpired,
    Deadline,
    Closed,
    Protocol,
    Limit,
    SubscribeRejected { code: i64 },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Unknown {
    Deadline,
    Closed,
    Protocol,
    Limit,
    MissingMessageId,
}

#[derive(Debug, Eq, PartialEq)]
pub enum Outcome {
    NotAttempted(NotAttempted),
    /// A correlated successful provider response, not evidence of receipt/read by a human.
    ProviderAccepted { message_id: String },
    ProviderRejected { code: i64 },
    DeliveryUnknown(Unknown),
}

#[derive(Debug, Eq, PartialEq)]
pub enum Status {
    AwaitingSubscribe,
    ReadyToSend,
    AwaitingDelivery,
    Finished(Outcome),
}

/// One short-lived exchange on one transport connection; never reattach it to a new socket.
pub struct Exchange {
    approved: Option<Approved>,
    sub_id: String,
    send_id: String,
    deadline: Instant,
    inbound_frames: usize,
    inbound_bytes: usize,
    status: Status,
}

impl Exchange {
    /// Consume an approval and secret for the exact bot. Start before transport setup so
    /// the caller can use the same absolute deadline for DNS, TLS, write, and reads.
    /// Do not release the returned frame onto an unverified or expired connection.
    pub fn begin(
        approved: Approved,
        credential_bot: &str,
        secret: Zeroizing<String>,
    ) -> Result<(Self, Frame), Error> {
        let now = Instant::now();
        if now >= approved.0.review_until {
            return Err(Error::Review);
        }
        if credential_bot != approved.0.bot.as_str()
            || secret.is_empty()
            || secret.len() > MAX_SECRET_BYTES
            || secret.chars().any(|c| c.is_whitespace() || c.is_control())
        {
            return Err(Error::Credential);
        }
        let mut randomness = [0u8; 32];
        getrandom::fill(&mut randomness).map_err(|_| Error::Random)?;
        let sub_id = format!("{SUBSCRIBE}_{}", hex::encode(&randomness[..16]));
        let send_id = format!("{SEND}_{}", hex::encode(&randomness[16..]));
        let subscribe = encode(
            &Outbound {
                cmd: SUBSCRIBE,
                headers: OutboundHeaders { req_id: &sub_id },
                body: SubscribeBody {
                    bot_id: &approved.0.bot,
                    secret: secret.as_str(),
                },
            },
            approved.0.bot.len() + secret.len(),
        )?;
        let deadline = (now + SEND_WINDOW).min(approved.0.review_until);
        Ok((
            Self {
                approved: Some(approved),
                sub_id,
                send_id,
                deadline,
                inbound_frames: 0,
                inbound_bytes: 0,
                status: Status::AwaitingSubscribe,
            },
            subscribe,
        ))
    }

    pub fn deadline(&self) -> Instant {
        self.deadline
    }

    pub fn status(&self) -> &Status {
        &self.status
    }

    /// Consume the reviewed body and enter the possibly-sent state before releasing
    /// any bytes. Retry is never allowed even if the transport reports a write error.
    pub fn begin_send(&mut self) -> Result<Frame, Error> {
        if !self.live() {
            return Err(Error::Phase);
        }
        if self.status != Status::ReadyToSend {
            return Err(Error::Phase);
        }
        let Some(approved) = self.approved.as_ref() else {
            self.protocol_error();
            return Err(Error::Phase);
        };
        let frame = encode(
            &Outbound {
                cmd: SEND,
                headers: OutboundHeaders {
                    req_id: &self.send_id,
                },
                body: SendBody {
                    chatid: &approved.0.recipient,
                    msgtype: "markdown",
                    markdown: Markdown {
                        content: approved.0.text.as_str(),
                    },
                },
            },
            approved.0.recipient.len() + approved.0.text.len(),
        );
        match frame {
            Ok(frame) if self.live() => {
                self.approved = None;
                self.status = Status::AwaitingDelivery;
                Ok(frame)
            }
            Ok(_) => Err(Error::Phase),
            Err(error) => {
                self.protocol_error();
                Err(error)
            }
        }
    }

    /// Feed a complete authenticated WebSocket text message. Binary and transport
    /// errors must terminate the exchange via close; never process them as JSON.
    pub fn receive(&mut self, input: &[u8]) -> &Status {
        if self.live() {
            self.receive_live(input);
        }
        self.status()
    }

    fn receive_live(&mut self, input: &[u8]) {
        if input.len() > MAX_FRAME_BYTES
            || self.inbound_frames >= MAX_INBOUND_FRAMES
            || input.len() > MAX_INBOUND_BYTES - self.inbound_bytes
        {
            self.abort(NotAttempted::Limit, Unknown::Limit);
            return;
        }
        self.inbound_frames += 1;
        self.inbound_bytes += input.len();
        let Ok(envelope) = serde_json::from_slice::<Envelope>(input) else {
            self.protocol_error();
            return;
        };
        match envelope.cmd.as_deref() {
            Some("aibot_msg_callback" | "aibot_callback") => return,
            Some("aibot_event_callback") => {
                // A fresh session can displace an existing one, or itself be displaced.
                // Never treat that frame as an ACK even if a request ID happens to match.
                match serde_json::from_slice::<EventFrame>(input) {
                    Ok(frame) if frame.body.event.eventtype == "disconnected_event" => {
                        self.close();
                    }
                    Ok(_) => {}
                    Err(_) => self.protocol_error(),
                }
                return;
            }
            _ => {}
        }
        let (expected_id, expected_cmd) = match &self.status {
            Status::AwaitingSubscribe => (&self.sub_id, SUBSCRIBE),
            Status::AwaitingDelivery => (&self.send_id, SEND),
            _ => return,
        };
        let Some(headers) = envelope.headers else {
            self.protocol_error();
            return;
        };
        if headers.req_id.as_str() != expected_id.as_str() {
            return;
        }
        if !(matches!(envelope.cmd.as_deref(), None | Some(""))
            || envelope.cmd.as_deref() == Some(expected_cmd))
        {
            self.protocol_error();
            return;
        }
        let Some(code) = envelope.errcode else {
            self.protocol_error();
            return;
        };
        if !self.live() {
            return;
        }
        if expected_cmd == SUBSCRIBE {
            if code == 0 {
                self.status = Status::ReadyToSend;
            } else {
                self.finish(Outcome::NotAttempted(NotAttempted::SubscribeRejected {
                    code,
                }));
            }
        } else if code != 0 {
            self.finish(Outcome::ProviderRejected { code });
        } else {
            let id = serde_json::from_slice::<DeliveryFrame>(input)
                .ok()
                .and_then(|frame| frame.body)
                .and_then(|body| body.msgid)
                .and_then(MessageId::bounded);
            if !self.live() {
                return;
            }
            self.finish(match id {
                Some(message_id) => Outcome::ProviderAccepted { message_id },
                None => Outcome::DeliveryUnknown(Unknown::MissingMessageId),
            });
        }
    }

    /// Call for cancellation, EOF, write failure, invalid WebSocket message type,
    /// or other I/O faults. If the deadline has elapsed, preserve that distinction.
    pub fn close(&mut self) -> &Status {
        if self.live() {
            self.abort(NotAttempted::Closed, Unknown::Closed);
        }
        self.status()
    }

    fn live(&mut self) -> bool {
        if matches!(self.status, Status::Finished(_)) {
            return false;
        }
        let now = Instant::now();
        if now >= self.deadline {
            let reason = if self
                .approved
                .as_ref()
                .map(|a| now >= a.0.review_until)
                .unwrap_or(false)
            {
                NotAttempted::AuthorizationExpired
            } else {
                NotAttempted::Deadline
            };
            self.abort(reason, Unknown::Deadline);
            return false;
        }
        true
    }

    fn protocol_error(&mut self) {
        self.abort(NotAttempted::Protocol, Unknown::Protocol);
    }

    fn abort(&mut self, unsent: NotAttempted, sent: Unknown) {
        self.finish(if self.status == Status::AwaitingDelivery {
            Outcome::DeliveryUnknown(sent)
        } else {
            Outcome::NotAttempted(unsent)
        });
    }

    fn finish(&mut self, outcome: Outcome) {
        self.approved = None;
        self.status = Status::Finished(outcome);
    }
}

fn valid_identity(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= MAX_ID_BYTES
        && !id.chars().any(|c| c.is_whitespace() || c.is_control())
}

fn encode(frame: &impl Serialize, variable_bytes: usize) -> Result<Frame, Error> {
    // Inputs were validated; six JSON bytes per input byte is the worst case.
    // Reserve once to avoid copying unzeroized secret bytes on Vec growth.
    let mut bytes = Zeroizing::new(Vec::with_capacity(6 * variable_bytes + 512));
    serde_json::to_writer(&mut *bytes, frame).map_err(|_| Error::Encoding)?;
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(Error::Encoding);
    }
    Ok(Frame(bytes))
}

#[derive(Serialize)]
struct Outbound<'a, T> {
    cmd: &'static str,
    headers: OutboundHeaders<'a>,
    body: T,
}
#[derive(Serialize)]
struct OutboundHeaders<'a> {
    req_id: &'a str,
}
#[derive(Serialize)]
struct SubscribeBody<'a> {
    bot_id: &'a str,
    secret: &'a str,
}
#[derive(Serialize)]
struct SendBody<'a> {
    chatid: &'a str,
    msgtype: &'static str,
    markdown: Markdown<'a>,
}
#[derive(Serialize)]
struct Markdown<'a> {
    content: &'a str,
}

#[derive(Deserialize)]
struct Envelope {
    cmd: Option<String>,
    headers: Option<InboundHeaders>,
    errcode: Option<i64>,
}
#[derive(Deserialize)]
struct InboundHeaders {
    req_id: String,
}
#[derive(Deserialize)]
struct EventFrame {
    body: EventBody,
}
#[derive(Deserialize)]
struct EventBody {
    event: ProviderEvent,
}
#[derive(Deserialize)]
struct ProviderEvent {
    eventtype: String,
}
#[derive(Deserialize)]
struct DeliveryFrame {
    body: Option<DeliveryBody>,
}
#[derive(Deserialize)]
struct DeliveryBody {
    msgid: Option<MessageId>,
}
#[derive(Deserialize)]
#[serde(untagged)]
enum MessageId {
    Text(String),
    Signed(i64),
    Unsigned(u64),
}
impl MessageId {
    fn bounded(self) -> Option<String> {
        match self {
            Self::Text(s) if valid_identity(&s) => Some(s),
            Self::Signed(n) => Some(n.to_string()),
            Self::Unsigned(n) => Some(n.to_string()),
            _ => None,
        }
    }
}
