//! Versioned, write-through resume state for a normalized source grant.
use crate::events::EventError;
use rusqlite::{Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MAX_SEQUENCE: i64 = 9_007_199_254_740_991;
pub(crate) const SCHEMA: &str = "
    CREATE TABLE native_source_progress(
      producer TEXT PRIMARY KEY NOT NULL,
      sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 9007199254740991)
    ) WITHOUT ROWID;
    CREATE TABLE native_source_progress_receipts(
      producer TEXT NOT NULL, idempotency_key TEXT NOT NULL,
      sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 9007199254740991),
      PRIMARY KEY(producer, idempotency_key),
      FOREIGN KEY(producer, idempotency_key) REFERENCES native_ingest_receipts(producer, idempotency_key)
    ) WITHOUT ROWID;
";

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Advance {
    pub expected_revision: String,
    pub sequence: i64,
}

impl Advance {
    pub fn validate(&self) -> Result<(), EventError> {
        let valid_marker = self.expected_revision.strip_prefix("s1-").and_then(|s|s.split_once(':'))
            .is_some_and(|(scope, n)| {
                scope.len() == 64 && scope.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    && (1..=16).contains(&n.len()) && n.bytes().all(|b|b.is_ascii_digit())
            });
        if !valid_marker || !(1..=MAX_SEQUENCE).contains(&self.sequence) {
            return Err(EventError::BadRequest("invalid source checkpoint"));
        }
        Ok(())
    }
}

#[derive(Debug, PartialEq, Eq, Serialize)]
pub(crate) struct Checkpoint {
    pub sequence: i64,
    pub revision: String,
}

pub(crate) fn checkpoint(archive: &str, owner: &str, sequence: i64) -> Checkpoint {
    let data = [b"source-progress-v1:".as_slice(), archive.as_bytes(), b":", owner.as_bytes()].concat();
    Checkpoint { sequence, revision: format!("s1-{}:{sequence}",hex::encode(Sha256::digest(data))) }
}

pub(crate) fn installed(sql: &Connection) -> Result<bool, EventError> {
    let (objects, tables):(i64,i64) = sql.query_row(
        "SELECT count(*),count(CASE WHEN type='table' AND name IN
         ('native_source_progress','native_source_progress_receipts') THEN 1 END)
         FROM sqlite_schema WHERE name='native_source_progress' COLLATE NOCASE
           OR name='native_source_progress_receipts' COLLATE NOCASE", [], |r|Ok((r.get(0)?,r.get(1)?)))?;
    match (objects,tables) {
        (0,0) => Ok(false),
        (2,2) => Ok(true),
        _ => Err(EventError::Storage)
    }
}

pub(crate) fn sequence(sql: &Connection, owner: &str) -> Result<i64, EventError> {
    let found: Option<i64> = sql.query_row(
        "SELECT sequence FROM native_source_progress WHERE producer=?", [owner], |r|r.get(0)).optional()?;
    match found {
        None => Ok(0),
        Some(seq) if (1..=MAX_SEQUENCE).contains(&seq) => Ok(seq),
        _ => Err(EventError::Storage)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn revision_shape_and_integer_boundary_are_bounded() {
        let marker = checkpoint("archive", "@source-v1:owner", 0);
        assert_ne!(marker, checkpoint("archive2", "@source-v1:owner", 0));
        assert_ne!(marker, checkpoint("archive", "@source-v1:owner2", 0));
        for sequence in [1, MAX_SEQUENCE] {
            assert!(Advance { expected_revision: marker.revision.clone(), sequence }.validate().is_ok());
        }
        for sequence in [0, -1, MAX_SEQUENCE + 1] {
            assert!(Advance { expected_revision: marker.revision.clone(), sequence }.validate().is_err());
        }
        for invalid in [format!("{}:1", marker.revision), "legacy".into(), "a".repeat(500)] {
            assert!(Advance { expected_revision: invalid, sequence:1 }.validate().is_err());
        }
    }

    #[test]
    fn partial_progress_schema_is_not_treated_as_initial_state() {
        let sql = Connection::open_in_memory().unwrap();
        assert!(!installed(&sql).unwrap());
        sql.execute_batch(SCHEMA).unwrap();
        assert!(installed(&sql).unwrap());
        sql.execute_batch("DROP TABLE native_source_progress_receipts").unwrap();
        assert!(matches!(installed(&sql), Err(EventError::Storage)));
    }
}
