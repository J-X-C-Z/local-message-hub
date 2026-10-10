# Development Log

## 2026-10-10 - Durable import jobs

### Scope

- Added the v3 `import_jobs` table with source-file SHA-256 fingerprints,
  normalized validation summaries, durable states, progress counters and
  committed-row checkpoints.
- Confirmed JSON/CSV imports now stage normalized records under the local Hub
  storage directory, then ingest in 500-message transactions. Cancellation is
  observed at batch boundaries; a crash replays only the last uncheckpointed
  batch, which is safe because message ingestion is idempotent.
- The Hub resumes queued/running jobs at startup, retains failed/cancelled
  staging data for diagnosis, and blocks backup restore while imports are active.
- Added local status/list/cancel APIs, a task history/progress UI, and a read-only
  MCP `list_imports` tool. No import content is logged or sent externally.
- Expanded the existing statistics view into the overview dashboard with live
  source counts/status, local SQLite+WAL size and the five latest import jobs.

### Verification

- Full focused backend/source test suite: **95 tests passed**, including restart
  recovery, cancellation before the first batch, HTTP job state, MCP status and
  existing WeChat/WeCom/QQ reader tests.
- `node --check webui/app.js`, Python `py_compile`, and `git diff --check`: passed.

### Remaining roadmap work

- Stable cursor pagination, dashboard, Hermes client example, and concurrent
  MCP/WAL access are now verified. MCP settings remain open, along with local
  metadata/audit, source checkpoints, Telegram import and reproducible Windows
  packaging.

## 2026-10-10 - Stable cursor pagination and MCP client coverage

### Scope

- Added opaque URL-safe continuation cursors to unified search and per-
  conversation message listing while preserving offset pagination for existing
  clients. Cursors use timestamp plus SQLite row id and are bound to the query
  scope; using one with different filters is rejected.
- Wired cursor state through HTTP search/message endpoints, WebUI next/previous
  search pages and load-earlier messages, and MCP `search_messages` /
  `list_messages`. Exports intentionally ignore page cursors and remain complete.
- Added regression tests for same-timestamp ordering, inserts between pages,
  cursor scope validation, HTTP continuation, MCP continuation and concurrent
  MCP reads during SQLite WAL writes.
- Documented the standalone MCP server for Hermes, including an explicit
  read-only tool allowlist and opaque cursor behavior.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_store hub.test_server hub.test_mcp -q`
  - Result: **74 tests passed**, including concurrent MCP reads while a second
    process committed 80 synthetic messages, malformed JSON handling and a
    check that message text is absent from MCP stderr.
- Full import/search/MCP/WeChat/QQ focused backend suite:
  - Result: **100 tests passed**.
- `node --check webui/app.js`, Python compilation, and `git diff --check`:
  passed.

### Remaining roadmap work

- MCP settings UI, local metadata and write authorization/audit, Telegram
  import, adapter-level incremental checkpoints, delete-local-data workflow,
  clean Windows packaging and recorded performance/recovery measurements.

## 2026-10-10 - MCP settings and privacy-preserving operation history

### Scope

- Added schema v4 with a bounded MCP operation table. Calls store only tool
  name, outcome and timestamp; arguments and chat content are never persisted.
- Added `/api/mcp` to report bundled server availability, stdio/read-only mode,
  a machine-local Hermes config, and the latest operation records.
- Added a Settings section with the MCP lifecycle/permission state, one-click
  config copy, and recent call history.

### Verification

- Store, HTTP and MCP tests cover operation-log bounds/content omission, local
  Hermes settings response, schema migration, and successful/failed call records.
- Full import/search/MCP/WeChat/QQ focused backend suite: **102 tests passed**.
- `node --check webui/app.js`, Python compilation and `git diff --check`:
  passed.

## 2026-10-10 - Platform capability matrix

### Scope

- Documented per-platform history boundaries, cached-media availability,
  adapter checkpoint status and export limits in one comparison matrix.
- Explicitly distinguished idempotent indexing from true incremental source
  acquisition; only QQ's bounded roaming job currently persists scan progress.

### Verification

- Reviewed the matrix against the documented local readers and the current
  adapter/checkpoint roadmap. No source data was read or changed.

## 2026-10-10 - Local metadata schema

### Scope

- Advanced the owned SQLite schema to v5 with separate conversation metadata
  (notes, tags and archive state), contact identity links and a general
  operation log. Imported message columns remain unchanged.
- Kept the bounded MCP read-operation history distinct from the future
  attributable management audit trail.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_store -q`
  - Result: **41 tests passed**, including assertions that metadata and audit
    tables are separate from message rows.
- Full import/search/MCP/WeChat/QQ focused backend suite after schema v5:
  **103 tests passed**.
- `node --check webui/app.js`, Python compilation and `git diff --check`:
  passed.

## 2026-10-10 - Roadmap, recovery and JSON/CSV import

### Scope

- Inspected the existing Electron/WebUI + Python hub and local WeChat,
  WeCom and QQ readers; did not inspect `.local`, `.runtime`, private databases,
  keys or live chat records.
- Kept the current architecture. The Rust/Tauri default in the taskbook does
  not fit this repository's already integrated Electron and Python source
  readers; a parallel rewrite would not improve the user's current app.
- Added `docs/roadmap.md` with acceptance-based IDs and truthful statuses.
- Added local SQLite online backup/download and upload/restore controls in
  Settings. Restore validates integrity, version and required schema columns,
  waits for active sync/history jobs, and snapshots the current index first so
  a failed restore rolls back. Backups contain only the Hub index, not source
  application databases, credentials or media cache.

- Added JSON/CSV upload preview and normalization for the Hub's own export
  schema and common English/Chinese field aliases. Invalid records are
  isolated and reported, writes are idempotent by the existing composite
  message key, and the UI requires a fresh preview after changing file/platform/
  account. Uploads are currently synchronous and capped at 256 MiB; persistent
  jobs, cancellation, restart recovery and streaming JSON remain open work.
- Added an optional SQLite FTS5 trigram index with transactional triggers and
  rebuild-on-upgrade, retaining escaped literal LIKE as the final matcher and
  the short-term fallback. Search results now open an 8-message neighborhood
  on either side of the selected hit; the hit is highlighted in the chat view.
- Added an independent read-only MCP stdio process with bounded tools for
  search, conversations, messages, contexts, contacts and indexed source counts.
  It uses the shared SQLite file, emits protocol-only stdout, and has a Codex
  setup guide. Import-job status and stable cursor pagination are not exposed
  yet; no write tools exist.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_imports hub.test_mcp hub.test_store hub.test_server integrations.wechat.test_media integrations.qq.test_provider -q`
  - Result: **92 tests passed**.
- `node --check webui/app.js`
  - Result: passed.
- `integrations/wechat/runtime/venv/Scripts/python.exe -m py_compile hub/imports.py hub/mcp_server.py hub/store.py hub/server.py`
  - Result: passed.
- `git diff --check`
  - Result: passed.
- `npm run typecheck`
  - Result: not run successfully; this checkout has no `node_modules`, so `tsc`
    is unavailable. The root package currently describes the upstream WeFlow
    Electron frontend, not the standalone `webui/` app.
- Rust toolchain check:
  - Result: `cargo` and `rustc` are not installed. No Rust workspace/build is
    claimed.

### Known gaps

- Source adapter incremental checkpoints, MCP write authorization, local-data
  deletion and reproducible Windows packaging are not implemented.
- Telegram Desktop JSON currently has a tested parser, but Telegram is not
  registered as a hub platform and cannot yet be imported end to end.
- Performance/recovery measurements and a private-data-free CI workflow remain
  open.
- QQ roaming history is bounded to the available private-chat endpoint and
  cannot prove server-side completeness. WeChat/WeCom show local snapshots and
  locally cached media, not unavailable server archives.

### Next

1. Add a deliberate MCP write opt-in with validated tools and durable audit.
2. Add true per-source incremental checkpoints and recovery evidence.
3. Finish deletion, Telegram end-to-end registration, Windows packaging and CI.

## 2026-10-10 - Local conversation/contact management

### Scope

- Connected v5 local metadata to conversation and contact queries. The WebUI
  now edits conversation notes, tags and archive state and links/unlinks
  contacts through a shared identity ID.
- Added local same-origin management endpoints and a bounded operation history.
  Audit entries record action/entity/time only; note text and chat content are
  excluded. These operations change only Hub-owned metadata tables.
- Added a read-only Telegram Desktop JSON parser for chat/message/date/sender
  and media metadata. Unsafe attachment paths are omitted; exported content is
  never opened or executed. It remains unregistered in the Hub platform model.

### Verification

- Store and HTTP management tests: **79 passed**, including source-message
  immutability, metadata roundtrip, identity link/unlink, audit-content privacy
  and cross-origin rejection.
- Telegram adapter tests: **4 passed**.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **112 tests
  passed**.
- Python `py_compile`, `node --check webui/app.js`, and `git diff --check`:
  passed.

## 2026-10-10 - Reproducible checks and local data deletion

### Scope

- Added a Windows GitHub Actions workflow and one PowerShell entry point for
  synthetic backend tests, WebUI JavaScript syntax, Python compilation and
  whitespace checks. CI installs only public test packages and does not need
  local runtime state or account data.
- Added a two-step, same-origin local index deletion flow. It pauses automatic
  sync, requests active import/QQ roaming cancellation and removes only the
  Hub SQLite index, owned import/backup files and generated QQ roaming folders.
  Platform source data, account settings and unrelated storage files are
  retained and reported.

### Verification

- Clean-checkout PowerShell script: **94 tests passed**; static checks passed.
- Local deletion API/storage tests: **2 passed**, including confirmation,
  index recreation, backup/import/roaming cleanup, preservation of unrelated
  files/configuration, and retained-data reporting.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite after the deletion
  workflow: **117 tests passed**.

## 2026-10-10 - Opt-in MCP metadata writes

### Scope

- Kept MCP read-only by default and added a local Settings opt-in that is read
  by each newly started stdio process. The generated client allowlist changes
  with the switch.
- When enabled, three bounded tools can edit Hub-owned conversation metadata
  and contact identity links. They cannot alter imported message rows or
  platform databases. Every mutation writes an action/entity/time audit record;
  the separate MCP log stores tool name and outcome only.
- Settings labels the effective permission and notes that an MCP client must
  restart after changing its tool surface.

### Verification

- Added tests for hidden-by-default tools, denial without side effects,
  persisted local opt-in, tool annotations, allowed metadata mutations and
  unchanged source message content.
- Focused MCP/server/store tests: **85 tests passed**.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **115 tests
  passed**.
- Python `py_compile`, `node --check webui/app.js`, and `git diff --check`:
  passed.

## 2026-10-10 - Telegram end-to-end import

### Scope

- Registered Telegram as an index/query platform while keeping native sync and
  the connected-source list limited to WeChat, WeCom and QQ.
- Added a dedicated Telegram Desktop JSON import path to the shared import API
  and WebUI. Preview exposes unsafe attachment-path warnings; safe relative
  paths remain metadata and are never opened from the export directory.
- Added HTTP preview/job tests and proved imported Telegram messages appear in
  both Telegram-scoped and all-platform search. The import's account override
  takes precedence over exported personal metadata.

### Verification

- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **121 tests
  passed**.
- This completes P6-02. P6-03 remains open because native readers still lack
  persistent source cursors. P6-06 remains open because the current Electron
  package is still the upstream WeFlow app and excludes the Hub and local-only
  QQ runtime; see the roadmap gate before making any release claim.

## 2026-10-10 - Synthetic performance and recovery baseline

### Scope

- Added `scripts/benchmark-local-hub.py`, a reproducible benchmark over a
  temporary database with 25,000 synthetic bilingual messages. It measures
  initial FTS search, all stable-cursor pages, a second SQLite connection
  writing while the reader searches, and online backup/restore.
- Existing tests cover interrupted import resumption, backup rejection and
  rollback. The benchmark verifies recovered row counts; no source chat data is
  read or written.

### Verification

- Windows 11, Python 3.12.10, SQLite 3.49.1 with FTS5 trigram: 25,000-row
  ingest **1.347 s**; FTS search **0.074 s**; 50 cursor pages of 500 rows
  **3.577 s**; online backup **0.149 s**; restore **0.391 s**.
- Concurrent WAL writer committed **200** synthetic QQ rows with no errors;
  backup restore recovered all **25,000** WeChat rows and the **200** pre-backup
  QQ rows. Post-backup data was correctly absent after restore.
- `scripts/check-local-hub.ps1`: **98 tests passed**, plus JavaScript syntax,
  Python compilation and `git diff --check`.
- P6-07 remains in progress until P6-03 source-cursor interruption/replay is
  implemented and measured.

## 2026-10-10 - Sync recovery and Windows source bundle

### Scope

- Added durable per-conversation offsets for WeChat and QQ. Each message page
  and its next offset commit in the same SQLite transaction, so a failed page
  does not advance the checkpoint. A restarted Hub resumes an incomplete scan.
- These providers expose offset pagination rather than stable source cursors;
  a completed sync still starts over, and insertions during a partial scan can
  shift offsets. WeCom remains a full materialized snapshot. This is recovery
  progress, not proof of true incremental synchronization.
- Added an explicit Windows source-portable ZIP staging script with runtime and
  private-state exclusions. It extracts to a temporary path and smoke-tests the
  Hub UI, source list, isolated storage and read-only MCP API.
- The ZIP is not a self-contained app: it requires system Python, Node/npm and
  first-run network access, and does not include NapCat/QCE. The Electron
  builder still targets the retained upstream WeFlow app.

### Verification

- `scripts/check-local-hub.ps1`: **102 tests passed**; JavaScript syntax,
  Python compilation, synthetic benchmark and `git diff --check` passed.
- Benchmark: 25,000 synthetic rows ingested in **1.276 s**; FTS search
  **0.066 s**; 50 cursor pages **3.584 s**; WAL writer committed 200 rows;
  backup **0.150 s** and restore **0.409 s** with expected recovery counts.
- Hub restart tests prove WeChat and QQ resume at their last committed offset;
  storage tests prove checkpoint isolation and message/checkpoint rollback.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **125 tests
  passed**. Rejected short POST bodies are drained within the existing size
  bound before returning 403, avoiding intermittent Windows connection resets;
  cross-origin and form-post rejection tests pass in the full suite.
- P6-03, P6-06 and P6-07 remain open; see `docs/roadmap.md` for release gates.

## 2026-10-10 - Self-contained Windows package and durable WeChat cursor

### Scope

- Added a Windows x64 package build that embeds the Hub, UI assets, standalone
  MCP executable, and SHA-256-verified official NapCat/QCE v6.3.2 shell. Runtime
  startup uses app-local storage and performs no package install or download.
- Added WeCom first-run configuration and moved integration settings/media
  defaults under `%LOCALAPPDATA%\LocalMessageHub` for the packaged application.
- Persisted WeChat's stable `(sort_seq, shard, local_id)` cursor atomically with
  each imported batch. Completed syncs continue from the committed cursor.
- Defined and tested source-specific reconciliation: QQ's successful full scan
  restarts from page zero; WeCom replays its local snapshot and does not delete
  rows absent from a potentially incomplete cache. No zero-rescan promise is
  made for either source.
- Roadmap acceptance is complete: P6-03, P6-06, and P6-07 now have evidence,
  with source-specific limitations recorded in the capability matrix.

### Verification

- `scripts/check-local-hub.ps1`: 135 tests passed; WebUI syntax, Python compile,
  synthetic benchmark, and `git diff --check` passed.
- Windows portable package smoke test passed against the built executable:
  HTTP UI + Lucide, three platform entries, isolated storage, and read-only MCP.
- A separate delivery audit reran the packaged Hub against an empty temporary
  store and completed `initialize`/`exit` through the bundled standalone MCP
  executable over stdio. No real account or chat data was used.
- QQ reconciliation tests prove a fresh full pass starts at page zero and picks
  up new messages; WeCom tests prove ID-based update/insert behavior without
  deleting an indexed row omitted from a later local snapshot.
- Build runs from a temporary staging tree that excludes private state. No real
  account credentials, chat database, or chat records were accessed.
- The final portable ZIP contains 10,258 entries and is 161,431,721 bytes;
  SHA-256: `9D5EA3386213ADD4B04EAED2854E69B6E296BF5BFAD20AE92E56B450CE1313BC`.
  Archive enumeration and required-file checks passed, and a content scan found
  no local account/data files or known machine/user-specific markers. The smoke
  test was run against the packaged directory before ZIP creation, not after
  extracting the ZIP.
