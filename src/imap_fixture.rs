//! A TLS-only IMAP protocol fixture, intentionally not a real provider connector.
use crate::{eml_import,events::{EventError,EventStore,IngestRequest},legacy_import::read_stable_source};
use rusqlite::{Connection,OptionalExtension};
use rustls::{ClientConfig,ClientConnection,RootCertStore,StreamOwned,pki_types::{CertificateDer,ServerName}};
use serde::Serialize;
use sha2::{Digest,Sha256};
use std::{io::{self,BufRead,BufReader,Read,Write},net::{Ipv4Addr,SocketAddr,SocketAddrV4,TcpStream},
    path::Path,sync::Arc,time::{Duration,Instant}};

const NAME: &str="hub-imap-fixture.localhost";
const GREETING: &str="* PREAUTH [CAPABILITY IMAP4rev1] local-message-hub synthetic fixture";
const MODE: &str="lmh-imap-fixture-v1";
const SCHEMA: &str="
    CREATE TABLE native_imap_fixture_progress (
      source TEXT PRIMARY KEY NOT NULL,
      uidvalidity INTEGER NOT NULL CHECK(uidvalidity BETWEEN 1 AND 4294967295),
      uid INTEGER NOT NULL CHECK(uid BETWEEN 1 AND 4294967295)
    ) WITHOUT ROWID;
";

#[derive(Debug,thiserror::Error)]
pub enum Error {
    #[error("invalid synthetic IMAP fixture scope, TLS trust or sequence")]
    Config,
    #[error("synthetic IMAP TLS fixture unavailable or certificate verification failed")]
    Transport,
    #[error("synthetic IMAP fixture response outside the readonly protocol or expected UID")]
    Protocol,
    #[error("synthetic IMAP fixture UIDVALIDITY changed")]
    UidValidity,
    #[error("synthetic IMAP fixture checkpoint does not match expected previous UID")]
    Resume,
    #[error("fixture message is not supported plain-text EML for this account")]
    Message,
    #[error("target is not an available initialized ordinary Rust archive")]
    Archive,
    #[error("confirmation does not match this exact IMAP fixture preview")]
    Confirmation,
    #[error("fixture commit failed; retry exact preview if the result is uncertain")]
    Apply,
}
type Result<T> = std::result::Result<T,Error>;

#[derive(Clone,Copy)]
pub struct Window { pub uidvalidity: u32, pub after: u32, pub uid: u32 }

impl Window {
    fn valid(self) -> bool { self.uidvalidity != 0 && self.uid > self.after }
}

pub struct Settings<'a> {
    pub port:u16,
    pub ca_der:&'a Path,
    pub leaf_sha256:&'a str,
    pub account:&'a str,
    pub window:Window,
}

pub(crate) struct Advance {
    source:String,
    window:Window,
}

impl Advance {
    /// The exact scoped input is validated before the ordinary write lock is taken.
    pub(crate) fn check(&self,sql:&Connection,create:bool)->std::result::Result<(),EventError> {
        let kind:Option<String>=sql.query_row(
            "SELECT type FROM sqlite_schema WHERE name='native_imap_fixture_progress'", [], |r|r.get(0)).optional()?;
        let cursor=match kind.as_deref() {
            Some("table") => sql.query_row(
                "SELECT uidvalidity,uid FROM native_imap_fixture_progress WHERE source=?", [&self.source],
                |r|Ok((r.get::<_,u32>(0)?,r.get::<_,u32>(1)?))).optional()?,
            None => {
                if create { sql.execute_batch(SCHEMA)?; }
                None
            }
            _ => return Err(EventError::Storage),
        };
        if let Some((validity,uid))=cursor {
            if validity != self.window.uidvalidity { return Err(EventError::FixtureUidValidity); }
            if uid != self.window.after { return Err(EventError::FixtureResume); }
        } else if self.window.after != 0 { return Err(EventError::FixtureResume); }
        Ok(())
    }

    pub(crate) fn record(&self,sql:&Connection)->std::result::Result<(),EventError> {
        sql.execute("INSERT INTO native_imap_fixture_progress(source,uidvalidity,uid) VALUES (?,?,?)
            ON CONFLICT(source) DO UPDATE SET uid=excluded.uid",
            (&self.source,self.window.uidvalidity,self.window.uid))?;
        Ok(())
    }
}

struct TimedSocket(TcpStream,Instant);
impl TimedSocket {
    fn remaining(&self)->io::Result<Duration> {
        self.1.checked_duration_since(Instant::now()).filter(|d|!d.is_zero())
            .ok_or_else(||io::Error::new(io::ErrorKind::TimedOut,"TLS fixture deadline"))
    }
}
impl Read for TimedSocket {
    fn read(&mut self,b:&mut[u8])->io::Result<usize> {
        self.0.set_read_timeout(Some(self.remaining()?))?;
        self.0.read(b)
    }
}
impl Write for TimedSocket {
    fn write(&mut self,b:&[u8])->io::Result<usize> {
        self.0.set_write_timeout(Some(self.remaining()?))?;
        self.0.write(b)
    }
    fn flush(&mut self)->io::Result<()> { self.0.flush() }
}

fn fixture_io(_:io::Error)->Error { Error::Transport }

fn numeric(value:&str)->Option<u32> {
    if value.is_empty() || value.starts_with('0') || value.len()>10 || !value.bytes().all(|b|b.is_ascii_digit()) {
        return None;
    }
    value.parse().ok()
}

fn wire_line(reader:&mut impl BufRead)->Result<String> {
    let mut data=Vec::new();
    reader.take(514).read_until(b'\n',&mut data).map_err(fixture_io)?;
    let data=data.strip_suffix(b"\r\n").ok_or(Error::Protocol)?;
    if data.len()>512 || !data.iter().all(|&b|(b' '..=b'~').contains(&b)) { return Err(Error::Protocol); }
    String::from_utf8(data.to_vec()).map_err(|_|Error::Protocol)
}

fn command<S:Read+Write>(reader:&mut BufReader<S>,data:&[u8])->Result<()> {
    reader.get_mut().write_all(data).and_then(|_|reader.get_mut().flush()).map_err(fixture_io)
}

fn greeting(account:&str)->String {
    let mut hash=Sha256::new();
    hash.update(b"imap-fixture-mailbox-v1\0");
    hash.update(account);
    format!("{GREETING} {}",hex::encode(hash.finalize()))
}

fn read_only<S:Read+Write>(stream:S,window:Window,account:&str)->Result<Vec<u8>> {
    let mut read=BufReader::new(stream);
    if wire_line(&mut read)? != greeting(account) { return Err(Error::Protocol); }
    command(&mut read,b"a1 EXAMINE INBOX\r\n")?;
    // The fixture deliberately uses a single wire grammar, not an arbitrary IMAP client.
    if wire_line(&mut read)? != "* FLAGS (\\Seen)" { return Err(Error::Protocol); }
    let exists=wire_line(&mut read)?;
    let exists=exists.strip_prefix("* ").and_then(|s|s.strip_suffix(" EXISTS"))
        .and_then(numeric).ok_or(Error::Protocol)?;
    let validity=wire_line(&mut read)?;
    let validity=validity.strip_prefix("* OK [UIDVALIDITY ").and_then(|s|s.strip_suffix("] fixture epoch"))
        .and_then(numeric).ok_or(Error::Protocol)?;
    if wire_line(&mut read)? != "a1 OK [READ-ONLY] EXAMINE complete" { return Err(Error::Protocol); }
    if validity != window.uidvalidity { return Err(Error::UidValidity); }
    command(&mut read,format!("a2 UID FETCH {} (UID BODY.PEEK[])\r\n",window.uid).as_bytes())?;
    let announce=wire_line(&mut read)?;
    let (seq,suffix)=announce.strip_prefix("* ").and_then(|v|v.split_once(" FETCH (UID ")).ok_or(Error::Protocol)?;
    if numeric(seq).is_none_or(|n|n>exists) { return Err(Error::Protocol); }
    let (uid,lit)=suffix.split_once(" BODY[] {").ok_or(Error::Protocol)?;
    if numeric(uid) != Some(window.uid) { return Err(Error::Protocol); }
    let count=lit.strip_suffix('}').and_then(numeric).ok_or(Error::Protocol)? as usize;
    if count>eml_import::MAX_FILE { return Err(Error::Protocol); }
    let mut bytes=vec![0;count];
    read.read_exact(&mut bytes).map_err(fixture_io)?;
    if wire_line(&mut read)? != ")" || wire_line(&mut read)? != "a2 OK FETCH complete" {
        return Err(Error::Protocol);
    }
    Ok(bytes)
}

fn fetch(settings:&Settings<'_>)->Result<Vec<u8>> {
    let ca=read_stable_source(settings.ca_der,16*1024).map_err(|_|Error::Config)?;
    let mut roots=RootCertStore::empty();
    roots.add(CertificateDer::from(ca)).map_err(|_|Error::Config)?;
    let provider=Arc::new(rustls::crypto::ring::default_provider());
    let config=ClientConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13]).map_err(|_|Error::Config)?
        .with_root_certificates(roots).with_no_client_auth();
    let host=ServerName::try_from(NAME).map_err(|_|Error::Config)?;
    let mut client=ClientConnection::new(Arc::new(config),host).map_err(|_|Error::Transport)?;
    let address=SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST,settings.port));
    let mut socket=TimedSocket(TcpStream::connect_timeout(&address,Duration::from_secs(1)).map_err(fixture_io)?,
        Instant::now()+Duration::from_secs(4));
    while client.is_handshaking() { client.complete_io(&mut socket).map_err(fixture_io)?; }
    let leaf=client.peer_certificates().and_then(|lst|lst.first()).ok_or(Error::Transport)?;
    if hex::encode(Sha256::digest(leaf.as_ref())) != settings.leaf_sha256 { return Err(Error::Transport); }
    read_only(StreamOwned::new(client,socket),settings.window,settings.account)
}

fn progress(settings:&Settings<'_>)->Result<Advance> {
    let pin=settings.leaf_sha256;
    if settings.port==0 || !settings.window.valid() || pin.len()!=64 || !pin.bytes().all(|b|
        b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) || !eml_import::canonical_account(settings.account) {
        return Err(Error::Config);
    }
    let identity=serde_json::to_vec(&(MODE,NAME,pin,"INBOX",settings.account)).map_err(|_|Error::Config)?;
    Ok(Advance{source:hex::encode(Sha256::digest(identity)),window:settings.window})
}

fn prepare(settings:&Settings<'_>,archive:&EventStore,advance:&Advance)->Result<IngestRequest> {
    let bytes=fetch(settings)?;
    let message=eml_import::decode(&bytes,settings.account).map_err(|_|Error::Message)?;
    let binding=serde_json::to_vec(&(MODE,archive.archive_id(),&advance.source,settings.window.uidvalidity,
        settings.window.after,settings.window.uid,hex::encode(Sha256::digest(bytes)))).map_err(|_|Error::Config)?;
    Ok(IngestRequest{idempotency_key:hex::encode(Sha256::digest(binding)),messages:vec![message]})
}

fn map_cursor(error:EventError)->Error {
    match error {
        EventError::FixtureUidValidity=>Error::UidValidity,EventError::FixtureResume=>Error::Resume,
        _=>Error::Apply,
    }
}

#[derive(Debug,Serialize)]
pub struct Preview { confirmation:String, uidvalidity:u32, previous_uid:u32, uid:u32 }

#[derive(Debug,Serialize)]
pub struct Receipt { batch_id:String, uidvalidity:u32, uid:u32, created:usize, unchanged:usize }

pub fn preview(directory:&Path,settings:&Settings<'_>)->Result<Preview> {
    let advance=progress(settings)?;
    let archive=EventStore::open(directory).map_err(|_|Error::Archive)?;
    let mut read=archive.read_connection().map_err(|_|Error::Archive)?;
    let tx=read.transaction().map_err(|_|Error::Archive)?;
    archive.verify_connection(&tx).map_err(|_|Error::Archive)?;
    advance.check(&tx,false).map_err(map_cursor)?;
    drop(tx); // Do not retain SQLite across bounded network I/O.
    let request=prepare(settings,&archive,&advance)?;
    request.validate(eml_import::PRODUCER).map_err(|_|Error::Message)?;
    Ok(Preview{confirmation:request.idempotency_key,uidvalidity:settings.window.uidvalidity,
        previous_uid:settings.window.after,uid:settings.window.uid})
}

pub fn commit(directory:&Path,settings:&Settings<'_>,confirmation:&str)->Result<Receipt> {
    let advance=progress(settings)?;
    let archive=EventStore::open(directory).map_err(|_|Error::Archive)?;
    let request=prepare(settings,&archive,&advance)?;
    if request.idempotency_key!=confirmation { return Err(Error::Confirmation); }
    let batch_id=request.idempotency_key.clone();
    let receipt=archive.import_imap_fixture(request,&advance).map_err(map_cursor)?;
    Ok(Receipt{batch_id,uidvalidity:settings.window.uidvalidity,uid:settings.window.uid,
        created:receipt.created,unchanged:receipt.unchanged})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct Pipe { reads:io::Cursor<Vec<u8>>, sends:Vec<u8> }
    impl Read for Pipe { fn read(&mut self,b:&mut[u8])->io::Result<usize> { self.reads.read(b) } }
    impl Write for Pipe {
        fn write(&mut self,b:&[u8])->io::Result<usize> { self.sends.write(b) }
        fn flush(&mut self)->io::Result<()> { Ok(()) }
    }
    const ACCOUNT:&str="fixture@example.invalid";
    const WINDOW:Window=Window{uidvalidity:77,after:0,uid:9};
    fn headers(validity:u32)->String {
        format!("{}\r\n* FLAGS (\\Seen)\r\n* 1 EXISTS\r\n* OK [UIDVALIDITY {validity}] fixture epoch\r\na1 OK [READ-ONLY] EXAMINE complete\r\n",greeting(ACCOUNT))
    }

    #[test]
    fn exact_wire_grammar_and_no_payload_on_wrong_epoch() {
        let mut wire=Pipe{reads:io::Cursor::new((headers(77)+"* 1 FETCH (UID 9 BODY[] {3}\r\nbin)\r\na2 OK FETCH complete\r\n").into_bytes()),..Default::default()};
        assert_eq!(read_only(&mut wire,WINDOW,ACCOUNT).unwrap(),b"bin");
        assert_eq!(wire.sends,b"a1 EXAMINE INBOX\r\na2 UID FETCH 9 (UID BODY.PEEK[])\r\n");
        let mut wrong=Pipe{reads:io::Cursor::new(headers(78).into_bytes()),..Default::default()};
        assert!(matches!(read_only(&mut wrong,WINDOW,ACCOUNT),Err(Error::UidValidity)));
        assert_eq!(wrong.sends,b"a1 EXAMINE INBOX\r\n");
    }

    #[test]
    fn oversized_literal_and_unexpected_uid_do_not_read_body() {
        for announced in ["* 1 FETCH (UID 9 BODY[] {262145}\r\n",
            "* 1 FETCH (UID 8 BODY[] {3}\r\n","* 2 FETCH (UID 9 BODY[] {3}\r\n"] {
            let mut wire=Pipe{reads:io::Cursor::new((headers(77)+announced).into_bytes()),..Default::default()};
            assert!(matches!(read_only(&mut wire,WINDOW,ACCOUNT),Err(Error::Protocol)));
        }
        let mut unsolicited=Pipe{reads:io::Cursor::new(b"* OK real IMAP server\r\n".to_vec()),..Default::default()};
        assert!(matches!(read_only(&mut unsolicited,WINDOW,ACCOUNT),Err(Error::Protocol)));
        assert!(unsolicited.sends.is_empty());
    }

    #[test]
    fn cursor_does_not_change_across_other_fixture_identity() {
        let db=Connection::open_in_memory().unwrap();
        let a=Advance{source:"one".into(),window:WINDOW};
        a.check(&db,false).unwrap();
        assert!(db.prepare("SELECT uid FROM native_imap_fixture_progress").is_err());
        a.check(&db,true).unwrap();
        a.record(&db).unwrap();
        assert!(matches!(a.check(&db,false),Err(EventError::FixtureResume)));
        let peer=Advance{source:"two".into(),window:WINDOW};
        peer.check(&db,false).unwrap();
        let foreign=Advance{source:"one".into(),window:Window{uidvalidity:1,..WINDOW}};
        assert!(matches!(foreign.check(&db,false),Err(EventError::FixtureUidValidity)));
    }
}
