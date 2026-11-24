# Local Message Hub Rust rebuild-001

This is a new local rebuild from surviving **003**, not the previously tested
066. Original start/run behavior and public main remain unchanged.

## Included
- One exact-account grant for an optional desktop identity; additional scopes,
  producers and MCP tool changes are deliberately outside this milestone.
- List/search, contacts plus account choices, message context and quote queries
  use the account before fetching data. Exact foreign context gets ordinary 404.
  Scoped list pagination uses the account-local normalized message ID for
  timestamp ties. This is row-level selection, not arbitrary legacy metadata
  sanitization or isolation from all timing effects.
- Account-local, scope-bound event replay and SSE positions. Legacy unscoped
  requests retain their previous wire behavior. Same-archive restart resumes;
  old native 003 archives can apply the explicit stopped-service upgrade.
- Source tree, including pinned Cargo.lock, reproducible inputs and tests.
  The enclosing distribution also contains a self-contained Git bundle and
  evidence. No Windows or macOS executable is supplied.

## Verification
- Rust 1.100.0 x86_64 Linux: `cargo test --release --locked --offline --all-targets`
  (26 passes, 0 failures; one optional differential ignored by this command).
- Pinned Python compatibility: `cargo test --release --locked --offline --test
  python_store_equivalence -- --ignored` (1 passed). Make the stock Python
  `hub/store.py` from commit `c7052745fab3fa1880c6e3a496f40609185668b9`,
  blob `1065ba686034b9cc052652328d6a668a6b42bf86`, importable via
  `PYTHONPATH`. The comparator checks actual Python-generated SQLite and exact
  unscoped read results (conversations, messages, search, contacts, context,
  quoted context and cursor continuation); it does not test HTTP drop-in parity.
- New loopback end-to-end cases cover colliding IDs across accounts, grant/cursor
  rejection, stable scoped checkpoints despite foreign activity, restart resume,
  and old-native backfill preserving the original cursor generation. A separate
  detached snapshot quote case proves no byte changes to the input.
- Draft 2020-12 grant schema and cursor patterns checked locally using jsonschema
  4.26.0. No real user data, provider/credential, browser, Windows or
  full legacy drop-in check was performed.

## Progress metrics
Rust production: 3,387 physical lines before dedicated tests/first module test
guard, vs 3,168 in 003. Historical legacy baseline: 26,429 physical lines in
Python `hub/` plus `integrations/`, excluding tests and vendored upstream
code. The resulting 12.8% is source size, not feature parity. This milestone
does not establish any of the previously tracked 124 legacy HTTP operations as
drop-in compatible.
