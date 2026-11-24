# Local Message Hub Rust rebuild-003

This candidate continues rebuild-002; it is not the old 066. Public main and
the default Python backend are unchanged.

- New native `GET /api/v1/messages/export`: UTF-8 JSON and CSV downloads for
  exactly one conversation in an explicitly scoped desktop grant with
  `messages.export` and `messages.read`. Works against both tested source modes.
- No media, attachments or arbitrary provider metadata are copied; no files
  are written. Time bounds and fixed row/cell/output limits prevent partial
  output. CSV text guards against common formula prefixes even after whitespace.
  Message text remains private and can itself contain URLs or secrets.
- A migration regression separately verifies conversation notes/tags/archived
  state and linked contact identities after projection and real HTTP reads,
  with colliding IDs across two accounts and two platforms.

## Test evidence
- `cargo test --release --locked --offline --all-targets`: 39 passed on Linux
  x86_64 with Rust 1.100.0. Includes snapshot/live exports, permission and scope,
  body/row/cell caps, invalid role and duplicate request fields, source byte preservation,
  formula prefixes, and colliding identity migration.
- `cargo test --release --locked --offline --all-targets -- --ignored`:
  3 passed with the actual stock Python modules from
  `c7052745fab3fa1880c6e3a496f40609185668b9` importable via `PYTHONPATH`.
  Verified Git blobs: `hub/store.py`
  `1065ba686034b9cc052652328d6a668a6b42bf86`; `hub/exports.py`
  `c684c4e62f6a0d9a83d67a80e96669f235b5f698`. Two live export responses match
  their exact corresponding stock Python renderer bytes on selected UTF-8
  safe-projected fixtures. This is not legacy HTTP contract parity.
- Validated Draft 2020-12 grant schema and checked the new OpenAPI method.
  No browser, Windows, macOS, real account or real provider tested.

## Progress
3,903 physical Rust production lines / 26,429 original Python lines = 14.8%
source volume (rebuild-002: 13.8%), not functional completion. Fifteen native
HTTP operations including health. 0/124 old HTTP routes have full drop-in
acceptance; new export matches only limited safe projected renderer samples.
