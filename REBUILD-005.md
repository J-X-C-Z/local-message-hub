# Local Message Hub Rust rebuild-005

Local successor of rebuild-004. No GitHub push, provider login, or change
to the Python default backend.

- A new operator-run **read-only preview** of a bounded legacy v1 media ZIP.
  Verifies the archive/index hashes, ZIP directory and local headers,
  format/manifest/counts/member sizes/CRC/SHA-256, plus actual indexed QQ
  and WeChat cache references. Receipt reports only digests and counts.
- Only the SQLite index is staged at a fixed filename in private temporary
  directories. None of the archive-controlled media paths or payloads are
  materialized. Original content is not changed. No provider runtime is used.
- This does not import, restore, migrate media, or replace a legacy HTTP
  operation. It is not a general-purpose ZIP reader. See the explicit subset
  and size limits in README. Missing-media is a reported manifest hint.
- Production parses only the one final bounded ZIP32 directory and simple
  matching local records. Stored data and raw Deflate with mandatory
  StreamEnd/consumed-range checks are the only decoders. General ZIP library
  is a test-only fixture writer, absent from normal runtime dependencies.


## Test evidence
- Linux x86_64, Rust 1.100.0. `cargo test --release --locked --offline --all-targets`:
  52 standard tests. Includes real ZIP bytes, QQ/WeChat references, duplicate or
  extra declarations, wrong contents/size/digests, malformed local headers/invalid final directory, exact Deflate range/budgets,
  corrupt stored member, oversized input, unchanged source and no leaked
  scratch files or cache paths in the receipt.
- `cargo test --release --locked --offline --all-targets -- --ignored`:
  5 optional checks. New case generates an actual mixed archive with the
  unmodified pinned stock Python `hub.backups.create_media_backup`. It uses
  the pinned WeChat provider's pure helper isolated from provider imports,
  does not execute the live provider, and verifies QQ plus WeChat, excluding
  an orphan cache item. Stock modules importable through `PYTHONPATH`:
  `hub/store.py` Git blob `1065ba686034b9cc052652328d6a668a6b42bf86`;
  `hub/exports.py` `c684c4e62f6a0d9a83d67a80e96669f235b5f698`;
  `hub/backups.py` `102170e0c74007045e7e4aa72df3c2303d22ffa0`;
  `integrations/wechat/provider.py` `9d99952aa9dad3b26a4800b17b00bbbbce214897`.
  All blobs from `c7052745fab3fa1880c6e3a496f40609185668b9`.
- No Windows, macOS, real browser, real backup, or real provider accounts tested.

## Progress
4,818 production Rust physical lines vs 26,429 historical Python lines:
18.2% source size, up from 16.7%; not feature completion. 20 native HTTP
operations including health, unchanged. 0/124 old HTTP operations fully proven.
