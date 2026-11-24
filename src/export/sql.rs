//! Generator for the fixed PostgreSQL text-only interchange subset.
//! The Hub never parses, imports, or executes these bytes.
use super::{Capped, Record, Result, StoreError, write_error};
use std::io::Write;

pub(super) const HEADER: &[u8] = b"\
-- Local Message Hub text-only PostgreSQL export; source messages are not modified.
BEGIN;
CREATE TABLE IF NOT EXISTS local_message_hub_messages (
  platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
  conversation_title TEXT, message_id TEXT, timestamp BIGINT, sender_id TEXT,
  sender_name TEXT, type TEXT, text TEXT, is_self BOOLEAN, media_url TEXT,
  file_name TEXT, raw_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_lmh_messages_scope_time ON local_message_hub_messages (platform, account_id, conversation_id, timestamp);
CREATE TABLE IF NOT EXISTS local_message_hub_resources (platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL, resource_index INTEGER NOT NULL, resource_json TEXT NOT NULL);
";
pub(super) const END: &[u8] = b"COMMIT;\n";

fn literal(out: &mut Capped, value: Option<&str>) -> Result<()> {
    let Some(value) = value else {
        return out.write_all(b"NULL").map_err(|_| write_error());
    };
    let data = value.as_bytes();
    // PostgreSQL text cannot contain NUL. Reject the whole artifact, rather
    // than silently changing a message or identity as the original does.
    if data.contains(&0) {
        return Err(StoreError::BadQuery("NUL is not representable in PostgreSQL text"));
    }
    // PostgreSQL escape string, not sensitive to standard_conforming_strings.
    // Quote doubling and backslash escaping consume only literal contents.
    // Encode control characters and Unicode line separators so data cannot
    // make the generated INSERT look like multiple physical lines.
    out.write_all(b"E'").map_err(|_| write_error())?;
    let mut start = 0;
    for (at, ch) in value.char_indices() {
        if matches!(ch, '\'' | '\\' | '\u{2028}' | '\u{2029}') || ch.is_control() {
            out.write_all(&data[start..at]).map_err(|_| write_error())?;
            match ch {
                '\'' => out.write_all(b"''"),
                '\\' => out.write_all(b"\\\\"),
                ch => write!(out, "\\u{:04x}", ch as u32),
            }.map_err(|_| write_error())?;
            start = at + ch.len_utf8();
        }
    }
    out.write_all(&data[start..]).map_err(|_| write_error())?;
    out.write_all(b"'").map_err(|_| write_error())
}

pub(super) fn row(out: &mut Capped, row: &Record) -> Result<()> {
    out.write_all(b"INSERT INTO local_message_hub_messages (platform, account_id, conversation_id, conversation_title, message_id, timestamp, sender_id, sender_name, type, text, is_self, media_url, file_name, raw_type) VALUES (")
        .map_err(|_| write_error())?;
    for field in [
        Some(row.platform.as_str()), Some(row.account_id.as_str()),
        Some(row.conversation_id.as_str()), row.conversation_title.as_deref(), Some(row.id.as_str()),
    ] {
        literal(out, field)?;
        out.write_all(b", ").map_err(|_| write_error())?;
    }
    write!(out, "{}, ", row.timestamp).map_err(|_| write_error())?;
    for field in [row.sender_id.as_deref(), row.sender_name.as_deref(), row.kind.as_deref(), row.text.as_deref()] {
        literal(out, field)?;
        out.write_all(b", ").map_err(|_| write_error())?;
    }
    out.write_all(if row.is_self {
        b"TRUE, NULL, NULL, NULL);\n"
    } else {
        b"FALSE, NULL, NULL, NULL);\n"
    }).map_err(|_| write_error())
}
