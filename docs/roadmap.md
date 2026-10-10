# Local Message Hub Roadmap

This roadmap tracks acceptance against the Local Message Hub taskbook while
preserving the repository's existing Windows-first Electron + Python
architecture and native local readers. A parallel Rust/Tauri rewrite would
duplicate working integrations and is not part of the current delivery path.
Platform access must remain local, read-only, and accurately scoped; unavailable
capabilities are never represented as connected or complete.

Status is based on code and tests, not file presence. `DONE` means its listed
acceptance has been verified. `IN_PROGRESS` means work is actively underway.

## Phase 0 - Project Foundation

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P0-01 | Preserve existing project and define architecture | Root, `hub/`, `integrations/`, `webui/` | - | Keep the current unified application and its in-repository readers; document intentional deviations from Rust/Tauri. | DONE |
| P0-02 | Reproducible checks and CI | `scripts/`, `.github/`, docs | P0-01 | One documented command set runs synthetic backend tests, frontend syntax/compile checks, and CI without private runtime data. | DONE |
| P0-03 | Roadmap and development log | `docs/roadmap.md`, `docs/development-log.md` | P0-01 | Every task has explicit acceptance and status; phase verification is recorded. | DONE |

## Phase 1 - Unified Store and Import

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P1-01 | Unified SQLite index | `hub/store.py` | P0-01 | Composite platform/account keys, WAL, busy timeout, idempotent transactions, normalized conversation/message/contact queries pass focused tests. | DONE |
| P1-02 | Safe exports | `hub/exports.py`, `hub/test_store.py` | P1-01 | JSON/CSV/Markdown/TXT/HTML preserve provenance; HTML escapes content and CSV prevents formula injection. | DONE |
| P1-03 | General JSON and CSV import | `hub/imports.py`, HTTP API, WebUI | P1-01 | Validate and normalize user-selected JSON/CSV, isolate bad rows, report counts/errors, support repeat import without duplicates. | DONE |
| P1-04 | Import jobs and provenance | `hub/store.py`, `hub/server.py` | P1-03 | Persist file fingerprints, job state and errors; restart-safe status and bounded cancellable processing. | DONE |
| P1-05 | Large-file and failure coverage | `hub/test_imports.py`, `tests/fixtures/` | P1-03 | 1,000+ bilingual synthetic messages, malformed rows and repeated imports are tested without private data. | DONE |

## Phase 2 - Search

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P2-01 | Scalable full-text index | `hub/store.py` migrations | P1-01 | FTS5 trigram when available, explicit literal short-query fallback, indexed search and consistency tests. | DONE |
| P2-02 | Context and stable paging | `hub/store.py`, `hub/server.py`, WebUI | P2-01 | Read a message with surrounding context; search filters, deterministic ordering and stable continuation locate original messages. | DONE |
| P2-03 | Search regression suite | `hub/test_store.py` | P2-01 | Chinese short/long terms, English, punctuation, dates, platform/account collisions and index rebuild are covered. | DONE |

## Phase 3 - Standalone MCP

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P3-01 | Shared-storage MCP process | `hub/mcp_server.py`, launch scripts | P1-01 | Independent stdio process reads the same SQLite database with UI closed; stdout contains protocol only. | DONE |
| P3-02 | Read-only query tools | MCP tools, integration tests | P2-02 | Search, get message/context, conversations/messages, contacts, sources and import status use bounded validated pagination. | DONE |
| P3-03 | Client setup and privacy | `docs/mcp-contract.md`, examples | P3-01 | Codex/Hermes examples, private content excluded from logs, malformed input and concurrent access tested. | DONE |

## Phase 4 - Desktop Interface

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P4-01 | Unified browsing/search | `webui/`, `hub/server.py` | P1-01, P2-02 | Three-platform switch, conversations, messages, filters, attachments and cross-platform search use the shared index. | DONE |
| P4-02 | Source and sync management | Integrations, `webui/` | P1-01 | Source state, errors, progress and QQ login/history are truthful; source databases remain read-only. | DONE |
| P4-03 | Overview dashboard | `webui/`, `hub/server.py` | P1-04 | Counts, source status, recent imports and local index size display real values. | DONE |
| P4-04 | User file import workflow | `webui/`, `hub/server.py` | P1-03 | Select, validate, preview, import/cancel, and inspect a real import report without mock records. | DONE |
| P4-05 | MCP settings | `webui/` | P3-01 | Show server availability, launch configuration, read/write mode and recent operation records. | DONE |

## Phase 5 - Local Management

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P5-01 | Local metadata schema | `hub/store.py` migrations | P1-01 | Tags, contact identities, notes, archive state and operation log are separate from imported messages. | DONE |
| P5-02 | Management API and UI | `hub/server.py`, `webui/` | P5-01 | Edit notes, manage tags, archive conversations and link/unlink identities; original messages never change. | DONE |
| P5-03 | MCP write authorization | MCP, settings, audit | P3-02, P5-01 | Read-only by default; explicit write opt-in, parameter validation, authorization and durable audit for every mutation. | DONE |
| P5-04 | Permission and audit tests | `hub/` and MCP tests | P5-02, P5-03 | Denied writes have no side effects; allowed changes are attributable and reversible where appropriate. | DONE |

## Phase 6 - Adapters, Resilience, Delivery

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P6-01 | Platform capability matrix | `docs/LOCAL_HUB.md`, adapters | P0-03 | Distinguish local history, bounded QQ roaming, cached media, incremental sync and unsupported exports per platform. | DONE |
| P6-02 | Telegram export adapter | `integrations/`, `hub/imports.py`, WebUI | P1-03 | Parse Telegram Desktop JSON, preview warnings, import as a registered query platform, and verify cross-platform search; clearly remain import-only, with no native sync claim. | DONE |
| P6-03 | Source checkpoints and replay reconciliation | Integrations, `hub/store.py` | P1-01 | WeChat commits a stable keyset cursor with each batch. QQ resumes interrupted positional scans, then starts a new completed sync at page zero and reconciles by stable message ID. WeCom replays its full local snapshot, upserts by message ID, and never infers deletion from a missing cached row. Tests define convergence and the limits of each contract; zero-rescan behavior is not promised where the upstream reader cannot provide it. | DONE |
| P6-04 | SQLite backup and restore | `hub/store.py`, `hub/server.py`, WebUI | P1-01 | Consistent online backup; validate integrity/schema before restore; preserve or recover from failure; test on temporary databases. | DONE |
| P6-05 | Delete local indexed data | `hub/server.py`, `webui/` | P5-01 | Explicit two-step confirmation, pause automatic sync, request active job cancellation, remove only LMH-owned data and report retained source-app data. | DONE |
| P6-06 | Reproducible Windows package | `scripts/build-windows-portable.ps1`, docs | P3-01, P4-04, P4-05 | A bundled Windows x64 EXE, standalone MCP EXE, UI assets, and verified official NapCat/QCE v6.3.2 runtime build from pinned public dependencies; the packaged directory passes an isolated smoke test without installed Python or Node. No account data/config is staged. | DONE |
| P6-07 | Performance and recovery validation | Tests, `scripts/benchmark-local-hub.py`, docs | P2-01, P6-04 | Synthetic 25k-message search/pagination, concurrent WAL writes, interrupted import and checkpointed WeChat/QQ batch resumption, plus backup/restore have repeatable tests or recorded benchmark results. Source-level incremental acquisition is tracked separately in P6-03. | DONE |

## Current Delivery Gate

Telegram Desktop JSON is import-only; native sync remains the three local
readers (WeChat, WeCom, QQ). Source behavior intentionally differs: WeChat uses
a stable forward cursor; QQ uses committed positional progress and a complete
page-zero replay on the next sync; WeCom replays a full materialized snapshot.
QQ and WeCom reconcile by stable message ID without deleting cached history.
The full roadmap checklist is complete under these tested source contracts;
this does not claim zero-rescan synchronization or equal historical coverage
across the three platforms. QQ roaming remains bounded by QCE/Tencent limits.
