# Query Performance and Memory

Measured 2026-10-11 with synthetic records only. No private chat bodies,
accounts, databases, or attachments are used by these benchmarks.

## Changes

- Stable cursors use an indexed `(timestamp, rowid)` range comparison instead
  of an OR predicate that scanned preceding rows. Exact live totals and
  deterministic ordering are preserved.
- Source polling uses one lightweight grouped counter query, not three full
  statistics reports with daily/type/media aggregation.
- WeCom streams SQLite rows, selects only a bounded top-k query result, and
  clones only one ingestion batch. After successful Hub ingestion it releases
  the redundant full-text snapshot, retaining sparse attachment metadata,
  members, counts and notices. Independent native queries reload on demand.
- QCE roaming exports are validated then parsed incrementally with the bundled
  ijson parser. Corrupt JSON is rejected before any row is yielded; UTF-8 BOM,
  nested attachments and large integer IDs are covered. The Hub still commits
  batches of 500, preserving existing cancellation and partial-import behavior.
- QQ history reads keep the Hub's 500-record sync batches, but page through QCE
  in 100-record requests and set QCE's underlying `batchSize` to 100. QCE's
  fetch endpoint preloads the requested page plus ten pages, so a cache miss is
  bounded to about 1,100 rather than 5,500 rows per conversation request; the
  synthetic provider test verifies page continuation and both request bounds.
- QCE latest-message cache keys no longer include the current timestamp, so
  repeated latest-page requests can reuse one entry. Each fetch also removes
  expired entries across peers before lookup; focused Rust tests cover key
  stability and expiration pruning. This bounds stale cache retention but does
  not establish the cause of a large live QCE process working set/private bytes.
- Common exports write UTF-8 incrementally; XLSX compresses rows directly into
  its worksheet entry. The public API still returns complete bytes, so output
  size remains a lower bound on memory. ChatLab retains its existing aggregate
  document construction and is not claimed to be fully streaming.
- WebUI retains at most 500 loaded messages while preserving the continuation
  cursor and cumulative offset. This does not truncate the database or search
  results. Latest returns to the first page; older records remain accessible.
  Inactive view state and transcript caches are released/bounded. Formatters are
  reused; existing SVG icons are not recreated on each poll; hidden polls pause
  and overlapping polls are prevented. Videos do not preload offscreen media.
- Web search returns at most 1,200 characters of message text per hit, selected
  with SQLite `substr`; a 60-result UI page therefore materializes at most
  72,000 preview characters instead of up to 60 full bodies. Matching still
  runs against full indexed text. MCP and export searches retain full bodies.
- Local media tokens are capped at 4,096. Cached image reads use bounded reads
  in addition to file-size checks (MCP 8 MiB, avatars 2 MiB).
- WeChat encrypted image reads are capped at 64 MiB with a bounded read after
  the initial size check, so a cache file that grows during access cannot cause
  an unbounded input allocation. This does not change source files or perform
  key scanning.

## Reproducible Measurements

Timings vary with host load. Python tracemalloc figures measure Python
allocations, not total process RSS or SQLite/native allocations.

The CI synthetic benchmark uses deliberately broad ceilings for 25,000-row
ingest, first search, full cursor pagination, backup and restore. These are
catastrophic-regression guards, not performance targets; the measured timings
remain the comparison data.

| Workload | Before | After |
|---|---:|---:|
| WeCom 100k messages, query returns 50, extra Python peak | 20,802,320 B | 14,199 B |
| WeCom 100k messages, retained snapshot, 1k attachment entries | 91,876,588 B | 9,116,102 B |
| QCE JSON 50k messages, 55,538,904-byte input, Python peak | 146,224,024 B | 617,847 B |
| QCE parse above, seconds | 0.399 | 3.616 |
| JSON export 20k x 1 KiB text, Python peak | 72.61 MiB | 25.13 MiB |
| XLSX export same input, Python peak | 211.96 MiB | 6.44 MiB |
| TXT export same input, Python peak | 87.40 MiB | 22.61 MiB |
| HTML export same input, Python peak | 137.77 MiB | 24.46 MiB |
| Deep cursor SQL only, 100 reads / 100k rows, seconds | 0.9766 | 0.0145 |
| Full messages() deep page, 30 calls, seconds | 0.1529 | 0.0794 |
| Full search() deep page, 30 calls, seconds | 0.1604 | 0.0644 |
| Source polling, 10 calls / 100k rows, seconds | 0.4538 | 0.0392 |
| WebUI after browsing 100 pages / 10k messages, retained rows | 10,000 | 500 |
| WebUI retained serialized fixture size | 6,648,891 B | 332,501 B |
| Edge native/DOM heap after synthetic 10k-message browsing | 216,404,992 B | 14,218,768 B |

Full store queries include exact counts, record conversion, sender-avatar
lookup and cursor encoding. Both versions return equal item counts and totals.
The SQL-only speedup must not be described as the end-to-end API speedup.
Streamed parsing and some export formats trade throughput for lower peak
memory; a single oversized JSON token/row can still allocate proportionally
to that token/row. Generic import and ChatLab export remain bounded by their
existing size limits, rather than becoming arbitrary-size streaming operations.

Commands from the repository root:

```powershell
python -m hub.benchmark_memory
python integrations/wecom/benchmark_memory.py
python scripts/benchmark-memory.py
node --test webui/test-memory-bounds.cjs
./scripts/check-local-hub.ps1
```

The memory benchmarks operate only on disposable synthetic fixtures. Keep
their measured components separate from overall browser/desktop process usage.
The real-browser check uses intercepted synthetic APIs only, measures CDP
embedder heap after garbage collection, verifies every page has at most 500
message nodes, and exercises Latest/context exit on desktop and 390x844 mobile.
The browser heap result is not a process RSS measurement. Metadata and screenshots
are stored under ignored `output/`; `webui/test-memory-browser.cjs` is an optional
Playwright CLI run-code function, not an extra dependency of the regular gate.

## 2026-10-13 Query and Usability Refresh

These comparisons use disposable synthetic data. Query timings cover 30 complete
store calls, including exact totals and record conversion, rather than SQL alone.
Rendering peaks exclude the caller's pre-existing input list and measure additional
Python allocations; they are not total process memory.

| Workload | Before | After |
|---|---:|---:|
| 100k messages, context near beginning, 51 rows | 0.3902 s | 0.0984 s |
| Same context near middle | 0.3980 s | 0.0951 s |
| Same context near end | 0.3960 s | 0.0951 s |
| 50k conversations, QQ list, first 100 rows | 1.2255 s | 0.0563 s |
| Same conversation list, all platforms | 0.8406 s | 0.7424 s |
| QQ 64 MiB binary hash, Python peak | 64.008 MiB | 0.259 MiB |
| 50k WeChat contacts, Python peak | 33.847 MiB | 27.313 MiB |
| ChatLab JSON, 20k messages x 1 KiB, rendering peak | 75.01 MiB | 27.22 MiB |
| ChatLab JSONL, same input, rendering peak | 31.37 MiB | 26.02 MiB |

Message context uses indexed tuple ranges and one look-ahead row per side to
determine continuation, keeping the exact total and stable same-timestamp order.
Conversation lists use SQLite DESC's existing NULL-last behavior so single-platform
queries can use the existing ordering index. QQ hashes read incrementally; WeChat
contact conversion no longer retains an intermediate fetchall list. ChatLab writes
each projected message directly to the output buffer, retaining only its header
and member roster. Source messages and output bytes are still retained by the
existing export API; this is not an arbitrary-size, fully streaming HTTP export.
Message and automatic exports now use existing stable cursors instead of repeated
deep offsets. Automatic export continues to operate on an isolated backup snapshot.

Identical data-source polling skips DOM writes. Attachment-export progress updates
only its toolbar, preserving media nodes until completion. Synthetic checks observe
one initial page redraw for 12 unchanged polls and one final page redraw across
five progress updates. Stale search, statistics, member-list and send-queue
responses cannot refill inactive views. Daily statistics and expanded member
caches are released when leaving statistics.
Supported page hashes now survive refresh and direct navigation; unknown hashes
fall back to chat, while the existing QQ-login route remains separate. The live
8772 source page was refreshed successfully without triggering login or sync.

Contact export now preserves the UI's account and contact-kind filters. Both
launchers reuse a known running Hub even when it is locked; the portable launcher
also respects explicit --storage and --no-browser. Concurrent first launches and
direct hub.server invocations do not yet have a cross-process storage lock.

Focused verification: store/export/HTTP and source/startup checks, all WebUI Node
checks, bytecode/syntax checks and git diff --check. No live source login or message
delivery was used. The optional real-browser memory benchmark was not rerun.

## Scheduled Export Streaming (2026-10-13)

The scheduled-export path now reads ascending `(timestamp, id, rowid)` keyset
batches from its transactionally consistent SQLite backup and writes all nine
formats directly to the same-directory temporary file before `fsync` and atomic
replacement. XLSX writes its worksheet entry through `zipfile` without an
intermediate archive buffer. ChatLab source-platform metadata is a small
distinct query; first-occurrence members are produced by a SQLite window query
and streamed before message records. Cancellation is checked between
message/member records and batches, and failed/cancelled output is removed.

The synthetic regression writes 3,000 generated 1 KiB messages to a temporary
file for each format and asserts Python traced peak allocation stays below
2 MiB; compressed XLSX output is smaller than the text formats. This is a
bounded-writer regression, not a process-RSS measurement or throughput claim.

## WeCom Attachment Staging (2026-10-14)

The loopback attachment endpoint copies selected uploads to Hub-owned staging
storage with 1 MiB reads rather than accumulating the request into a
`bytearray` and then making a second whole-file copy. SHA-256 and supported text
encoding are checked incrementally; the staged file is published only after
signature validation and atomic replacement. Synthetic tests verify bounded
read sizes, truncation/over-read rejection, and cleanup after invalid input.
This only bounds incoming staging memory: the current bot sender still loads a
staged attachment before encoding its protocol chunks. No process-RSS or
real-delivery benchmark is claimed.
