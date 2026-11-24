//! Private claimed admission of one page from a separately authorized,
//! frozen copy of the original rendered append log. No acquisition code.

use super::{
    ClaimCommand, Envelope, Error, EventStore, FORMAT_WITH_NOTICES, MAX_BATCH_BYTES, MAX_ROWS,
    Plan, Result, Row, SourceId, fingerprint, hex, id, jsonl_lineage, load, map_apply,
    plan_input, read_stable_source, scope,
};
use crate::source_jobs::LocalDetachedClaim;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, path::Path};

const MAX_ARTIFACT: usize = 8 * 1024 * 1024;
const MAX_LINES: usize = 10_000;

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum Attestation {
    AuthorizedFrozenWecomRenderedCopy,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Selection {
    account_binding: Attestation,
    account_id: String,
    source_sha256: String,
    start_line: usize,
    row_count: usize,
    position: Option<Position>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Position {
    generation: u32,
    sequence: u32,
    previous_checkpoint: String,
}

impl Position {
    fn valid(&self) -> bool {
        self.generation == 1
            && match self.sequence {
                0 => false,
                1 => self.previous_checkpoint == "initial",
                _ => hex(&self.previous_checkpoint, 64),
            }
    }
}

fn preview_position(
    store: &EventStore,
    account: &str,
    lineage: &str,
    claim: &LocalDetachedClaim,
) -> Result<Position> {
    let mut db = store.read_connection().map_err(|_| Error::Archive)?;
    let tx = db.transaction().map_err(|_| Error::Archive)?;
    store.verify_connection(&tx).map_err(|_| Error::Archive)?;
    claim.verify_in(&tx).map_err(map_apply)?;
    let head = load(&tx, account).map_err(map_apply)?;
    let marker = jsonl_lineage(&tx, account).map_err(map_apply)?;
    match (head, marker.as_deref()) {
        (None, None) => Ok(Position {
            generation: 1,
            sequence: 1,
            previous_checkpoint: "initial".into(),
        }),
        (Some((source, head)), Some(mark))
            if source.as_str() == lineage && mark == lineage && head.generation == 1 && !head.complete =>
        {
            Ok(Position {
                generation: 1,
                sequence: head.sequence.checked_add(1).ok_or(Error::Cursor)?,
                previous_checkpoint: head.checkpoint,
            })
        }
        _ => Err(Error::Cursor),
    }
}

fn page_rows(bytes: &[u8], account: &str, start: usize, count: usize) -> Result<Vec<Row>> {
    let raw = bytes.strip_suffix(b"\n").ok_or(Error::Format)?;
    let end = start.checked_add(count).ok_or(Error::Format)?;
    let mut selected = Vec::with_capacity(count);
    let mut seen = BTreeSet::new();
    let mut selection_bytes = 0usize;
    for (index, line) in raw.split(|&b| b == b'\n').enumerate() {
        if index >= MAX_LINES || line.is_empty() || line.len() > MAX_BATCH_BYTES {
            return Err(Error::Format);
        }
        let row: Row = serde_json::from_slice(line).map_err(|_| Error::Format)?;
        // Rows without account/platform are allowed only because the entire
        // exact artifact was independently bound to the approved account.
        if row.platform.as_deref().is_some_and(|p| p != "wecom")
            || row.account_id.as_deref().is_some_and(|a| a != account)
        {
            return Err(Error::Scope);
        }
        if !matches!(row.kind.as_str(), "text" | "sys") {
            return Err(Error::Format);
        }
        let message_id = match &row.message_id {
            SourceId::Exact(value) if id(value) => value.clone(),
            SourceId::Number(value) => value.to_string(),
            _ => return Err(Error::Format),
        };
        // The original log itself deduplicates stringified message IDs.
        // Refuse ambiguity even if an explicit ID lies outside the page.
        if !seen.insert(message_id) {
            return Err(Error::Format);
        }
        if (start..end).contains(&index) {
            selection_bytes += line.len() + 1;
            if selection_bytes > MAX_BATCH_BYTES {
                return Err(Error::Format);
            }
            selected.push(row);
        }
    }
    if selected.len() != count {
        return Err(Error::Format);
    }
    Ok(selected)
}

pub(super) fn plan(
    source: &Path,
    store: &EventStore,
    account: &str,
    lineage: &str,
    claim: &LocalDetachedClaim,
    command: ClaimCommand,
    mut selection: Selection,
) -> Result<(Plan, Selection)> {
    scope(account, lineage)?;
    if selection.account_id != account {
        return Err(Error::Scope);
    }
    if !hex(&selection.source_sha256, 64)
        || !(1..=MAX_ROWS).contains(&selection.row_count)
        || selection.start_line > MAX_LINES - selection.row_count
    {
        return Err(Error::Format);
    }
    selection.position = Some(match (command, selection.position.take()) {
        (ClaimCommand::Preview, None) => preview_position(store, account, lineage, claim)?,
        (ClaimCommand::Import | ClaimCommand::Status, Some(position)) if position.valid() => position,
        _ => return Err(Error::Cursor),
    });

    let bytes = zeroize::Zeroizing::new(read_stable_source(source, MAX_ARTIFACT).map_err(|_| Error::Source)?);
    if hex::encode(Sha256::digest(bytes.as_slice())) != selection.source_sha256 {
        return Err(Error::Confirmation);
    }
    let messages = page_rows(bytes.as_slice(), account, selection.start_line, selection.row_count)?;
    let position = selection.position.as_ref().ok_or(Error::Cursor)?;
    // Bind the attestation, exact account/artifact bytes, chosen line span
    // and original reviewed local position, without carrying any raw payload.
    let proof = fingerprint(&("wecom-claimed-rendered-jsonl-page-v1", lineage, &selection))?;
    let input = Envelope {
        format: FORMAT_WITH_NOTICES.to_owned(),
        account_id: account.to_owned(),
        source_id: lineage.to_owned(),
        generation: position.generation,
        sequence: position.sequence,
        previous_checkpoint: position.previous_checkpoint.clone(),
        complete: false,
        conversations: Vec::new(),
        messages,
    };
    let plan = plan_input(input, proof, true, store, account, lineage)?;
    // A selected page must contain concrete text. Never advance the local
    // position based solely on placeholders or ignored attachments.
    if plan.request.messages.is_empty() {
        return Err(Error::Format);
    }
    Ok((plan, selection))
}
