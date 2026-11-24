# Local Message Hub Rust rebuild-004

This verified candidate follows rebuild-003. No GitHub push or change of the
default Python backend was made.

- Native-live-only API for user conversation notes, tags and archive flag,
  plus an explicit contact identity assignment and clear. An exact-account
  desktop grant requires separate capabilities for each edit or audit read.
  Writes use caller-chosen target and expected revision; no guessed links.
- State and per-account scrubbed audit metadata commit together. An identical
  state request is unchanged even if its revision is stale; a different stale
  request fails. Clearing an absent mapping is unchanged. Audit records contain
  field names only, not old/new values, credentials, automatic actions or sends.
- Older native profiles can add the audit schema with a stopped-service CLI
  upgrade. Captured metadata and global/scoped event cursors remain intact.
  Snapshot mode and legacy profiles are not writable.

## Test evidence
- Linux x86_64, Rust 1.100.0, `cargo test --release --locked --offline --all-targets`:
  45 passed. Real local process tests through the scoped API cover same-ID
  collisions, capacity/validation, revision race and retry, intact cursor
  namespaces, re-opening after restart, redacted audit/error, denial in
  snapshot mode, and rollback when the audit transaction fails.
- `cargo test --release --locked --offline --all-targets -- --ignored`
  (4 passed): stock Python differential using synthetic data, including
  a comparison of five metadata/link/clear operations to the equivalent original
  store results. `PYTHONPATH` must contain the pinned modules from
  `c7052745fab3fa1880c6e3a496f40609185668b9`: `hub/store.py`
  `1065ba686034b9cc052652328d6a668a6b42bf86`; `hub/exports.py`
  `c684c4e62f6a0d9a83d67a80e96669f235b5f698`. The revision and audit policy
  differ deliberately from the original; this is not old HTTP parity.
- Draft 2020-12 grant/schema checks passed. No browser, Windows, macOS,
  external providers or real accounts were tested.

## Progress
4,412 production Rust lines / 26,429 original Python lines = 16.7% source
volume (rebuild-003: 14.8%), not feature completion. 20 native HTTP operations
including health. 0/124 old HTTP operations have full drop-in acceptance.
