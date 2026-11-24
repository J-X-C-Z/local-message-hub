//! Operator-invoked, explicitly pinned IMAP transport preview. No archive or scheduler.
use crate::{eml_import::{MAX_FILE, canonical_account}, legacy_import::read_stable_source};
use base64::{Engine, engine::general_purpose::STANDARD};
use rustix::fs::{FileType, fstat};
use rustls::{ClientConfig, ClientConnection, RootCertStore, StreamOwned,
    pki_types::{CertificateDer, ServerName}};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    io::{self, BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
const SESSION_TIMEOUT: Duration = Duration::from_secs(8);
const MAX_LINE: usize = 1_024;
const MAX_EXAMINE_LINES: usize = 32;
const MAX_LOGIN: usize = 255;
const MAX_SECRET: usize = 512;
const MAX_CREDENTIAL_FRAME: usize = MAX_LOGIN + MAX_SECRET + 2;
pub const MAX_EXPLICIT_UIDS:usize=8;
const MAX_BATCH_RAW:usize=MAX_FILE;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("invalid IMAP preview scope or trust material")]
    Config,
    #[error("expected one private credential pipe with a bounded two-line input")]
    Credentials,
    #[error("private mailbox identity does not match the selected account fingerprint")]
    AccountScope,
    #[error("IMAP preview connection, deadline or TLS verification failed")]
    Transport,
    #[error("server rejected or does not support the selected authentication")]
    Authentication,
    #[error("IMAP response outside the supported read-only subset")]
    Protocol,
    #[error("IMAP UIDVALIDITY does not match the selected epoch")]
    Epoch,
}

type Result<T> = std::result::Result<T, Error>;

pub struct Settings<'a> {
    pub hostname: &'a str,
    pub endpoint: SocketAddr,
    pub ca_der: &'a Path,
    pub leaf_sha256: &'a str,
    pub mailbox: &'a str,
    pub uidvalidity: u32,
    pub uid: u32,
}

#[derive(Debug, Serialize)]
pub struct Preview {
    mode: &'static str,
    uidvalidity: u32,
    uid: u32,
    octets: usize,
    archive_written: bool,
}

impl Settings<'_> {
    fn validate(&self) -> Result<()> {
        let host = self.hostname;
        if !(3..=253).contains(&host.len()) || !host.contains('.') || host.parse::<std::net::IpAddr>().is_ok()
            || !host.split('.').all(|label| (1..=63).contains(&label.len())
                && !label.starts_with('-') && !label.ends_with('-')
                && label.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'))
            || self.endpoint.port() == 0 || self.endpoint.ip().is_unspecified()
            || self.endpoint.ip().is_multicast() || self.endpoint.ip() == std::net::Ipv4Addr::BROADCAST
            || matches!(self.endpoint, SocketAddr::V6(ip) if ip.scope_id() != 0 || ip.flowinfo() != 0)
            || self.mailbox != "INBOX" || self.uidvalidity == 0 || self.uid == 0
            || self.leaf_sha256.len() != 64 || !self.leaf_sha256.bytes().all(|b|
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Config);
        }
        Ok(())
    }
}

struct Credentials {
    secret_frame: Zeroizing<Vec<u8>>,
    separator: usize,
}
impl Credentials {
    fn load() -> Result<Self> {
        let stdin = io::stdin();
        let mode = fstat(&stdin).map_err(|_| Error::Credentials)?.st_mode;
        if FileType::from_raw_mode(mode as _) != FileType::Fifo { return Err(Error::Credentials); }
        let mut secret_frame = Zeroizing::new(Vec::with_capacity(MAX_CREDENTIAL_FRAME + 1));
        stdin.lock().take((MAX_CREDENTIAL_FRAME + 1) as u64)
            .read_to_end(&mut secret_frame).map_err(|_| Error::Credentials)?;
        if secret_frame.len() > MAX_CREDENTIAL_FRAME || secret_frame.last() != Some(&b'\n') {
            return Err(Error::Credentials);
        }
        let separator = secret_frame.iter().position(|&b| b == b'\n').ok_or(Error::Credentials)?;
        let account = std::str::from_utf8(&secret_frame[..separator]).map_err(|_| Error::Credentials)?;
        if !(1..=MAX_LOGIN).contains(&account.len()) || !canonical_account(account) {
            return Err(Error::Credentials);
        }
        let password = &secret_frame[separator + 1..secret_frame.len() - 1];
        if !(1..=MAX_SECRET).contains(&password.len()) || !password.iter().all(|b| (b' '..=b'~').contains(b)) {
            return Err(Error::Credentials);
        }
        Ok(Self { secret_frame, separator })
    }

    fn account(&self) -> Result<&str> {
        std::str::from_utf8(&self.secret_frame[..self.separator]).map_err(|_| Error::Credentials)
    }

    fn wire(&self) -> Result<Zeroizing<Vec<u8>>> {
        let account = &self.secret_frame[..self.separator];
        let password = &self.secret_frame[self.separator + 1..self.secret_frame.len() - 1];
        let mut payload = Zeroizing::new(Vec::with_capacity(2 + account.len() + password.len()));
        payload.push(0); // no explicit authorization identity or delegation
        payload.extend_from_slice(account);
        payload.push(0);
        payload.extend_from_slice(password);
        let encoded_len = payload.len().div_ceil(3) * 4;
        let mut encoded = Zeroizing::new(vec![0; encoded_len + 2]);
        if STANDARD.encode_slice(payload.as_slice(), &mut encoded[..encoded_len])
            .map_err(|_| Error::Credentials)? != encoded_len
        {
            return Err(Error::Credentials);
        }
        encoded[encoded_len..].copy_from_slice(b"\r\n");
        Ok(encoded)
    }
}

struct DeadlineSocket(TcpStream, Instant);
impl DeadlineSocket {
    fn remaining(&self) -> io::Result<Duration> {
        self.1.checked_duration_since(Instant::now()).filter(|left| !left.is_zero())
            .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "IMAP deadline"))
    }
}
impl Read for DeadlineSocket {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.0.set_read_timeout(Some(self.remaining()?))?;
        self.0.read(buffer)
    }
}
impl Write for DeadlineSocket {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.0.set_write_timeout(Some(self.remaining()?))?;
        self.0.write(buffer)
    }
    fn flush(&mut self) -> io::Result<()> { self.0.flush() }
}

fn transport(_: io::Error) -> Error { Error::Transport }

type Tls = StreamOwned<ClientConnection, DeadlineSocket>;

fn connect(settings: &Settings<'_>) -> Result<(Tls, [u8; 32])> {
    let cert = read_stable_source(settings.ca_der, 16 * 1024).map_err(|_| Error::Config)?;
    let ca_hash = Sha256::digest(&cert).into();
    let mut roots = RootCertStore::empty();
    roots.add(CertificateDer::from(cert)).map_err(|_| Error::Config)?;
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13]).map_err(|_| Error::Config)?
        .with_root_certificates(roots).with_no_client_auth();
    let host = ServerName::try_from(settings.hostname.to_owned()).map_err(|_| Error::Config)?;
    let mut tls = ClientConnection::new(Arc::new(config), host).map_err(|_| Error::Transport)?;
    let socket = TcpStream::connect_timeout(&settings.endpoint, CONNECT_TIMEOUT).map_err(transport)?;
    let mut socket = DeadlineSocket(socket, Instant::now() + SESSION_TIMEOUT);
    while tls.is_handshaking() { tls.complete_io(&mut socket).map_err(transport)?; }
    let leaf = tls.peer_certificates().and_then(|chain| chain.first()).ok_or(Error::Transport)?;
    if hex::encode(Sha256::digest(leaf.as_ref())) != settings.leaf_sha256 {
        return Err(Error::Transport);
    }
    Ok((StreamOwned::new(tls, socket), ca_hash))
}

fn line(reader: &mut impl BufRead) -> Result<Zeroizing<Vec<u8>>> {
    let mut bytes = Zeroizing::new(Vec::with_capacity(MAX_LINE + 3));
    reader.take((MAX_LINE + 3) as u64).read_until(b'\n', &mut bytes).map_err(transport)?;
    let len = bytes.len().checked_sub(2).ok_or(Error::Protocol)?;
    if len > MAX_LINE || &bytes[len..] != b"\r\n" || !bytes[..len].iter().all(|b| (b' '..=b'~').contains(b)) {
        return Err(Error::Protocol);
    }
    bytes.truncate(len);
    Ok(bytes)
}

fn text(bytes: &[u8]) -> Result<&str> {
    std::str::from_utf8(bytes).map_err(|_| Error::Protocol)
}

fn send<S: Read + Write>(wire: &mut BufReader<S>, cmd: &[u8]) -> Result<()> {
    wire.get_mut().write_all(cmd).and_then(|_| wire.get_mut().flush()).map_err(transport)
}

/// Require an exact response tag and status token, without treating server
/// response text as instructions or reporting it in diagnostics.
fn status<'a>(response: &'a str, tag: &str, state: &str) -> Option<&'a str> {
    let (token, remainder) = response.split_once(' ')?;
    if token != tag { return None; }
    let (status, message) = remainder.split_once(' ').unwrap_or((remainder, ""));
    status.eq_ignore_ascii_case(state).then_some(message)
}

fn code(response: &str) -> Option<&str> {
    let (inside, rest) = response.strip_prefix('[')?.split_once(']')?;
    (rest.is_empty() || rest.starts_with(' ')).then_some(inside)
}

fn number(value: &str, zero: bool) -> Option<u32> {
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|b| b.is_ascii_digit())
        || (value.starts_with('0') && (!zero || value != "0"))
    {
        return None;
    }
    value.parse().ok()
}

fn authenticate<S: Read + Write>(wire: &mut BufReader<S>, credentials: Credentials) -> Result<()> {
    let greeting = line(wire)?;
    if status(text(&greeting)?, "*", "OK").is_none() { return Err(Error::Protocol); }
    send(wire, b"a1 CAPABILITY\r\n")?;
    let caps = line(wire)?;
    let items = status(text(&caps)?, "*", "CAPABILITY").ok_or(Error::Protocol)?;
    if !items.split_ascii_whitespace().any(|v| v.eq_ignore_ascii_case("AUTH=PLAIN"))
        || !items.split_ascii_whitespace().any(|v|
            v.eq_ignore_ascii_case("IMAP4rev1") || v.eq_ignore_ascii_case("IMAP4rev2"))
    {
        return Err(Error::Authentication);
    }
    if status(text(&line(wire)?)?, "a1", "OK").is_none() { return Err(Error::Protocol); }
    send(wire, b"a2 AUTHENTICATE PLAIN\r\n")?;
    let continuation = line(wire)?;
    if !matches!(continuation.as_slice(), b"+" | b"+ ") { return Err(Error::Authentication); }
    let response = credentials.wire()?;
    drop(credentials);
    send(wire, &response)?;
    drop(response);
    if status(text(&line(wire)?)?, "a2", "OK").is_none() { return Err(Error::Authentication); }
    Ok(())
}

fn examine<S: Read + Write>(wire: &mut BufReader<S>, expected: u32) -> Result<u32> {
    send(wire, b"a3 EXAMINE INBOX\r\n")?;
    let mut epoch = None;
    let mut exists = None;
    for _ in 0..MAX_EXAMINE_LINES {
        let bytes = line(wire)?;
        let response = text(&bytes)?;
        if let Some(message) = status(response, "a3", "OK") {
            if !code(message).is_some_and(|c| c.eq_ignore_ascii_case("READ-ONLY")) {
                return Err(Error::Protocol);
            }
            if epoch != Some(expected) { return Err(Error::Epoch); }
            return exists.filter(|&n| n > 0).ok_or(Error::Protocol);
        }
        if let Some(message) = status(response, "*", "OK") {
            let code = code(message).ok_or(Error::Protocol)?;
            let (kind, data) = code.split_once(' ').ok_or(Error::Protocol)?;
            if kind.eq_ignore_ascii_case("UIDVALIDITY") {
                let current = number(data, false).ok_or(Error::Protocol)?;
                if current != expected { return Err(Error::Epoch); }
                if epoch.replace(current).is_some() { return Err(Error::Protocol); }
            } else {
                let allowed_numeric = ["UIDNEXT", "UNSEEN", "HIGHESTMODSEQ"]
                    .iter().any(|k| kind.eq_ignore_ascii_case(k))
                    && !data.is_empty() && data.len() <= 20
                    && data.bytes().all(|b| b.is_ascii_digit());
                let allowed_flags = kind.eq_ignore_ascii_case("PERMANENTFLAGS") && flags(data);
                if !allowed_numeric && !allowed_flags { return Err(Error::Protocol); }
            }
        } else if let Some(data) = status(response, "*", "FLAGS") {
            if !flags(data) { return Err(Error::Protocol); }
        } else {
            let (value, kind) = response.strip_prefix("* ").and_then(|s| s.split_once(' ')).ok_or(Error::Protocol)?;
            let value = number(value, true).ok_or(Error::Protocol)?;
            if kind.eq_ignore_ascii_case("EXISTS") {
                if exists.replace(value).is_some() { return Err(Error::Protocol); }
            } else if !kind.eq_ignore_ascii_case("RECENT") {
                return Err(Error::Protocol);
            }
        }
    }
    Err(Error::Protocol)
}

fn flags(value: &str) -> bool {
    value.strip_prefix('(').and_then(|s| s.strip_suffix(')'))
        .is_some_and(|s| !s.contains(['(', ')', '[', ']', '{', '}', '"']))
}

fn fetch_one<S: Read + Write>(wire: &mut BufReader<S>, tag:&str, uid: u32, exists: u32,
    max_bytes:usize, body: &mut impl Write) -> Result<usize>
{
    if max_bytes==0||max_bytes>MAX_FILE{return Err(Error::Protocol);}
    send(wire, format!("{tag} UID FETCH {uid} (UID RFC822.SIZE BODY.PEEK[]<0.{}>)\r\n", max_bytes + 1).as_bytes())?;
    let bytes = line(wire)?;
    let response = text(&bytes)?;
    let mut fields = response.split(' ');
    if fields.next() != Some("*")
        || fields.next().and_then(|s| number(s, false)).is_none_or(|n| n > exists)
        || !fields.next().is_some_and(|s| s.eq_ignore_ascii_case("FETCH"))
        || !fields.next().is_some_and(|s| s.eq_ignore_ascii_case("(UID"))
        || fields.next().and_then(|s| number(s, false)) != Some(uid)
        || !fields.next().is_some_and(|s| s.eq_ignore_ascii_case("RFC822.SIZE"))
    {
        return Err(Error::Protocol);
    }
    let size = fields.next().and_then(|s| number(s, false)).ok_or(Error::Protocol)? as usize;
    if size > max_bytes || !fields.next().is_some_and(|s| s.eq_ignore_ascii_case("BODY[]<0>")) {
        return Err(Error::Protocol);
    }
    let literal = fields.next().and_then(|v| v.strip_prefix('{')?.strip_suffix('}'))
        .and_then(|v| number(v, false)).ok_or(Error::Protocol)? as usize;
    if literal != size || fields.next().is_some() { return Err(Error::Protocol); }

    // The caller supplies either a discard sink or a bounded in-memory buffer.
    // No wire event can choose a path or write to an archive.
    let mut scratch = Zeroizing::new([0_u8; 4_096]);
    let mut remaining = literal;
    while remaining != 0 {
        let step = remaining.min(scratch.len());
        wire.read_exact(&mut scratch[..step]).map_err(transport)?;
        body.write_all(&scratch[..step]).map_err(transport)?;
        remaining -= step;
    }
    if line(wire)?.as_slice() != b")" { return Err(Error::Protocol); }
    let complete = line(wire)?;
    if !status(text(&complete)?, tag, "OK").is_some_and(|s| !s.starts_with('[')) {
        return Err(Error::Protocol);
    }
    Ok(literal)
}

type Fetched = (usize, Option<Zeroizing<String>>, [u8; 32]);
type Session = (BufReader<Tls>, Option<Zeroizing<String>>, [u8; 32],u32);

fn start_selected_session(settings:&Settings<'_>,expected_account:Option<&str>)->Result<Session> {
    settings.validate()?;
    let credentials = Credentials::load()?;
    let account = if let Some(expected) = expected_account {
        let account = credentials.account()?;
        if hex::encode(Sha256::digest(account.as_bytes())) != expected {
            return Err(Error::AccountScope);
        }
        Some(Zeroizing::new(account.to_owned()))
    } else { None };
    let (tls, ca_hash) = connect(settings)?;
    let mut wire = BufReader::new(tls);
    authenticate(&mut wire, credentials)?;
    let exists = examine(&mut wire, settings.uidvalidity)?;
    Ok((wire,account,ca_hash,exists))
}

fn logout<S:Read+Write>(wire:&mut BufReader<S>,tag:&str)->Result<()> {
    send(wire,format!("{tag} LOGOUT\r\n").as_bytes())?;
    if status(text(&line(wire)?)?, "*", "BYE").is_none()
        || status(text(&line(wire)?)?, tag, "OK").is_none()
    {
        return Err(Error::Protocol);
    }
    Ok(())
}

fn fetch_from_private_stdin(settings: &Settings<'_>, expected_account: Option<&str>,
    body: &mut impl Write) -> Result<Fetched>
{
    let (mut wire,account,ca_hash,exists)=start_selected_session(settings,expected_account)?;
    let octets = fetch_one(&mut wire, "a4", settings.uid, exists, MAX_FILE, body)?;
    logout(&mut wire,"a5")?;
    Ok((octets, account, ca_hash))
}

pub(crate) fn valid_uid_selection(uids:&[u32],previous:u32)->bool {
    (2..=MAX_EXPLICIT_UIDS).contains(&uids.len()) && uids[0]>previous
        && uids.windows(2).all(|pair|pair[0]<pair[1])
}

type ExplicitBatch = (Zeroizing<String>, [u8; 32], Vec<Zeroizing<Vec<u8>>>);

/// One pinned TLS session with exactly the supplied, validated UID sequence.
/// Refuse an oversized literal before copying any of it into an item buffer.
pub(crate) fn fetch_explicit_batch(settings:&Settings<'_>,expected_account:&str,uids:&[u32])
    ->Result<ExplicitBatch> {
    if !valid_uid_selection(uids,0)||uids.first().copied()!=Some(settings.uid) {return Err(Error::Config);}
    let (mut wire,account,ca_hash,exists)=start_selected_session(settings,Some(expected_account))?;
    let mut remaining=MAX_BATCH_RAW;
    let mut bodies=Vec::with_capacity(uids.len());
    for (index,&uid) in uids.iter().enumerate() {
        if remaining==0 {return Err(Error::Protocol);}
        let mut buffer=Zeroizing::new(Vec::new());
        let tag=format!("a{}",index+4);
        let used=fetch_one(&mut wire,&tag,uid,exists,remaining,&mut *buffer)?;
        if used!=buffer.len(){return Err(Error::Protocol);}
        remaining=remaining.checked_sub(used).ok_or(Error::Protocol)?;
        bodies.push(buffer);
    }
    logout(&mut wire,"a99")?;
    Ok((account.ok_or(Error::AccountScope)?,ca_hash,bodies))
}

pub(crate) fn fetch_for_import(settings: &Settings<'_>, expected_account: &str,
    body: &mut Zeroizing<Vec<u8>>) -> Result<(Zeroizing<String>, [u8; 32])>
{
    let (octets, account, ca_hash) = fetch_from_private_stdin(settings, Some(expected_account), &mut **body)?;
    if octets != body.len() { return Err(Error::Protocol); }
    Ok((account.ok_or(Error::AccountScope)?, ca_hash))
}

pub fn preview_from_private_stdin(settings: &Settings<'_>) -> Result<Preview> {
    let (octets, _, _) = fetch_from_private_stdin(settings, None, &mut io::sink())?;
    Ok(Preview { mode: "imap-readonly-preview", uidvalidity: settings.uidvalidity,
        uid: settings.uid, octets, archive_written: false })
}
