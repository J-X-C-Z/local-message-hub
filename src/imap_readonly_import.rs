//! Operator preview and explicit append-only commit, independent of the synthetic fixture.
use crate::{eml_import, events::{EventError, EventStore, IngestRequest, MAX_BATCH_BYTES},
    imap_readonly::{self, Settings}, plugins::AccountScope};
use rusqlite::{Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::path::Path;
use zeroize::Zeroizing;

const TABLE: &str = "native_imap_readonly_progress_v1";
const MAX_SOURCES: i64 = 256;
const SCHEMA: &str = "
CREATE TABLE native_imap_readonly_progress_v1(
    source_key TEXT NOT NULL PRIMARY KEY CHECK(length(source_key)=64),
    uidvalidity INTEGER NOT NULL CHECK(uidvalidity BETWEEN 1 AND 4294967295),
    uid INTEGER NOT NULL CHECK(uid BETWEEN 1 AND 4294967295)
) WITHOUT ROWID;";

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("invalid IMAP import account fingerprint, UID window or confirmation")]
    Input,
    #[error(transparent)]
    Transport(#[from] imap_readonly::Error),
    #[error("mail is outside the supported directly addressed incoming plain-text EML subset")]
    Message,
    #[error("target is not an available ordinary Rust archive")]
    Archive,
    #[error("local IMAP source epoch or previous UID differs")]
    Cursor,
    #[error("confirmation does not match these exact IMAP bytes, verification or archive scope")]
    Confirmation,
    #[error("IMAP import was not committed; use the original confirmation for an exact retry")]
    Apply,
}
type Result<T> = std::result::Result<T, Error>;

pub struct Import<'a> {
    pub transport: Settings<'a>,
    pub account_sha256: &'a str,
    pub previous_uid: u32,
}

#[derive(Debug, Serialize)]
pub struct Preview {
    confirmation: String,
    uidvalidity: u32,
    previous_uid: u32,
    uid: u32,
    octets: usize,
    archive_written: bool,
}

#[derive(Debug, Serialize)]
pub struct Receipt {
    batch_id: String,
    uidvalidity: u32,
    uid: u32,
    created: usize,
    unchanged: usize,
}

pub(crate) struct Advance {
    source_key: String,
    uidvalidity: u32,
    previous_uid: u32,
    uid: u32,
}

impl Advance {
    fn cursor(&self, db: &Connection, create: bool) -> std::result::Result<Option<(u32,u32)>, EventError> {
        let object: Option<(String, String)> = db.query_row(
            "SELECT name,type FROM sqlite_schema WHERE name=? COLLATE NOCASE",
            [TABLE], |row| Ok((row.get(0)?, row.get(1)?)),
        ).optional()?;
        Ok(match object {
            Some((name, kind)) if name == TABLE && kind == "table" => db.query_row(
                "SELECT uidvalidity,uid FROM native_imap_readonly_progress_v1 WHERE source_key=?",
                [&self.source_key], |row| Ok((row.get::<_, u32>(0)?, row.get::<_, u32>(1)?)),
            ).optional()?,
            None => {
                if create { db.execute_batch(SCHEMA)?; }
                None
            }
            _ => return Err(EventError::Storage),
        })
    }

    pub(crate) fn check(&self, db: &Connection, create: bool) -> std::result::Result<(), EventError> {
        match self.cursor(db, create)? {
            Some((epoch, uid)) if epoch == self.uidvalidity && uid == self.previous_uid => Ok(()),
            None if self.previous_uid == 0 => {
                if create {
                    let count: i64 = db.query_row("SELECT count(*) FROM native_imap_readonly_progress_v1",
                        [], |row| row.get(0))?;
                    if count >= MAX_SOURCES { return Err(EventError::Storage); }
                }
                Ok(())
            }
            _ => Err(EventError::SourceCheckpointConflict),
        }
    }

    pub(crate) fn verify_receipt(&self, db: &Connection) -> std::result::Result<(), EventError> {
        match self.cursor(db, false)? {
            Some((epoch, uid)) if epoch == self.uidvalidity && uid >= self.uid => Ok(()),
            _ => Err(EventError::Storage),
        }
    }

    pub(crate) fn record(&self, db: &Connection) -> std::result::Result<(), EventError> {
        db.execute("INSERT INTO native_imap_readonly_progress_v1(source_key,uidvalidity,uid) VALUES(?,?,?)
            ON CONFLICT(source_key) DO UPDATE SET uid=excluded.uid",
            (&self.source_key, self.uidvalidity, self.uid))?;
        Ok(())
    }
}

fn hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn archive(directory: &Path, inputs: &Import<'_>) -> Result<EventStore> {
    if !hex64(inputs.account_sha256) || inputs.transport.uid <= inputs.previous_uid
        || inputs.transport.uidvalidity == 0
    {
        return Err(Error::Input);
    }
    EventStore::open(directory).map_err(|_| Error::Archive)
}

fn digest<T: Serialize>(value: &T) -> Result<String> {
    let bytes = serde_json::to_vec(value).map_err(|_| Error::Input)?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

struct Prepared {
    request: IngestRequest,
    scope: AccountScope,
    advance: Advance,
    octets: usize,
}

fn prepare(store: &EventStore, input: &Import<'_>) -> Result<Prepared> {
    let transport = &input.transport;
    let mut raw = Zeroizing::new(Vec::with_capacity(eml_import::MAX_FILE));
    let (account, ca) = imap_readonly::fetch_for_import(transport, input.account_sha256, &mut raw)?;
    let octets = raw.len();
    let source_key = digest(&("hub-imap-readonly-source-v1", transport.hostname,
        transport.endpoint.to_string(), hex::encode(ca), transport.leaf_sha256,
        transport.mailbox, input.account_sha256))?;
    let idempotency_key = digest(&("hub-imap-readonly-receipt-v1", store.archive_id(),
        &source_key, transport.uidvalidity, input.previous_uid, transport.uid,
        hex::encode(Sha256::digest(raw.as_slice()))))?;
    let message = eml_import::decode(raw.as_slice(), account.as_str()).map_err(|_| Error::Message)?;
    // An IMAP INBOX record establishes mailbox membership, not that a From
    // header claiming the same address was sent by the user. This lane only
    // accepts incoming messages addressed directly to the explicit mailbox.
    if message.is_self { return Err(Error::Message); }
    let request = IngestRequest { idempotency_key, messages: vec![message] };
    let scope = AccountScope { platform: "email".into(), account_id: account.as_str().to_owned() };
    if !request.matches_account(&scope) { return Err(Error::Message); }
    request.validate(eml_import::PRODUCER).map_err(|_| Error::Message)?;
    Ok(Prepared { request, scope, octets, advance: Advance {
        source_key, uidvalidity: transport.uidvalidity, previous_uid: input.previous_uid, uid: transport.uid,
    } })
}

pub fn preview(directory: &Path, inputs: &Import<'_>) -> Result<Preview> {
    let store = archive(directory, inputs)?;
    let prepared = prepare(&store, inputs)?;
    let mut read = store.read_connection().map_err(|_| Error::Archive)?;
    let tx = read.transaction().map_err(|_| Error::Archive)?;
    store.verify_connection(&tx).map_err(|_| Error::Archive)?;
    prepared.advance.check(&tx, false).map_err(|err| match err {
        EventError::SourceCheckpointConflict => Error::Cursor,
        _ => Error::Archive,
    })?;
    Ok(Preview {
        confirmation: prepared.request.idempotency_key,
        uidvalidity: inputs.transport.uidvalidity, previous_uid: inputs.previous_uid,
        uid: inputs.transport.uid, octets: prepared.octets, archive_written: false,
    })
}

pub fn commit(directory: &Path, inputs: &Import<'_>, confirmation: &str) -> Result<Receipt> {
    if !hex64(confirmation) { return Err(Error::Input); }
    let store = archive(directory, inputs)?;
    let prepared = prepare(&store, inputs)?;
    if prepared.request.idempotency_key != confirmation { return Err(Error::Confirmation); }
    let receipt = store.import_imap_readonly(prepared.request, &prepared.scope, &prepared.advance)
        .map_err(|err| match err {
            EventError::SourceCheckpointConflict => Error::Cursor,
            _ => Error::Apply,
        })?;
    Ok(Receipt { batch_id: confirmation.to_owned(), uidvalidity: inputs.transport.uidvalidity,
        uid: inputs.transport.uid, created: receipt.created, unchanged: receipt.unchanged })
}


/// Exact declared UIDs, not a discovery range. The transport's UID must be
/// the first declared item; this keeps the singleton API unchanged.
pub struct Batch<'a> {
    pub transport:Settings<'a>,
    pub account_sha256:&'a str,
    pub previous_uid:u32,
    pub uids:&'a [u32],
}

#[derive(Serialize)]
struct Observation { uid:u32, octets:usize }

#[derive(Serialize)]
pub struct BatchPreview {
    confirmation:String,
    uidvalidity:u32,
    previous_uid:u32,
    selected:Vec<Observation>,
    next_uid:u32,
    octets:usize,
    archive_written:bool,
    completeness:&'static str,
}

#[derive(Serialize)]
pub struct BatchReceipt {
    batch_id:String,
    uidvalidity:u32,
    previous_uid:u32,
    selected_uids:Vec<u32>,
    next_uid:u32,
    created:usize,
    unchanged:usize,
    completeness:&'static str,
}

const EXPLICIT_HIGH_WATER:&str="explicit_uid_selection_only_not_mailbox_completeness";

fn batch_archive(directory:&Path,input:&Batch<'_>)->Result<EventStore> {
    if !hex64(input.account_sha256)||input.transport.uidvalidity==0
        ||!imap_readonly::valid_uid_selection(input.uids,input.previous_uid)
        ||input.uids.first().copied()!=Some(input.transport.uid) {
        return Err(Error::Input);
    }
    EventStore::open(directory).map_err(|_|Error::Archive)
}

fn prepare_batch(store:&EventStore,input:&Batch<'_>)->Result<(Prepared,Vec<Observation>)> {
    let transport=&input.transport;
    let (account,ca,bodies)=imap_readonly::fetch_explicit_batch(transport,input.account_sha256,input.uids)?;
    if bodies.len()!=input.uids.len(){return Err(Error::Message);}
    // Share the singleton source identity and high-water table. The receipt
    // domain, ordered raw hashes, and selected cardinality are batch-specific.
    let source_key=digest(&("hub-imap-readonly-source-v1",transport.hostname,
        transport.endpoint.to_string(),hex::encode(ca),transport.leaf_sha256,
        transport.mailbox,input.account_sha256))?;
    let mut messages=Vec::with_capacity(input.uids.len());
    let mut hashes=Vec::with_capacity(input.uids.len());
    let mut observed=Vec::with_capacity(input.uids.len());
    let mut octets=0;
    for (&uid,raw) in input.uids.iter().zip(bodies) {
        let message=eml_import::decode(raw.as_slice(),account.as_str()).map_err(|_|Error::Message)?;
        if message.is_self{return Err(Error::Message);}
        messages.push(message);
        observed.push(Observation{uid,octets:raw.len()});
        octets+=raw.len();
        hashes.push((uid,hex::encode(Sha256::digest(raw.as_slice()))));
    }
    let idempotency_key=digest(&("hub-imap-readonly-explicit-batch-v1",store.archive_id(),
        &source_key,transport.uidvalidity,input.previous_uid,&hashes))?;
    let request=IngestRequest{idempotency_key,messages};
    let scope=AccountScope{platform:"email".into(),account_id:account.as_str().to_owned()};
    if !request.matches_account(&scope){return Err(Error::Message);}
    request.validate(eml_import::PRODUCER).map_err(|_|Error::Message)?;
    let encoded=Zeroizing::new(serde_json::to_vec(&request.messages).map_err(|_|Error::Message)?);
    if encoded.len()>MAX_BATCH_BYTES{return Err(Error::Message);}
    let uid=input.uids.last().copied().ok_or(Error::Input)?;
    let advance=Advance{source_key,uidvalidity:transport.uidvalidity,previous_uid:input.previous_uid,uid};
    Ok((Prepared{request,scope,advance,octets},observed))
}

pub fn preview_batch(directory:&Path,input:&Batch<'_>)->Result<BatchPreview> {
    let store=batch_archive(directory,input)?;
    let (prepared,selected)=prepare_batch(&store,input)?;
    let mut conn=store.read_connection().map_err(|_|Error::Archive)?;
    let tx=conn.transaction().map_err(|_|Error::Archive)?;
    store.verify_connection(&tx).map_err(|_|Error::Archive)?;
    prepared.advance.check(&tx,false).map_err(|err|match err {
        EventError::SourceCheckpointConflict=>Error::Cursor,_=>Error::Archive,
    })?;
    Ok(BatchPreview{confirmation:prepared.request.idempotency_key,
        uidvalidity:input.transport.uidvalidity,previous_uid:input.previous_uid,
        selected,next_uid:prepared.advance.uid,octets:prepared.octets,archive_written:false,
        completeness:EXPLICIT_HIGH_WATER})
}

pub fn commit_batch(directory:&Path,input:&Batch<'_>,confirmation:&str)->Result<BatchReceipt> {
    if !hex64(confirmation){return Err(Error::Input);}
    let store=batch_archive(directory,input)?;
    let (prepared,_)=prepare_batch(&store,input)?;
    if prepared.request.idempotency_key!=confirmation{return Err(Error::Confirmation);}
    let receipt=store.import_imap_readonly_batch(prepared.request,&prepared.scope,&prepared.advance)
        .map_err(|err|match err {EventError::SourceCheckpointConflict=>Error::Cursor,_=>Error::Apply})?;
    Ok(BatchReceipt{batch_id:confirmation.to_owned(),uidvalidity:input.transport.uidvalidity,
        previous_uid:input.previous_uid,selected_uids:input.uids.to_vec(),next_uid:prepared.advance.uid,
        created:receipt.created,unchanged:receipt.unchanged,completeness:EXPLICIT_HIGH_WATER})
}
