# Local Message Hub Roadmap

This roadmap tracks acceptance against the Local Message Hub taskbook while
preserving the repository's existing Windows-first Electron + Python
architecture and native local readers. A parallel Rust/Tauri rewrite would
duplicate working integrations and is not part of the current delivery path.
Platform access must remain local, read-only, and accurately scoped; unavailable
capabilities are never represented as connected or complete.

**Last verified local source state (2026-10-14):** QQNT databases are read-only; new
keys are protected by current-user Windows DPAPI in private Hub configuration.
An earlier bounded scan through the running Hub verified and saved the local
QQ key. The latest approved direct scanner retry found no QQ process accessible
to the current user and read 0 MiB; the existing key still reports the database
as ready, so no replacement key or import was needed. The current live status
is connected with the prior local sync complete. Explicitly authorized
read-only syncs completed for the local WeChat and WeCom sources; the last
status check before shutdown reported all three sources as
`connected` without errors or account/message totals. No account identifiers or
message totals are included in this public roadmap. A bounded local preview
check returned cached avatars for WeChat and QQ; WeCom contact rows currently
have no avatar URL. The latest WebUI-aligned media probe read bounded byte
ranges from a cached WeChat image and WeChat/QQ voice items. No cached item was
found in the bounded sample for WeChat video, WeCom media, or QQ image/video.
This verifies local resolver reads, not complete decode or playback; broader
media coverage and complete media acceptance remain open. Online roaming
remains disabled. A read-only live check on 2026-10-15 after starting through
the repository launcher found all three sources connected without errors,
including completed QQ indexing. This point-in-time state may change; no account
identifiers or message totals are retained here.
NapCat is retained only as an optional sender that requires an online session and
explicit per-message approval; real delivery is unverified. Real cached-media
parity and complete media acceptance remain IN_PROGRESS. QQ snapshots still
rescan from the beginning rather than reusing offsets.
At the initial 2026-10-15 startup checkpoint, the UI and source-status API
returned successfully and all three source entries reported connected without
errors. The send capability was disabled then; that snapshot does not describe
later local settings. No real delivery is claimed.

**P8-P14 local follow-up (2026-10-15):** the full local gate passed with 811
Python tests, four source-client mutation-gate checks, 28 primary WebUI checks,
privacy/package checks, and the 25,000-message performance benchmark. Follow-up
focused tests also passed after correcting unknown WeChat image suffix handling
and refusing duplicate display-name targets in the GUI sender. The rebuilt
current-worktree portable and source packages passed isolated smoke and
`local-worktree` manifest verification. P9/P13 live delivery, P11 real-cache
parity, P12 live desktop/native acceptance, and P14 hosted verification for
these uncommitted changes remain open.

**P14 current-worktree gate (2026-10-14):** After the core search capability
alignment, `scripts/check-local-hub.ps1` passed for the modified worktree: 789
Python tests, four source-client mutation-gate checks, 32 primary WebUI checks,
packaging/privacy checks, and the 25,000-message benchmark all passed. The
hosted package evidence below is for committed HEAD `75fa73185b5b1acf960da35ef331901c3284deef`;
these current uncommitted changes have not been published or hosted-verified.

**Latest P14 package refresh (2026-10-15):** Rebuilt the current worktree's
Windows portable package (9,783 files; 586,784,821 bytes) and sanitized source
archive (690 files; about 25.1 MB). Isolated Hub/UI/MCP startup, source
privacy scans, and a `local-worktree` manifest verification passed. The source
archive contains no exact test target or text. Manifest run ID is
`local-currentworktree-20261015-010`; hosted CI and uploaded/downloaded artifact
verification still cover the committed revision, not this uncommitted
worktree.

**Latest P14 current-worktree gate (2026-10-14):** After the P11 export,
analytics and media-index validation fixes, `scripts/check-local-hub.ps1`
passed with 792 Python tests, four source-client mutation-gate tests, 32
WebUI checks, package privacy and deterministic ZIP checks. The 25,000-message
benchmark completed in 1.577 seconds for ingest, 0.035 seconds for first
search, 2.223 seconds for 50 cursor pages, 0.092 seconds for backup and 0.295
seconds for restore, with zero WAL failures. This validates the current
worktree locally; hosted CI and artifact round-trip evidence still applies to
committed HEAD `75fa73185b5b1acf960da35ef331901c3284deef`, not these
uncommitted changes.

**P14 local package verification (2026-10-14):** Windows portable and sanitized
source packages were rebuilt from the current code worktree. The portable Hub
and standalone read-only MCP executable passed isolated startup/protocol smoke
checks; package privacy scans and a SHA-256 manifest verification passed for
both outputs. The manifest is labeled `local-worktree` and is not hosted release
or upload/download evidence. This was an earlier checkpoint and is superseded
by the later code and package verification recorded below.

**Latest P14 full local gate (2026-10-14):** after QQ reply-preview media and
private analytics scope fixes, `scripts/check-local-hub.ps1` passed 793 Python
tests, four source-mutation gate checks, 32 WebUI checks, privacy and
deterministic packaging checks, and the synthetic 25,000-message benchmark.
Benchmark: 1.427 s ingest, 0.031 s first search, 1.959 s for 50 cursor pages,
0.095 s backup, 0.245 s restore, and zero WAL failures. The matching Windows
portable and sanitized source packages passed isolated smoke, privacy, and
manifest verification. Hosted verification still refers to the previously
committed revision; these worktree edits remain uncommitted.

**Local follow-up (2026-10-14):** WeChat 4.x `local_type=11000` is now normalized
as a sticker placeholder and included in sticker filtering/statistics across
chat, media API, WebUI, and MCP. Both sync paths preserve only an emoji MD5
that passes the existing XML allowlist, for the existing account-bound cache
reader. No real type-11000 cache entry has been verified, and no path is
inferred. The anti-recall settings copy now matches the existing
read-only/fail-closed capability. Synthetic regressions cover both sync paths,
classification and media counts; actual cached sticker rendering remains part
of the open P11 local-media acceptance.
The Hub was subsequently restarted with automatic sync disabled; one explicit
read-only three-platform sync completed with all sources connected. The live
WebUI rendered cached WeChat conversation avatars. The latest approved QQ scan
found no current-user-readable QQ process and read 0 MiB; the previously
verified local key remains usable. A P8 follow-up aligns HTTP and MCP global
search with the always-enabled core `search` capability while chat browsing is
disabled; conversation/message reads remain chat-gated. A completeness
assertion requires an explicit capability mapping for every MCP tool.
Hindsight was unavailable during this audit.
The local p376 Windows portable and sanitized source packages include this
follow-up. A later hosted Windows run passed the complete local gate, built and
smoke-tested both packages, then verified the uploaded and downloaded artifact
bytes and provenance for its committed revision. P14 hosted acceptance is
complete for that revision. The newer uncommitted worktree has local package
verification but no hosted run or artifact round-trip, so the P14 row remains
IN_PROGRESS until its current source state receives that evidence. P9, P11,
P12-01, and P13 retain their real-device acceptance items below.
P9 local send preparation also now accepts exact WeChat `@chatroom` targets
across service, MCP schema and WebUI validation, while rejecting chat-type
mismatches; this only prepares a request and never delivers it. The source
privacy scanner now inspects CSV/TSV text, and the source packager excludes
these data files entirely so exports cannot enter the public source archive.
The synthetic 25,000-message benchmark now enforces broad 60-second ingest and
pagination, 10-second first-search, and 30-second backup/restore ceilings as
catastrophic-regression guards; hosted P14 CI and artifact roundtrip now pass.

**P13 local queue snapshot (2026-10-15):** the already-running loopback Hub
returned HTTP 200. No approval was consumed and no message was delivered. The
running process predates the current worktree edits, so this snapshot does not
verify the latest source in the live service. Local queue state is omitted from
this public roadmap.

**P14 current-worktree package refresh (2026-10-15):** the latest portable
package contains 9,783 files; the sanitized source archive contains 690
entries. Both were rebuilt from this worktree and passed isolated startup,
read-only MCP, privacy, and local manifest byte verification. This remains
local-worktree evidence; hosted verification and upload/download round-trip
for these uncommitted changes are still open.

**P8-02 MCP config follow-up (2026-10-14):** client configuration now maps
`search_messages` to the always-on global search capability and `list_sources`
to `stats`; chat browsing can be disabled without hiding global search.
Focused HTTP coverage verifies both gates.

**P13-01 local preparation follow-up (2026-10-14):** a prior local operation
reported that a user-provided WeChat text request was recorded as
`pending_approval`; a later sanitized probe reported that no request was
queued. The Hub is now stopped, and its private storage was not inspected, so
the request's current persisted status is unverified. No message was delivered.
Public documentation omits the target, content, account and ID.

**P9 login probe validation follow-up (2026-10-14):** NapCat login readiness now
requires an integer OneBot `retcode`; boolean and floating-point zero values
remain unavailable and cannot report an account as online. Synthetic coverage
passes without contacting NapCat. Live QQ runtime and delivery acceptance stay
open.

**P11 QQ cache gallery preview follow-up (2026-10-14):** visible QQ global-cache
image tiles now use the existing bounded lazy resolver with their exact account
and opaque resource ID. Page changes still abort pending previews, and resolver
URLs remain same-origin tokens. Real-cache parity and device acceptance remain
open.

Status is based on code and tests, not file presence. `DONE` means its listed
synthetic/local acceptance has been verified. `IN_PROGRESS` includes work whose
implementation is present but whose real-device or package-run acceptance is
still outstanding.

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
| P6-06 | Reproducible Windows package | `scripts/build-windows-portable.ps1`, docs | P3-01, P4-04, P4-05 | A bundled Windows x64 EXE, standalone MCP EXE, UI assets, and verified official NapCat/QCE v6.3.2 runtime build from pinned public dependencies; the packaged directory passes isolated Hub startup and standalone MCP initialize/tools/list/exit stdio smokes without installed Python or Node. No account data/config is staged. Current local build passes all listed checks; real QQ login/delivery remains in P9. | DONE |
| P6-07 | Performance and recovery validation | Tests, `scripts/benchmark-local-hub.py`, docs | P2-01, P6-04 | Synthetic 25k-message search/pagination, concurrent WAL writes, interrupted import and checkpointed WeChat/QQ batch resumption, plus backup/restore have repeatable tests or recorded benchmark results. Source-level incremental acquisition is tracked separately in P6-03. | DONE |

## Phase 7 - V2 Baseline Audit

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P7-01 | WeFlow/Hub capability matrix | `docs/weflow-gap-matrix.md`, `docs/roadmap.md` | P0-P6 | Compare pinned WeFlow main and Hub main; record upstream entrypoints, real unified-UI state, reuse path and synthetic acceptance for every listed capability. Distinguish local database mutation, SSE/webhook event push and real IM delivery. | DONE |
| P7-02 | Clean-checkout CI dependency audit | `requirements-ci.txt`, `.github/workflows/local-hub-ci.yml` | P7-01 | CI installs all public test import dependencies and the documented PowerShell check passes without private runtime state. | DONE |

## Phase 8 - Capability Management

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P8-01 | Persisted feature registry and Settings UI | `hub/`, `webui/` | P7-01 | Per-module switches persist across restart; unsupported capabilities show unavailable reasons; core search/settings remain available; closing modules does not delete data. | DONE |
| P8-02 | Server-side capability enforcement | HTTP API, MCP, background tasks | P8-01 | Every restricted route/tool/task checks current permission server-side; direct calls cannot bypass hidden UI; tests cover denied requests and no side effects. MCP `list_sources` and `lmh://sources` discovery/read both honor the `stats` capability; disabling stats leaves chat tools/resources available. | DONE |

**P8-02 revocation follow-up (2026-10-12):** MCP `resources/list`,
`resources/templates/list`, `prompts/list`, and `prompts/get` now refresh the
current capability registry on every request. Revoking `mcp` denies all four
endpoints, while revoking `chat` removes message resource templates from an
already-running MCP session. Regression coverage passes; HTTP routes and
background ingestion/task revocation were also re-audited.

**P8-02 send-approval revocation follow-up (2026-10-12):** Turning either
QQ or WeCom bot sending off clears every outstanding one-time approval
challenge, including challenges for the other sender. Re-enabling a sender
does not revive an old challenge; synthetic HTTP regressions verify that both
senders reject the stale token without delivery and accept a newly issued one.

**P8-02 quoted-message route follow-up (2026-10-12):** The direct
`/api/quoted-message` route now checks the `chat` capability just like the
other message-context routes. A focused HTTP regression confirms that disabling
chat returns 403 with `capability_disabled` and `capability=chat`; this closes
an omitted route from the capability map.

**P8-02 search-route follow-up (2026-10-14):** The direct `/api/search` route
uses the always-enabled core `search` capability, while conversation browsing,
message context and direct message-page routes remain gated by `chat`. MCP
`search_messages` follows the same core-search contract. Focused HTTP and MCP
regressions confirm search remains usable with chat disabled while message
listing stays denied.

## Phase 9 - QQ Text Sending

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P9-01 | Narrow send service and simulated adapter | `hub/`, `integrations/qq/` | P8-02 | One text-only contract with explicit account/conversation/content/request ID, target validation, local approval, durable audit and sent/failed/unknown states. Simulation covers errors, request dedupe, concurrent approval claiming exactly one delivery, restart recovery of in-flight sends to `unknown/interrupted`, and no retry without contacting QQ. | DONE |
| P9-02 | NapCat OneBot 11 adapter | `integrations/qq/`, Settings | P9-01 | Loopback-only endpoint config and NapCat runtime JSON use atomic replacement. Local UI approval requires a request-bound, 60-second one-time challenge; disabling either send capability clears outstanding challenges. Explicit OneBot rejection becomes `failed`; transport interruption, timeout, malformed response, redirect, server error or success without a message ID becomes `unknown`, and no status triggers blind retry. Fake private/group contracts plus a local socket server cover bearer auth, bounded bodies, 401/408, redirect blocking, timeout, restart recovery, and one-request delivery behavior without launching NapCat or contacting QQ. Settings retains the latest 100 outcomes; terminal/unknown requests are read-only. The approval UI is not a boundary against other same-user processes. QQ history is acquired separately by the read-only local database reader; NapCat is retained only as the optional send transport. Local send targets are now resolved from the indexed QQ conversation, so a private conversation stored as a QQNT uid is converted to its numeric uin before the send request enters the queue and back to the visible local id during review. Synthetic HTTP/provider and WebUI coverage asserts the conversion, the local-database fallback and the refusal of unresolvable identities without launching NapCat or contacting QQ. Live NapCat/runtime and real-account send acceptance remain open. | IN_PROGRESS |

**P9-02 login-status UI follow-up (2026-10-12):** when the local status API
explicitly reports unavailable, the login modal now says it cannot confirm the
state instead of implying that verification is underway. Focused UI assertions
cover unavailable and waiting-for-scan states. The open local UI still requires
the user to scan and confirm; no account login or history sync is claimed.

**P9-02 explicit sender readiness follow-up (2026-10-14):** Settings now offers
an on-demand, local-only NapCat readiness check. It calls only OneBot
`get_login_info` on `127.0.0.1`, reports endpoint reachability and the returned
logged-in QQ identity, and makes no request when QQ sending is disabled. It is
not run at startup. Synthetic sender, route, and WebUI tests pass; no live
NapCat endpoint or QQ account was contacted, so device acceptance remains open.

**P9-02 malformed receipt follow-up (2026-10-14):** only an exact integer
OneBot `retcode` is accepted. Boolean and floating-point values now leave the
delivery outcome `unknown`, even when Python would compare them equal to zero;
only an exact nonzero integer rejection is classified as `failed`. The focused
17-test sender suite passes. Live NapCat/QQ delivery remains unverified.

**P9 read-only key-reader bound follow-up (2026-10-14):** Windows PID
enumeration now accommodates up to 65,536 system processes; the 256-process
candidate cap applies only after same-user QQ executable validation, so
unrelated system processes cannot consume the QQ candidate allowance. Focused
regressions cover QQ after 256 unrelated PIDs and the non-QQ-heavy case. Two
explicitly approved live scans reached the configured safety limit without
verifying a key; no key was saved and no history import began.

**P9 scan diagnostics follow-up (2026-10-14):** terminal QQ key-discovery
status now exposes only bounded process, candidate, and MiB-read counts; the
WebUI displays them after a completed scan. It omits PIDs, paths, account
identifiers, and candidate material. Focused Hub and WebUI checks pass. This
does not add counts retroactively to the already completed scan.

**P9 local QQ history acceptance (2026-10-14):** after fresh user approval, a
bounded read-only key discovery verified and saved the QQ database key locally.
The Hub automatically indexed the local history and the QQ source reached
`connected` with no reported error. The QQ database was not modified, and no
key, account identifier, or message content was added to the repository. This
closes local QQ read-source acceptance; live NapCat sending remains open.

**P9 login-status authentication follow-up (2026-10-13):** the local status API
now reports `online` from NapCat's `isLogin` account-authentication field, not
`coreReady` (runtime initialization); QR retrieval uses the same authenticated
state and still requests a code from a ready-but-unauthenticated runtime.
Synthetic HTTP/provider regressions cover both sides. Real QR scan and history
import remain unverified; P9 stays `IN_PROGRESS`.

**P9-02 QR refresh recovery (2026-10-12):** if NapCat QR refresh fails, the
adapter now retries the non-refresh QR read before considering a QQ launch.
This prevents a transient refresh failure from needlessly falling through to a
second QQ launch while the original login is active. Synthetic provider coverage
asserts the fallback returns before any launch/configuration work; runtime scan
and device history acceptance remain open.

**P9 history follow-up (2026-10-12):** QCE `faceElement` and `replyElement`
payloads now map to a field allowlist for the existing QQ rich-content renderer.
`walletElement` is reduced to a bounded summary card with its raw amount/token
fields discarded. Synthetic provider/UI regressions confirm the content renders
and unknown/raw fields do not survive. Live NapCat and account history/media
acceptance remain open.

**P9 QQ history-import budget validation (2026-10-13):** import budgets now
accept only JSON integers or ASCII decimal strings; booleans, floats, and
fractional strings are rejected instead of being silently coerced. Focused
server/provider regressions pass. This does not verify live QQ login, roaming
history availability, or real-account attachment retrieval; P9 remains
IN_PROGRESS.

**P9 wallet follow-up (2026-10-12):** QCE `walletElement` now becomes a bounded
summary card in QQ history; raw amount/token payloads are discarded. Synthetic
provider and escaped-UI regressions pass. Live account acceptance remains open.

**P9 location follow-up (2026-10-12):** QCE `shareLocationElement` now renders
as a bounded location summary without coordinates or raw payload. Provider and
existing QQ rich-content UI regressions pass; live NapCat/history acceptance
remains open.

**P9 partial-sync follow-up (2026-10-12):** A QCE provider error while fetching
one conversation no longer discards later readable conversations: the Hub
retains the failed conversation's existing checkpoint, records only a safe
failure count, and returns the source state as partial. A good/failure/good
synthetic regression covers continuation and checkpoint preservation. A
separate bounded read-only live probe confirmed one sampled conversation fetch
succeeded and a subsequent fetch failed; this does not verify full QQ history.

**P9 pagination follow-up (2026-10-12):** If QCE repeats the same complete page
while claiming another page exists, the provider now stops before appending
duplicate messages or issuing further fetches. This is limited to consecutive
pages with complete `msgId`/`msgSeq` identity tuples; short-page `hasNext`
continuation remains supported. A synthetic regression verifies the repeated
page is fetched only twice. Live NapCat history acceptance remains open.

**P9 bounded-fetch follow-up (2026-10-12):** Hub sync still consumes batches of
500 messages, but the QQ provider now fetches them from QCE in 100-message
pages and explicitly sets QCE `batchSize=100`. This reduces the request-side
prefetch window estimated from the integrated QCE route from about 5,500 to
1,100 rows per conversation cache miss; it does not attribute all live QCE
memory use to Hub. Synthetic page/continuation and sync-resume regressions pass.
The already-running service must reload this code before it affects live QQ
reads; no live sync was triggered for this change.

**P9 bounded-response follow-up (2026-10-13):** The shared QQ QCE JSON reader
now reads at most 32 MiB plus one detection byte and rejects oversized
responses before JSON parsing. This caps Hub-side response buffering for rich
history and other QCE JSON routes; it does not bound QCE's own allocations or
the server process's memory. A synthetic HTTP-response regression verifies the
read limit and safe failure. Live QQ remains unconnected and unverified.

**P9 login-state validation follow-up (2026-10-13):** NapCat's `isLogin` flag
is accepted only when it is the JSON boolean `true`; malformed truthy values
cannot suppress the QR or make the status API report an authenticated account.
A focused provider regression covers string, integer, and null values. This
does not verify live QQ login or history sync; P9 remains `IN_PROGRESS`.

## Phase 10 - MCP Governed Sending

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P10-01 | Prepare/approve/status tools | `hub/mcp_server.py`, MCP contract | P9-01 | With `QQ 文本发送` enabled, MCP exposes QQ prepare/status tools; with `企业微信机器人文本发送` enabled and a valid bot identity configured, it exposes WeCom bot prepare/status tools. Both create/read auditable requests only; MCP never approves, rejects, or delivers. Each platform capability is checked on direct calls; WeCom requests must match the configured bot ID and remain isolated from QQ request lookups. Local UI approval is request-bound and one-time. | DONE |

**P10-01 client-config follow-up (2026-10-13):** `/api/mcp` includes
WeCom bot prepare/status tools only when the send capability is enabled and
the configured identity is valid, matching runtime MCP discovery. Credential
status errors fail closed. Synthetic HTTP coverage checks enabled, disabled,
unconfigured, and unavailable states; no real delivery is performed.

## Phase 11 - WeFlow Core Feature Alignment

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P11-01 | Media, analytics and reports | Hub, WebUI | P8-02 | Media, private/group analytics, annual and pair reports use unified SQLite; pair reports include reply count, average reply time, and fastest reply time for consecutive counterpart-to-self messages within a 1-hour session window, with optional year and counterpart filters; pair-report APIs reject group/unsupported conversation kinds before aggregation; group member rankings use one batched aggregate query per page instead of N+1 queries, and the WebUI requests only its displayed top 12 while full observed-member counts remain accurate; same-second counterpart-to-self replies count as valid zero-second responses with a focused regression. Image preview and source limits have synthetic/API tests. The media library filters source (all/chat/Moments/QQ cache), platform plus exact account, attachment type, conversation title and inclusive UTC+08 date range; one SQLite UNION applies account-aware filter/count/order/pagination across indexed chat media and Moments media, while JSON1 expands WeCom attachments and QQ roaming resources. The media account picker uses a composite platform/account identity; MCP `list_media` accepts the same optional account scope, and the QQ cache picker only matches the currently authenticated QCE account. A separate QQ cache view pages QCE's authenticated local resource gallery; opaque in-memory handles bind each item to the current QQ account, URLs stay server-side, preview/ZIP export use a canonical encoded local path, and item/file/total byte limits remain enforced. MCP additionally lists QQ gallery metadata by opaque ID and returns explicitly selected cached media under its existing local 8 MiB boundary. QQ gallery and message-media downloads stream in 1 MiB reads, enforce the 50 MiB ceiling and expected gallery size, and atomically cache complete files only. Annual reports render a labeled 24-bucket UTC+08 hourly distribution, including empty hours; annual and all-time month charts always return ordered 1–12 buckets with zero-filled missing months. Overview/private/relationship media counters use the same safe JSON1 predicate, counting each WeCom/QQ text parent with indexed attachments once rather than counting attachment entries; WeChat image rows with NULL `raw_type` count as media, while explicit local type 47 stays excluded under SQLite NULL semantics; exact-account cached type-47 stickers have a separate preview/MCP/ZIP path, while cache misses remain searchable placeholders and excluded from ordinary image filters. WeChat V1/V2 image decrypt reads are bounded to 64 MiB (including a growth-after-stat guard) to prevent unbounded allocation from oversized cache files; keys remain offline/explicit and Hub-private, the image AES memory scanner is disabled, and JPEG/PNG/GIF/WebP/BMP signatures retain previewable extensions, including legacy XOR WebP detection. Explicit WeChat media export prefers an already-cached, complete original image then falls back to mid/thumbnail while ordinary preview stays mid-tier. WeChat local type 47 emoji messages remain searchable as `[表情]`; generic emoji rows are not treated as ordinary images, while exact-account cached stickers use the separately gated sticker preview/MCP/ZIP path and cache misses remain placeholders. WeChat cached video/file staging validates the account media subtree, rejects symlink escapes, writes atomically and copies in bounded 1 MiB chunks without modifying source files. The downloader re-resolves the indexed local ID and shard and allowlists the four WeFlow-confirmed file localType variants; unrelated types are rejected by regression coverage. WeChat SILK cache writes now use same-directory temporary files and atomic replacement, preserving prior complete cache entries on failure. WeCom cached SILK voice attachments reuse the shared local decoder after reading only the file header; QQ cached audio MCP reads now signature-check and label QCE SILK v3 (including its 0x02-prefixed header variant) and AMR/AMR-WB as audio, with focused synthetic coverage. Decoding remains unavailable when the optional decoder is missing. Optional default-off voice transcription accepts only an indexed voice-message identity and provider-resolved local PCM WAV, uses an already-installed Sherpa/SenseVoice runtime and model, never downloads them, and keeps transcripts in memory only; injected-recognizer tests cover API/UI, identity binding, cache miss, invalid audio, and no-network model status. WeChat relationship achievements now count verified `raw_type=50` voice/video call records toward the upstream `calls_5` milestone; no message-body parsing is used. Annual report CSV export contains the same first/last message, busiest-day and busiest-hour values shown in the report UI. A synthetic HTTP end-to-end resolver test now covers indexed WeChat image/voice/video/file through same-origin media tokens, missing-cache behavior, path redaction, and source-file hash stability. MCP attachment image signatures include BMP, verified end-to-end with a cached QQ BMP returned as MCP image content; full WeFlow resource/decryption parity remains open. | IN_PROGRESS |
| P11-02 | Resource management and complete exports | Hub, WebUI | P8-02 | JSON/CSV/Markdown/TXT/HTML/XLSX/SQL/ChatLab message exports preserve platform, exact account, conversation ID, and message ID provenance with injection protections; contact JSON/CSV exports preserve only normalized profile scalar allowlist values, never QCE `raw` or unknown nested payload, URLs or token/credential-like values. Current group roster exports as JSON/CSV with platform/account/group provenance, field whitelist, CSV formula protection including values with leading whitespace/BOM, avatar/token omission, export capability gate, and explicit 1,000-member limit (larger rosters fail rather than truncate). Media library supports cross-page selection of up to 100 individual attachments from chat, QQ roaming, WeCom, cached Moments and the authenticated QQ global cache gallery into a cancellable, capability-gated local ZIP task (50 MiB per file/512 MiB total) with a manifest and API/UI progress. Disabling the media capability cancels and joins active ZIP jobs; re-enabling it resumes job acceptance. QQ cache export accepts only opaque account-bound resource identity; the client cannot submit URLs or paths, and QCE-declared size is checked against the bounded downloaded bytes. Batch export supports QQ resource indexes 0-100 to match the provider reader and rejects 101; other platforms retain their 0-49 bound. WeChat chat-image ZIP export defaults to best available and can request only cached `mid`, `thumb`, or `original`; missing originals fail that item without triggering source downloads. The selector is limited to ordinary WeChat chat images, not Moments, WeCom, QQ, or other media. Cached WeChat type-47 stickers resolve only within the exact account's allowlisted local cache and may be previewed, explicitly read through MCP, or included in ZIP export; unresolved stickers remain searchable placeholders, and sticker classification stays separate from ordinary image filters/counters. Synthetic resolver/job/UI tests cover tier forwarding, sticker cache boundaries, path rejection and unchanged default behavior. Synthetic HTTP/UI tests verify QQ gallery metadata/preview identity and provider URL/path redaction. Synthetic HTTP end-to-end tests run real WeChat/WeCom/QQ Hub resolvers and the separate Moments catalog resolver into ZIP, verifying bytes, identity-only manifests, path/credential redaction, caller-path rejection, and source immutability; indexed WeCom attachments remain exportable even when attached to a text-type parent message. Moments ZIP jobs use only account/post/media-index/variant identity, re-resolve the catalog locator, revalidate cache-root containment, and never accept or expose caller paths. Batch export enforces budgets per chunk and checks cancellation while reading; local media playback/preview uses bounded 1 MiB streaming with single byte-range support. Media-library keyword filters match conversation title, filename, attachment name, chat message text, sender name/ID, or Moments post content before SQLite count/pagination. WeChat video/file cache staging also uses bounded memory, an account-root check and atomic Hub-cache writes. Optional media backup bundles the index snapshot with indexed account-scoped Hub WeChat media-cache files and referenced Hub-managed QQ roaming files; restore validates archive paths, budgets and SHA-256 before merging and refuses conflicting overwrites. Legacy unscoped and orphan cache files plus source-client databases/caches are excluded. Clearing the local index removes managed attachment archives. A schema-v8 SQLite Moments catalog atomically replaces one account's sanitized posts/media after successful explicit WeChat sync, tracks intentionally empty catalogs, filters/pages without rescanning `sns.db`, and stores only validated cache-relative locators; missing/invalid cache roots still allow text catalog refresh with media marked unavailable. Export resource projection now preserves repeated identical attachments inside one list while taking the maximum multiplicity across mirrored aliases, and tests exercise JSON, ChatLab and SQL output. The active-job cancellation regression passes; full resource library/decryption parity and real-device acceptance remain open. | IN_PROGRESS |

P11-01 media-library follow-up (2026-10-14): One WeChat chat image can explicitly
resolve the best tier already present in the local cache; the UI updates only
that tile and preserves its current preview on a cache miss. Ordinary viewport
previews remain on `mid`; full resource/decryption parity and real-cache
acceptance remain open.

P11-01 follow-up (2026-10-14): The WeChat image cache resolver recognizes the
suffixes classified by the pinned WeFlow reader's active selector: original
`_h/.h/_hd/.hd`, base `.dat`, and thumbnail `_t/.t/_thumb`; it deterministically
selects the largest file within each tier. B/W/C cache suffixes are not assigned
a quality tier because the active selector does not classify them. Account-root
containment checks remain in place. Focused synthetic coverage verifies the
known tiers and rejects unknown B/W/C tiers; full resource/decryption parity
and real-cache acceptance remain open.

P11-01 follow-up (2026-10-14): The local QQ parser now preserves QCE-style
`market_face` messages as image resources, including filename, MD5 and size, so
the existing account-scoped cache resolver, preview and export paths can read
them. Focused parser, cache and WebUI regressions passed. The real device's
market-face cache naming has not been verified; full media parity remains open.

P11-01 follow-up: WeChat type-47 stickers now resolve only a previously cached
WeFlow emoji file under the exact account-hashed namespace and allowlisted
extensions. The MD5 is retained as validated metadata; no emoticon database or
network fallback is used. Missing, cross-account, or unsafe cache entries remain
unavailable, while message text stays `[表情]`. Synthetic fixtures cover GIF,
PNG, traversal, account isolation, and missing entries. Full resource/decryption
parity remains open.

P11-01 follow-up (2026-10-12): QQ forward-resource validation now recognizes
QCE-exported AMR/AMR-WB, ADTS AAC, and M4A-family audio signatures while
rejecting generic `isom` video as voice; synthetic fixtures pass. Annual and
pair-report hourly charts expose a UTC+08 accessible name and hide decorative
bars from assistive technology. Focused provider and report-export checks pass;
real-device media acceptance and full WeFlow parity remain open.

P11 image-key and QQ media-index follow-up (2026-10-13): QQ attachment lookup
reuses a bounded process-local directory index only when account, roots, and
attachment match criteria all agree; regression coverage verifies that a
different attachment cannot reuse stale matches. WeChat image keys may be
entered explicitly after local-cache validation, then are stored per account
using current-user Windows DPAPI; status/config APIs do not reveal the key.
Memory scanning remains disabled for WeChat. Focused synthetic tests pass;
real-account image/decryption acceptance remains open.

This follow-up supersedes the earlier P11-01/P11-02 table wording that described
type-47 stickers as unsupported for preview/export. Cached stickers now work
through preview, explicit MCP reads, and ZIP export; unresolved stickers remain
searchable placeholders. Sticker classification still stays separate from
ordinary image filters and counters.

The annual report now downloads a UTF-8 CSV containing its overview, monthly,
hourly, and message-type aggregates; it exports no message bodies. The browser
CSV writer quotes fields and neutralizes formula-leading text.

P11 resource-export manifests now include a streaming SHA-256 for every
successfully archived attachment, verified against synthetic WeChat and Moments
fixtures; this does not alter source identity or path-redaction behavior.

**P14 local-worktree package refresh (2026-10-13):** portable p344 contains
the WeChat DPAPI image-key path and current QQ media-index fix; its bundled
Hub/WebUI/source/MCP startup checks and privacy audit pass. A refreshed source
package and byte-verified local-worktree manifest are recorded below. Hosted
Actions and artifact upload/download remain unverified, so P14 remains
`IN_PROGRESS`.

QQ roaming history chat bubbles now render stored structured text, mentions,
faces, reply/forward previews, locations and share-card summaries. Media remains
on the existing local resolver path; upstream HTML is intentionally ignored.
Regular QQ sync now stores a compact structured projection instead of duplicating
the raw QCE message payload, while preserving the chat-type metadata needed for
later media lookup.

Group analytics retains a top-12 default member list and now loads the full
observed sender ranking only when requested for one exact platform/account/group;
the response is capped at 5,000 members and cached in the page for collapse and
reopen. This avoids fetching every group's complete ranking at once.

P11-01 voice diagnostics now perform an on-demand, exact-shard lookup only
after an indexed WeChat voice playback request misses local audio. The bounded
response contains an allowlisted reason and byte count only; ordinary message
pages do not scan media indexes, and no source path or server ID reaches HTTP/UI.
The media index query treats local message ID `0` as a valid identity rather
than falling back to an unfiltered voice scan, including legacy schemas without
`download_status`. Synthetic provider/HTTP coverage includes duplicate local
IDs across shards and redaction. This closes a diagnosability gap, not full
media/decryption parity.

P11 media classification follow-up: WeChat `raw_type=47` rows remain in chat
history and the unified media view as `sticker`, but are excluded from image
filters and media counters. Overview message-type breakdown groups them under
`sticker`. One focused synthetic store regression and frontend syntax check
passed; this does not close the broader WeFlow resource/decryption parity gap.

P11 media navigation follow-up: chat-backed media tiles now expose an
accessible, tooltip-labeled action to open the source message in its conversation
context. Moments and QQ global-cache tiles omit it because they do not identify
a chat message. Focused synthetic rendering verifies this boundary; full media
and WeFlow parity remain open.

P11 MCP attachment follow-up: explicitly requested cached file attachments now
carry signature-derived PDF, ZIP, RTF, or OLE MIME when recognized, with unknown
formats remaining `application/octet-stream`. Bytes are returned unchanged; no
archive is opened or extracted. One synthetic resolver check covers PDF/ZIP/RTF;
existing local path, signature, and 8 MiB checks remain in force.

P11 MCP media-list follow-up (2026-10-12): `list_media` accepts optional
inclusive UTC+08 `start` and `end` calendar dates, matching the media-library
filter. Synthetic tests cover both boundaries, one-sided ranges, and invalid
dates; MCP still returns metadata only.

P11 MCP attachment discovery follow-up (2026-10-12): `list_media` now expands
WeCom attachments and QQ roaming resources attached to text messages into
individual metadata entries. Each entry retains a safe `resource_index` and a
URI bound to its parent message, and can be passed to the existing explicit
`get_attachment` tool. The MCP path excludes Moments and QQ cache gallery
resources, which remain on their dedicated tools, and strips attachment source
paths/URLs from listed metadata. Synthetic coverage verifies pagination,
type filtering, ordinary WeChat media, URI identity, source redaction, and
WeCom/QQ index handoff; no media bytes are returned by the listing.

P11 WXGF/WXAM follow-up: WeChat HEVC-backed images resolve through the
repository's pinned `ffmpeg-static` binary in source-launched and portable Hub
and MCP runtimes. Portable packages retain the upstream binary license/build
notices; the 64 MiB JPEG output bound and original `.wxgf` fallback are
unchanged. Synthetic conversion/fallback tests pass, and portable p332 smoke
executed its bundled FFmpeg without relying on the system PATH. Source p336
passed privacy and extracted-startup checks, and manifest p336 matches both
package byte sets. Real-account preview acceptance remains open.

P11 reporting follow-up: annual reports can be downloaded as local CSV with
overview, monthly, hourly and message-type aggregates; message bodies are not
included and formula-leading values are neutralized. Pair reports also export
the selected private conversation's aggregate counts, hours and message types;
neither export contains message bodies.

P11 contacts now allow exact account-scoped browsing and export, and MCP
`list_contacts` accepts an optional account ID within its required platform.
This improves identity isolation but does not close the broader P11 resource and
media parity gaps.

P11 QQ avatar follow-up: the bundled QCE emits private-chat avatars from
`q1.qlogo.cn` and group avatars from `p.qlogo.cn`. Both exact HTTPS hosts are
now accepted by the WebUI and the Hub's offline cache reader. This only serves
an already-present, signature-checked local cache entry; it does not fetch
avatars. Synthetic UI/cache tests cover these URLs and reject lookalike hosts.
Real QQ avatar cache availability remains unverified.

## Phase 12 - Advanced Optional Modules

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P12-01 | Moments, notifications, automated export and account safety | Hub, WebUI, integrations | P8-02 | Automated local export and default-off loopback SSE notifications are implemented. Automatic export pages a temporary transactionally consistent SQLite backup so concurrent sync writes cannot shift OFFSET pages and duplicate or omit records; the snapshot is removed after the job. Notifications support platform filters, all-conversations mode or an explicit conversation allowlist, per-conversation mutes that always take precedence, and standard/silent browser presentation; payloads remain body-free. Synthetic WebUI regressions verify notification clicks select the exact platform/account/conversation, focus and load that chat, never expose a sentinel message body, and close an established event stream if browser notification permission is revoked. When one tab loses its body-free SSE stream, it now checks lock status and reloads to the lock page only if another tab actually locked the account; transient network errors do not force a logout. When a lock-enabled tab resumes visibly, it also checks server session validity without resetting the idle timer and returns to the lock screen after expiry; transient status failures do not force logout. Synthetic tests cover both resume behavior and multiple same-event listeners. Isolated Edge acceptance verified lock setup, immediate lock, 423 API denial, three-second idle relock, and successful unlock after expiry; it exposed and fixed passphrase loss when FormData was read after disabling the input. Real desktop-notification delivery remains open. App lock gates the loopback WebUI/HTTP APIs, hashes a 12+ character passphrase with PBKDF2, rate-limits unlock attempts, keeps at most 64 in-memory sessions while reaping expired sessions on unlock, and closes active SSE streams after lock; independent MCP stdio access is outside its scope. Wheel scrolling is counted as browser activity so sustained wheel-only reading refreshes the configured idle timer. A default-off Moments module performs bounded, read-only local `sns.db` parsing during explicit sync, then serves SQL-backed author/text/date filters, paging and UI/MCP queries from a per-account sanitized SQLite catalog; each page loads media in one batched SQLite query instead of one query per post; keyword search matches post text and comment content, not usernames or media URLs. Filtered JSON/CSV/Markdown/TXT/HTML exports strip media credentials and harden CSV formulas/HTML. Parsed `refCommentId` relationships render escaped reply-to-author labels when the referenced comment exists; missing references render as ordinary comments. Export reads 50-record SQLite pages and serializes incrementally into a byte-capped buffer, aborting at 32 MiB before retaining more output or fetching later pages. Catalog replacement is transactional, cache locators are relative and boundary-validated, and cached text remains queryable when the WeChat reader is unavailable; with multiple cached accounts, callers explicitly select one and media links stay unavailable without that account's reader/cache. WeFlow-compatible existing cache entries are resolved offline, signature/boundary checked, displayed through same-origin 15-minute tokens, and available to MCP only through an explicit image request or MP4/WebM video resource, capped at 8 MiB; no CDN fetch occurs and arbitrary paths/URLs are rejected. MCP `get_attachment` additionally returns explicitly requested cached image, video, voice or file content under the same 8 MiB local-cache boundary, checking media signatures before labeling supported media types. Moments XML media parsing follows the repository's WeFlow `snsService.ts` attribute format for URL/thumb token, key, md5 and enc_idx fields, with child-text fallback; synthetic XML coverage verifies this without network access. Read-only inspection verified representative local `sns.db` and `head_image.db` schemas; the avatar table is matched to contacts by exact username (never inferred from md5), and bounded JPEG-signature blobs are cached under Hub-owned storage during sync with a local-avatar-first policy. Synthetic tests cover schema/key gating, identity, signature/size bounds, local-over-URL precedence, and unchanged fallback behavior. Other schema variants fail closed; synthetic WebUI template-render tests cover avatars in conversation rows, messages, inspector details, and group rosters. Synthetic browser/pixel rendering passed with isolated data; real-account acceptance remains open. Other real-account media/cache acceptance remains open; risky client interception remains separately gated. | IN_PROGRESS |
| P12-02 | Relationship footprint and optional modules | Hub, WebUI, MCP | P8-02 | Read-only derived views and objective private-chat milestones reuse the normalized index, are platform/account/conversation scoped, content-free, and pass synthetic identity/date-boundary tests. Media milestones include WeCom attachments and QQ roaming resources, count each message once per category, and never inspect message text. The default-off module also exposes capability-gated MCP tools for conversation footprint, UTC+08 timeline, and objective milestones. Semantic journey inference is intentionally excluded. | DONE |
| P12-03 | Anti-recall/source-client mutation gate | `hub/capabilities.py`, WeChat integration, WebUI | P8-02 | `anti_recall` remains default-off and server-forced unavailable. Source-message edit/insert/delete IPC also routes through the main-process fail-closed gate and never invokes `chatService` until a separately reviewed backup/rollback flow is approved. The pinned WeFlow trigger path delegates installation to an external WCDB native component absent from this repository, so trigger semantics, rollback, schema/version compatibility and backup guarantees cannot be verified. Do not expose install/uninstall until the native implementation is inspectable, isolated-database install/check/uninstall/idempotence/rollback tests pass, the user separately approves source-client mutation, and each operation shows the exact account/database and backup/restore impact before execution. No real source DB was modified. | NOT_STARTED |
| P12-04 | Moments delete/hide interception gate | WeChat integration, WebUI | P8-02 | Moments anti-delete/hide interception is distinct from chat anti-recall and is not implemented. Keep unavailable until its native implementation is inspectable, source-client mutation is separately approved, and isolated-database backup/install/check/uninstall/idempotence/rollback behavior is verified. No real source DB was modified. | NOT_STARTED |

**P11 QQ relationship media alias follow-up (2026-10-14):** Objective media
achievement filters now recognize legacy QQ `extra.media` entries and the
provider's `kind`/`k` category fields when `type` is absent or empty. The
synthetic analytics suite passes 26 tests, including image/voice alias counts;
full media parity and real-cache acceptance remain open.

**P11 export and P12 avatar follow-up (2026-10-12):** SQL message exports now
write canonical message IDs instead of null identities, verified by executing
the generated SQL against a synthetic database. The avatar UI accepts only the
exact local `hub-avatar://wechat/<64 lowercase hex>` handle and maps it to the
same-origin Hub avatar endpoint; synthetic URL validation/rendering and HTTP
tests pass. This does not constitute real-account browser acceptance. P11 full
WeFlow resource/decryption parity and real-device media acceptance remain open.

**P12-01 scheduled-export follow-up (2026-10-13):** The earlier OFFSET wording
above is superseded. Scheduled exports now use ascending keyset batches from the
transactionally consistent SQLite snapshot and stream all nine formats directly
to the atomic temporary file, including XLSX and ChatLab. ChatLab members are
queried in stable first-message order and serialized incrementally. Focused
tests cover concurrent ingest, cancellation cleanup, every format, and fixed
writer memory over 3,000 synthetic messages. Real account/browser acceptance
remains open.

**P11-02 resource-retention follow-up (2026-10-14):** Resource ZIP jobs now
allow only one queued/running/cancelling job per manager, retain at most 64
terminal records, and bound completed archives by the task budget plus 2 MiB
of ZIP overhead. The actual archive size is checked before publishing its
download URL. Startup cleanup removes only exact tool-owned orphan job folders.
The focused resource suite passes 15 tests; real-device and full resource/
decryption parity remain open.

**P11 MCP sticker retrieval follow-up (2026-10-14):** `get_attachment` now
normalizes only WeChat `raw_type=47` messages without indexed child attachments
to the image reader, reusing the exact-account local emoji cache resolver and
the existing MCP size, signature, and cache-root checks. Unknown sticker types
and cache misses remain rejected. The MCP image-content contract regression
passes; no real cache was read in this change.

**P12 notification permission follow-up (2026-10-14):** Returning to a visible
or focused tab rechecks notification permission and reconnects the message event
stream when appropriate, so a revoked permission does not leave stale browser
state. Synthetic click-target, lock, and permission tests pass; real-device
notification acceptance remains open.

**P12 notification payload minimization follow-up (2026-10-14):** the SSE
message data now contains only the platform, account, conversation ID, title,
and kind used by the UI. Sender name, timestamp, message type, and internal row
ID are omitted from JSON; the row ID remains only in the SSE event ID for
reconnect. A focused HTTP regression verifies the exact field set. Real desktop
notification delivery remains open.

## Phase 13 - Additional Platform Sending

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P13-01 | WeChat/WeCom send adapters | `integrations/`, Hub, WebUI, MCP | P9-01, P10-01 | Personal WeChat sending is implemented as a default-off `wechat_send` capability: Win32 `ctypes` drive the already-open conversation window of the logged-in client, with no injection, DLL hook or client database write, and delivery is only claimed when a read-back of the cached message rows shows the same text from the local account. Real delivery on a real conversation remains unverified. WeCom has a self-contained standard-library TLS WebSocket adapter for independent bot-identity Markdown, image, file, AMR voice, and video messages; it never impersonates the logged-in employee. Capability defaults off; bot credentials use current-user Windows DPAPI; the browser uploads only a user-selected file to a loopback endpoint, where allowlisted extension/signature/size checks create an opaque local attachment ID. Preparation is durable and every text/media delivery requires request-bound one-time UI approval; attachment bytes are revalidated before upload and removed after delivery/rejection. The stdlib protocol follows upload init/chunk/finish with matching type and sends the returned `media_id`; chunks are 512 KiB, up to 100. MCP remains text-prepare/status-only and cannot upload, approve, or deliver attachments. QQ APIs cannot access bot requests. Ambiguous send receipts become unknown and are never retried. Synthetic protocol, attachment, HTTP-route, WebUI and MCP regressions pass. No real bot credentials/device delivery, target reachability or official acknowledgement behavior has been verified; employee-identity WeChat sending and the personal WeChat route remain unverified against a real conversation, so no real delivery is claimed for either channel. References: [official WeCom AI bot SDK protocol](https://github.com/WecomTeam/aibot-node-sdk), [official upload/send instructions](https://github.com/WecomTeam/wecom-unified/blob/main/skills/wecom-unified/references/wecomcli-message.md). | IN_PROGRESS |

**P13 clipboard-preservation follow-up (2026-10-14):** personal WeChat text
entry now uses Unicode keyboard events instead of replacing the system
clipboard. Synthetic sender tests verify the existing clipboard value is
untouched on successful composition and a pre-send failure, and validate the
native input structure size. Real WeChat composition and delivery remain
unverified.

**P13 WebUI route follow-up (2026-10-14):** the personal WeChat composer now
tags requests as `wechat`, so prepare/review/approve/reject use the WeChat
approval endpoints instead of the QQ endpoints. The conversation inspector
also names the selected platform correctly. Focused WebUI regressions pass;
real WeChat sending remains unverified.

**P13 attachment-upload memory follow-up (2026-10-14):** the loopback
attachment endpoint now streams selected request bytes to staged storage in
1 MiB chunks instead of buffering and copying the full upload. Hashing, text
encoding checks and cleanup are covered by synthetic tests. **Superseded by
the P13 stream-send follow-up below:** current code revalidates the staged file
in bounded reads, rewinds the same handle, and uploads 512 KiB protocol chunks;
it does not load the full staged file into memory. No real bot delivery was
performed.

**P13 WeChat receipt-baseline follow-up (2026-10-14):** if the local message
reader cannot establish a pre-send watermark, delivery now fails before GUI
input. An empty valid baseline remains distinct from a failed read, preventing
an older duplicate from being accepted as proof of a new delivery. Synthetic
regressions pass; real WeChat delivery remains unverified.

**P13 exact-window identity follow-up (2026-10-14):** the personal WeChat sender
now accepts only an exact contact title or exact fixed `chat history` title;
arbitrary suffixes can no longer match a different conversation by prefix.
Synthetic title checks pass without opening a client or sending a message. Real
window-title variants and delivery remain unverified.

**P13 ambiguous-target and draft review follow-up (2026-10-15):** the sender
now rejects a display name that resolves to multiple exact local contacts before
GUI input. The per-message approval warning explicitly says that any unsent
text in the target composer will be cleared. Synthetic reader, sender, and UI
checks pass; live window and delivery acceptance remain open.

**P11/P13 media follow-up (2026-10-14):** the media gallery and preview modal
now recognize HEIC/TIFF MIME types and show an explicit browser-unsupported
message with an original-download link instead of a broken inline image. The
original remains served as an attachment and available to batch export/MCP.
QQ cache gallery MIME now reaches the tile and preview path from indexed
metadata or the media resolver; HEIC resolution returns the original filename,
and `/local-media` serves HEIC as `image/heic` with attachment disposition.
Synthetic HTTP tests verify MIME, download name and byte-for-byte original
delivery. P13's stream-send path was verified against `open_for_send` and its
bounded 512 KiB sender reads; synthetic media/send suites pass. No real media
cache or bot delivery was accessed.

**P13 bot heartbeat follow-up (2026-10-14):** After at least 25 seconds idle,
the standalone WebSocket sender issues a protocol `ping` before its next
command. Ordering and timeout behavior pass synthetic tests; this does not
verify a real bot connection or delivery.

**P13 WebSocket receive deadline (2026-10-13):** A single receive now uses
one monotonic deadline across fragmented frames and interleaved control frames,
so repeated pings cannot extend bot-send waits indefinitely. The synthetic
endless-ping regression passes; it does not verify real bot delivery.

**P13 media chunk regression (2026-10-12):** A synthetic 512 KiB+1 upload
now verifies ordered chunk indexes, byte-perfect reassembly, and exactly one
final bot send using the returned media ID. This closes the local multi-chunk
protocol regression only; real bot credentials, device delivery, target
reachability, and official receipt behavior remain unverified.

## Phase 14 - Release Acceptance

| ID | Task | Goal / modules | Depends | Implementation and acceptance | Status |
|---|---|---|---|---|---|
| P14-01 | Performance, backup, security and package audit | CI, docs, Windows package | P7-P13 | CI runs backup/restore and synthetic performance checks with broad catastrophic-regression ceilings, builds the Windows portable package, executes isolated Hub/MCP/source smokes, and writes a deterministic SHA-256 manifest over both packages. Source ZIP entries use ordinal path ordering and a fixed timestamp; the packaging smoke verifies repeated identical builds produce identical bytes. Privacy audits block local databases, caches, credentials and user-profile paths; only hash-pinned NapCat/QCE templates are allowed. The manifest regression suite binds hosted creation, pre-upload verification and downloaded-artifact verification to the same GitHub commit/run provenance. Local p377 evidence is recorded below. Hosted run 38547565569 on code commit `c38d5fb90f9195d348831932ef5fb93afe7b273f` and run 38550004873 on current commit `75fa73185b5b1acf960da35ef331901c3284deef` both passed the full Windows gate, portable and source package builds/smokes, pre-upload manifest verification, artifact upload/download, and downloaded-byte/provenance verification. Latest artifact `LocalMessageHub-windows-release` is 251,741,288 bytes and retained until 2026-10-21. | IN_PROGRESS |

**Hosted P14 checkpoint (2026-10-14):** Run 38544901625 passed the GCC
installation step and failed at `npm ci` because strict peer resolution rejects
the existing React 19 / `ansi-to-react` peer range. The WCDB compatibility
workflow already uses `--legacy-peer-deps`; the Windows packaging workflow,
package smoke, and build instructions now use that same lockfile setting. No
dependency versions changed. A hosted run and artifact round trip are still
pending.

Run 38545314515 then passed GCC and npm installation, but 788 Windows tests
reported 17 failures and 8 errors. Most were caused by temp paths alternating
between the runner's long profile path and its 8.3 alias. CI now pins
`TEMP`/`TMP` to the runner's `RUNNER_TEMP` before checks; its complete
test/build/artifact gate must be rerun before P14 can close.

Run 38546410777 failed before job creation because `runner.temp` was used in a
job-level environment block. CI now sets the temp variables from
`RUNNER_TEMP` in a PowerShell step after the runner starts. The next hosted run
will verify both the context fix and Windows tests.

**P14 acceptance status (2026-10-12):** The P14-01 row above is an earlier
checkpoint; its 478/20-test and p163/p164 figures are superseded by the local
p195/p198 refresh below: 497 Python tests, 2 mutation-gate tests, 21 WebUI
tests, and the 25,000-message benchmark (0.029 s first search, 1.862 s for 50
cursor pages, zero WAL failures). Hosted Actions and artifact round-trip remain
unverified, so P14 remains IN_PROGRESS.

**P14 QCE allowlist and complete local-gate refresh (2026-10-14):** The
portable builder now normalizes a supplied QCE SHA-256 digest and rejects
malformed, missing, or ambiguous whitelist anchors; package smoke tests cover
those cases. The complete local gate passed 504 Python tests, 4 source-mutation
gate tests, 27 WebUI test files, isolated package/privacy checks, and source ZIP
reproducibility. A separate 25,000-message benchmark passed (0.029 s first
search, 1.952 s for 50 cursor pages, no WAL failures). Hosted Actions and
artifact upload/download remain unverified; the broad worktree is still not
represented by a reviewed commit.

**P14 local p199/p202 refresh (2026-10-12):** Portable p199 was rebuilt from
the current worktree and passed staged privacy scanning, isolated Hub/UI/source
and read-only MCP checks, plus standalone MCP stdio initialize/tools/list/exit.
The 125-file sanitized source bundle p202 passed archive privacy and extracted
startup checks. The local-worktree manifest passed provenance and byte
verification for p199/p202 and run `local-20261012-p202` against base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; this is local evidence only, not
hosted CI or an uploaded artifact. P14 remains IN_PROGRESS.

**P14 upload round-trip follow-up (2026-10-12):** after uploading the 7-day
release artifact, CI downloads that same artifact and reruns the manifest
provenance and byte verifier against the downloaded portable directory and
source ZIP. The local fixture mirrors the CI package directory and
`LocalMessageHub-artifact-manifest.json` filename, and rejects a wrong commit
SHA or mutated downloaded bytes. The actual Actions upload/download
implementation remains subject to hosted-runner acceptance.

**P14 latest complete local gate (2026-10-12):** the latest full gate passed
427 Python tests, 17 WebUI tests, privacy and manifest fixtures, syntax/compile
and diff checks, and the 25,000-message benchmark (0.070 s first search,
2.596 s for 50 pages, zero WAL failures). This includes QQ merged-forward
attachment handling and the explicit cached-avatar MCP tool. Focused regressions
also verify QCE's `0x02`-prefixed SILK, ADTS AAC and
M4A-family audio brands, generic MP4 video rejection, failed-avatar fallback,
and exact-account MCP avatar bytes. All builds and byte verification
are local-worktree evidence only. Historical hosted run
38101275283 failed on missing CI cryptography and a runner-specific path
assertion; the current worktree contains fixes for both, but no new hosted run
or artifact upload has been triggered.

**P14 local package refresh (2026-10-12):** after the QQ location-card and
avatar/WXGF bounds follow-ups, portable v124 and the 114-entry sanitized source
bundle passed their isolated Hub/UI/API/MCP smokes and privacy scan. Manifest
`LocalMessageHub-artifact-manifest-local-v124.json` binds both package outputs
to local run `local-20261012-p14-v124`; this is not a hosted Actions run or
signature. The unverified bundled FFmpeg binary was excluded; WXGF conversion
uses a compatible locally installed FFmpeg when present. Hosted CI and uploaded
artifact round-trip remain unverified.

**P14 local gate follow-up (2026-10-13):** the stable worktree passed
`scripts/check-local-hub.ps1`: 550 Python tests, 4 source-mutation tests, 24
WebUI tests, syntax/compile checks, package privacy/manifest/smoke fixtures, and
the synthetic 25,000-message benchmark (0.031 s first search, 1.437 s for 50
cursor pages, zero WAL failures). The first run overlapped a parallel P11 file
write and failed four report checks; focused analytics/MCP/HTTP regressions and
the subsequent complete gate passed after the write settled. The notification
click-target regression is now included in the aggregate gate. No new portable
package or hosted Actions run was produced; those release checks remain open.

**P11 contacts follow-up (2026-10-12):** WeChat contacts now classify official
accounts only when the username starts with `gh_` and the observed
`contact.local_type` equals 1, matching the pinned WeFlow rule. The reader joins
the local `biz_info` type into the fixed `subscription`/`service`/`enterprise`
enum; UI type filtering and contact export use only that normalized value.
Current classified WeChat contact records can be exported, but article history
and full profile coverage are not included. Synthetic provider/store/API/export
checks pass. Real-account schema acceptance remains open.

## Current Delivery Gate

Telegram Desktop JSON remains import-only; native sync is the three local
readers (WeChat, WeCom, QQ), with source-specific checkpoint/replay contracts.
Phases P0-P8 and synthetic P9/P10 acceptance are verified. P8 sync and Moments
catalog writes share a gate with capability changes: disabling sync cancels
in-flight source sync before a later database write, and disabling Moments
prevents an in-flight catalog read from committing. Deterministic regressions
cover both races. P9-02 now has a
real loopback-socket test against an ephemeral synthetic HTTP server, but no
live NapCat runtime or QQ account acceptance has occurred. QQ history
normalization now includes bounded merge-forward previews, face/reply, wallet
summaries and call-record content in the existing renderer, with focused
synthetic coverage; actual NapCat/device acceptance remains open. P11 media/resource
coverage is bounded to locally resolvable attachments and retains the documented
full WeFlow resource/decryption parity gaps. The media library can be opened
from a chat and scoped to that exact conversation; Store/API tests verify the
filter excludes Moments and unrelated conversations. P12 local export, body-free
notifications, WebUI/API lock, recognized-schema Moments and content-free
relationship metrics have synthetic coverage. This Windows installation's
`sns.db` and `head_image.db` schemas were verified by cached-key-only read-only
inspection; exact-username JPEG avatar caching now has bounded synthetic
coverage. MCP contact lists retain avatar references as metadata, while an
explicit `get_contact_avatar` call returns only a signature-checked image from
the exact contact's existing local cache. QQ and WeCom currently provide avatar
URL metadata only; no verified local byte-cache reader exists for these
platforms, so uncached avatars use the initials fallback. Real-account avatar rendering and
other-version schema/cache acceptance remain open. Moments now has an explicit-sync per-account SQLite
catalog whose cached media now joins the unified SQLite media library and
bounded batch ZIP export; MCP can return explicitly requested cached Moments
images and signature-checked MP4/WebM videos via the search-issued local token,
using image blocks or embedded video resources and an 8 MiB response cap. The
lock does not govern independent MCP stdio.
P13 now has an opt-in WeCom bot-identity adapter for Markdown and validated
image/file/AMR voice/video attachments, with per-message local approval; it is
not an employee sender. Protocol and UI tests are synthetic only: no real
credentials, account, device, or delivery were used, and live receipt behavior
remains unverified. Personal WeChat sending remains unavailable.
An isolated headless Edge run against temporary synthetic data verified P12
unlock, notification permission timing and conversation navigation, body-free
notifications, wheel-based idle refresh, and SSE revocation after lock; it did
not access the user's browser profile or any source-app account. The separate
schema audit did not print message, contact, key, or image content.

P6-06 remains complete; live QQ login and message delivery remain separate P9
acceptance. No source-app account data/config is staged.

Package inventories and provenance are recorded by the generated manifests
under `output/windows`; local `build_context=local-worktree` is not hosted
release provenance. Source and portable packages exclude local message
databases, account settings, and runtime state. The full local gate most
recently passed 427 Python and 17 UI tests, package privacy/manifest fixtures,
syntax/compile checks, and the
25,000-message benchmark (0.070 s first search, 2.596 s for 50 pages, 0.077 s
backup, 0.220 s restore, zero WAL failures).
Synthetic regressions verify authenticated loopback HTTP transport, 401 and
timeout-no-retry behavior for QQ sending, plus multibyte UTF-8 Moments XML byte-
limit enforcement. WebUI retention tests cap
loaded messages at 500 after 10,000 browsed; payload and markup reductions are
fixture metrics, not whole-process RSS claims. Details and before/after
measurements are in [`performance.md`](performance.md).

P0-P8 and P10 are complete (10 of 15 phases); P9 and P11-P14 remain in progress
or awaiting external/live acceptance. Hosted CI has not run on the current
reviewed changes; the latest hosted run on
the old `main` commit failed for a missing CI dependency and a path-sensitive MCP
config assertion, both fixed locally. No changes have been pushed. The portable
package now passes its full local EXE smoke on this host, but P14 remains
IN_PROGRESS pending reviewed hosted CI and artifact upload, in addition to P9
real QQ acceptance, P11 parity gaps, P12 real-account rendering/cache acceptance, and a verified
authorized P13 sender path. These requirements do not claim
zero-rescan synchronization or equal historical coverage across platforms;
QQ roaming remains bounded by QCE/Tencent limits. WeFlow baseline and gaps are
tracked in [`weflow-gap-matrix.md`](weflow-gap-matrix.md).

**P14 local package refresh checkpoint (2026-10-12):** v102 packages include the
QQ face/reply normalization, BMP MCP image support, SQL message-ID correction,
and local avatar URL mapping. Portable and source privacy scans, isolated UI/API
and standalone MCP smokes passed. The generated local manifest verified the
portable directory and source ZIP byte-for-byte against HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `build_context=local-worktree`;
this is local evidence only. Hosted CI and upload round-trip remain unverified.

**P12 roster-avatar follow-up (2026-10-12):** WeCom group-member results now
reuse the matching user ID's avatar from the already-loaded local contact
directory when the group roster has no avatar field. A focused synthetic
adapter regression passed. This does not fetch remote avatars; uncached QQ and
WeCom avatars still use the UI fallback, and real-account/browser acceptance
remains open.

**P11/P12 local media and avatar follow-up (2026-10-12):** QQ cached-audio MCP
signature detection now recognizes QCE's ADTS AAC (`audio/aac`) before generic
MPEG audio and M4A-family ISO-BMFF audio brands (`audio/mp4`), while rejecting
generic `isom` video as voice; focused synthetic MCP cache tests passed. Avatar
images that fail to load are removed so the existing initials fallback is
visible, with a focused WebUI regression. No real accounts or remote media were
used; full WeFlow resource parity and real-browser/account acceptance remain
open.

**P14 local package refresh (2026-10-12):** portable v115 and sanitized source
v115 include NapCat atomic-config, QCE M4A, and exact-identity cached-avatar MCP
support. Portable EXE/UI and standalone MCP stdio smokes, source privacy and
extracted API/MCP smokes, and local manifest byte/provenance verification
passed. Hosted CI and artifact upload/download remain unverified.

**P9/P11 bounded history follow-up (2026-10-12):** QQ history now maps QCE
`markdownElement` into searchable text and bounded rich content, dropping raw
payload fields; QQ provider tests passed (31). WeChat cached video/file lookup
now resolves only the message timestamp's `YYYY-MM` cache directory, avoiding
recursive month scans and same-name cross-month selection; missing/invalid
timestamps fail closed and WeChat media tests passed (31). The unified local
gate passed 402 Python and 14 WebUI tests. These changes improve local parity
but do not close P9 live NapCat acceptance or P11 full resource/decryption and
real-device parity. P12 real-account/browser acceptance, P13 authorized sender
availability, and P14 hosted Actions/artifact round-trip remain open.

The local deliverables were refreshed as portable v116 and sanitized source
v116. EXE/UI/standalone-MCP and extracted-source smokes, privacy scan, and
local-worktree manifest byte/provenance verification passed; this is not a
hosted CI artifact.

**P9/P11 follow-up (2026-10-12):** QQ history normalization now maps QCE
`atType` 1/2 into bounded `@all`/member rich-content entries, while preserving
the source text for search and rejecting boolean type confusion. Annual reports
now include the top 10 inbound contacts per selected platform/account/year;
the API, WebUI and CSV expose display names/counts without account IDs, contact
IDs, or message bodies. The complete local gate passed 406 Python and 14 WebUI
tests, package privacy/manifest fixtures, and the 25,000-message benchmark.
The portable/source v119 packages now include these changes and the current
roadmap/log entries. Isolated
EXE/UI/standalone-MCP and extracted-source smokes, package privacy scans, and
manifest byte/provenance verification passed against HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, run
`local-20261012-roadmap-v119`, with `build_context=local-worktree`. These local
artifacts are not hosted CI outputs. P9 live NapCat, P11 full
resource/decryption parity, P12 real-account acceptance, P13 an authorized
sender route, and P14 hosted CI plus artifact round-trip remain open.

**P11/P14 local refresh (2026-10-12):** Cached WeChat type-47 stickers now
remain inline after local cache resolution, and group-roster responses are
discarded if the selected platform/account/conversation changes before they
return; both regressions are in the 16-script WebUI gate. The portable v120
bundle passed isolated EXE/UI and standalone MCP initialize/tools/list/exit
smokes. Sanitized source v120 passed archive privacy and extracted Hub/MCP
smokes. The local-worktree manifest verified both artifacts byte-for-byte and
bound them to commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` and run
`local-20261012-roadmap-v120`. This does not replace hosted Actions or a hosted
artifact upload/download round-trip; P14 remains IN_PROGRESS. The full local
gate remains 406 Python and 16 WebUI tests. P9 live QQ, P11 full media parity,
P12 real-account/browser acceptance, and a verified authorized P13 sender route
remain open.


**P9/P11 update (2026-10-14):** one freshly approved QQ read-only key scan
ended `not_found` within the configured 180-second/512 MiB bounds. No key was
saved and no QQ history import started. P11 annual-report parity now includes
the UTC+08 7×24 weekday/hour heatmap and longest consecutive active-day range
in the API, WebUI, and CSV; focused synthetic tests pass. Real QQ history and
remaining resource/decryption parity are still unverified.

**P9 latest scan result (2026-10-14):** a subsequent user-approved read-only
scan completed in about 19 seconds with `not_found`. The local database remains
`key_required`; no key was saved and no history import started.

**P9 retry (2026-10-14):** after the user's approval, the local Hub performed
one more scan of current-user QQ processes with its 180-second/512 MiB bounds.
It returned `not_found`; QQ remains `key_required`, no key was saved, and no
history sync/import started. A bounded negative scan does not establish that a
key is absent.

**P11/P13 local contract follow-up (2026-10-14):** relationship achievement
counts now normalize WeCom's localized attachment types (`图片`, `语音`,
`视频`, `文件`) consistently with the media library. WeChat send preparation
also validates `conversation_id` before using string operations, so JSON null
returns a client validation error instead of an internal error. The focused
analytics/sending/HTTP tests pass (43 total). P11 real-cache and P13 real-send
acceptance remain open.

**P11 QQ WMA cache follow-up (2026-10-12):** MCP returns QCE gallery WMA
resources as `audio/x-ms-wma` only when the account-bound opaque resource is
trusted as audio, its safe filename ends in `.wma`, and cached bytes carry the
ASF signature. Synthetic MCP/provider regressions pass for valid audio and
forged type/extension rejection. The local media route now permits this MIME
inline, and the WebUI offers an audio element plus an explicit download
fallback. HTTP and WebUI regressions verify those paths, but actual browser WMA
codec playback is not assumed. This covers one additional cached format; P11
full resource/decryption parity and live-device acceptance remain open.

**P14 local workflow contract follow-up (2026-10-12):** the existing package
manifest fixture now asserts the upload artifact name and package/manifest
paths, matching download name/path, step order, and verification of downloaded
portable/source/manifest inputs. The fixture passes locally. Hosted Actions and
artifact transfer are still unverified, so P14 remains IN_PROGRESS.

**P12 avatar-source audit (2026-10-12):** Code review confirmed the WeChat
`head_image.db` reader described above is present and tested. QQ QCE/NapCat and
WeCom readers expose avatar URL metadata but no verified local image-byte API or
cache layout; QCE's avatar export action is not treated as a read-only cache
reader. The WebUI remains cache-only and falls back to initials for unavailable
images. `avatar-and-attachments.md` now documents this platform boundary. No
personal data, source-client action, or network image request was used.
Portable v121 and sanitized source v121 were rebuilt with the corrected docs;
isolated startup/MCP smokes and local manifest byte/provenance verification
passed for run `local-20261012-roadmap-v121`. These are local-worktree
artifacts, not hosted Actions outputs.

**P11 V2 image-container validation follow-up (2026-10-12):** WeChat V2
image parsing now rejects truncated AES blocks and XOR tails extending beyond
the container before decrypting or slicing payload data. The malformed-length
regression and existing explicit-key decrypt fixture pass (32 focused media
tests). This improves local parser correctness but does not establish full
WeFlow format parity or real-account cache acceptance; P11 remains IN_PROGRESS.

**P9 paging validation follow-up (2026-10-12):** The shared QQ history reader
now requires exact integer `limit`/`offset` values, rejecting Python booleans
and floats before issuing a QCE request. Its focused provider regression passes;
live QQ/NapCat history and media acceptance remain open.

**P14 local acceptance refresh (2026-10-12):** The current worktree gate passed
408 Python tests, all 16 WebUI tests, privacy and manifest fixtures, syntax and
compile checks, and the 25,000-message benchmark (0.066 s first search,
2.587 s for 50 cursor pages, 0.077 s backup, 0.216 s restore, zero WAL
failures). Portable and sanitized source v123 passed isolated Hub/UI/API and
standalone MCP smokes, archive privacy checks, and a local upload/download
directory round-trip against the byte/provenance manifest. Run
`local-20261012-p14-v123`, `build_context=local-worktree`, is not hosted Actions
evidence; P14 remains IN_PROGRESS pending real hosted CI and artifact upload.

**P14 local package refresh (2026-10-12):** Portable v125 and sanitized source
v125 were rebuilt after the WXGF decoder distribution decision and docs/tests
updates. Both packaged startup paths and read-only MCP smoke checks passed; the
source ZIP privacy/extracted-package check and local artifact manifest
provenance/hash verification also passed for `local-20261012-p14-v125`.
`build_context=local-worktree`; this does not satisfy hosted Actions or its
artifact upload/download acceptance, so P14 remains IN_PROGRESS.

**P11/P12 offline validation follow-up (2026-10-12):** WeCom media-context
parsing now skips malformed non-object rows and handles invalid MD5 field types
without aborting the snapshot parse. Relationship achievements now accept only
explicitly supported private conversation kinds; unknown kinds fail closed.
The focused combined WeCom/analytics/server suite passed 161 tests. These
synthetic results do not replace real-device media/cache acceptance.

**P14 v126 local refresh (2026-10-12):** v126 includes the P11/P12 follow-up
above. Portable executable and standalone MCP smokes passed; the sanitized
source archive passed privacy/extracted startup checks; manifest provenance and
package hashes verified for `local-20261012-p14-v126` (`local-worktree`). This
supersedes v125 locally, but not hosted Actions/upload acceptance; P14 remains
IN_PROGRESS.

**P11/P13 follow-up (2026-10-12):** WXGF conversion now passes a 64 MiB + 1
byte FFmpeg output-file cap as well as rejecting any result above 64 MiB on
readback; synthetic tests cover the command option and oversized fallback.
The P13 API audit found only WeCom app-identity send APIs, bounded customer-
service replies, and WeChat Official Account passive replies, not a supported
personal-client send endpoint for arbitrary existing chats. P13 therefore
remains NOT_STARTED and disabled rather than shipping a simulated adapter.
P11 still needs real-device cache/schema acceptance and complete resource parity.
The full local gate passes 415 Python tests and all 16 WebUI tests; privacy/
manifest self-tests, syntax checks, benchmark and `git diff --check` also pass.
Hosted Actions remains unverified.

**P14 v127 local refresh (2026-10-12):** v127 packages the WXGF output-file
limit and P13/P11 audit notes. Portable/standalone MCP and sanitized source
smokes passed; the local artifact manifest verified package bytes and
provenance for `local-20261012-p14-v127`. This remains local-worktree evidence;
hosted Actions and upload/download acceptance are still pending.

**P11/P12 offline follow-up and P14 v128 (2026-10-12):** QQ/WeCom attachment
metadata is now represented in CSV, HTML, XLSX, JSON, SQL, ChatLab, Markdown and
TXT exports using an allowlist that omits local paths and temporary URLs; SQL
resources use a separate table to preserve compatibility with existing message
tables. Synthetic export cases pass. WeChat image completeness checks now honor
WebP/BMP container lengths, and Moments queries no longer assume SQLite rowid;
focused WeChat media and Moments tests pass. The full local gate passed 418
Python tests, 16 WebUI tests, privacy/manifest fixtures, syntax checks and the
25,000-message benchmark. Portable and sanitized source v128 passed isolated
startup/MCP and privacy smokes; the local manifest verified package hashes for
`local-20261012-p14-v128` (`local-worktree`, base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Source ZIP SHA-256:
`3089005E76FE04544FCEE9997544D2A706CB675ABD5E20C5CD2A635CBB87738C`.
No live-account data was used. Real device/cache acceptance and hosted Actions
artifact upload/download remain unverified, so P11, P12 and P14 remain in
progress.

**P9/P12 follow-up and P14 v129 local refresh (2026-10-12):** QQ call records
with `avRecordElement` normalize as system messages; bounded nested image/file/
video/voice metadata in live QQ merged-forward previews is retained with URLs,
paths and raw QCE IDs excluded, and the WebUI labels it `未缓存` without a
download action. Moments author name/avatar initials fall back to the username
when no nickname exists; location parsing retains finite coordinates,
`poiAddressName`, and a separate label. The full local gate passed 420 Python
tests and 16 WebUI tests, privacy/manifest fixtures, syntax checks and the
25,000-message benchmark (0.068 s first search, 2.606 s for 50 cursor pages,
0.077 s backup, 0.216 s restore, zero WAL failures). Portable v129 passed
isolated Hub/UI/three-source/read-only MCP and standalone MCP stdio smokes. The
sanitized 114-file source archive passed privacy and extracted startup checks.
Manifest provenance and package hashes verify for
`local-20261012-p14-v129` (`local-worktree`, base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`); source ZIP SHA-256:
`B35BBEE5248F16AEB2BF528C52BAE71C440919A8AB9821C3E1C32B07A1BE8EAF`.
Nested QQ forwarded attachments are metadata-only and remain unavailable until
a bounded local resolver is implemented; no real account/client was accessed.
Real QQ/Moments device/cache acceptance and hosted Actions upload/download
remain outstanding.

**P14 v130 local refresh (2026-10-12):** Rebuilt portable and source packages
after the roadmap and local-gate count were synchronized. Portable v130 passed
the isolated Hub/UI/three-source/read-only MCP and standalone MCP stdio smokes;
the 114-file source archive passed privacy and extracted startup checks. The
manifest verifies for `local-20261012-p14-v130` (`local-worktree`, base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Source ZIP SHA-256:
`B4C71624512B1B7367C94E4F74EB44C8B9682D135EBCD46BBC5D69E0779E8FC7`.
This remains local package evidence; hosted Actions and upload/download are
unverified, so P14 remains IN_PROGRESS.

**P11 WeChat file dispatch follow-up (2026-10-12):** The WeFlow media downloader
already supports file localTypes `49`, `34359738417`, `103079215153`, and
`25769803825`, but the Hub provider routed only `49`, leaving three supported
file variants unavailable to media preview/export. The provider now dispatches
all four through the existing account-bound, read-only local downloader; a
synthetic provider regression covers each type. Focused WeChat media tests pass.
Real account/cache acceptance and complete cross-platform resource parity remain
open; local v130 packages predate this follow-up and must be refreshed before
release.

**P9/P11 QQ forward attachment follow-up (2026-10-12):** Added explicit,
on-demand expansion and download for indexed QQ merged-forward attachments.
NapCat access is limited to fixed local bridge calls; account/message identity,
opaque 10-minute handles, 50-resource/50 MiB per-file/512 MiB active-cache
bounds, signature checks, and Hub-cache containment are enforced. The UI shows
truncation, limits image reads to four concurrent requests, and aborts requests
when leaving the chat. CSP now permits locally-created blob image previews.
Silk/unknown formats remain unsupported. The full local gate passed 425 Python
tests and 17 WebUI tests; the 25,000-message benchmark had 0.068 s first search,
2.609 s for 50 pages, and zero WAL failures. No real NapCat account was queried;
P9/P11 live-device/cache acceptance and P11 full resource-library parity remain
open. Refresh local packages before release; hosted Actions is still unverified.

**P14 v131 local refresh (2026-10-12):** Portable v131 passed isolated Hub/UI,
three-source, read-only MCP, and standalone MCP stdio smokes. The sanitized
115-file source archive passed privacy and extracted startup checks. Manifest
verification passed for `local-20261012-p14-v131` (`local-worktree`, base
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Source ZIP SHA-256:
`8B4F67A23ACB9F3A6AF904765D68AB6B845A52C3861850FB5BF15AF227500F54`.
This is local-worktree evidence only; hosted Actions and artifact upload/download
acceptance remain outstanding.

**P9/P11 MCP QQ forward media follow-up (2026-10-12):** Added paired
`list_qq_forward_attachments` and `get_qq_forward_attachment` MCP tools. The
first returns safe metadata and short-lived opaque handles for indexed
attachments; the second returns only the explicitly selected item. Both bind
to account/conversation/message scope and use the fixed local NapCat/QCE bridge;
MCP bytes are capped at 8 MiB and paths/raw bridge identifiers are never
returned. The existing `media` capability gates both tools. Focused
MCP/provider/server regression tests pass. No real QQ account was queried; keep
P9/P11 live acceptance and full WeFlow resource parity open.

**P14 v133 local refresh (2026-10-12):** The updated portable package passed
isolated Hub/UI, three-source, read-only MCP, and standalone MCP stdio smokes;
the sanitized 115-file source bundle passed privacy and extracted startup
checks. Manifest verification passed for `local-20261012-p14-v133`
(`local-worktree`, base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). The portable
package includes the QQ forward MCP reader. This remains local-worktree
evidence; hosted Actions and artifact upload/download acceptance remain open.
The current source archive is p134 and manifest v134 binds it to portable p133;
both package byte checks pass.

**P14 v137 local refresh (2026-10-12):** Portable p135 and sanitized source
p137 include the final paired QQ merged-forward MCP attachment tools and the
current roadmap/development log. Manifest v137 byte/provenance verification
passed for both exact package inputs (`local-20261012-p14-v137`,
`local-worktree`, base
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). The current local gate passed 427
Python and 17 WebUI tests, package privacy/manifest fixtures, syntax/compile and
diff checks, and the 25,000-message benchmark. This does not satisfy hosted
Actions or uploaded-artifact round-trip acceptance; P14 remains IN_PROGRESS.

**P14 v140 local refresh (2026-10-12):** Portable p140 and the 116-file
sanitized source bundle include the current WeChat location renderer and send
settings fix. Both passed isolated startup; portable MCP stdio initialize/tools/
list/exit, package privacy scan, manifest byte/provenance verification, and
manifest fixture checks passed. Build context is `local-worktree` on base
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; this is not hosted Actions or
upload/download acceptance. Full-suite status remains the prior 427 Python / 17
WebUI gate; this turn verified 134 server tests, 39 QQ provider tests, and the
affected location, QQ rich-content, and send-history UI regressions. The script
now invokes 18 WebUI regressions; a new full-suite run remains pending.

**P11 WeChat location cards (2026-10-12):** WeChat `local_type=48` messages
normalize bounded `<location>` XML attributes into Hub-owned title, address,
and finite in-range coordinates. The WebUI renders an escaped offline card;
there is no map link, remote tile, or network request. Parser and renderer
synthetic checks pass, including malformed/entity/oversize XML, coordinates,
escaping, and the no-external-link boundary. Real-account schema/display
acceptance and full WeFlow media parity remain open; P11 stays IN_PROGRESS.

**Latest local gate and p141 refresh (2026-10-12):** the full gate at that
refresh passed 431 Python and 19 WebUI tests, privacy/manifest fixtures,
syntax/compile and diff checks, and the 25,000-message benchmark (0.069 s first
search, 2.683 s across 50 cursor pages, zero WAL failures). Portable p141 and
the 117-file source bundle passed isolated startup, three-source UI, read-only
MCP, standalone MCP stdio, privacy and manifest byte/provenance checks. Artifacts
are local worktree builds only. The later parallel implementation gate passed
434 Python and 19 WebUI tests and is recorded in the development log. Hosted
Actions, GitHub upload/download round-trip, real account/browser acceptance,
full WeFlow media parity, and the gated native source-mutation features remain
outstanding.

**P11/P14 current follow-up (2026-10-12):** fixed WeChat file-media dispatch
for the three 64-bit `local_type` values already allowlisted by the local
resolver; masking them to 32 bits had made them unreachable. A 4-type synthetic
dispatcher regression passes. Windows portable v143 and the refreshed 117-file
source bundle v144 both passed isolated startup checks; privacy scans passed
and the local manifest binds their package bytes to
`local-20261012-roadmap-v144` (`local-worktree`, base
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). The current full local gate passes
435 Python and 19 WebUI tests (25,000-message first search 0.066 s, 50 pages
2.573 s, zero WAL failures). Hosted Actions/artifact round-trip, real account
and browser acceptance, complete WeFlow resource parity, and separately gated
source-client mutation/send acceptance remain incomplete.

**P11 quoted-message follow-up (2026-10-12):** WeChat app-message type 57 now
extracts a bounded quote sender/summary from normalized local type-49 content;
unsupported quoted media uses placeholders. Parsing rejects DTD/entities and
oversize XML, and WebUI output is escaped. Both WeChat sync paging paths carry
the normalized quote. The server module passed 136 tests and the existing
WeChat render regression passed. Exact quote navigation and cached image/video/
voice previews are implemented; quoted card rendering and real-account
acceptance remain open. No real account was queried.

**P11 quote-navigation follow-up (2026-10-12):** `refermsg.svrid` is retained
as an exact decimal string and matched to the same account/conversation's
indexed `server_id` before opening the existing message-context view. The
reference message itself must be indexed and carry the same target ID; missing
or ambiguous targets fail closed. Synthetic parser/provider/HTTP/UI regressions
pass. Older indexed messages need a subsequent local sync to gain source-ID
mapping. A later follow-up adds cached image/video/voice previews with the same
account/conversation and source-ID checks; quoted card previews and real-account
acceptance remain open.

**P11 quoted-media follow-up (2026-10-12):** `/api/messages` now attaches a
preview only when the quote's source ID uniquely resolves to an indexed WeChat
message in the same account and conversation. It reuses the local media
resolver and returns only a same-origin Hub token; it never triggers a source
sync/download or exposes a path/remote URL. Missing cache, unsupported media,
disabled media capability, and cross-account/ambiguous references remain
placeholders. Focused HTTP/UI tests and the full local gate pass; quoted card
previews and real-device cache parity remain open.

**P14 local v146 refresh (2026-10-12):** Windows portable v146 includes the
quoted-message implementation and passed staged privacy scanning, isolated
Hub/UI/three-source and read-only MCP checks, and standalone MCP initialize/
tools/list/exit. The 117-file source v146 passed archive privacy and extracted
startup checks. Manifest v146 verified exact portable/source bytes and
`local-worktree` provenance for base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The focused 136-test server module
and WeChat renderer regression passed. This is local evidence only; hosted
Actions/artifact round-trip, real-device/browser acceptance, WeFlow media
parity, and gated native mutation remain incomplete. After this record, the
source bundle and manifest were refreshed to v147 around unchanged portable
v146 so the bundle contains the current documentation.

**P14 local p195/p198 refresh (2026-10-12):** Portable p195 and sanitized
source p198 (125 files) include quoted-media previews and the latest
documentation.
Portable EXE/UI/three-source/read-only MCP and standalone MCP stdio checks,
source archive privacy/extracted startup checks, portable privacy scan, and
manifest byte/provenance verification passed for run
`local-20261012-p198` with `local-worktree` context and base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The integrated gate passed 497
Python tests, 2 mutation-gate tests, 21 WebUI tests, and the 25,000-message
benchmark (0.029 s first search, 1.862 s for 50 cursor pages, zero WAL
failures). Hosted Actions/artifact round-trip, real account/cache acceptance,
full WeFlow parity, and separately authorized source mutation/message delivery
remain unverified.

**P11 quoted-message follow-up (2026-10-12):** WeChat app-message type 57 now
extracts a bounded quote sender/summary from normalized local type-49 content;
unsupported quoted media uses placeholders. Parsing rejects DTD/entities and
oversize XML, and WebUI output is escaped. Both WeChat sync paging paths carry
the normalized quote. The server module passed 136 tests and the existing
WeChat location/render regression passed. Exact quote navigation and cached
image/video/voice previews are now implemented. Quoted card previews and
real-account acceptance remain open; no real account was queried.

**P9/P11 bounded media follow-up (2026-10-12):** QQ `liveGiftElement` now
normalizes to a 256-character `live_gift` summary and renders an escaped,
dedicated card; recipient and raw payload fields are discarded. Legacy
WeChat XOR image fallback now uses the explicit key, or the locally available
`cfg_dword` low byte, before retaining legacy candidates. Synthetic provider,
renderer, and media regressions passed (90 Python tests and two focused WebUI
tests). Live QCE/QQ and real-device media acceptance remain open.

**P14 local v148 refresh (2026-10-12):** Portable v148 passed staged privacy
scanning and the isolated Hub/UI/three-source, read-only MCP, and standalone
MCP stdio smokes. The 117-file source v150 passed archive privacy and extracted
startup checks. Manifest v151 verified exact package bytes and
`local-worktree` provenance for base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the package smoke fixture also
verified user-data environment isolation, empty storage, and server identity.
Hosted Actions and artifact upload/download remain unverified.

**P9/P11 local follow-up (2026-10-12):** QQ `structLongMsgElement` is reduced
to a bounded escaped summary; upstream `resId` and XML content are not exposed.
WeChat V1 DAT XOR output now requires a supported image signature before it can
be cached, so an incorrect key fails closed. Synthetic QQ/WeChat coverage and
the related QQ rich-content and forwarded-attachment UI regressions pass. Live
QCE/QQ and real-device WeChat acceptance remain open; P9/P11 stay
IN_PROGRESS.

**P11 image completeness follow-up (2026-10-12):** WeChat JPEG/PNG cache
completeness now strips only the documented bounded container footer before
checking the image end marker. Trailing non-footer bytes no longer make a
damaged original appear complete. The media synthetic suite passes; real
WeChat cache/version acceptance remains open.

**P12/P13 gate audit (2026-10-12):** The pinned WeFlow upstream commit
`2f23e72cebf7f309a0f4d129307d16d7b0cd41c3` and this repository contain only
WCDB wrapper bindings, not auditable native trigger source/library. P12-03/04
therefore remain unavailable pending a versioned, licensed, reproducible native
implementation plus isolated-database lifecycle/rollback tests and separate
user approval before any source-client mutation. The official WeCom AIBot
route is a robot identity, not verified delivery as the current employee;
personal WeChat has no verified supported local send route. At this audit
checkpoint P13 had not started and no send was attempted; the later same-day
follow-up below records the opt-in bot-identity text adapter and its remaining
live-acceptance limits.

**P14 local v150 refresh (2026-10-12):** Portable v150 passed Windows
isolated Hub/UI startup, three-source/read-only MCP, standalone MCP stdio,
QCE v6.3.2 hash checks, and staged privacy scanning. Sanitized source v154
passed archive privacy and extracted startup; manifest v155 verified its exact
bytes with portable v150 and `local-worktree` provenance for base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The package smoke, privacy, and
manifest fixtures also passed. Hosted Actions and uploaded-artifact round-trip
remain unverified. Source and manifest are refreshed after this entry to
include it.

**P14 local v149 refresh (2026-10-12):** Portable v149 and sanitized source
v152 passed isolated startup, three-source/read-only MCP, standalone MCP stdio,
privacy, and package-smoke checks. Manifest v153 verified exact package bytes
and `local-worktree` provenance for base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The synthetic package fixture
verified environment isolation/restoration, occupied-port handling, and
rejection of wrong-storage/nonempty-conversation fixtures. This is local-only
evidence; hosted Actions and artifact upload/download remain unverified, so
P14 stays IN_PROGRESS. The source bundle and manifest are refreshed after this
record to include the current documentation.

**P12-03/04 source-mutation boundary follow-up (2026-10-12):** The retained
Electron app had install/uninstall paths outside the Python Hub capability
gate. Its five main-process IPC handlers and all three Moments HTTP mutation
routes (trigger install/uninstall and post deletion) now fail closed; read-only
trigger status checks remain available. The destructive Moments delete control
is disabled in the UI with an explanation. The focused synthetic gate tests
cover IPC and HTTP responses and verify zero native/provider calls. No
source-client database was changed. These capabilities remain
unavailable pending an auditable native implementation and the separately
approved backup/rollback workflow. P14's latest local v157 package checks passed
for the Hub deliverables; hosted Actions and actual GitHub artifact download
remain unverified.

**P12 Moments row-deletion boundary follow-up (2026-10-12):** A second audit
found the ordinary Moments post-delete IPC and HTTP route bypassed the trigger
installation gate. They now use the same fail-closed source-mutation boundary;
the UI action is disabled. `node --experimental-strip-types --test
scripts/test-source-mutation-gate.cjs` passes all three tests, and renderer plus
Electron TypeScript checks pass. P14 local packages must be refreshed to include
this change; no source DB was touched.

**P14 local p203/p205 refresh (2026-10-12):** Rebuilt the portable package and
sanitized source bundle after the Moments mutation gate fix. The p203 portable
passed isolated UI/source/MCP startup checks; p205 source passed privacy and
isolated startup checks. A p205 local-worktree manifest binds both packages to
SHA-256, size, commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, and run
`local-20261012-p205`; verification against both package byte sets passed.
Hosted Actions and artifact upload/download are still unverified, so P14 remains
IN_PROGRESS.

**P9/P11 local follow-up (2026-10-12):** `/api/qq/login-status` now returns a
non-sensitive unavailable state when the local NapCat login API cannot be
reached instead of surfacing a generic HTTP 500; its successful response still
exposes only phase/online/availability. The affected server suite passed 137
tests. WeChat cached SILK export now streams SQLite `VoiceInfo.voice_data` in
1 MiB chunks into a same-directory temporary file before `fsync` and atomic
replacement, avoiding a second full-size in-memory BLOB; the focused media
suite passed 42 tests. No account or source database was queried by these
checks. NapCat live acceptance and P11 real-device/resource parity remain open.

**P14 local artifact round-trip follow-up (2026-10-12):** Portable v150, source
v155, and manifest v156 passed provenance/byte verification both in the normal
package directory and after copying all three into a separate local directory.
This verifies local copy integrity only; hosted Actions and GitHub artifact
download remain unverified.

**Latest local follow-up (2026-10-12):** P11 WXGF handling retries
WeFlow-style VPS-group candidates and prefers bounded embedded JPEG/PNG
signature extraction, now including embedded GIF/WebP/BMP; resource ZIP
cancellation is checked during 1 MiB-block compression. QQ forwarded images
identify BMP and QQ `faceIndex=0` remains a valid face. P12 avatar URL validation
rejects backslash paths that browsers could normalize cross-origin, and each
avatar render parses its URL once. P14 standalone MCP smoke receives temporary
user/app/source paths and a disabled loopback QCE URL; its regression verifies
isolation and parent environment restoration. The current-tree local gate
passed 454 Python tests, two source-mutation gate tests, 19 WebUI tests,
privacy/manifest and extracted startup/read-only MCP checks, and the 25,000-
message benchmark (0.067 s first search, 2.667 s for 50 cursor pages, zero WAL
failures). `npm run test:wcdb` passed 44 tests and `npm run typecheck` passed
renderer and Electron. The final local refresh for this entry is portable v162,
sanitized source v167, and manifest v168 with `local-worktree` provenance.
Hosted Actions and GitHub artifact upload/download remain unverified, so P14
remains IN_PROGRESS. P9 live NapCat/account validation, P11 complete
resource/decryption parity and device acceptance, P12 real-account/browser
acceptance, and P13 supported authorized identity delivery also remain open.

**Parallel local follow-up (2026-10-12):** QQ merged-forward media validation
now accepts both `#!SILK_V3` and QCE's `0x02`-prefixed variant as audio/SILK;
all 46 QQ provider tests pass. Package privacy scanning now includes redirected
`APPDATA`, with a synthetic external-to-profile path regression; that fixture
passes. `git diff --check` passes. WeChat media parity was re-audited; no
additional format expansion was made without an authoritative format
reference. Hosted CI and live-device acceptance remain outstanding.

**Export and account-lock follow-up (2026-10-12):** JSON export now removes
raw attachment aliases and emits only allowlisted resource metadata, closing a
path/URL/token leak from top-level attachment fields. Account-lock disable now
holds the state lock across password verification and configuration removal,
so a concurrent passphrase change cannot be deleted. Focused export (19),
account-safety (4), HTTP lock (1), QQ provider (46), and package privacy checks
pass. P11/P12 real-data/browser acceptance and P14 hosted artifact acceptance
remain outstanding.

**P14 package refresh (2026-10-12):** portable v163 and the 117-file source
bundle v169 passed local privacy and isolated startup checks; the portable
executable also passed standalone MCP stdio initialize/tools/list/exit. Local
manifest v170 verifies exact package bytes and `local-worktree` provenance for
commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. These local checks do not
verify hosted Actions or GitHub upload/download round-trip; P14 remains
IN_PROGRESS.

**P12 synthetic browser acceptance update (2026-10-12):** the earlier P12-01
row's statement that browser/pixel rendering remains open is superseded for
synthetic data. An isolated Hub served one synthetic conversation/contact and
PNG avatar; Edge headless rendered the avatar in the conversation row, message,
and inspector, and pixel samples confirmed visible non-empty pixels. The avatar
endpoint returned `200 image/png`. Real-account acceptance remains open; no
real account, source database, or existing local server was accessed.

**P9-02 receipt validation follow-up (2026-10-12):** the OneBot sender now
requires `message_id` to be an integer in the protocol's signed int32 range;
booleans, strings, floats, objects, and out-of-range integers are treated as
`unknown`, never as successful delivery. Focused QQ sender coverage passes.
Actual NapCat and real-account delivery acceptance remains open; no message was
sent during this check.

**P11 report range follow-up (2026-10-12):** annual and pair reports now both
offer an all-time or single-year range, matching the pinned WeFlow behavior.
All-time is represented as a missing year / `year: null`, not a fabricated
calendar year; pair-report queries and both CSV exports preserve the chosen
range. Focused analytics/server and WebUI export checks pass. P11 remains
IN_PROGRESS for the remaining media/device acceptance gaps.

**P14 latest local package refresh (2026-10-12):** portable v164 passed local
privacy and isolated startup/UI/three-source/read-only MCP/standalone MCP
checks; sanitized source v174 passed archive privacy and extracted startup.
Manifest v174 verifies both package byte inventories and `local-worktree`
provenance on base commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This
does not verify hosted Actions or an uploaded-artifact round trip; P14 remains
IN_PROGRESS.

The Windows package filters and privacy audit also reject the WeCom bot DPAPI
credential file, its `wecom-bot-outbox` directory, and `.blob` attachment files.
The source package required-file check and portable staging check pin the bot
credential, media-store, and sender modules. The synthetic privacy regression
passes; a package rebuild after this audit change and hosted artifact
upload/download remain pending.

**P11 WeChat quote MCP follow-up (2026-10-12):** added the read-only
`get_quoted_message_context` tool. It accepts the exact account, conversation,
and indexed reference message, derives the source identifier only from local
quote metadata, and returns the unique same-scope target context without that
raw identifier. Missing, ambiguous, or cross-conversation targets fail closed;
synthetic MCP coverage passes. Real-account acceptance remains open.

**P11 QQ cache audio preview follow-up (2026-10-12):** QQ's authenticated
resource gallery reports audio as `audio`, while the shared local preview
expects `voice`. The WebUI now normalizes that provider media kind at preview
time so cached audio opens in the audio player instead of the generic file
download view. A focused synthetic UI regression covers the player markup;
real QCE/cache playback remains unverified and P11 stays IN_PROGRESS.

**P8/P10 send challenge revocation (2026-10-12):** the latest integrated local
gate passes 479 Python tests, 2 source-mutation gate tests, 20 WebUI tests and
the package privacy/startup/MCP checks. The 25,000-message benchmark reports
0.066 s first search, 3.305 s for 50 cursor pages and zero WAL failures.
Disabling either sender now invalidates outstanding approval challenges for
both senders; the focused HTTP regression proves stale challenges cannot
deliver after re-enable. Package artifacts must be refreshed to include this
change; hosted Actions and upload/download acceptance remain open.

Disabling a sender revokes unconsumed challenges only. Durable pending requests
remain queued, and a delivery already authorized by the user cannot be
cancelled by disabling the capability.

**P14 send-revocation package check (2026-10-12):** portable p166 and
sanitized source p167 passed local isolated startup, privacy and MCP smokes;
manifest p168 verified package bytes and `local-worktree` provenance. The
source package and manifest are refreshed after recording this result.

**P11/P13/P14 continuation (2026-10-12):** schema v9 adds a composite
`(platform, timestamp)` index for platform-filtered newest-first message search.
On 90,000 synthetic rows, 30 searches took 0.034 s with the index versus
0.491 s without it; the integrated 25,000-message benchmark now reports 0.028 s
for first search and 1.961 s for 50 cursor pages. This adds a small local index
storage cost. WeCom bot delivery now deletes approved staged media if credential
lookup or attachment revalidation fails; synthetic regressions and the 483-test
local gate pass. Real-account avatar/media/browser acceptance remains
outstanding; live connection state and local endpoints are omitted from this
public roadmap.
The public Actions page currently shows only two historical initial runs, not
this dirty worktree; GitHub-hosted build and actual artifact download remain
unverified. P9 live NapCat, full P11 resource parity, P12 gated native features,
P13 real bot delivery, and P14 hosted artifact acceptance remain in progress.
That verification checkpoint used portable p174, sanitized source p175,
and manifest p176; the manifest verified package bytes and `local-worktree`
provenance. The P14 table's older p163/p164 and 478-test figures are historical;
this continuation records the newer 483-test result.

**P11/P13 cache-boundary follow-up (2026-10-12):** Moments cache resolution now
classifies AVIF/AVIS as AVIF and HEIC-family `ftyp` brands as HEIC rather than
video, and rejects truncated or invalid declared ISO-BMFF box sizes. WeCom bot
attachment staging rejects a redirected `wecom-bot-outbox` symlink at startup
and before cache operations. Combined focused Moments/provider and WeCom
credential/media verification passed 31 tests. Real-device media compatibility
and live bot delivery remain unverified; path checks do not eliminate a
concurrent symlink-swap race.

**P14 / local-source acceptance refresh (2026-10-12):** integrated validation
now passes 486 Python tests, 2 source-mutation gate tests, and 20 WebUI tests;
the synthetic benchmark reports 0.029 s first search and 1.384 s for 50 cursor
pages, with zero WAL failures. Real-account connection state and local endpoints
are omitted from this public roadmap. Portable `p179`, sanitized source `p182` (124 files), and
manifest `p183` pass package privacy, extracted startup/read-only MCP smoke,
and byte/provenance verification for base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Remaining live acceptance includes
WeCom/QQ account setup, visible avatar/media rendering, QQ NapCat QR login,
real WeCom bot delivery, full P11 resource parity, P12 gated native features,
and hosted Actions/upload-download. Packages are local-worktree artifacts, not
hosted CI releases.

**Provider compatibility follow-up (2026-10-12):** QQ history requests now
preserve group-chat routing when the canonical conversation uses `kind: group`;
WeChat avatar reads tolerate additive columns in `head_image`. The integrated
check now passes 488 Python tests, 2 source-mutation gates, and 20 WebUI tests.
The 25k synthetic-message benchmark completed 50 cursor pages in 1.825 s with
zero WAL failures. Final refreshed artifacts are portable `p190`, sanitized
source `p189` (124 files), and manifest `p191`; package privacy, isolated
startup/read-only MCP smoke, and byte/provenance checks passed. WeChat was
connected and synced locally at the user's request; no message bodies were
opened or transmitted. QQ and WeCom still need their own account authorization.

**P9 live connection attempt (2026-10-12):** A local QQ history attempt exposed
a Hub/runtime readiness mismatch. The account-level status and counters are
omitted from this public roadmap. No message bodies were opened or transmitted;
NapCat/QQ was not restarted or reconfigured. P9 live history acceptance remains
open.

**P9 live sync correction (2026-10-12):** The Hub/runtime readiness issue was
resolved for the local test path, but real-account sync counters and per-platform
connection states are omitted from this public roadmap. No message bodies were
opened or transmitted. Live QQ history and WeCom acceptance remain open.

**Latest connection and local gate refresh (2026-10-12):** Real-account sync
counters, per-platform connection states, and local endpoints are omitted from
this public roadmap. No message body was opened or transmitted by the agent.
MCP `list_media` now
discovers WeCom/QQ attachments on text-parent messages with safe resource
indices and parent URIs, and the send toolbar names the selected WeCom bot
identity correctly. The integrated local gate passes 492 Python tests, 2
source-mutation tests, 20 WebUI tests, package privacy/startup/MCP checks, and
the synthetic 25,000-message benchmark (0.029 s first search, 1.416 s for 50
cursor pages, zero WAL failures). Hosted artifact upload/download and the
remaining QQ/WeCom/live-device acceptance are still open. Portable `p193`,
sanitized source `p193` (124 files), and manifest `p193` were built from this
local worktree; package privacy, extracted startup/read-only MCP smokes, and
manifest byte/provenance verification passed. Hosted artifact download remains
unverified.

**P9 QQ paging timeout follow-up (2026-10-12):** A bounded live probe of six
incomplete checkpoints found five message-page requests timing out at 10.02-
10.03 seconds and one returning 500 rows in 9.81 seconds. The provider's
default request timeout is 10 seconds. QQ `/api/messages/fetch` pages now use
an independent 30-second cap; other QCE requests retain the configured default.
All 49 QQ provider tests pass, including a regression that verifies the longer
timeout is scoped to message pages. The running Hub has not loaded this change
yet, so full QQ history acceptance remained unverified at that checkpoint.

**P9/P12 local account acceptance continuation (2026-10-12):** Live login state,
sync counters, and avatar observations are omitted from this public roadmap;
real-cache media/decryption parity remains unverified. No message body was
opened or transmitted, no source database was modified, and no message was
sent. The latest integrated local gate passes 492 Python tests, 2 source
mutation-gate tests, 20 WebUI tests, `npm run typecheck`, and all 44
`npm run test:wcdb` tests; the 25,000-message benchmark reports 0.030 s first
search, 1.602 s for 50 cursor pages, 0.080 s backup, 0.238 s restore, and zero
WAL failures. The endpoint-specific 30-second message-page timeout is covered
by provider tests but does not prove full live history. No message body was
read. Windows packages have not yet been refreshed after the current worktree
changes.

**P9 targeted QQ recovery follow-up (2026-10-12):** `/api/sync` now accepts a
QQ-only `conversation_id`, validates it against the current online account's
conversation list, and scans only that conversation using the existing
checkpoint/transaction path. The chat header exposes this as “仅重新同步此 QQ
会话”, so a user can retry a failed or incomplete chat without rereading every
completed conversation. Synthetic tests cover invalid platform/identity,
exact-ID matching, the HTTP route, and one-conversation scan; the button and
request payload have a WebUI regression. This does not prove live-device
recovery; full QQ history remains unverified.

**P11 quoted-media preview follow-up (2026-10-12):** paged WeChat messages
now resolve a quote preview only when its source ID maps uniquely to an indexed
message in the same platform, account, and conversation. Image/video/voice
previews reuse the Hub's local media resolver and same-origin media token;
missing cache, disabled media capability, ambiguous identity, or unsupported
content remains text-only. No remote URL or source path is returned, and the
preview path does not trigger platform sync/download requests. Focused HTTP and
WebUI regressions cover account/conversation isolation, cache miss, HTML
escaping, and URL/path redaction. This closes quoted-message inline media
preview only; full WeFlow media parity and real-device cache acceptance remain
open.

**P11/P14 viewport media preview and local acceptance (2026-10-12):** the
media gallery now resolves regular WeChat image thumbnails only when their
tiles approach the viewport (240 px root margin). It uses the existing local
cache resolver at `mid` tier, caps concurrent work at three, and disconnects
the observer/aborts outstanding requests on page changes. QQ, WeCom, stickers,
video, and original-image fetches are not automatically resolved. The focused
fake-API regression covers offscreen deferral, visibility-triggered resolution,
the concurrency cap, and stale-page cancellation. The full local gate passed
499 Python tests, 3 source-mutation gates, 22 WebUI regressions and package
privacy/startup/MCP fixtures; the 25,000-message benchmark reports 0.038 s
first search, 1.391 s for 50 cursor pages, 0.074 s backup, 0.229 s restore,
and zero WAL failures. Portable p206, sanitized source p208 (126 files), and
manifest p208 passed local privacy, isolated startup/read-only MCP, and
byte/provenance verification. Hosted Actions and artifact upload/download are
still unverified. Real-account sync state and counters are omitted from this
public roadmap. No message bodies were opened or transmitted by these checks,
and no source-client database was modified. P11 full resource parity, P9 complete QQ/device acceptance, P12
native mutation acceptance, P13 real bot delivery, and hosted P14 acceptance
remain open.

**P11/P13 follow-up (2026-10-12):** chat-source media queries now include
WeChat type-47 stickers as a separate sticker category, while ordinary image
filters still exclude them. WeCom bot prepare binds the requested account to
the configured bot identity, and delivery revalidates the identity against
current credentials to fail closed after credential rotation. Focused
synthetic store, HTTP, and delivery regressions pass. These changes do not
close full P11 resource parity or P13 real-credential delivery acceptance.

**P11 MCP sticker filter follow-up (2026-10-12):** MCP `list_media` now
accepts and advertises the existing `sticker` media category. Synthetic
coverage verifies a WeChat type-47 row is returned for sticker filtering and
not ordinary image filtering. This is metadata-only and does not close full
WeFlow resource/decryption parity or real-device media acceptance.

**P14 current-worktree package refresh (2026-10-12):** rebuilt the portable
Windows package (`p211`) and sanitized 127-file source ZIP (`p212`) from the
current worktree. Portable UI, three-source API, read-only MCP,
and standalone MCP stdio smoke checks pass; the source archive passed privacy
and extracted-startup checks. Package manifest/byte verification and package
privacy/manifest/smoke fixtures pass. The manifest uses `local-worktree`
context using `p212`, with base commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; this
records the dirty local package inputs but is not a commit of them. Hosted CI
and artifact upload/download remain unverified, so P14 stays IN_PROGRESS.

**P11 account-scoped WeChat cache backup follow-up (2026-10-12):** WeChat media
cache identities now include the source account, and optional media backups
include only files in cache directories derived from indexed media rows in the
SQLite snapshot. Restore validates indexed identity, archive path, size and
SHA-256 before merging to the Hub cache. Legacy unscoped cache entries,
unreferenced cache directories and WeChat source-client files are excluded.
This supersedes the P11-02 table's earlier QQ-only backup description. Focused
backup/provider tests pass; real-account cache portability remains unverified,
so P11 remains IN_PROGRESS.

**P11 MCP analytics follow-up (2026-10-12):** MCP now exposes bounded,
content-free private/group conversation profiles plus annual and exact-scope
private pair reports, using the existing `advanced_analytics` and
`annual_reports` capabilities. Synthetic MCP coverage verifies account scoping,
group rejection for pair reports, disabled-capability hiding/direct denial, and
absence of message bodies. This closes the analytics-tool exposure gap only;
full WeFlow parity and real-data acceptance remain open. The later
`list_group_members` follow-up closes the previously missing roster MCP access.

**P14 package refresh (2026-10-12):** rebuilt the portable Windows bundle as
local p213 and the 127-file sanitized source archive as p215 after the final
MCP documentation update. Portable/source smoke, privacy scanning,
deterministic manifest byte/hash verification, and manifest/privacy/smoke
fixtures pass. Manifest p215 binds both artifacts and records base commit
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree` context; it
does not claim the dirty worktree is committed. A later documentation-only
roadmap correction is not yet included in those packages, so they need a
refresh before delivery. Hosted CI and artifact upload/download round-trip
remain unverified, so P14 stays IN_PROGRESS.

**P14 source-only refresh (2026-10-12):** sanitized source archive p216
contains 127 files and passes the privacy scan plus extracted Hub/WebUI,
three-source, isolated-storage, and read-only MCP endpoint smoke. It was built
before this status note and is not a paired release: the portable p213 and
manifest p215 predate the current roadmap edits. A complete portable/source
pair and manifest refresh remains required. The bundled QCE process is still
active and host free memory was about 1.9 GiB, so portable rebuilding was
deferred. P14 remains IN_PROGRESS.

**P11/P13 current-worktree follow-up (2026-10-12):** MCP now exposes the
existing group-member reader through bounded, account- and conversation-scoped
`list_group_members`; it is capability-gated and does not trigger sync. The
MCP suite (34 tests), focused roster/approval configuration tests (3 tests),
and diff whitespace check pass. WeCom bot credential rotation now revokes
only bot-platform approval challenges; global send disable still revokes all
pending challenges. Focused regression tests pass. No real delivery occurred.

**P9 local connection acceptance (2026-10-12):** real-account login state,
sync counters, process metrics, and local endpoints are intentionally excluded
from this public roadmap. Live account/media acceptance remains open;
source-client databases remain read-only. P9 device sync, P11 real
media/resource parity, P12 gated native features, P13 actual bot delivery,
and P14 hosted artifacts remain open.

**P14 current-worktree validation (2026-10-12):** `scripts/check-local-hub.ps1`
passed 509 Python tests, 4 source-mutation gate tests, 23 WebUI tests, package
privacy/manifest/smoke fixtures, syntax checks, and the 25,000-message benchmark
(0.030 s first search, 1.450 s for 50 cursor pages, 0.087 s backup, 0.237 s
restore, zero WAL failures). This does not build a new portable/source package.
The live bundled QCE process was using about 14.9 GiB and system free memory was
0.75 GiB, so package rebuilding was deferred to avoid exhausting the host.
On the live incomplete 8772 index, a random no-match `limit=1` search took 103 ms
cold and 5/2 ms warm across three probes, returning zero rows without reading
message bodies; this narrow lookup does not verify result rendering or full
search acceptance.
Hosted run 38101275283 failed before packaging due to missing CI `cryptography`
and a test tied to the local checkout name; both fixes exist in the dirty
worktree, not on the hosted SHA. Green hosted CI and artifact upload/download
remain unverified and need a reviewed, explicitly authorized push.

**P11 QQ forwarded WMA follow-up (2026-10-12):** multi-level QQ forward
attachments now accept `audio/x-ms-wma` only when the first 16 bytes match the
ASF Header Object GUID. The cached attachment and selected MCP read share the
existing account-bound opaque handle path; malformed signatures remain
rejected. The 52-test QQ provider suite, scoped Hub/MCP attachment tests, and
QQ forwarded-attachment WebUI regression pass. This closes one locally
resolvable format gap only; P11 full resource/decryption parity, real-account
cache availability, and browser WMA codec acceptance remain open.

**P14 current-worktree source refresh (2026-10-12):** source archive p218
contains 127 sanitized files and passed archive/extracted privacy scans plus
isolated WebUI, three-source, empty-storage, and read-only MCP startup checks.
This source-only artifact is not paired with a current portable package or
manifest; hosted Actions and artifact download remain unverified. P14 stays
IN_PROGRESS.

**P9 QQ login-status config follow-up (2026-10-12):** invalid local QCE URL
configuration now maps to the existing unavailable response instead of HTTP
500. The route regression is covered; no runtime restart or login was performed.

**P9 local QR handoff (2026-10-12):** restarting the local Hub with the
repository-managed runtime restored the loopback QQ login status and the UI
renders the QR flow. The QR is waiting for the user to confirm on their phone;
no real QQ login or roaming-history acceptance is claimed. P9 remains
IN_PROGRESS.

**P12 Moments parser follow-up (2026-10-12):** normal media field lookup now
skips the nested `livePhoto` subtree, preventing a Live Photo URL/thumbnail
from being duplicated as ordinary media while retaining its structured
`livePhoto` entry. Nine focused synthetic tests pass; real-account Moments
media/cache acceptance and broader P11 parity remain open.

**P11 export provenance follow-up (2026-10-12):** Markdown and TXT message
exports now include the exact account ID alongside platform, conversation ID,
and message ID. A two-account collision regression verifies both identities
survive rendering; broader resource-format and real-account acceptance remain
open.

**P11 cached video poster follow-up (2026-10-12):** WeChat media-gallery video
tiles now lazy-load an already-cached JPEG poster or thumbnail through the
account/shard-bound local resolver. The poster URL is kept separate from the
playable MP4 URL; missing posters do not disable video playback. Provider, Hub
API, and UI regressions pass. Full WeFlow resource/decryption parity remains
open.

**P9 QCE cache-retention follow-up (2026-10-12):** default latest-page cache
keys now remain stable across requests, and each fetch prunes expired entries
for all peers before lookup. Two focused Rust cache regressions pass. This
reduces duplicate/stale message-cache retention but does not prove the cause of
the unusually large live QCE private-bytes measurement. QQ login is still
waiting for the user to scan the displayed local QR; no runtime was restarted.

**P11 avatar-source audit (2026-10-12):** WeChat reads bounded avatar bytes
from its local avatar database and caches them under an account-independent
Hub-local opaque URL. QQ and WeCom providers currently retain only upstream
avatar URLs; the Hub avatar endpoint is deliberately offline and only serves
previously cached bytes. No verified QQ/WeCom local avatar-cache source is
implemented, so their avatars can remain initials/placeholders. Do not add a
remote fetch fallback under the local-only privacy contract; source discovery
or an explicit change in privacy scope is required before claiming three-source
avatar parity. Six focused existing avatar/MCP tests pass.

**P13 staged bot-media cleanup (2026-10-12):** startup orphan cleanup now runs
after in-flight send recovery and only pending-approval or sending requests
retain staged WeCom bot attachments. Rejected, sent, failed and unknown terminal
requests no longer keep local staged bytes indefinitely. All 12
`hub.test_sending` tests pass; real bot delivery and service-side remote-media
retention are not verified.

**P12 source-mutation gate audit (2026-10-12):** message edit/insert/delete and
Moments trigger/delete routes remain fail-closed across Electron IPC and local
HTTP; the four focused synthetic gate tests pass, with no provider operation
invoked. The independent Hub backup-restore flow was not part of this gate
audit. P12-03/04 remain `NOT_STARTED`: the anti-recall/interception behaviors
are not implemented, and source-client mutation still requires separate user
approval plus an inspectable native implementation and isolated rollback tests.

**P14 packaging checkpoint (2026-10-12):** the latest paired artifacts remain
portable p213 plus source/manifest p215; source-only p218 has no matching
portable or manifest and does not represent the current dirty worktree. A new
portable build is deferred while the QQ runtime/login flow is active and the
host is under memory pressure. No process was restarted and no artifact was
uploaded; P14 remains `IN_PROGRESS`.

**P11 offline media follow-up (2026-10-12):** QQ MCP attachment reads now
resolve cached video, voice, and file resources through the same account-bound
local cache path as images, without starting QCE requests. WeChat's retained
WXGF image container can be explicitly returned by MCP with a dedicated MIME
type when local conversion is unavailable; it remains a raw container, not a
rendered image. WeCom's Chinese attachment labels normalize to canonical media
types in message bubbles, gallery filtering, and resource resolution. Focused
provider/server/store tests (57 total) and the WeCom renderer regression pass.
These synthetic/local-cache checks do not establish real-account media
availability; P11 remains `IN_PROGRESS`.

**P14 source-only refresh (2026-10-12):** the current worktree's 127-file
sanitized source archive passed the package privacy scan and extracted-package
Hub/WebUI/three-source/read-only-MCP smoke. It has no matching portable bundle
or provenance manifest; earlier p213/p215 artifacts do not represent this
worktree. P14 remains `IN_PROGRESS` pending a same-worktree
portable/source/manifest set and hosted artifact verification.

**P11 cached AVIF/HEIC MCP follow-up (2026-10-12):** explicit MCP attachment
reads now recognize cached AVIF/HEIC images from bounded ISO-BMFF brand
signatures and return their image MIME types. Synthetic QQ-cache MCP tests
pass; no local account cache or network was accessed. This closes only the
signature handoff gap and does not claim browser decoding or full media parity.

**P11 QQ forwarded AVIF/HEIC follow-up (2026-10-12):** merged-forward image
signature validation now recognizes bounded ISO-BMFF AVIF/HEIC brands, matching
the ordinary QQ cached-image MCP path while rejecting generic MP4 brands.
Synthetic signature tests pass; real account media availability and browser
decoding remain unverified.

**P13 media approval parity follow-up (2026-10-12):** a staged media request
must now contain no text, enforced by the shared send-validation boundary; the
composer clears, disables, and hides its text field in media mode so approval
shows exactly what the adapter will deliver. Focused SendService and WebUI
regressions pass. Real enterprise-bot delivery remains unverified and still
requires a separate explicit approval.

**P9 QCE cache byte-budget follow-up (2026-10-12):** message cache entries now
carry a recursive heap estimate, skip caching an entry above 128 MiB, and evict
oldest entries before the combined estimate exceeds 128 MiB. This estimate is
not a hard resident-memory bound: allocator/layout overhead and concurrent
cache-hit clones are not accounted for. Responses are not truncated. Rust unit
tests were added for nested-size estimation and oldest-entry eviction; a direct
installed-toolchain offline run passes all four focused cache tests. This is a
code-level guard, not a measured reduction of the already-running QCE process;
no restart or live sync was performed.

**P11 pair-report hour buckets follow-up (2026-10-12):** the pair-report
24-hour chart now fills missing hours with zero-count buckets, matching the
annual-report chart so fixed hour labels remain aligned for sparse data. A
focused WebUI regression verifies all 24 bars and the 00:00/09:00 values.
P11 remains `IN_PROGRESS` for the other media, source-parity, and real-cache
acceptance items above.

**P9 QQ scan handoff follow-up (2026-10-12):** after the prior local NapCat
process exited, the bundled login flow was started again and the local Hub was
restored on loopback with automatic synchronization disabled. `/api/sources`
returned all three providers and `/api/qq/login-status` returned
`waiting_qrcode`, `online=false`, `available=true`. A fresh QR was shown to the
user; account confirmation and history/media synchronization remain pending.

**P14 local package-set follow-up (2026-10-12):** the Windows portable build
initially stopped at the privacy scan because generated Rust `target` metadata
was included from the upstream source tree. The portable staging exclusion now
omits `target`; the rebuilt portable package and a refreshed source bundle pass
privacy and isolated startup checks, and a `local-worktree` SHA-256 manifest
verifies both package byte sets. Hosted CI and real artifact upload/download
remain unverified, so this does not complete P14.

**P14 current-worktree refresh (2026-10-12):** portable p231 and source p232
were rebuilt after the pair-report UTC+08 year-boundary regression was added.
Both package startup smokes pass; the portable bundle includes the pinned QCE
runtime and passes the privacy scan. A `local-worktree` manifest verifies both
byte sets against the current HEAD/provenance context; the source archive's
roadmap and analytics test match the worktree exactly. Hosted Actions and a
hosted upload/download round trip remain unverified; P14 remains `IN_PROGRESS`.

**P14 full local gate rerun (2026-10-12):** the worktree passed 531 Python
tests, 4 source-mutation gate tests, 23 WebUI tests, package privacy, manifest
and isolated startup fixtures, syntax checks, and the 25,000-message benchmark.
This supersedes the earlier 509-test count. The hosted run still reflects the
older failing commit; no push or hosted artifact round trip was performed.

**P11 report accessibility follow-up (2026-10-12):** annual and pair-report
hourly charts now expose all 24 UTC+08 hour/count values in their accessible
names rather than hiding every bar from screen readers. The focused pair-report
WebUI regression and syntax check pass. Real browser screen-reader acceptance
remains outstanding; P11 remains `IN_PROGRESS`.

**P11 QQ cache date-filter follow-up (2026-10-13):** the media library now
shows its existing UTC+08 start/end controls for the QQ global-cache gallery.
The Hub converts inclusive date bounds to milliseconds. Vendored QCE source
filters local file metadata before count/order/pagination and returns an
explicit capability marker; the provider fails closed when an older runtime
omits it, rather than returning unfiltered rows. Provider, HTTP integration,
and WebUI regressions cover boundary forwarding and control visibility. The
currently bundled QCE binary predates this source change; rebuilding and
accepting that runtime, and the remaining P11 parity gaps, are still open.

**P11 QQ forwarded MP4 preview (2026-10-13):** merged-forward attachments
whose verified local MIME is exactly `video/mp4` now load through the existing
bounded resolver and render a native controls-enabled player, while retaining
a separate download action. Blob URLs are released by the existing rerender
lifecycle. Synthetic WebUI coverage passes; real cached media/browser playback
and the remaining P11 parity gaps are still open.

**P11 original SILK fallback (2026-10-13):** when local decoding is unavailable
or fails, the identity-bound resolver can expose the original cached SILK
through the existing random local-media token. It requires a SILK signature and
containment in the platform-managed cache root; successful decode still serves
WAV, and mismatched message identity/path escape issues no token. The resolve
response labels this fallback `audio/silk` with a `.silk` download name, and the
WebUI offers download without rendering an unsupported browser audio player;
WMA and playable audio retain their prior preview behavior. Focused synthetic
HTTP and WebUI regressions pass; real-account cache-layout and playback
acceptance remain open.

**P12 local export privacy follow-up (2026-10-12):** scheduled export now rejects
UNC paths and, on Windows, mapped network drives so exported chat content stays
on local disks. The UNC regression is covered by `hub.test_auto_export`; the
Windows drive-type API branch has a synthetic regression for remote versus fixed
drives, but has not been exercised against a real mapped drive. P12-01 remains
`IN_PROGRESS` for real-account and browser acceptance.

**P12 automatic-export cancellation follow-up (2026-10-13):** a regression now
cancels immediately after the temporary export has been flushed and fsynced but
before atomic publication. It verifies that cancellation leaves neither a
partial file nor a final export; the existing cleanup path passes this boundary.
This is synthetic local coverage and does not close P12-01 browser/account
acceptance.

**P11 QQ reply-preview media follow-up (2026-10-14):** local QQ parsing now
retains bounded attachment metadata embedded in kind-7 reply previews and
exposes those items through the existing account-scoped cache resolver. The
WebUI renders a cache-read action inside the quoted block without duplicating
the item in the message attachment strip. Synthetic parser, cache-resolution,
and rendering checks pass; real-account cache coverage remains unverified, so
P11 stays `IN_PROGRESS`.

**P11 private analytics scope follow-up (2026-10-14):** private-profile
aggregation now requires a matching conversation row with an explicitly
recognized private kind. Orphan messages with unknown conversation scope are
excluded, while recognized private conversations remain included. Synthetic
analytics coverage passes; this closes the orphan-classification gap only and
does not close broader P11 media/export acceptance.

**P13 local WeChat delivery probe (2026-10-14):** the user selected one indexed
conversation and supplied a test message. The current 8765 Hub uniquely
resolved that conversation, but its read-only send-readiness probe reported no
WeChat window, so no request was queued and no message was sent. The
temporarily enabled `wechat_send` capability was restored to off. Real WeChat
delivery remains unverified pending an already logged-in desktop client and
per-request approval.

**P14 local package refresh (2026-10-12):** the full local gate passes with 532
Python tests, 4 source-mutation gate tests, and 23 WebUI tests. Portable p235
passes executable/UI/Lucide, three-source, isolated-storage, and standalone
read-only MCP smoke checks; source p235 passes privacy and clean-start checks.
Manifest p235 verifies both inventories and local-worktree provenance. Hosted Actions,
upload/download round-trip, real-account acceptance, and live
mapped-drive behavior remain unverified; P14 stays `IN_PROGRESS`.

**P14 manifest refresh p236 (2026-10-12):** the portable directory and
sanitized source ZIP were reverified against manifest `local-p236-20261012`
with `local-worktree` provenance and HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The manifest matches both package
inventories, byte counts and SHA-256 values; the portable package contains
10,359 files (407,418,881 bytes) and the source ZIP is 595,264 bytes. Hosted
Actions and hosted upload/download remain unverified, so P14 stays
`IN_PROGRESS`.

**P13 local attachment review visibility (2026-10-13):** the pending WeCom bot
approval view now embeds validated local images/videos and offers a local
download for AMR voice/documents. The endpoint still serves only signed local
cache entries on pending requests, uses `no-store`/`nosniff`, and never approves
or sends. Focused route and UI tests pass. Actual bot delivery and device/ACK
acceptance remain open; P13 stays `IN_PROGRESS`.

**P14 current-worktree local gate (2026-10-13):**
`scripts/check-local-hub.ps1` passed against the current uncommitted source:
privacy, manifest, extracted-package smoke, 566 Python tests, four source-
mutation gate tests, 25 WebUI tests, syntax checks, and synthetic performance /
recovery benchmark. Two closed-socket `ResourceWarning` messages appeared while
the Python suite still exited successfully. This local gate does not run hosted
Windows Actions or prove a hosted artifact upload/download round-trip; P14
remains `IN_PROGRESS`.

**P9 QQ runtime diagnosis (2026-10-12):** the bundled NapCat loopback login API
responds `waiting_qrcode` and its local QR cache is being refreshed, but the Hub
on port 8772 reports login status unavailable. QCE history services on ports
40653/40654 are not listening. No QQ/NapCat process was restarted and no chat
database was read; QR confirmation and history synchronization remain open.

**P9 live QR handoff follow-up (2026-10-13):** a direct read-only call to the
bundled NapCat provider returned `waiting_qrcode` and a fresh login challenge,
while the Hub at 8772 still returned `available=false` and QCE ports 40653/40654
remained closed. The challenge was handed to the user but is not stored in the
repository or logs. No scan, QQ history read, sync, or process restart occurred;
P9 real-account acceptance remains open.

**P9 stale QR presentation follow-up (2026-10-12):** when login status is
unavailable, the WebUI now replaces an already-open QR with an explicit
unavailable state and a retry action, preventing scans of a stale code. The
focused login-modal regression and syntax check pass. This UI correction does
not restore the Hub/NapCat runtime mismatch or QCE history service.

**P9 status fallback regression (2026-10-13):** provider tests now assert that
when QCE history endpoints are unavailable but NapCat's local login endpoint
is reachable, QQ remains available for login with `waiting_qrcode`; when both
services are unavailable it reports unavailable. The public status object is
asserted not to expose QR or token fields. Two isolated tests pass. This does
not establish that the currently running Hub process has loaded the matching
runtime configuration; live QR/login and history sync remain unverified.

**P9 launcher runtime-path follow-up (2026-10-13):** `start_hub.py` now pins
`QCE_BUNDLE_DIR` to the repository-managed NapCat/QCE runtime for the Hub child,
instead of inheriting a stale override from the launching shell. A focused
launcher regression passes. An isolated Hub using that runtime reports
`waiting_qrcode`/available while the already-running port-8772 Hub still reports
unavailable; it was not restarted. This is a startup-path correction, not live
QQ login or history acceptance.

**P14 local-worktree refresh (2026-10-13):** portable p238 and sanitized
source p239 were rebuilt after the stale-QR UI correction and P9 provider
status regression. Portable p238 contains 10,359 files (407,420,579 bytes);
source p239 contains 127 files (595,964 bytes). Portable and extracted-source
startup smokes, source privacy scan, and manifest byte/provenance verification
pass under `local-p239-20261013` / `local-worktree`, based on HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the build includes dirty local
worktree inputs and is not represented by that commit. Hosted Actions and a
hosted artifact upload/download round trip remain unverified, so P14 stays
IN_PROGRESS.

**P14 local package and full-gate refresh p251 (2026-10-13):** the current
worktree passed `scripts/check-local-hub.ps1`: 545 Python tests, 4 source-client
mutation-gate tests, 23 WebUI tests, package privacy/manifest/smoke fixtures,
syntax checks, and the 25,000-message benchmark. Portable p251 passed bundled
Hub/UI/three-source and standalone MCP stdio smokes. Sanitized source p251
contains 129 files and passed privacy plus extracted startup checks. Manifest
`local-p251-20261013` matches the package bytes and records base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree` provenance;
it includes dirty worktree content and is not represented by that commit.
Hosted Actions and hosted artifact upload/download remain unverified, so P14
stays `IN_PROGRESS`.

**P14 p270 portable / p271 source manifest (2026-10-13):** refreshed sanitized
source p271 after the current-worktree portable p270 build. Source privacy and
extracted empty-storage startup passed. Manifest `local-20261013-p271`
verifies 10,365 portable files (426,846,282 bytes) and source p271
(24,954,085 bytes) against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree` provenance.
This manifest note is outside p271 itself. Hosted Actions and artifact
upload/download remain unverified; P14 stays `IN_PROGRESS`.

**P14 p270 current-worktree package refresh (2026-10-13):** rebuilt the
portable package from current source, including the P11 WeCom media-gallery
and P13 staged-media outcome fixes. Packaged Hub and standalone MCP isolated
smokes and final privacy scanning passed. Manifest `local-20261013-p270`
verifies 10,365 portable files (426,846,282 bytes) and source p270
(24,953,881 bytes) against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree` provenance;
the QCE hash is `e0b707ed7a550f312639ed247411592ae2005eafad607d746d6ad1644b75f762`.
The source archive is refreshed separately after this record; hosted Actions
and artifact upload/download remain unverified, so P14 stays `IN_PROGRESS`.

**P13 staged-attachment cleanup follow-up (2026-10-13):** if a WeCom bot media
prepare fails before a durable send request retains the uploaded attachment,
the Hub removes that unreferenced staged file immediately. If a post-commit
error occurs, the pending request's attachment remains intact. Synthetic HTTP
regressions cover both outcomes and confirm no delivery occurs; real bot
credentials and delivery acceptance remain open.

**P13 Windows DPAPI verification (2026-10-13):** the current Windows host now
executes a real current-user DPAPI protect/unprotect round trip using a synthetic
bot identity in a temporary directory. The test confirms ciphertext does not
contain the plaintext secret and removes the temporary credential. This verifies
local DPAPI integration only; real bot authorization and delivery remain open.

**P11 original SILK UI fallback and P14 p252 refresh (2026-10-13):** the local
media resolver now returns a stable SILK MIME type and download filename when
decoding is unavailable or fails. Message and QQ resource previews download raw
SILK instead of showing an unsupported audio element; WAV/MP3 and WMA behavior
remains covered. The full local gate passes 546 Python tests, 4 source-client
mutation-gate tests, 23 WebUI tests, package privacy/manifest/smoke fixtures,
syntax checks, and the 25,000-message benchmark. Portable p252 contains 10,363
files (407,468,080 bytes) and passes bundled Hub/UI/three-source and standalone
MCP stdio smokes; sanitized source p252 has 129 files (603,209 bytes) and
passes privacy and extracted startup checks. Manifest
`local-p252-20261013` verifies both package byte inventories with
`local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; it contains local working-tree
changes not represented by that commit. A clean `git archive HEAD` audit found
the CI helper scripts and current implementation/tests are not present in the
tracked base; hosted CI and artifact transfer remain unverified. Real-account
cache playback remains unverified, and P11/P14 stay `IN_PROGRESS`.

**P12 notification permission regression (2026-10-13):** the WebUI regression
now verifies it never requests browser notification permission implicitly;
disabling never prompts, explicit enable requests only from the browser's
default permission state, denial leaves the capability disabled, and an
already-granted permission is not requested again. The focused synthetic VM
test passes. This does not replace real-browser notification delivery
acceptance, so P12-01 remains `IN_PROGRESS`.

**P11/P12/P14 local-worktree refresh p253 (2026-10-13):** the full local gate
passes 547 Python tests, 4 source-client mutation-gate tests, 23 WebUI tests,
package privacy/manifest/smoke fixtures, syntax checks, and the synthetic
25,000-message benchmark. A new synthetic cross-layer regression sends
original/mid/thumb WeChat image tiers through the real resource resolver into a
ZIP export, verifies explicit-original failure, archive SHA-256, and unchanged
source cache bytes. The notification permission regression remains covered.
Portable p253 contains 10,363 files (407,481,472 bytes) and passes bundled
Hub/UI/three-source plus standalone MCP stdio smokes. Sanitized source p253
contains 129 files and passes privacy and extracted startup checks. The refreshed
manifest verifies both package byte inventories with `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the artifacts include dirty
working-tree changes not represented by that commit. Hosted Actions, hosted
artifact transfer, real-account media playback, and real-browser notification
delivery remain unverified; P11, P12, and P14 stay `IN_PROGRESS`.

**P14 local-worktree package refresh p254 (2026-10-13):** rebuilt the portable
Windows package and sanitized source bundle after the P11/P12/P14 changes.
The portable package contains 10,363 files (407,503,630 bytes); the source
bundle is 607,710 bytes with 129 files inside. Bundled Hub/UI/three-source and
standalone MCP stdio smoke checks passed; the source bundle passed privacy and
isolated extracted-startup checks. Manifest `local-20261013-p254` verifies both
package byte inventories against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`
with `local-worktree` provenance; the packages include uncommitted worktree
changes and are not reproducible from that commit. Hosted Actions/upload and
real-account scan/sync remain unverified, so P14 remains `IN_PROGRESS`.

**P13 WebSocket reserved-bit validation and P14 p255 refresh (2026-10-13):**
the stdlib WeCom bot WebSocket reader now rejects any RSV1/2/3 bit because no
extension is negotiated. A synthetic wire regression covers each bit; all 11
WeCom sender/media tests pass. The full local gate passes 551 Python tests, 4
source-client mutation-gate tests, 24 WebUI tests, package privacy/manifest/
smoke fixtures, syntax checks, and the synthetic 25,000-message benchmark.
Portable p255 contains 10,363 files (407,505,178 bytes); sanitized source p255
is 608,050 bytes and contains 129 files. Bundled Hub/UI/source/MCP smokes,
source privacy scanning, extracted startup, and manifest byte verification
passed. The manifest records `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; these artifacts include uncommitted
worktree changes. This does not verify live WeCom delivery, real QQ scan/sync,
or hosted Actions/upload, so P13/P14 remain `IN_PROGRESS`.

**P13 control-frame conformance and P14 p256 refresh (2026-10-13):** the
WeCom WebSocket reader now accepts/ignores legal Pong heartbeats and rejects
control frames with payloads above the RFC 6455 125-byte limit. Synthetic tests
cover Pong followed by a receipt and oversized ping rejection. The full local
gate passes 553 Python tests, 4 source-client mutation-gate tests, 24 WebUI
tests, package privacy/manifest/smoke fixtures, syntax checks, and the synthetic
25,000-message benchmark. Portable p256 has 10,363 files (407,506,904 bytes);
sanitized source p256 is 608,416 bytes and contains 129 files. Package startup,
source/MCP smokes, privacy scan, and manifest byte verification pass. Manifest
`local-20261013-p256` records `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; it includes uncommitted changes.
Live WeCom delivery and hosted Actions/upload remain unverified, so P13/P14
remain `IN_PROGRESS`.

**P13 handshake validation and P14 p257 refresh (2026-10-13):** the WeCom
bot handshake now requires the case-insensitive `Upgrade` token in `Connection`
and rejects server-selected extensions/subprotocols that the client did not
offer. Synthetic socket tests cover missing Connection, comma-separated mixed-
case tokens, and unsolicited negotiation. The full local gate passes 554 Python
tests, 4 source-client mutation-gate tests, 24 WebUI tests, package privacy/
manifest/smoke fixtures, syntax checks, and the synthetic 25,000-message
benchmark. Portable p257 has 10,363 files (407,510,350 bytes); sanitized source
p257 is 608,935 bytes and contains 129 files. Both package smokes, privacy scan,
and manifest byte verification pass. Manifest `local-20261013-p257` records
`local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the packages contain uncommitted
worktree changes. Real WeCom/QQ acceptance and hosted CI/upload remain open, so
P13/P14 stay `IN_PROGRESS`.

**P13 frame-length validation and resource-cancel ordering (2026-10-13):** the
WeCom WebSocket reader rejects non-minimal 16/64-bit payload lengths and a set
64-bit length high bit, while accepting canonical 126- and 65,536-byte frames.
The resource ZIP worker now publishes `cancelled`/`failed` only after temporary
files are removed, so a terminal cancellation status guarantees cleanup has
finished. Focused tests pass (26); the full local gate passes 556 Python, 4
source-client mutation-gate, and 24 WebUI tests, package privacy/manifest/smoke
fixtures, syntax checks, and a 25,000-message benchmark with no WAL failures.
This source revision is newer than the p257 packages above; a refreshed package
and manifest are still required. The locally rebuilt vendored QCE binary is
outside the repository and is not yet used by the package builder, so packaged
QQ cache date filtering is not verified. P11/P13/P14 remain `IN_PROGRESS`.

**P14 p258 local-worktree package refresh (2026-10-13):** portable Windows
build completed with 10,363 files (407,513,918 bytes); bundled Hub/UI/three-
source, isolated-storage, read-only MCP, and standalone MCP stdio checks passed.
Sanitized source p258 contains 129 files (609,759 bytes), passes the privacy
scan and extracted empty-storage startup check. Manifest
`local-20261013-p258` verifies both package inventories against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree` provenance;
the packages include uncommitted worktree changes. QQ login start currently
refuses to launch because `QQ.exe` is already running, so no new QR session or
account sync was verified. The package still uses the official QCE executable,
not the locally rebuilt date-filter-capable binary. Hosted Actions/artifact
transfer and real-account delivery/media checks remain open; P11/P13/P14 remain
`IN_PROGRESS`.

**P11 vendored QCE build and P14 p260 refresh (2026-10-13):** Windows
packaging now compiles QCE from the repository's pinned v6.3.2 source and
Cargo.lock, checks the loopback bind and required date-filter capability, and
embeds the resulting binary SHA-256 in the staged provider allowlist. The
sanitized source bundle now includes the vendored QCE tree, lockfile, GPL
license, and build script. The complete local gate passes 557 Python tests,
4 source-mutation tests, 24 WebUI tests, package/privacy/manifest smokes,
syntax checks, and the synthetic 25,000-message benchmark. Portable p260 has
10,364 files (426,909,831 bytes); source p260 has 669 files (24,941,758 bytes).
The package manifest `local-20261013-p260` verifies both artifacts with
`local-worktree` provenance against HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; this is not hosted CI or a signed
release. The packaged provider accepted the packaged QCE binary, and that
binary started in an isolated profile and accepted a loopback connection. No
QQ session/account data was accessed. QQ QR/login/history acceptance, broader
P11 media parity, live WeCom delivery, hosted Actions/upload, and real-account
media rendering remain open; P11/P13/P14 stay `IN_PROGRESS`.

**P9 QQ login handoff clarification (2026-10-13):** the running local Hubs
on 8772 and 8773 both returned `available=false` for QQ login, and neither
the NapCat bootloader nor QQ process had a listening login bridge. The QQ
process guard therefore prevents launching a second client; the UI now gives
a Chinese, non-destructive instruction to fully exit QQ from the system tray
and retry. No process was terminated and no QR/account data was obtained;
real login/history acceptance remains open.

**P11 QQ gallery alias follow-up (2026-10-13):** all audited QQ media consumers
now mirror the reader's `extra.media` fallback when `extra.roaming_resources`
is absent or not an array; an explicit `roaming_resources` array, including an
empty one, remains authoritative. This covers chat/unified media queries,
media analytics, indexed resource resolution and ZIP export, MCP selected
attachments, and referenced-media backup inclusion. Focused regressions cover
each layer; the four-module backend run passed 272 tests and the latest five
focused alias/precedence tests passed. This closes the alias consistency gap,
not full resource/decryption parity or real-cache acceptance; P11 remains
IN_PROGRESS.

**P14 QCE linker drift guard (2026-10-13):** the vendored QCE build now requires
GCC 16.2.0 and prints the selected linker version; hosted setup no longer asks
MSYS2 to update the entire installation before building. The package smoke
contract checks both constraints. This prevents silent compiler-version drift,
but is not a reproducible compiler package lock or a fixed QCE binary digest;
the latter still needs a trusted clean hosted build and reviewed digest. Hosted
Actions and artifact round-trip remain unverified, so P14 stays IN_PROGRESS.

**P14 local QCE rebuild and runtime smoke (2026-10-13):** the GCC prerequisite
is available at `C:\msys64\mingw64\bin\gcc.exe` even though it is not on the
interactive PATH. The pinned Rust 1.98.1/GNU + GCC 16.2.0 build completed from
the vendored source and Cargo.lock; the resulting local binary SHA-256 is
`a0f8b6c4f34b88c90f37f1ffcd9da6c2c1dac7a5667e7b665c642add28c64711`. An
isolated temporary profile smoke returned healthy `standalone`, `online=false`,
listened only on `127.0.0.1`, and an authenticated empty gallery response
reported `dateFilterSupported=true`. The generated token and QCE files were
confined to a temporary profile; no NapCat bridge, QQ session, or account data
was used. This is local evidence, not a reviewed/approved package hash or hosted
CI/artifact round-trip; P14 remains IN_PROGRESS.

The packaged p260 QCE binary was also launched independently under the same
isolated profile: SHA-256
`adf2db90cd93d508a6d8c9e739fa22d68a37e547601757ee311c8c0f7f1b2107`, healthy
standalone/offline, bound only to `127.0.0.1`, and its authenticated empty
gallery response reported `dateFilterSupported=true`. The portable package's
duplicated QCE copies have the same hash. This closes the local packaged-runtime
smoke only; it does not verify QQ login/history or hosted artifact provenance.

**P12 notification capability follow-up (2026-10-13):** Settings detects
browsers without the `Notification` API, explains that desktop notifications
are unsupported, and disables enabling that capability; if it was already
enabled elsewhere, disabling it remains possible. Synthetic rendering covers
supported/unsupported and enabled/disabled combinations, and the regression
is included in the local gate. Real browser permission and notification-click
acceptance remain open.

**P14 p262 final-package privacy gate (2026-10-13):** the portable builder now
runs its privacy scanner again on the assembled payload after packaged smoke,
and a regression asserts this ordering. The portable p262 build passed the
bundled Hub/WebUI/three-source and standalone MCP checks, then passed the final
privacy scan; an independent scan also passed. Sanitized source p262 passed its
privacy and extracted-startup checks. The local-worktree manifest verifies
10,365 portable files (426,828,372 bytes) and the 24,947,064-byte source ZIP
against HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with
`local-20261013-p262` provenance. QCE SHA-256 is
`481999bc4de1c59e01e6b04539e2ba770e4f5cfbf999f95161968f73878bcc45`.
Hosted Actions/artifact transfer and real-account acceptance remain open;
P14 remains `IN_PROGRESS`.

**P14 p263 `.pyc` regression and package refresh (2026-10-13):** the privacy
fixture now explicitly rejects a synthetic `provider.pyc`, matching the
post-smoke final-package scan added to the portable builder. The focused privacy
and package-smoke fixtures pass. Rebuilt portable p263 and sanitized source
p263 both pass package/privacy and isolated startup checks. Manifest
`local-20261013-p263` verifies 10,365 portable files (426,830,834 bytes) and
the 24,947,361-byte source ZIP against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree` provenance.
The newly built QCE hash is
`fe8831b62a2c311e08cb3bdfbb3f39535b0a47f01502f6dbd50304ea89660386`; it passed
the source capability checks and was included in p263. The exact packaged
binary's independent runtime/date-filter smoke is recorded below. Hosted
CI/artifact transfer and real-account acceptance remain open; P14 remains
`IN_PROGRESS`.

**P14 p264 current-source archive (2026-10-13):** after recording the p263
build results above, the sanitized source bundle was refreshed as p264 so it
includes those roadmap, development-log, and `.pyc` regression updates. Its
extracted startup and privacy checks pass. Manifest
`local-20261013-p264` binds the unchanged portable p263 directory (10,365
files, 426,830,834 bytes) to source p264 (24,947,811 bytes), byte-for-byte,
with `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This still is not hosted CI or an
artifact upload/download round-trip; P14 remains `IN_PROGRESS`.

**P14 p263 packaged QCE runtime follow-up (2026-10-13):** the exact packaged
QCE hash `fe8831b62a2c311e08cb3bdfbb3f39535b0a47f01502f6dbd50304ea89660386`
started in standalone/offline mode with an isolated temporary profile, passed
`/health`, and an authenticated empty `/api/resources/files` request with
`startTime`/`endTime` returned `dateFilterSupported=true`. Only loopback was
queried; NapCat, QQ, and account data were not accessed. This closes the local
runtime check for this hash, not QQ login/history, hosted CI, or artifact
upload/download acceptance; P9/P11/P14 remain `IN_PROGRESS`.

**P14 p265 source refresh and manifest (2026-10-13):** sanitized source p265
includes the p263 packaged-QCE runtime follow-up and passed archive privacy
scanning plus extracted empty-storage WebUI/MCP startup. Manifest
`local-20261013-p265` verifies p263 portable (10,365 files, 426,830,834 bytes)
and the 24,948,110-byte p265 source ZIP against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; provenance is `local-worktree`,
not hosted CI. This entry records the new archive in the worktree and is not
included inside that same archive. Hosted artifact transfer and real-account
acceptance remain open; P14 stays `IN_PROGRESS`.

**P9 live NapCat QR status update (2026-10-13):** a read-only probe of the
currently running local NapCat WebUI at `127.0.0.1:6099` authenticated and
reported `waiting_qrcode`; `GetQQLoginQrcode` returned a valid challenge, which
was rendered to a temporary local PNG for the user. This supersedes the earlier
P9 diagnostic that no NapCat login bridge was listening. Hub login status on
the existing preview instances remains inconsistent (`available=false`, a
generic provider error, or the existing-QQ process guard), and QCE history
ports `40653`/`40654` remain closed. The user has not confirmed a scan; no
history read/sync or message delivery was performed. A fresh current-source Hub
was started on loopback port 8774 with empty temporary storage: its status API
reports `waiting_qrcode/available=true`, and `/api/qq/start` returned a QR image
shown in the UI. QQ currently has zero indexed messages, and its source is
`login_required`; QCE history remains offline. The later P11 follow-up records
the separate successful WeChat sync in this isolated Hub. P9 remains
`IN_PROGRESS`.

**P14 p266 source refresh (2026-10-13):** the sanitized p266 source archive
includes this current NapCat runtime status update and the p263 QCE runtime
evidence; archive privacy and extracted empty-storage WebUI/MCP startup passed.
Manifest `local-20261013-p266` byte-verifies p263 portable (10,365 files,
426,830,834 bytes) and source p266 (24,948,550 bytes) against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree` provenance.
This entry itself is in the worktree rather than inside p266. Hosted CI and
artifact transfer remain unverified; P14 stays `IN_PROGRESS`.

**P11 real WeChat read-only acceptance (2026-10-13):** with the user's prior
authorization, the isolated local Hub completed a read-only sync from the
current WeChat installation. The WebUI index, media catalog, and contact
records populated, including locally cached avatar data. No message bodies,
account identifiers, local paths, or account-volume metrics are recorded here;
the isolated instance uses temporary local storage. Source-client databases
were not modified. P11 real-device acceptance is now verified for this local
WeChat source; QQ login/history and WeCom local snapshot acceptance remain
open.

**P14 p267 privacy refresh (2026-10-13):** the sanitized source p267 archive
includes the WeChat acceptance note above and passed privacy scanning plus
extracted empty-storage WebUI/MCP startup. Manifest
`local-20261013-p267` verifies portable p263 (10,365 files, 426,830,834 bytes)
and source p267 (24,948,901 bytes) against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree` provenance.
This manifest result is recorded in the worktree, not inside p267. Hosted
Actions and artifact transfer remain unverified; P14 stays `IN_PROGRESS`.

**P14 p268 source refresh (2026-10-13):** sanitized source p268 includes the
P11 local WeChat acceptance and corrected P9 QQ-only message status; archive
privacy and extracted empty-storage WebUI/MCP startup passed. Manifest
`local-20261013-p268` verifies p263 portable (10,365 files, 426,830,834 bytes)
and source p268 (24,949,007 bytes) against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree` provenance.
This p268 result is recorded in the worktree, not inside p268. Hosted Actions
and artifact transfer remain unverified; P14 stays `IN_PROGRESS`.

**P11 real WeCom read-only acceptance (2026-10-13):** using a temporary
local-only copy of the existing WeCom read configuration and the cryptography
already bundled with the portable runtime, the isolated Hub completed a
read-only WeCom sync. Conversations, messages, contacts, and media metadata
became queryable through the same Hub index; source databases were not
modified. No account identifiers, paths, keys, content, or account-volume
metrics are recorded. The temporary index/config are not the persistent app
store. This verifies local source ingestion, not real media preview/export or
avatar rendering; QQ login/history remains open.

**P14 p269 source refresh (2026-10-13):** sanitized source p269 includes the
P11 WeCom acceptance and passed archive privacy scanning plus extracted
empty-storage WebUI/MCP startup. Manifest `local-20261013-p269` verifies p263
portable (10,365 files, 426,830,834 bytes) and source p269 (24,949,283 bytes)
against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with
`local-worktree` provenance. The manifest result is in the worktree, not
inside p269. Hosted Actions and artifact transfer remain unverified; P14
stays `IN_PROGRESS`.

**P11 native WeCom media-cache resolution (2026-10-13):** integrated the local
`CacheMapping.mapping` to decrypted `file_table4.md5` join into the owned reader,
removing the need for a separately generated media index. The reader only
resolves existing files inside the configured account cache; database access is
read-only and cache paths remain internal. The standalone extractor's hard-coded
account configuration and destructive export cleanup were not copied. The
CacheMapping category is retained as each attachment's media type and selects
bounded category-specific candidates for bare filenames; cache-root containment
checks remain. The unified image/video/voice/file filters are covered by 21
focused WeCom adapter/engine, HTTP resolver, and media-type filter tests,
including synthetic fixtures for all four categories.
The already-running Hub predates this change, so real-account preview/export is
not yet verified in the refreshed app; P11 remains `IN_PROGRESS`.

A read-only directory-presence check on the configured local cache found
`Cache/Image` and `Cache/File`, but no dedicated `Cache/Video`, `Cache/Voice`,
or `Cache/Audio` directories. The category-specific video/voice candidates are
therefore synthetic-only; generic cache fallbacks remain enabled. No filenames
or cache contents were inspected, and this does not prove real video/voice
preview availability.

**P11 real WeCom cached-image resolver acceptance (2026-10-13):** the updated
reader was run against the configured local source with isolated temporary
storage. It found an existing cached image through the native index join,
resolved it within the configured account cache, and verified the image
signature. Temporary configuration, snapshots, and decrypted test storage were
removed; source-client databases were only copied/read, never modified. No
account identifiers, paths, filenames, message content, or volume metrics were
retained. This verifies the real reader, not the WebUI: the running Hub predates
the code change, so visual preview/export acceptance remains open and P11 stays
`IN_PROGRESS`.

**P13 WeCom staged-media cleanup outcome (2026-10-13):** a local attachment
cleanup failure after delivery can no longer overwrite the sender's successful
or `delivery_unknown` outcome as `failed`, avoiding a misleading retry state.
The existing startup orphan cleanup remains responsible for stale staged
blobs. Synthetic service-level regressions cover both outcomes and existing
pre-send cleanup tests remain green. No real delivery was performed; live bot
acceptance and other P13 validation remain open, so P13 stays `IN_PROGRESS`.

**P13 compose cancellation follow-up (2026-10-13):** closing or replacing a
send composer now aborts its in-flight local request and invalidates the form;
late upload/prepare responses cannot continue into or reopen the review UI.
The regression simulates closing during a delayed attachment upload and
verifies that no prepare request follows. If a prepare request already reached
the local server before cancellation, its pending approval may remain visible
in send history; it still requires the separate explicit approval to deliver.

**P11 WeCom media-gallery preview follow-up (2026-10-13):** the existing lazy,
visibility-triggered gallery resolver now includes indexed WeCom image
attachments and stores resolved same-origin URLs on the attachment entry. The
focused regression verifies the platform and resource index while retaining
the three-request bound and cancellation on page changes. This closes the
synthetic WebUI path only; refreshed-app real-cache preview, video/voice
coverage, complete WeFlow media parity, and QQ acceptance remain open, so P11
stays `IN_PROGRESS`.

**P11 QQ media-gallery preview follow-up (2026-10-13):** indexed QQ roaming
image attachments now use the same bounded, visibility-triggered resolver as
WeChat/WeCom image previews. The synthetic regression checks account, message,
and resource-index forwarding plus resource URL writeback; no local path or QQ
service URL reaches the browser. This verifies the UI contract only, not real
QQ cache availability or refreshed-app rendering; P11 remains `IN_PROGRESS`.

**P9 QR authentication and P14 p272 follow-up (2026-10-13):** NapCat
`coreReady` no longer counts as an authenticated QQ login in either status
polling or QR retrieval; `isLogin` is authoritative, with synthetic HTTP,
provider, and UI regressions passing. Current-worktree portable p272 and source
p272 artifacts passed package privacy, isolated startup, and manifest byte
verification (`local-worktree`, base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Real QR/history acceptance and
hosted CI/artifact transfer remain unverified; P9 and P14 stay `IN_PROGRESS`.

**P14 p275/p276 local release verification (2026-10-13):** the current
worktree passed `scripts/check-local-hub.ps1` (569 Python tests, 4 source-
mutation gates, 25 WebUI tests, syntax, package privacy/isolated smoke, and
the synthetic 25,000-message benchmark). Portable p275 passed standalone Hub
and MCP runtime smokes; sanitized source p276 passed privacy and extracted
startup checks. Manifest `local-20261013-p276` pairs portable p275 (10,365
files, 426,847,838 bytes) with source p276 using `local-worktree` provenance
against HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact
transfer and real QQ account acceptance remain open; P14 stays `IN_PROGRESS`.

**P14 p272/p274 local release-input verification (2026-10-13):** the full
current-worktree gate passed (568 Python tests, four source-mutation safety
gates, WebUI tests, syntax, package privacy/manifest/isolated smoke, and the
synthetic 25,000-message benchmark). The p272 portable package and refreshed
p274 sanitized source archive passed paired manifest byte verification with
`local-worktree` provenance. Hosted Actions and artifact upload/download remain
unverified; the worktree is uncommitted, so P14 remains `IN_PROGRESS`. The p274
verification note itself is kept in the worktree, not embedded in that archive.

**P11 avatar regression and P14 p277 refresh (2026-10-13):** synthetic
WeCom avatar rendering/cache tests pass; the refreshed sanitized source p277
passed privacy scanning and extracted empty-storage startup. Manifest
`local-20261013-p277` verifies portable p275 (10,365 files,
426,847,838 bytes) and source p277 (24,956,770 bytes) with `local-worktree`
provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
Hosted Actions/artifact transfer and real QQ history remain unverified; P14
remains `IN_PROGRESS`.

**P9 QCE child recovery (2026-10-13):** the bundled NapCat plugin now
restarts an unexpectedly exited local `qce-server` with bounded exponential
backoff (1, 2, 4, 8, and 16 seconds), resets the retry budget after five stable
minutes, and pauses after five consecutive restart attempts until the plugin is
restarted. The Windows package builder stages this reviewed in-repository
plugin runtime over the hash-pinned upstream runtime. Syntax and backoff-bound
assertions pass; the plugin TypeScript test harness could not run because its
`tsx` dev dependency is absent. No current QQ/NapCat process was restarted.
Packaged-runtime and real-account recovery/history acceptance remain open;
P9 stays `IN_PROGRESS`.

**P9 p279 packaged recovery runtime (2026-10-13):** Windows portable p279
build and isolated Hub/MCP smoke passed. Inspection of the package confirms
the bundled `napcat-plugin-qce/runtime/rustBridge.mjs` contains the bounded
recovery policy (SHA-256
`52ca5b5aa7f175181e505221b07a0967d12cfff025d72e87cdf94c3601681e76`); the
vendored QCE server hash is
`c6814a1b51df1293d2002b51e90c025b75cf7b90dc661f8d4269c4363f9f1cd8`. This
proves the staged runtime and package startup, not restart after a real child
crash or a QQ history sync. Live acceptance remains open; P9 stays
`IN_PROGRESS`.

**P11 WeCom avatar cache acceptance follow-up (2026-10-13):** synthetic UI
coverage now verifies allowlisted WeCom avatar URLs render for conversations
and group members; the HTTP endpoint serves only signature-checked bytes
already present in Hub-owned cache and returns a cache-miss error without any
network request. This verifies the offline cache contract, not acquisition of
real WeCom avatar bytes; no WeCom avatar downloader or source-cache mapping is
claimed.

**P9 QCE recovery integration regression (2026-10-13):** a Node built-in
integration test starts a fake loopback QCE child, makes its first process exit
unexpectedly, verifies that the plugin restarts it and the API becomes
reachable again, then verifies clean shutdown. The test runs without the
upstream `tsx` dependency and is included in the plugin's unit-test patterns.
It is synthetic process-lifecycle coverage only; actual NapCat/QQ restart and
history sync remain unverified, so P9 remains `IN_PROGRESS`.

**P9/P14 p280 refresh (2026-10-13):** portable p280 includes the reviewed
vendored QCE plugin recovery runtime and rebuilt QCE server. The package passed
isolated Hub and standalone MCP startup smokes plus the package privacy scan;
the QCE release build completed successfully. A fresh run of the Node recovery
integration test passes its crash/restart/health/shutdown assertions. This is
package and synthetic lifecycle evidence only: no NapCat restart, QQ QR
confirmation, or history sync was performed. P9 remains `IN_PROGRESS`.

**P9 live status correction (2026-10-13):** current loopback probes show the
older preview on port 8772 has no available QQ provider; isolated Hub port
8776 reports `waiting_qrcode` and `available=true`. QCE history ports 40653
and 40654 remain closed. This establishes which UI can currently request a
QR code, not that a QR was scanned or that history can sync. No account data
was read and no NapCat process was restarted.

**P14 p280/p283 package pair (2026-10-13):** portable p280 (10,366 files,
426,886,859 bytes) and sanitized source p283 (24,959,782 bytes) pass manifest
`local-20261013-p283` verification with `local-worktree` provenance against
base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The packaged reviewed QCE
plugin runtime hash is
`9e1dc9ba45ce0f536a575ec2b1f0895d35240422976ecb5809d3e0e1bb55c2c1`. Local
package verification only; hosted CI and uploaded-artifact round trip remain
unverified, so P14 stays `IN_PROGRESS`.

**P14 p279/p281 local package pair (2026-10-13):** portable p279 (10,365
files, 426,881,302 bytes) and sanitized source p281 (24,959,470 bytes) pass
manifest `local-20261013-p281` byte verification with `local-worktree`
provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
Source p281 includes the QQ recovery integration regression. This is local
build evidence only; hosted Actions/artifact transfer remain unverified and
P14 remains `IN_PROGRESS`.

**P13 media-upload rejection regression (2026-10-13):** a synthetic WeCom
chunk rejection now asserts upload stops before finish and before any message
send. The focused sender/media suite passes 17 tests. This verifies failure
handling only; no bot credentials, network target, real delivery, or official
receipt was exercised, so P13 remains `IN_PROGRESS`.

**P11 paged attachment preview fix (2026-10-13):** clicking a server-paged
WeCom/QQ media row now resolves and previews the selected `media_entry.resource`
by its `resource_index`, instead of incorrectly requiring the resource to be
present in the parent message's attachment array. Focused regressions cover
resolver arguments, URL writeback, and preview type/name; media gallery,
resource rendering, and chat-context tests pass. This is synthetic UI evidence;
real-account media remains unverified and P11 stays `IN_PROGRESS`.

**P11 QQ forwarded WebM preview (2026-10-13):** the forwarded-resource reader
now preloads `video/webm` as well as MP4, matching the renderer and provider's
supported MIME types. The regression verifies the actual `<video>` preview and
download affordance for WebM. Focused forwarded-attachment, media-gallery,
WeCom-resource, and media-context tests pass. This does not verify a real QQ
cache or browser codec support; P11 remains `IN_PROGRESS`.

**P13 WeCom rejected media chunk (2026-10-13):** synthetic sender/media tests
verify a rejected upload chunk stops before upload finish and before message
delivery; the focused suite passes 17 tests. No live bot target or receipt was
used, so P13 remains `IN_PROGRESS`.

**P14 package-manifest regression and CI toolchain audit (2026-10-13):** the
manifest self-test passes, including hidden-file roundtrip, artifact path,
provenance, byte-change, and reparse-point checks. The hosted Windows workflow
installs GCC from a rolling MSYS2 repository while the QCE build requires GCC
16.2.0; drift therefore fails closed, but long-term hosted build availability
is not pinned. No hosted run or artifact transfer was performed; P14 remains
`IN_PROGRESS`.

**P14 p284/p286 local package pair (2026-10-13):** portable p284 (10,366
files, 426,893,707 bytes) includes the WebM preview change; sanitized source
p286 includes the latest source and docs. Manifest `local-20261013-p286`
verifies both packages with `local-worktree` provenance
against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. The packaged QCE
server SHA-256 is `ecccb81b66d6722d7016ca22a1529d9604f11003e2caa659026e1d063ad67b9d`;
the staged QCE plugin runtime SHA-256 remains
`9e1dc9ba45ce0f536a575ec2b1f0895d35240422976ecb5809d3e0e1bb55c2c1`. This is
local package evidence only; hosted Actions and artifact transfer remain open.

**P9 QQ rich-history summaries (2026-10-13):** QQ normalization now preserves
bounded display summaries for structured long-message and tofu-record elements
without retaining their resource IDs, XML, or raw payload. All 62 QQ provider
tests and the rich-content WebUI regression pass. This is synthetic parser/UI
evidence only; QR confirmation, live QCE history, and real cached media remain
unverified, so P9 remains `IN_PROGRESS`.

**P13 WeCom fragmented WebSocket receipts (2026-10-13):** the standard-library
client now reassembles valid fragmented text responses, answers interleaved
ping frames, ignores pong frames, and caps the aggregate message at 1 MiB. The
18 focused sender/media tests and Python syntax checks pass. No real bot
credential or delivery was used; P13 remains `IN_PROGRESS`.

**P14 p287/p290 package refresh (2026-10-13):** portable p287 and sanitized
source p290 are built from the current QQ history and WeCom protocol changes;
manifest `local-20261013-p290` pairs them against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` using `local-worktree` provenance.
Local privacy/startup and byte verification pass. Hosted Actions and remote
artifact transfer remain unverified, so P14 remains `IN_PROGRESS`.

**P14 full current-worktree gate and source refresh (2026-10-13):** the complete
`scripts/check-local-hub.ps1` gate passes with 572 Python tests, four
source-mutation fail-closed tests, the WebUI suite, syntax checks, package
privacy/startup smokes, manifest self-tests, and the synthetic 25,000-message
benchmark. The sanitized source bundle was refreshed as p291 to include this
verification record; manifest `local-20261013-p291` byte-verifies it alongside
portable p287 with `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and remote artifact
transfer remain unverified, so P14 remains `IN_PROGRESS`.

**P14 GCC archive pin follow-up (2026-10-13):** the Windows workflow now
downloads GCC 16.2.0-5 and its matching libgcc/libstdc++ packages from the
official MSYS2 mirror, verifies each SHA-256 before installation, and checks
the installed compiler version. It uses the setup action's reported MSYS2
directory rather than assuming a runner path. The three archives were
downloaded and their hashes verified against the [official GCC package](https://packages.msys2.org/packages/mingw-w64-x86_64-gcc), [libgcc package](https://packages.msys2.org/packages/mingw-w64-x86_64-libgcc), and [libstdc++ package](https://packages.msys2.org/packages/mingw-w64-x86_64-libstdc%2B%2B)
pages. The setup action is configured to update its package database and
installed packages first. A read-only pacman transaction against the older
local MSYS2 installation correctly rejected its `16.2.0-4` runtime dependency;
it changed no local packages and does not verify the hosted sequence. Workflow
YAML and embedded PowerShell syntax parse locally. Hosted installation, the
full Actions run, and uploaded-artifact round trip remain unverified.

**P4/P11 local-source sync acceptance follow-up (2026-10-13):** read-only sync
completed against the already-connected local WeChat and WeCom sources, and
both reported a completed last-sync state. No account identifiers, message
contents, or per-source counts were copied into repository files. This does
not verify cached-avatar/media acquisition or the latest package's live UI;
those P11 checks remain open. QQ QR/history acceptance also remains open.

**P11 QQ media compatibility follow-up (2026-10-13):** QQ chat bubbles and the
media gallery now read the provider's `extra.media` compatibility field when
`extra.roaming_resources` is absent, while retaining the latter as authoritative
when it is an array. Focused synthetic WebUI regression tests pass. Real QQ
cache availability, avatar rendering, and refreshed-app browser acceptance
remain unverified; P11 stays `IN_PROGRESS`.

**P8/P14 regression follow-up (2026-10-13):** annual pair-report response
timing now applies the selected UTC+08 calendar-year bounds before calculating
adjacent-message pairs; a New Year boundary regression covers a prior-year
counterpart. The package smoke structural check now matches the checksum-pinned
GCC/MSYS2 workflow (`update: true`, action-reported install path, and pinned GCC
archive hash) instead of requiring the superseded `update: false` workflow.
The complete local gate passes with 572 Python tests, four source-mutation
fail-closed tests, the WebUI suite, privacy/package startup checks and the
synthetic benchmark. Hosted Actions and artifact transfer remain unverified;
real-account analytics and media acceptance also remain open.

**P14 current-worktree package refresh (2026-10-13):** portable p295 rebuilt
the bundled QCE server with GCC 16.2.0 and passed isolated UI, three-source,
read-only MCP HTTP, and standalone MCP stdio startup checks without installed
Python/Node. Sanitized source p295 contains 671 files and passed privacy and
extracted startup checks. Manifest `local-20261013-p295` verifies exact package
bytes and `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and remote artifact
transfer remain unverified; P14 stays `IN_PROGRESS`.

**P9 live runtime recheck (2026-10-13):** the Hub instances on 8772, 8773,
and 8776 still report `login_required`; their NapCat login-status endpoint is
unavailable. NapCat WebUI at loopback port 6099 currently presents its local
token-login page, while the QCE/bridge ports 40653/40654 are not listening.
The existing launcher refuses to start a second QQ client while `QQ.exe` is
running. No credential was entered and no process was started or stopped; QR,
history, and media acceptance still require the user to exit QQ and retry in
the Hub.

**P11 media sender avatars (2026-10-13):** chat-media cards now show the
indexed sender's avatar and name through the existing local avatar proxy; QQ
global-cache entries without a sender remain anonymous. Synthetic WebUI
coverage passes. Real cache hit-rate and cross-platform visual acceptance
remain open.

**P14 p305 artifact re-verification (2026-10-13):** manifest p305 reverified
the exact portable p303/source p305 bytes and local-worktree provenance; source
inventory/privacy and manifest/privacy fixtures pass. No remote workflow or
artifact transfer was run; P14 remains `IN_PROGRESS`.

**P8-P14 local regression gate (2026-10-13):** `scripts/check-local-hub.ps1`
completed with 578 Python tests and the four source-mutation fail-closed tests
passing; the WebUI suite, isolated package/privacy checks, compilation, and
synthetic benchmark also ran without a failure. This is local synthetic and
packaging evidence, not hosted CI or real-account acceptance.

**P11 local avatar-cache probe (2026-10-13):** on the already-connected
read-only local instance, a bounded sample of eight exact `hub-avatar://`
handles returned HTTP 200 image responses. No contact identities or image
bytes were recorded. This confirms cache retrieval only; latest-package browser
rendering, other platform avatars, and QQ cache/media acceptance remain open.

**P11 real cached-image probe (2026-10-13):** on the same read-only local
instance, 2 of 3 sampled WeChat image records resolved to local image responses.
Three sampled WeCom and three sampled QQ image records did not yield local image
responses. This bounded sample records no message identities or bytes and does
not establish complete cache coverage or latest-package UI rendering; P11
remains `IN_PROGRESS`.

**P11 avatar URL boundary follow-up (2026-10-13):** WebUI avatar rendering now
accepts same-origin paths only for `/api/avatar`, the Hub's local-cache proxy;
arbitrary provider-supplied paths cannot initiate unrelated same-origin GETs.
External allowlisted avatar URLs still resolve only through the existing local
cache, never by network fetch. Focused regression coverage passes.

**P14 p299 local package verification (2026-10-13):** The portable Windows
package rebuilt the bundled QCE server with GCC 16.2.0 and passed isolated
WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP stdio, and
privacy checks. Sanitized source p299 contains 671 files and passed archive
privacy and extracted startup checks. Manifest `local-20261013-p299` pairs
portable p299 with source p299 using `local-worktree` provenance against base
HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; manifest byte verification
passed. The source ZIP is deterministic across repeated builds. Hosted Actions
and artifact transfer remain unverified, so P14 remains `IN_PROGRESS`.

**P14 CI documentation correction (2026-10-13):** `docs/windows-package.md`
now reflects the hosted workflow's actual `update: true` MSYS2 setup followed
by SHA-256-pinned GCC 16.2.0 package installation; it no longer claims CI
avoids a full-system update. Local package-manifest and smoke regressions pass.
No hosted run or artifact transfer was invoked, so this does not close P14.

**P14 p301 source refresh (2026-10-13):** sanitized source p301 includes the
CI documentation correction and current roadmap/log; its 671-file archive
passed privacy scanning and extracted startup checks. Manifest
`local-20261013-p301` pairs it with portable p299 and verifies exact bytes with
`local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. QQ remains `login_required` on
the currently probed Hub, with no QR confirmation or history sync. Hosted
Actions and artifact transfer remain unverified; P9 and P14 stay `IN_PROGRESS`.

**P11 search-result avatars (2026-10-13):** global message search now falls
back from a missing conversation avatar to the exact platform/account/contact
avatar, matching the conversation-list behavior. Explicit conversation avatars
including the empty-string case still win. Synthetic tests cover NULL/empty
fallback, precedence, and account isolation; remote avatars remain cache-only
and real-account rendering is unverified.

**P14 current-worktree package refresh (2026-10-13):** portable p303 rebuilt
the bundled QCE server with GCC 16.2.0 and passed isolated WebUI/Lucide,
three-source, read-only MCP HTTP, standalone MCP stdio, and privacy checks.
Sanitized source p303 contains 671 files and passed privacy scanning and
extracted startup checks. Manifest `local-20261013-p303` verifies both package
byte sets with `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and artifact
transfer remain unverified; P14 stays `IN_PROGRESS`.

**P14 p308 portable refresh (2026-10-13):** rebuilt the current worktree with
the latest P11 media sender-avatar UI and P13 attachment approval previews.
Isolated WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP stdio,
and package privacy checks passed. Sanitized source p311 contains 671 files and
passed privacy and extracted-startup checks. Manifest `local-20261013-p311`
verifies p308 and p311 with
`local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact transfer
and real QQ login/history remain unverified, so P9, P11, and P14 remain
`IN_PROGRESS`.

**P14 stale-artifact follow-up (2026-10-13):** fresh verification found that
the p308 and p310 manifests no longer match the existing p308 portable
directory (`LocalMessageHub.exe` hash changed after manifest creation). Those
manifests are not valid evidence for the current bytes. Rebuild to a new
directory and verify its portable/source pair before claiming a current local
release artifact. Hosted Actions and real-account acceptance remain open.

**P14 p312 local rebuild (2026-10-13):** fresh portable p312 passed isolated
WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP stdio, and
privacy checks; the vendored QCE server rebuilt with the pinned GCC toolchain.
Sanitized source p313 contains 671 files and passed privacy plus extracted
startup checks. Manifest `local-20261013-p313` verifies portable p312 and
source p313 bytes with `local-worktree` provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact transfer,
QQ login/history, and real-account media acceptance remain open; P9, P11, and
P14 stay `IN_PROGRESS`.

**P9/P11 local follow-up (2026-10-13):** QQ message-page reads now retry one
time only for transient transport failures and HTTP 5xx; auth/client errors
and non-idempotent roaming-export actions are not retried. MCP QQ cached-media
listing now accepts inclusive UTC+08 start/end dates and forwards millisecond
bounds to the existing local gallery provider. Focused retry, date-boundary,
invalid/reversed-range, and opaque-media tests pass. Real QQ login/history,
real-account media, and hosted artifact transfer remain unverified.

**P14 p314/p315 local package refresh (2026-10-13):** portable p314 passed
isolated WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP stdio,
privacy, and bundled-QCE build checks. Sanitized source p315 contains 671 files
and passed archive privacy and extracted startup checks. Manifest
`local-20261013-p315` matched both package byte sets with `local-worktree`
provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
Hosted Actions/artifact transfer and real-account acceptance remain open.

**P14 performance benchmark refresh (2026-10-13):** a fresh synthetic run
ingested 25,000 messages in 1.316 s, returned the first indexed search in
0.031 s, paged all 25,000 rows in 50 pages in 1.458 s, and completed backup/
restore in 0.083/0.244 s. The 200-message WAL writer completed without reader
failures. These figures are local synthetic evidence, not browser RSS or
real-account workload acceptance; hosted CI/artifact transfer remain open.

**P14 p338 source refresh (2026-10-13):** sanitized source p338 contains
673 files and includes the benchmark record above; archive privacy and
extracted WebUI/API/MCP startup checks pass. Manifest `local-20261013-p338`
matches source p338 and portable p332 with `local-worktree` provenance against
base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and artifact
upload/download remain unverified.

**P14 Git-visible privacy gate (2026-10-13):** the local acceptance gate now
scans tracked, modified, staged, and non-ignored untracked files for known local
account/config artifacts, current-user paths, and common high-confidence GitHub,
AWS, Slack, and private-key formats. Synthetic Git-repository tests cover those
findings and confirm ignored local files are excluded. The upstream NapCat/QCE
default config is allowlisted only at its exact vendor template path after
content review. Binary payload contents are not scanned by these text rules;
upstream archives and branded assets still rely on pinned provenance and manual
review. The 591-test Python and 27-test WebUI local gate passed, and sanitized
source p339 (673 files) passed archive privacy and extracted startup checks;
manifest `local-20261013-p339` verified its bytes with portable p332 against
base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted workflow evidence
remains open.

**P11/P13 verification follow-up (2026-10-13):** MCP `list_media` now exposes
exact conversation-ID filtering and requires the matching platform and account;
synthetic same-title conversations verify identity isolation. The WeCom sender
regression simulates a connection reset after a possible partial message write
and verifies status stays `unknown` across repeat approval with exactly one
send attempt. A stale attachment-review UI assertion was updated to require
the current local download review link. Focused checks pass; live bot delivery
and real-device media acceptance remain open.

**P14 p317/p318 local delivery refresh (2026-10-13):** the complete local gate
passed with 583 Python tests, 25 WebUI tests, source-mutation checks, privacy and
manifest gates, isolated portable/source startup, read-only MCP checks, and the
25,000-message memory benchmark. Portable p317 includes the freshly rebuilt
loopback QCE runtime; sanitized source p318 contains 671 files. Manifest
`local-20261013-p318` verifies both package byte sets with `local-worktree`
provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
Hosted Actions/artifact transfer and real-account acceptance remain unverified;
P9, P11, P12-01, P13, and P14 remain `IN_PROGRESS`, while P12-03/04 remain
`NOT_STARTED` pending native-channel evidence, isolated backup/rollback tests,
and explicit operation-level approval.

**P9 login diagnostic follow-up (2026-10-13):** the QQ login-status endpoint
now returns only allowlisted error codes for unavailable NapCat, authentication,
or local-configuration failures; the UI translates them into actionable text
without exposing exception content, credentials, or QR payloads. Focused server
and UI tests pass. A read-only local check found the NapCat WebUI reachable but
not authenticated by the Hub. No QQ/NapCat process was started or stopped and
no real history sync was performed; P9 remains `IN_PROGRESS` pending a valid
QQ scan and account/history acceptance.

**P12 native-trigger fail-closed regression (2026-10-13):** the isolated WCDB
optional-export harness now verifies that absent anti-revoke/Moments trigger
symbols return unavailable without native calls, and blank message session IDs
are rejected before trigger invocation. This verifies wrapper behavior only;
P12-03/04 remain `NOT_STARTED` because the native implementation and its
isolated lifecycle/rollback evidence are still absent.

**P12 HTTP mutation-boundary follow-up (2026-10-14):** the local source-mutation
regression now verifies that the actual Moments block-delete install and
uninstall routes (`POST`) and post-delete route (`DELETE`) dispatch to their
handlers, and that each handler keeps its provider call inside
`denySourceMutationRoute`. The focused script passes all four tests. This is
route-binding evidence only; P12-03/04 remain `NOT_STARTED` pending inspectable
native semantics, isolated backup/rollback lifecycle tests, and explicit
operation-level approval. No source client or database was accessed.

**P11 daily send-intensity parity (2026-10-13):** added a capability-gated,
read-only daily self-send distribution sourced from the Hub SQLite index. It
matches WeFlow's private-chat-only scope, UTC+08 calendar days, zero-filled
days, full-range daily baseline, 7-day moving average, and 31-day visible chart;
the API retains only 37 recent daily buckets while computing its baseline over
the full selected history. Synthetic analytics, HTTP/capability, and WebUI
rendering checks pass. Real-account analytics acceptance, broader P11 resource
parity, and P11-02 export acceptance remain open; P11 stays `IN_PROGRESS`.

**P11 private-session classification correction (2026-10-13):** daily self-send
analytics now requires a matching conversation row with a recognized private
kind; orphan messages with unknown scope are excluded instead of assumed
private. Synthetic service and HTTP fixtures cover explicit private scope and
the orphan exclusion. All 22 analytics tests and the focused endpoint/capability
regression pass. A WebUI regression covers all nine message export formats,
chat/search identity and filter forwarding, download naming, and request-failure
recovery. Portable p327 and sanitized source p330 passed local isolated
startup/privacy checks; the complete local gate passed 587 Python tests and
all registered source-mutation/WebUI/package/privacy checks. Source p331 and
manifest `local-20261013-p331` include that evidence and verify the current
source package with portable p327.
Hosted Actions/artifact transfer and real-account acceptance remain open.

**P11/P12/P13 local boundary follow-up (2026-10-13):** valid cached WeChat
BMP images now render inline only when the file begins with the BMP signature;
invalid `.bmp` payloads remain forced downloads. Automatic-export destinations
are resolved before directory creation and reject junction/symlink targets
that resolve to UNC paths or mapped network drives. The WeCom WebSocket reader
rejects malformed one-byte Close-frame payloads as protocol errors. Focused
HTTP, auto-export/account-safety, and sender tests pass; the complete local gate
passes with 594 Python tests, four source-mutation tests, and 27 WebUI tests.
No live source client or sender was contacted.

**Local acceptance refresh (2026-10-13):** clearing Hub-owned indexed data now
preserves the configured WeCom bot sender adapter; a synthetic regression
confirms request preparation remains available without delivering a message.
The complete local gate passes with 625 Python tests, four fail-closed
source-mutation tests, all registered WebUI tests, package privacy/manifest and
isolated startup checks, compilation, and the synthetic performance benchmark.
Hosted Actions/artifact round-trip remain unverified. QQ debugging is not yet an
integrated Hub feature; its local history reader remains read-only and does not
launch or debug the client. Existing real-account, native source-mutation and
hosted-release acceptance gaps remain open.

**Performance and usability follow-up (2026-10-13):** indexed context and
conversation queries, streamed QQ binary hashing, leaner WeChat contacts and
ChatLab projection, cursor-based export paging, stable source/media polling,
stale-response guards, contact-export filter preservation, locked-instance
reuse and refresh-safe page navigation are implemented. Focused checks pass;
synthetic before/after measurements and limitations are in performance.md.
Four idle previews were retired without stopping QQ/NapCat. Active 8772 backend
restart was rejected before execution, so its backend update remains pending.
This performance work does not complete outstanding real-account, native-channel
or hosted-delivery acceptance; roadmap statuses remain unchanged.

**QQ read-only key discovery integration (2026-10-13):** the QQ data-source UI
now offers an explicitly confirmed action when the local database reports a
missing/invalid key. It scans only same-user running QQ processes through
read/query-only handles, with one job, a 180-second deadline, and a 512 MiB
aggregate read cap. A candidate is persisted only after encrypted-page and
provider validation; status output is sanitized and discovery routes require
the sync capability. Synthetic coverage verifies confirmation, capability,
success/failure persistence boundaries, budget sharing, and UI dismissal.
The current real QQ source is already connected, so no live scan was performed.
This closes the Hub-integration gap only; it does not close real attachment,
native mutation, full parity, or hosted release acceptance.

**Local acceptance refresh (2026-10-13):** the complete `scripts/check-local-hub.ps1`
gate passes with 629 Python tests, four fail-closed source-mutation tests, 30
WebUI tests, package privacy/manifest and isolated startup checks, compilation,
and the synthetic performance benchmark. No real QQ key scan or source-client
mutation was run. Hosted Actions/artifact round-trip, real media coverage,
native source mutation and full WeFlow parity remain open; roadmap statuses do
not change from this local-only verification.

**P14 QQ local-reader package refresh (2026-10-13):** both package builders now
ship the local QQ database, WAL, and read-only key-discovery modules, with
explicit PyInstaller dynamic imports. Source p343 passed privacy and isolated
startup checks; portable p342 built the pinned QCE runtime and passed bundled
Hub/WebUI/source-list/MCP startup checks. The local-worktree manifest verifies
the p342 portable and p343 source package byte sets against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
This does not constitute a hosted release or real-account scan; P9/P11/P12-01/
P13/P14 stay `IN_PROGRESS`, and P12-03/04 stay `NOT_STARTED` pending their
separate account, native implementation, approval, and rollback evidence.

**QQ key-discovery owner check and final local gate (2026-10-13):** process
owner filtering now extracts the SID pointer from the Windows `TOKEN_USER`
buffer before comparing it with the current user; a regression test covers the
token layout. This fixes candidate-process discovery but does not run a live
scan. WeChat SILK decoding has bounded input and output, and WeCom media protocol
writes honor the shared deadline. The local gate passed 642 Python tests, four
source-mutation guard tests, 27 WebUI tests, privacy/package checks, compilation
and synthetic benchmarks. P9/P11/P12-01/P13/P14 remain `IN_PROGRESS`; P12-03/04
remain `NOT_STARTED` pending real-account, hosted-release, and native-channel
evidence.

**P11/P13 adapter-to-delivery boundary follow-up (2026-10-13):** a synthetic
WeCom cache fixture now flows through the real `WeComAdapter.media` resolver,
Hub identity checks, and cancellable resource ZIP worker. The regression checks
the selected attachment index and bytes, SHA-256 manifest, absence of cache
paths, and unchanged source hashes. A second regression directly verifies that
the production WebSocket wrapper forwards each remaining timeout to its
underlying socket. Focused tests pass; live device delivery and real cached
media acceptance remain open, so P11 and P13 stay `IN_PROGRESS`.

**P14 clean-checkout build follow-up (2026-10-13):** inspection found that the
Windows CI portable-build job did not install root npm dependencies, although
the builder requires `ffmpeg-static/ffmpeg.exe`. The workflow now installs the
tracked root lockfile with lifecycle scripts disabled, runs the pinned package's
binary installer explicitly, and verifies the binary before packaging. The
local package-smoke suite passes and asserts this workflow ordering. This is
only local verification of the workflow contract; the changes are not yet in a
clean committed checkout, and hosted Actions plus artifact upload/download
remain unverified. P14 remains `IN_PROGRESS`.

**P11 WeChat avatar freshness follow-up (2026-10-13):** when the local
`head_image` database contains multiple cached rows for one exact username,
avatar sync now selects the newest signature-valid image by `update_time`;
older duplicates no longer overwrite it during an unordered read. A synthetic
fixture inserts the newer image first and an older image afterward, proving the
time-based selection. This reads only the existing local encrypted database
through its cached key and writes only the Hub-owned avatar cache. No real
account data was inspected; P11 remains `IN_PROGRESS`.

**P11 WeChat roster avatar follow-up (2026-10-14):** group-member responses now
attach avatar references in one bounded query from the exact indexed WeChat
account, allowing the existing same-origin cache proxy to render local avatars
in the roster modal. A synthetic HTTP fixture verifies that an identical member
ID in another account cannot supply the displayed avatar. No source database or
network avatar lookup was added; real-account visual/schema acceptance remains
open, so P11 remains `IN_PROGRESS`.

**P8/P14 local acceptance refresh (2026-10-13):** the QQ key-discovery dialog
now discloses that successful verification saves the key locally and starts
local QQ history sync. Portable p357 and sanitized source p358 pass their
isolated startup checks; source privacy checks pass. The full local gate passes
646 Python tests, four fail-closed source-mutation tests, 27 WebUI tests,
package/privacy/startup checks, compilation, and the 25,000-message synthetic
benchmark. The Hub at `127.0.0.1:8774` reports all three sources disconnected;
no QQ process scan or source sync was run. The local-worktree manifest records
package hashes and provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact
round-trip, real account and media acceptance, native mutation, and full
WeFlow parity remain open; P14 remains `IN_PROGRESS`.

**QQ discovery deadline follow-up (2026-10-14):** the monotonic deadline now
covers database discovery, process enumeration/opening, and memory reads; process
candidates are capped at 256 and truncation is reported as `limit_reached`.
Eight synthetic tests cover the bound and handle cleanup. The later explicitly
confirmed live read-only attempt reached a scan safety limit and did not verify
or save a key. Real-account connection and historical-message sync remain open.

**P11 MCP import-pagination follow-up (2026-10-14):** `list_imports` now applies
`limit`/`offset` in SQLite and returns the full job count instead of slicing an
in-memory first-100 list. A 101-job regression verifies the second page and
total. This closes synthetic pagination behavior only; it does not alter source
or account acceptance.

**P11 WeChat group-roster avatar follow-up (2026-10-14):** the current roster
page now receives avatar references from the exact `wechat + account + contact`
identity through one bounded lookup, allowing the existing same-origin avatar
proxy to render them. A cross-account duplicate-ID regression passes; live
account rendering and alternate database schemas remain unverified.

**P11 Moments scan-budget correction (2026-10-14):** the local timeline reader
now accounts the UTF-8 XML body bytes, not the timestamp column, against its
32 MiB aggregate scan budget. A two-row synthetic regression proves it stops
before reading a later row once the budget is exhausted. This corrects the
implementation bound but does not widen recognized-schema or real-account
acceptance.

**P12 lock interaction follow-up (2026-10-14):** the unlock form prevents
parallel submissions, exposes validation busy state, and clears/refocuses the
passphrase input after failure. An isolated browser-DOM test covers failure,
duplicate submit and success navigation and is included in the local gate;
actual-device visual/keyboard acceptance remains open.

**P12 Moments XLSX follow-up (2026-10-14):** filtered Moments export now
supports XLSX alongside JSON/CSV/Markdown/TXT/HTML. The writer emits escaped
inline text, excludes media URLs/keys/tokens, enforces Excel's cell-size bound,
and aborts when the generated archive exceeds 32 MiB. Focused renderer, HTTP,
and WebUI regressions pass. This does not close real-account schema/cache
acceptance or P11 full resource-library/decryption parity.

**P11 Moments media follow-up (2026-10-14):** the explicit local MCP media
reader now supports verified AVIF/HEIC cached images. It checks the bounded
ISO-BMFF `ftyp` box and requires a recognized image brand; a mislabeled or
lookalike file fails closed. Synthetic resolver and MCP response tests pass.
This adds no network lookup and does not replace real-cache/browser/client
acceptance.

**P9 bounded QQ candidate scan follow-up (2026-10-14):** within each already
read process-memory chunk, key discovery now checks database-context candidates,
documented key forms, and generic 16-byte candidates in priority order. It no
longer spends the shared byte budget on a priority sweep before a second generic
sweep; SQLCipher-context key/MAC reads now also consume the same shared budget,
closing a path that could exceed the 512 MiB ceiling. The monotonic deadline,
process cap, and read-only handles remain. Synthetic regressions verify generic
candidate authentication within one pass and context-read budget enforcement.
A subsequent user-approved live scan through the running local Hub returned
`limit_reached`; the source still requires a key, no key was saved, and no QQ
sync started. The status does not distinguish its specific bound, so QQ source
connection/history sync remain unverified and P9 remains `IN_PROGRESS`.

**P11 exact ISO-BMFF image-brand follow-up (2026-10-14):** Moments cache media
classification now validates `ftyp` box length/alignment and compares exact
4-byte major/compatible brand slots for AVIF/HEIC, rejecting cross-slot
substring lookalikes. Focused Moments/media tests pass (18 tests). This is local
synthetic evidence only; cached real-account resource and browser acceptance
remain open, so P11 remains `IN_PROGRESS`.

**P14 local Windows package follow-up (2026-10-14):** built a real CPython 3.12
Windows portable directory in `%TEMP%` with the QCE/NapCat runtime deliberately
omitted, matching the current local-database/no-QR direction; at that point the
pinned Rust toolchain had not yet been found outside `PATH`. The
7,542-file, 287,772,374-byte package
started its WebUI and standalone read-only MCP in isolated empty storage,
without installed Python or Node; the package privacy scan passed. A fresh
sanitized source ZIP (684 included source files) and deterministic package
manifest were produced, then a copied package/ZIP/manifest passed the actual
byte-and-provenance verifier. The package README accurately states local QQ
history and discloses that QR, NapCat, and QCE cache-gallery features are absent
in this variant. The complete local gate passed 668 Python tests, four
fail-closed mutation tests, 35 WebUI tests, and the synthetic performance/backup
benchmark. This does not verify the default QCE-enabled build, a clean committed
checkout, hosted Actions, or hosted artifact upload/download; P14 remains
`IN_PROGRESS`.

**P11/P13 local bounded-work follow-ups (2026-10-14):** Moments timeline
pagination now retains only the requested page while scanning for exact
filtered totals and preserving order; 11 reader tests and the focused HTTP
route test pass. Moments JSON export now uses explicit field allowlists and
omits other indexed account IDs and unknown internal fields; 32 analytics and
export tests pass. WeCom startup cleanup now reclaims only stale, correctly
named orphan `.part` uploads while preserving recent staging files and live
attachments; 30 media/sender/credential tests pass. These are synthetic/local
checks and do not close real-account Moments/media or real WeCom delivery
acceptance; P11 and P13 remain `IN_PROGRESS`.

**P9 shared scan-budget follow-up (2026-10-14):** direct SQLCipher context
key/MAC reads and process-memory chunk reads now share the same 512 MiB budget;
the scan can no longer exceed that ceiling through context verification. The
32-test QQ reader/WAL/provider suite passes. No live process was accessed for
this change; the approved live attempt remains `limit_reached`.

**P14 current-worktree package refresh (2026-10-14):** rebuilt the QCE/NapCat-
omitted Windows portable package from the current source; its 7,546 files
(287,850,999 bytes) passed isolated UI and standalone read-only MCP startup
smokes without installed Python or Node. A sanitized 686-entry source ZIP
(about 25 MB) passed extracted startup and privacy checks. The full local
gate passed 673 Python tests, four fail-closed mutation tests, the WebUI suite,
and the synthetic performance/backup benchmark. A deterministic local-worktree
manifest binds both packages to base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`;
a copied delivery pair passed byte and provenance verification. Default
QCE-enabled build, clean committed-checkout verification, hosted Actions, and
hosted artifact transfer remain unverified; P14 stays `IN_PROGRESS`.

**P11 QQ image-signature and P12 schedule-race follow-up (2026-10-14):** QQ
forwarded-media classification now validates ISO-BMFF `ftyp` length, alignment,
and exact major/compatible brand slots, rejecting minor-version substring
lookalikes and malformed boxes. The QQ provider suite and scheduled-export suite
pass together (80 tests); the latter also covers that disabling a due schedule
cannot race worker startup. These are synthetic/local checks only.

**P14 pinned QCE build probe (2026-10-14):** located the pinned Rust/GCC toolchain
in its existing private tool directory and built `qce-server.exe` with the
locked build in an isolated temporary directory. Its SHA-256 was independently
verified as `823a6577252092b46926a0917024130619bceac0588028c119bbd883466d38dd`.
No package was produced: the portable builder cannot consume a prebuilt QCE
binary and would rebuild it; adding that build contract and the QCE-enabled
package smoke remains open. No NapCat or QQ process was started. P14 remains
`IN_PROGRESS`.

**P14 final local package refresh (2026-10-14):** rebuilt the QCE/NapCat-omitted
portable package after the QQ resource-signature and scheduled-export race fixes.
The 7,546-file package (287,861,409 bytes) passed isolated UI/MCP startup and
privacy checks. The sanitized 686-file source ZIP (about 25 MB) passed
extracted UI/MCP smoke and privacy checks. A local-worktree manifest was
verified against a copied delivery directory by exact file bytes and
provenance. QCE compilation passed separately, but the builder does not yet
package a prebuilt QCE binary; hosted CI and artifact transfer remain open, so
P14 stays `IN_PROGRESS`.

**P11/P12/P13 safety follow-ups (2026-10-14):** WeChat cached attachment
staging now caps one file at 50 MiB and rejects growth or size changes during
copy without replacing a prior complete cache file (synthetic media/provider
tests pass). Account-lock setup now atomically checks/configures the first
passphrase and creates the session, preventing two concurrent first setups
from both succeeding. WeCom bot credential validation now matches the send
queue's bot-ID contract. Focused P11/P12/P13 suites pass; no real account,
credential, or message delivery was used. Device/browser acceptance remains
open.

**P9 bounded QQ scan retry (2026-10-14):** one user-approved read-only scan was
started through the already-running Hub with the existing 180-second and
512-MiB limits. The Hub returned `limit_reached` with only its generic safety-
limit message; its live status endpoint does not expose which bound ended the
run. QQ remains `key_required`, no key was saved, and history import did not
start. No QQ database write or client restart occurred. A source-code fix now
exposes only a sanitized limit-reason category, but the already-running Hub
has not reloaded that code; P9 remains `IN_PROGRESS`.

**P14 QCE-enabled package refresh (2026-10-14):** a prebuilt QCE binary path
now requires an independently supplied matching SHA-256; source compilation
remains the default. The isolated Windows portable build contains 9,775 files
(586,359,542 bytes) and is at
`%TEMP%\LocalMessageHub-portable-qce-final-d215d8937522436b92d26b7501722405`.
Its package smoke, three-source/read-only MCP checks and privacy scans pass.
The packaged WeChat attachment module exactly matches the worktree SHA-256
`a68e2f6ddf5100877b20db40f26cd9d53d4badb8ebe99cc08898300135817725`; the
30,974,233-byte QCE executable matches its pinned build hash. No NapCat/QCE
server was launched. A sanitized 686-file source ZIP also
passed extracted startup and privacy checks. A `local-worktree` manifest for
both artifacts was verified by exact bytes and provenance against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted CI and artifact transfer
remain open, so P14 stays `IN_PROGRESS`.

**P11/P12/P13 regression and full-gate follow-up (2026-10-14):** Moments XML
traversal is now iterative, preserving document order while avoiding Python
recursion limits for deeply nested posts (1,500-level synthetic regression).
WeCom bot heartbeat timestamps are scoped to each delivery connection, so a
reused sender cannot carry stale ping state into a new connection. Notification
SSE events re-check the currently selected platform, exact account/conversation
allowlist, and mute list at delivery time; mute takes precedence. Focused
Moments, media, bot sender/media, and notification tests pass. A missing return
in the service test's `seed_moments` fixture was repaired after the first full
gate exposed dependent failures. The refreshed local gate passed 701 Python
tests, four fail-closed source-mutation tests, all 27 WebUI tests, package/privacy
and extracted-startup checks, deterministic source ZIP verification, and the
synthetic 25,000-message search/pagination/backup benchmark (0 WAL failures).
A subsequent approved, bounded QQ scan again reached `limit_reached`; no key was
verified or saved and no history import started. A lock-screen transition
barrier now rejects late API responses and stale UI writes; its asynchronous
response regression passes. P9 real-history acceptance, P11 full WeFlow resource
and real-cache acceptance, P12 real browser/device acceptance, P13 real bot
delivery, and P14 hosted CI/artifact transfer remain open.

**P9/P11/P12/P14 regression follow-up (2026-10-14):** the approved QQ
read-only key scan's live status remains `limit_reached` / `key_required`; no key
was verified or saved and no history import started. QQ attachment image
classification now validates ISO-BMFF `ftyp` length/alignment and complete
brand slots, with regressions for minor-version and misaligned lookalikes.
Capability tests prove `anti_recall` and `qq_history` cannot be enabled through
settings or survive restart, and the UI keeps them unavailable/non-interactive.
Package manifest tests now reject modified source ZIPs, portable inventory
additions/omissions, and traversal paths. The full local gate passed 725 Python
tests, four mutation-gate tests, all 27 WebUI tests, package/privacy and
extracted-startup checks, deterministic ZIP verification, and the 25,000-message
search/pagination/backup benchmark (zero WAL failures). Local synthetic
acceptance improved, but real QQ history/media acceptance, broader WeFlow
resource parity, native source-mutation gates, real bot delivery, and hosted CI
artifact upload/download remain open; P9/P11/P12-01/P13/P14 remain
`IN_PROGRESS`, and P12-03/04 remain `NOT_STARTED`.

**P11/P12 current-workspace follow-up (2026-10-14):** annual/all-time reports
now provide stable zero-filled 12-month series; JSON, ChatLab and SQL exports
preserve genuinely repeated identical attachments while deduplicating mirrored
resource aliases; and a visible lock-enabled browser tab rechecks server session
expiry without refreshing its idle timer. Their focused tests pass. The current
full local gate passed 731 Python tests, four fail-closed source-mutation tests,
all 27 WebUI tests, privacy/package/extracted-startup checks, deterministic ZIP
verification, and the synthetic 25,000-message benchmark with zero WAL failures.
Real QQ history, WeFlow/cache parity, browser/device lock, real send delivery,
native mutation acceptance, and hosted CI/artifact transfer are still unverified.

**P9/P13 local follow-up (2026-10-14):** after a fresh one-time approval, a
bounded QQ read-only scan returned `not_found` in about 18 seconds. The source
remains `key_required`; no key was verified/saved and no history import started.
QQ reader close now clears account-bound display metadata, UID mappings, and
media-index state, with a synthetic cross-account fail-closed regression. The
WeCom bot WebSocket handshake rejects empty or duplicate response-header names,
with synthetic duplicate `Upgrade` and `Sec-WebSocket-Accept` cases. Focused
QQ provider/WAL and WeCom sender/media tests pass (59 total). Real QQ history and
real bot delivery remain unverified; P9-02 and P13-01 stay `IN_PROGRESS`.
The refreshed local gate passed 732 Python tests, four source-mutation
fail-closed tests, all 27 WebUI tests, package/privacy/extracted-startup checks,
deterministic ZIP verification, and the 25,000-message search/pagination/backup
benchmark with zero WAL failures.

**P11/P12/P13 local regression follow-up (2026-10-14):** group analytics keeps
the full observed member count when the visible-member limit is zero; scheduled
export cancellation is serialized with atomic final-file publication; and QQ,
WeChat, and WeCom approval handlers recheck capability state after the shared
route gate. The refreshed local gate passed 735 Python tests, four
source-mutation fail-closed tests, all 27 WebUI tests, package/privacy and
extracted-startup checks, deterministic ZIP verification, and the 25,000-message
benchmark with zero WAL failures. The approved QQ scan returned `not_found`,
with no key saved and no history import. Real-account acceptance, P12-03/04
native mutation work, and hosted CI artifact transfer remain open; P9/P11/P12-01/
P13/P14 remain `IN_PROGRESS` and P12-03/04 remain `NOT_STARTED`.

**P9/P11 local follow-up (2026-10-14):** QQ key discovery serializes
configuration save, verification and failed-candidate rollback with concurrent
QQ settings updates; a deterministic lock-attempt regression confirms a newer
data directory is preserved and an unverified key is not left behind. The QQ
cached-resource gallery now honors non-page-aligned offsets, follows short
responses with `hasMore=true` for at most four bounded page requests, and
reports `has_more` from the requested offset and returned item count. Focused
synthetic tests pass. No scan or real QCE/cache access was performed. Device
pagination/reader behavior and real QQ history remain unverified, so P9-02 and
P11-01/P11-02 remain `IN_PROGRESS`; P12-03/04 remain `NOT_STARTED`. The fresh
full local gate passed 738 Python tests, four fail-closed source-mutation tests,
all 27 WebUI tests, privacy/package/extracted-startup checks, deterministic ZIP
verification, and the 25,000-message benchmark with zero WAL failures (0.030 s
first search, 1.522 s for 50 pages, 0.092 s backup, 0.268 s restore). Hosted
Windows Actions and artifact round-trip are still not verified.

**QQ read-only discovery retry (2026-10-14):** following the user's fresh
one-time approval, the running local Hub accepted one scan bounded to 180
seconds and 512 MiB. It returned `limit_reached`; no candidate key was verified
or saved, and the key-discovery path therefore did not start a new history
sync. The existing QQ index was not modified. QQ real-history acceptance
remains open and another scan requires fresh approval.

**P11 QQ gallery filter follow-up (2026-10-14):** the QQ global-cache view now
offers only QCE-supported image, video, voice, and file type filters; the
WeChat cached-sticker filter remains available in chat media views. A focused
WebUI regression verifies both option sets, and app syntax validation passes.

**P11 WeChat GIF completeness follow-up (2026-10-14):** cached GIF validation
now requires the final nonzero byte to be the GIF trailer, while allowing zero
padding after it. Truncated data with nonzero bytes after a trailer-like marker
is rejected; the focused regression and full WeChat media unit module pass.

**P8/P12/P13 local hardening follow-up (2026-10-14):** capability restore now
forces core modules on even when an older settings file contains `search:false`;
the core capability tests are included in the normal local gate. A notification
already displayed before account-lock navigation can no longer switch the
selected conversation when clicked during the lock transition. WeCom staged
media is validated and uploaded from the same open file in bounded 512 KiB
chunks; a content change during upload prevents media finalization and message
delivery. `scripts/check-local-hub.ps1` passed 743 Python tests, four
source-mutation gate tests, 27 WebUI tests plus the separate daily-chart, lock,
navigation/performance checks, package/privacy and extracted-startup checks,
deterministic ZIP verification, and the 25,000-message benchmark with zero WAL
failures (0.031 s first search, 1.495 s for 50 pages, 0.094 s backup, 0.245 s
restore). These are local synthetic/package checks only; real QQ history/media,
real browser notifications, real bot delivery, P12-03/04 native interception,
clean committed-checkout validation, hosted CI, and hosted artifact transfer
remain outstanding.

**P9/P11/P14 local acceptance follow-up (2026-10-14):** QQ attachment cache
staging now copies in bounded chunks with a 50 MiB ceiling, verifies source
identity/size/mtime before and after reading, checks indexed size and optional
MD5, removes failed staging files, and preserves an existing complete cache on
failure. Moments cache parsing and MCP media reads now accept WeFlow's BMP
signature. Package-manifest output now stages beside the destination and uses
atomic replacement, preserving the previous manifest if replacement fails.
The refreshed local gate passed 746 Python tests, four source-mutation gate
tests, all 27 WebUI tests plus the separate daily-chart, lock, and
navigation/performance checks, package/privacy and extracted-startup checks,
deterministic ZIP and manifest verification, and the 25,000-message benchmark
with zero WAL failures (0.038 s first search, 1.462 s for 50 pages, 0.085 s
backup, 0.237 s restore). No new QQ scan, account access, source mutation, or
real message delivery occurred. P9 real-history acceptance, P11 broader
resource/decryption and real-cache acceptance, P12 browser/native acceptance,
P13 real delivery, and P14 clean committed-checkout/hosted CI/artifact transfer
remain outstanding.

**P9/P11/P14 media and restore follow-up (2026-10-14):** QQ local attachment
validation now accepts QCE-mapped TIFF/BigTIFF signatures, while MCP reports
`image/tiff`, rejects lookalikes, and the local media route only serves valid
TIFF inline. WeChat's shared decrypted-image validator now correctly accepts
the `BM` signature used by legacy-XOR BMP images. Media-backup restore rejects
manifests above 64 MiB using a bounded read before parsing or restoring any
index/media content; a synthetic regression verifies cleanup and no restore
side effects. Focused suites passed (327 QQ/Hub/WeChat tests and 7 backup tests).
The refreshed full local gate passed 750 Python tests, four fail-closed
source-mutation tests, WebUI checks, package/privacy/extracted-startup checks,
deterministic ZIP/manifest checks, and the synthetic performance benchmark.
The earlier user-approved QQ key scan remains `limit_reached`; no key was
saved and no history sync began. These changes improve local synthetic
coverage, but P9 real-history acceptance, broader P11 resource/decryption and
real-cache acceptance, P12 real browser/native acceptance, P13 real delivery,
and P14 clean committed-checkout/hosted CI/artifact transfer remain open.

**P11/P13 local regression and source sync follow-up (2026-10-14):** the
user-authorized WeChat-only sync completed through the running local Hub; the
source reported `connected` and its local avatar database was available. After
switching the Hub to the bundled Python runtime, the user-authorized WeCom
local snapshot sync also reported `connected`. Earlier WeCom attempts under
system Python lacked the `Crypto` dependency; no source configuration or key
was changed. WeCom contact rows did not expose avatar URLs, so no WeCom avatar
acceptance is claimed. Account/message totals remain omitted from this public
roadmap. Moments
media-cache identity now matches WeFlow URL host canonicalization for userinfo,
default ports, non-default ports, and volatile parameters. WeCom upload fills
each bounded protocol chunk across legal short reads and rejects early EOF
before sending an incomplete chunk. Focused Moments/provider and WeCom
media/sender/route tests pass. The refreshed full local gate passed 754 Python
tests, four fail-closed source-mutation tests, WebUI checks, package/privacy
and extracted-startup checks, deterministic ZIP/manifest checks, and the
synthetic performance benchmark. QQ still has no verified key or new history
sync; WeCom snapshot failures now map known decryption/message-database cases to
fixed safe diagnostics without exposing paths or SQL details. Broader P11
media/decryption parity, P12 live acceptance, P13 real
delivery, and P14 clean committed-checkout/hosted CI and artifact transfer
remain open.

**P14 source ZIP verification follow-up (2026-10-14):** the local package smoke
now checks that generated ZIP entries have fixed timestamps, ordinal path order,
canonical relative paths, and no case-insensitive duplicate Windows paths, in
addition to comparing repeated build bytes. `scripts/test-package-smoke.ps1`
passes; this is local worktree evidence only and does not replace clean-checkout
or hosted artifact round-trip acceptance.

**P8-P14 complete local gate refresh (2026-10-14):**
`scripts/check-local-hub.ps1` passed 766 Python tests, four fail-closed
source-mutation tests, all 27 WebUI test files plus daily-chart, lock,
navigation/performance checks, package privacy and extracted-startup checks,
and deterministic ZIP/manifest verification. The 25,000-message synthetic
benchmark had zero WAL failures (0.032 s first search, 1.545 s for 50 pages,
0.085 s backup, 0.248 s restore). This does not close real QQ history/cache,
live browser/native behavior, real message delivery, clean committed-checkout,
hosted CI, or artifact-transfer acceptance; P9/P11/P12-01/P13/P14 remain
`IN_PROGRESS`, and P12-03/04 remain `NOT_STARTED`.

**P14 local package rebuild (2026-10-14):** fixed source-package smoke on
Windows virtual environments, where `Start-Process` reports the Python
launcher PID while the Hub records its service PID. The isolated storage and
loopback URL checks remain in place, followed by live HTTP checks. The
portable p358 package (9,783 files) and source p363 ZIP (690 entries) both
passed privacy/startup checks; the source archive contains QQ `key_store.py`
and `send_target.py`. Manifest p363 verifies every package file hash against
the local-worktree inputs. `scripts/test-package-smoke.ps1` also passes. This
does not provide a clean committed checkout, hosted CI run, or uploaded and
downloaded artifact round trip; P14 remains `IN_PROGRESS`.

**P14 p359/p364 refresh (2026-10-14):** after the MCP sticker and WeChat
clipboard-preservation changes, portable p359 (9,783 files, 586,673,701 bytes)
and source p364 (690 ZIP entries, 25,041,671 bytes) both passed privacy and
isolated startup checks. The portable standalone MCP initialize/tools/list/exit
smoke passed; source-package smoke verified Hub, UI, sources, and read-only MCP.
The deterministic ZIP smoke passed, and the local-worktree manifest verifies
both package inventories against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
Hosted CI and artifact upload/download remain unverified; P14 remains
`IN_PROGRESS`.

**P14 p360/p365 refresh (2026-10-14):** after sanitized QQ scan diagnostics,
portable p360 (9,783 files, 586,677,973 bytes) and source p365 (690 ZIP entries,
25,042,454 bytes) passed privacy and isolated startup checks. The portable
standalone MCP smoke and source ZIP deterministic-build smoke passed. Manifest
p365 verifies both package inventories against base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted CI and artifact
upload/download remain unverified; P14 remains `IN_PROGRESS`.

**P14 local-gate repair (2026-10-14):** the PowerShell test command now includes
`integrations.qq.test_send_target` in the same `unittest` invocation instead of
executing it as a separate shell command. A fresh `scripts/check-local-hub.ps1`
run passed 777 Python tests, four source-mutation-gate tests, the WebUI suite,
package/privacy/startup checks, the synthetic benchmark, and `git diff --check`.
Hosted Actions and artifact upload/download remain unverified, so P14 stays
`IN_PROGRESS`.

**P9/P14 latest status (2026-10-14):** the local QQ source currently reports
`connected` with a completed sync timestamp. A newly approved discovery retry
was short-circuited by the Hub because the database is already readable; it
did not scan QQ process memory or change local configuration. Manifest
`local-20261014-roadmap-p367-local-wechat-route` was reverified against the
p361 portable directory and p367 source ZIP; package bytes and provenance
match. The public repository's only Local Hub CI run still targets the initial
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` commit and failed before packaging,
with missing `cryptography` in that commit's CI requirements and a test tied to
a runner-specific path. Current worktree CI changes address those failures but
are not on the remote branch. P14 hosted CI/artifact round-trip and the other
live-device acceptance rows above remain open.

**P12-01 synthetic notification/export recheck (2026-10-14):** 31 focused
backend tests passed for account safety, scheduled export, Moments export, and
the body-free notification SSE endpoint, including capability/filter behavior
and stream closure when notifications are disabled or the account locks. Three
WebUI synthetic checks passed for notification targeting/privacy, unsupported
browsers, and lock-form submission. This verifies the local event and UI
contracts only; OS-level desktop notification delivery remains open. The
isolated Edge lock/relock/unlock acceptance is recorded in the development log
above and is no longer an outstanding browser-lifecycle item.

**P14 full local gate recheck (2026-10-14):** `scripts/check-local-hub.ps1`
passed from the current worktree: 779 Python tests, four source-mutation gate
tests, 32 WebUI tests, package/privacy/startup and deterministic ZIP checks,
syntax/compile checks, and `git diff --check`. The 25,000-message benchmark
reported 1.873 s ingest, 0.043 s first search, 2.893 s for 50 cursor pages,
0.126 s backup, 0.395 s restore, and zero WAL failures. Hosted Actions and its
uploaded/downloaded artifact round trip remain unverified; P14 stays
`IN_PROGRESS`.

**P11-01 local media probe follow-up (2026-10-14):** corrected the earlier
probe to pass the exact WebUI identity fields and nested
`media_entry.resource_index`. A bounded sample of indexed chat images produced
some resolved WeChat media URLs; other sampled resources returned the normal
unavailable-cache result without HTTP errors. The prior `message_missing` responses came from incorrect probe
parameters and do not indicate a product resolver defect. Broader platform
cache coverage and complete media acceptance remain unverified.

**P11-02 QQ attachment index parity (2026-10-14):** local QQNT message parsing
and media resolution now preserve and accept the same 0–100 attachment indices
already accepted by bounded ZIP export; index 101 remains rejected. Focused
synthetic checks cover the upper bound. Real cache availability remains a
separate device-data limitation.

**P14 local review package p371 (2026-10-14):** rebuilt the portable app and
sanitized 690-file source ZIP from the current worktree. Isolated startup and
read-only MCP checks passed; package privacy scans and the full local-worktree
manifest verification passed. This does not verify hosted Actions or the
uploaded/downloaded artifact round trip, so P14 remains `IN_PROGRESS`.

**P14 local review package p372 (2026-10-14):** refreshed both packages after
the P11 QQ media resolver change. The portable directory contains 9,783 files
(586,695,037 bytes); the sanitized source ZIP contains 690 files
(25,087,753 bytes). Portable Hub/UI/Lucide, three-source, isolated-storage,
read-only MCP and standalone MCP smokes passed; the extracted source bundle
passed Hub/UI/source/MCP smoke and privacy checks. Manifest p372 verifies both
package inputs against the local worktree. Hosted CI and GitHub artifact
upload/download remain unverified, so P14 remains `IN_PROGRESS`.

**P11 QQ media scan priority (2026-10-14):** the read-only local QQ resolver
now scans the requested media category and `chatpic` before the broad `chatraw`
tree, so a large general cache cannot consume the shared scan budget before a
known category cache is checked. A focused synthetic regression passed with a
one-entry scan limit; the existing 200,000-entry, five-second and 50 MiB
limits remain. Real-device QQ cache coverage and broader resource/decryption
parity remain unverified, so P11 remains `IN_PROGRESS`.

**P11 WeCom shared attachment identity (2026-10-14):** `file_table4` may map
one cached MD5 to multiple messages. The local resolver now links both
CacheMapping files and media-index fallbacks to every distinct message ID;
focused regression coverage verifies two messages share the resolved local
attachment. Broader real-device cache coverage remains unverified, so P11 stays
`IN_PROGRESS`.

**P14 local review package p373 (2026-10-14):** rebuilt the portable app and
sanitized source ZIP after the P11 WeCom shared-attachment and P13 WeChat
receipt-baseline fixes. The portable directory contains 9,783 files
(586,698,253 bytes); the source ZIP contains 690 files (25,088,378 bytes).
Isolated portable/source startup, three-source, UI, privacy and read-only MCP
smokes passed. The p373 manifest verifies both packages against the local
worktree. Hosted CI and GitHub artifact upload/download remain unverified, so
P14 stays `IN_PROGRESS`.

**P11/P12/P13 local regression follow-up (2026-10-14):** MCP attachment
inspection prompts now direct clients to select an exact `resource_index` from
`list_media` for WeCom/QQ text-parent attachments, including the explicit
selection needed when a message has multiple files. Shared lazy WeChat sending
now serializes sender creation, readiness, and the entire GUI delivery. WeCom
send classification marks an attempt at the actual send-frame write, keeping a
deadline failure before that write retryable while preserving `unknown` for
partial writes. When the app lock is enabled, every tab independently checks
the body-free local lock status every two seconds; disabling the lock or leaving
the page stops polling, and resume still performs an immediate check. Focused
MCP, sender, and browser regressions pass. The complete local gate passed 787
Python tests, four source-client mutation-gate tests, 32 WebUI checks, package
privacy/startup and deterministic ZIP checks, and the 25,000-message benchmark
with zero WAL failures. QQ history is indexed locally, but P9 live NapCat
delivery, broad P11 real-cache/resource parity, P12 real desktop-notification
delivery and native mutation acceptance, P13 real delivery, and P14 hosted CI
plus artifact round-trip remain unverified. P9/P11/P12-01/P13/P14 stay
`IN_PROGRESS`; P12-03/04 stay `NOT_STARTED`.

**P14 local review package p377 (2026-10-14):** rebuilt after the P11/P12/P13
follow-up. The portable package contains 9,783 files (586,714,465 bytes), and
the sanitized source ZIP is 25,090,581 bytes. Isolated Hub/UI/three-source and
read-only MCP startup, standalone MCP stdio, source ZIP startup, privacy scans,
and package-byte manifest verification against local worktree inputs all pass.
Manifest SHA-256 is
`ada67b5b0f20f82d551edd074d215b156d4bf0296fb2be2b964d28d19156e996`.
This local package checkpoint preceded hosted verification and is superseded by
the successful hosted runs recorded in P14-01 above.

**P11 QQ reply attachment index regression (2026-10-14):** the WebUI now
preserves original resource indices when quoted preview attachments are
filtered from the message attachment strip. The synthetic rendering regression
and full local gate pass. Real-account cache coverage remains open; P11 stays
`IN_PROGRESS`.

**P13 WeChat request readiness (2026-10-15):** a local test request is awaiting
the Hub and WeChat desktop sender to become available. No request was written or
delivered; the `wechat_send` capability state could not be read while the Hub was
stopped. Localized main-window title matching has been fixed. P13 stays
`IN_PROGRESS`.

**P12-03/04 pinned native-source audit (2026-10-14):** the repository and
WeFlow's pinned `2f23e72cebf7f309a0f4d129307d16d7b0cd41c3` tree contain only
the WCDB dynamic-binding wrapper, not an auditable trigger implementation or
WCDB library. The upstream interface document says the native component is
user-supplied and lists trigger ABI declarations only. This confirms that the
install/uninstall gates remain unavailable; the source-mutation gate suite
passes 4/4 and the WCDB optional-export harness passes 44/44 synthetic tests.
No binary or database was accessed. P12-03/04 remain `NOT_STARTED` pending
inspectable native source, license/provenance, and isolated lifecycle/rollback
acceptance.

**P11 resource snapshot follow-up (2026-10-14):** media ZIP export now compares
the opened file identity/size/mtime with its pre-copy metadata and rechecks the
open file after streaming. A same-size source edit during copy fails only that
attachment and removes its partial staging file. Synthetic race, growth, and
cancellation checks pass. This detects ordinary concurrent cache updates; a
writer that deliberately restores timestamps would require immutable snapshots.

**P8-P14 complete local gate and package refresh (2026-10-14):** the current
worktree passes 795 Python tests, four source-mutation gate checks, 32 WebUI
checks, package/privacy and deterministic ZIP checks, and the 25,000-message
performance run with zero WAL failures. Metrics: ingest 1.712s, first search
0.035s, 50-page pagination 1.585s, backup 0.107s, restore 0.274s. Rebuilt and
verified local-worktree packages contain 7,554 portable files (288,298,893
bytes) and a 690-file source ZIP (25,055,029 bytes). The base commit has a
successful hosted Windows/package/artifact round trip, but these local changes
remain uncommitted, so clean-checkout and hosted artifact acceptance for this
worktree remain open. P9 live NapCat receipt, broad P11 real-cache parity,
P12-01 live desktop notification and P12-03/04 native-source acceptance, and
P13 real delivery also remain open. A fresh sanitized sender probe reports the
WeChat account/window ready and desktop input available, but the main window is
not foreground. A read-only check confirms the exact target exists in the
local contact index, while its dedicated chat window is not open. The Hub is
stopped and local `wechat_send` is disabled, so the WeChat test request is not
queued; no content was entered and no message was delivered.

**P13 localized WeChat window follow-up (2026-10-14):** the GUI sender now
recognizes the exact localized main-window title `微信` alongside `Weixin`,
still requiring the expected window class and exact title. A sanitized full
readiness probe reports the account/window ready and desktop input available,
but the main window is not foreground; all 25 focused sender tests pass. The
Hub remains stopped and local `wechat_send` is disabled. The exact target is
present in the local contact index, but its dedicated chat window is not open;
the request is not queued and no message was sent. P13 remains `IN_PROGRESS`
pending a live Hub, enabling the module, an exact target window, and
per-request UI approval.

**P11 stable MCP cache reads (2026-10-14):** ordinary cached-image and Moments
MCP readers now bind the regular file's identity to its opened handle and
recheck that handle after the bounded read. The Hub server suite passed 208
tests, including the replacement-before-open regression; this closes a
path-replacement race, not full media/decryption parity. A same-inode writer
that deliberately restores modification time is not detected, and P11 remains
`IN_PROGRESS` pending broader real-cache acceptance.

**P8-P14 latest local gate and package (2026-10-14):** the current worktree
passes 797 Python tests, four source-mutation checks, 32 WebUI checks, package
privacy/determinism checks, and the 25,000-message benchmark with zero WAL
failures. Ingest/search/50-page pagination/backup/restore measured
1.552/0.030/1.519/0.099/0.260 seconds. The local-worktree portable package has
7,554 files (288,305,491 bytes), and the sanitized source ZIP is 25,056,673
bytes; isolated startup, privacy scans and manifest verification pass. Hosted
CI and artifact round-trip do not cover these uncommitted changes. The Hub is
not running on the checked local ports. A previous sanitized probe found no
queued WeChat request, but current persisted status is unverified; no message
was sent. P13 and P14 remain `IN_PROGRESS`; P9/P11/P12 acceptance gaps above
remain open.

**P9 OneBot port validation follow-up (2026-10-14):** NapCat config repair
and endpoint loading now require the port to be an exact integer; JSON `true`
can no longer pass Python's integer check as port 1. The 19-test QQ sender
suite passes, including replacement and fail-closed regressions. Live NapCat
and real-account sending are still unverified; P9 remains `IN_PROGRESS`.

**P11 Moments video tile follow-up (2026-10-14):** the media library now uses
only a distinct cached poster for video thumbnails. A playable Moments video
token is no longer rendered in an `<img>`; videos without posters show the
existing video icon, while cached image thumbnails are unchanged. The focused
synthetic WebUI test passes. Real media-cache acceptance remains open.

**P14 package refresh after focused fixes (2026-10-14):** the Windows
portable package (7,554 files, 288,307,456 bytes) and sanitized source ZIP
(25,098,410 bytes) were rebuilt from the current worktree. Isolated Hub/UI/MCP
and source ZIP startup, privacy scans, and local-worktree manifest provenance
and byte verification passed. Manifest SHA-256 is
`BD3C5BBD0F75154BD68E0EF3B104D59A82E59E88FC21F378C53DB25E2C6C6287`. The
changes remain uncommitted, so hosted CI/artifact round-trip evidence does not
cover them; P14 stays `IN_PROGRESS` for this worktree.

**P14 complete QQ runtime package (2026-10-14):** the vendored QCE server
compiled with the pinned Rust 1.98.1 GNU toolchain and GCC 16.2.0; its source
guards enforce IPv4 loopback binding. The resulting binary SHA-256 is
`e471dd4bc3b3fee22c518de122e8144bab734b1693b120f65fda1d61caf3dc89`. The
Windows portable package now includes that QCE server and the verified NapCat
6.3.2 shell (9,783 files, 586,737,019 bytes). The sanitized 690-file source
bundle, isolated Hub/UI/MCP/source smokes, and privacy checks passed. A local
manifest verifies exact bytes and base-commit provenance; current-worktree
hosted CI/artifact round-trip remains open, so P14 stays `IN_PROGRESS`.

**P8-P14 focused follow-up (2026-10-14):** WeChat cache resolution now accepts
the suffix variants indexed by WeFlow and falls back to its `_raw.mp4` cache
when the canonical video is absent. ChatLab exports preserve group metadata;
the render and streaming auto-export paths reject mixed group/private scopes
before publishing a file. Send receipts must be bounded, nonblank string IDs;
malformed receipts remain `unknown` and cannot trigger an automatic retry.
Message-event streams emit an initial/reconnect checkpoint so `Last-Event-ID: 0`
survives a disconnect gap, while event bodies remain limited to conversation
metadata. MCP handshake instructions now include personal WeChat preparation
and retain the rule that MCP cannot approve or deliver. Focused synthetic tests
passed for these changes. The Hub is stopped on the local ports checked in
this turn, so the user's exact test request was not queued and no message was
sent. Its target and text are intentionally omitted from this public roadmap.
P9/P11/P12/P13/P14 acceptance remains open for live
clients, native source implementation, and hosted CI/artifact round-trip.

**P9 indexed-target guard (2026-10-15):** numeric QQ UIN/group targets now go
through the same exact account/conversation lookup as local QQNT UID targets.
Preparation rejects missing conversations and private/group type mismatches
before writing an approval request. The MCP prepare path now shares that
resolver, hides preparation when it is unavailable, and rejects unindexed,
wrong-account and wrong-kind targets without creating requests; indexed UID to
UIN conversion remains supported. The send-focused HTTP suite passed 19 tests
and three focused MCP tests passed with synthetic records. No adapter delivered
a message; live NapCat delivery remains unverified.

**P11 parity audit (2026-10-15):** real cache/device acceptance remains open.
WeFlow recognizes `_b`, `.b`, `_w`, `.w`, `_c`, and `.c` image cache variants.
The Hub does not classify these variants as thumbnails or best-available
media until their semantics are verified; it currently exposes only the
confirmed thumbnail suffixes. Their exact dimensions are not inferred. Full
resource library and batch-decryption parity remains open.

**P8-P14 current local gate (2026-10-15):** the current worktree passes 811
Python tests, four source-mutation gate checks, 32 WebUI checks, source package
privacy/determinism and isolated-startup checks. The 25,000-message benchmark
measured 1.542 s ingest, 0.034 s first search, 1.565 s for 50 pages, 0.120 s
backup and 0.299 s restore, with zero WAL failures. This includes the shared
HTTP/MCP account-scoped QQ send-target resolver. These uncommitted changes still
need a hosted Windows run and artifact upload/download verification; live QQ,
WeChat, WeCom, native-source and full P11 media/decryption acceptance remain
open.

**P14 local package refresh (2026-10-15):** after the QQ MCP target validation
and source-comment privacy scrub, the Windows portable package rebuilt with
9,783 files and the sanitized source ZIP with 690 entries. Isolated portable
startup, three-source UI, read-only MCP, standalone MCP stdio, and package
privacy scans passed. The local-worktree manifest verified both package
inventories and hashes against base commit `75fa73185b5b1acf960da35ef331901c3284deef`.
The source archive contains no scanned profile path, local cache sample marker,
or local test-request marker. Hosted CI and uploaded/downloaded artifact
round-trip remain open; P14 stays `IN_PROGRESS`.

**P9/P14 local follow-up (2026-10-15):** the repository launcher started the
Hub successfully. Personal WeChat request preparation was refused because its
default-off sending capability remains disabled; no request was created and no
message was sent. After genericizing device-specific documentation and removing
local media-sample counts, the 690-entry sanitized source ZIP passed its
privacy scan and isolated WebUI, three-source, and read-only MCP startup checks.
The portable runtime had no code changes and was reused; a regenerated
`local-worktree` manifest verified its inventory together with the refreshed
source ZIP. Hosted CI and artifact round-trip remain open; P14 stays
`IN_PROGRESS`.

**P14 package refresh after P11 media changes (2026-10-15):** rebuilt the
Windows portable package (9,783 files; 586,789,162 bytes) and sanitized source
ZIP (690 files). Portable, isolated source Hub, three-source
UI, read-only MCP and standalone MCP stdio smoke checks and privacy scans
passed. The `local-worktree` manifest `local-019-p11-variants` verified exact
inventories and bytes against base commit
`75fa73185b5b1acf960da35ef331901c3284deef`; hosted CI and artifact round-trip
remain open, so P14 remains `IN_PROGRESS`.

**P8/P11/P13/P14 local follow-up (2026-10-15):** ChatLab export now rejects
unsupported, null, and orphaned conversation kinds instead of labeling them as
private; an actually empty export retains its established private default.
The WeChat sender validates display-name uniqueness even when that name equals
the conversation ID. B/W/C image suffixes remain unclassified pending upstream
semantics. Focused ChatLab and WeChat sender regressions pass. A fresh local
Windows portable build and sanitized source archive passed isolated startup,
MCP, privacy, and manifest checks. Real QQ/WeChat/WeCom delivery, full P11
resource/decryption and cache acceptance, P12 desktop/native acceptance, and
hosted verification of the current uncommitted tree remain open.
