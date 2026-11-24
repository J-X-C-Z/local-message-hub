# Detached search scalability (rebuild-034)

This measures real IPv4 loopback search requests, not a general product or
algorithm result. Run the exact pinned Python Handler and ChatStore on one
side and the exact optimized Linux Rust binary in read-only snapshot mode
on the other. No new production code or schema migration is required.

## Fixture and reproduction

- Pinned legacy revision: J-X-C-Z/local-message-hub at
  c7052745fab3fa1880c6e3a496f40609185668b9.
  scripts/benchmark-store-performance.py is verified against Git blob
  27c22832814b7f1738c5f51bd0e588553fbdd3fa. Execute only its exact synthetic
  row generator expression. Preserve its 30,000 rows, 60 conversations,
  500-row ingest batches, unpadded IDs, and four rows sharing timestamps.
- The stock ChatStore detaches one v9 snapshot using its backup API with
  the real messages_fts index. Give the Rust immutable reader the untouched
  snapshot and Python a byte-identical private copy, verified before open.
  Stock ChatStore can open its own copy in WAL mode; the Rust path retains
  the original snapshot bytes. Check both serve with FTS, plus its schema
  and all 30,000 candidate rows. Never open a live SQLite DB in Rust or
  raise the separate guarded importer’s 1,000-row limit.
- Run on Linux with Python 3.12 or comparable Python stdlib with SQLite FTS5:
      PYTHONPATH=/path/to/exact-pinned-tree PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark-search-scale.py /path/to/optimized/runtime /path/to/exact-pinned-tree/scripts/benchmark-store-performance.py result.json
  The harness checks the exact actual Python Handler/store/auth/capability
  and fixture generator Git blobs before use. Run with Python assertions
  enabled. Only synthetic paths and loopback traffic are used.
- Matched: 10 ordered row fields including ID, text, account and timestamp,
  total and has_more, actual expected rank and scope. Page length is 50;
  top offset 0 / direct deep offset 15,000, separately followed by each
  engine's **own** precomputed cursor (top → rank 51; deep → rank 15,051).
  Cursor continuation numbers time only the follow-up request, **not**
  acquiring the cursor with the first request nor walking from the start.
- The legacy HTTP handler ignores account_id on search, so the parity
  workload has exactly one account; a separate Rust test snapshot adds a
  peer with colliding ID/text and proves non-disclosure plus explicit
  foreign-account denial. Rust scoped order is timestamp then string ID;
  legacy uses timestamp then rowid. The same 16 low-ID rows arrive in a
  different order near IDs 8–11. Include that mismatch in the raw report
  and do **not** time it as an equivalent case. No full search parity.
- Six alternating paired warm trials. Each engine gets its own process per
  trial. Build/validate its cursor and warm eight requests untimed, then
  run 12 reads per case with case order rotated (72 timed reads per case
  and engine, 288 total each). The client issues serial new connections; it
  measures from connect/request through receiving and closing the entire
  body, with JSON verification outside the timer. OS caches are not
  flushed or isolated; there is no cold, startup, concurrency or capacity
  claim. No worker/auth or schema, SQLite runtime or response bytes parity.
- Summaries use nearest-rank wall p50/p95 from individual requests.
  Per-process sampled peak RSS is Linux VmRSS at phase endpoints and
  roughly 5 ms polling, summarized as the median trial peak; record
  process VmHWM and raw CPU tick deltas as context, excluding controller.
  Short peaks can be missed, OS caches are shared, RSS is approximate.
  Rust SQLite is bundled via libsqlite3-sys 0.30.1 (SQLite 3.46.0); Python's
  actually loaded SQLite version, snapshot identity/schema, RSS, CPU,
  raw requests and projections are in the release evidence.

The exact same snapshot rows do not make the products identical: Python
uses one shared stock connection, stock cookie auth and raw handler;
Rust uses an immutable read-only connection per query, scoped bearer,
additional account constraints, and different tie/cursor and payload
contracts. Compare each pair as deployed here, not across different hosts
or with the previous guarded message benchmark. An all-matching FTS term
and exact total stress count/scanning; selective terms, larger archives,
write load and production reader paths were not benchmarked.

## Rebuild-035: exact-stock FTS count and three-way comparison

Pass the exact extracted 034 ELF as a fourth positional argument after
the JSON output. Its SHA-256 is checked. The six trials cycle through
all six orders among stock Python, 034 Rust and 035 Rust. Require
byte-identical 034/035 bodies for every timed page (including count,
has_more, cursor and full message projection), and match the same shared
fields against Python. Separately verify both Rust binaries return the
same bodies with a genuinely present foreign account, selective Chinese
and ASCII, literal percent/underscore and double quotes, short term
fallback, exact date/conversation/type filters, empty/exhausted pages,
and cursor continuation/misuse. These edge cases are not used for the
HTTP headline comparison.

The original search executes separate total and rank SELECTs. The
034 COUNT plan can walk the account index and compute an FTS membership
list. 035 makes FTS5 enumerate matching rowids first, then joins each ID
back into the original exact account, conversation, type, timestamp and
escaped LIKE predicates. That is equivalent to membership for the
canonical stock FTS5 table's unique rowids; orphaned IDs are rejected
by the inner join, not treated as a message. The original rank SELECT,
pagination, cache and transaction behavior remain. The count optimization
is limited to scoped search on an immutable detached snapshot with the
exact pinned stock trigram virtual-table schema. Other FTS schema shapes
and non-FTS, unscoped, guarded, native-live, message and context reads
retain the original count. Filesystem/snapshot/authorization checks
are not bypassed or cached across requests.

SQL stage diagnostic controls: the standard-library SQLite engine runs
EXPLAIN QUERY PLAN and nine warmed timings each of both COUNT shapes
and the unchanged inner rank SELECT, using exactly those detached fixtures
and a fixed order/reverse-order alternation. Separate all-hit ASCII,
selective ASCII, selective Unicode and escaped-literal terms are checked
for exact counts. These are schematic stage probes on the Python-linked
SQLite library, not measurements of arbitrary SQLite versions or full
queries in each server. Only the 3-way HTTP results were measured from
the exact optimized delivered runtime and older extracted binary.
