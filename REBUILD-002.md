# Local Message Hub Rust rebuild-002

This verified-source milestone continues rebuild-001. It is not the previously
unavailable 066. The public repository and default Python backend are unchanged.

## Change
- Added the operator CLI `project-snapshot` to project a raw detached legacy
  v9 SQLite backup into a fresh private directory for Rust's existing
  `serve --snapshot` path.
- Fixed five-table schema, primary keys, original logical content and rowid
  ordering. The receipt reports row counts and input/output SHA-256. The original
  is opened read-only and its bytes are checked before and after projection.
- No in-place overwrite, no provider or plugin config, no operational/outbound
  tables, media ZIP, attachment bytes, native account events or secrets copied.
  Chat text and attachment metadata can still contain private data. FTS is not
  preserved; the projected snapshot uses the fallback.
- On tested Linux, private directory/file mode is enforced and failed or
  concurrent copies cannot publish a half-built final file. Windows and macOS
  were not tested. This is not the old HTTP backup/restore contract.

## Verification
- Linux x86_64, Rust 1.100.0: `cargo test --release --locked --offline --all-targets`.
  33 standard tests, including reopening the output through the actual native
  HTTP server, refused current WAL sidecars, wrong format/schema, public or
  redirected target, late transaction failure, no-replace and concurrent races.
- `cargo test --release --locked --offline --all-targets -- --ignored` with
  the actual stock `hub/store.py` importable via `PYTHONPATH`. Source from
  `c7052745fab3fa1880c6e3a496f40609185668b9`, verified Git blob
  `1065ba686034b9cc052652328d6a668a6b42bf86`. Two tests: the earlier store
  differential and newly projecting an actual Python-produced online backup
  with a committed row visible in WAL but not the original SQLite base file
  alone. Both input preservation and actual projected message data checked.
- All data was disposable synthetic data; no account, provider, real user
  data, mixed-media ZIP, Windows, or live browser tested.

## Progress
3,643 production Rust physical lines (excluding tests) vs the historical
26,429-line old Python baseline: 13.8% code size (rebuild-001: 12.8%).
The projected SQL schema is not included in the Rust-line numerator.
This changes no native HTTP operations and establishes no new overall
legacy HTTP drop-in compatibility (0/124 fully proven).
