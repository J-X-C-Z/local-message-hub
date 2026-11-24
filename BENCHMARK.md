# Legacy message-list read benchmark (rebuild-032 and 033)

This is one serial, localhost HTTP workload. The optimized Rust executable
runs the guarded legacy adapter; the pinned Python Handler and ChatStore run
in a separate Python process with inert provider/settings wiring. The pinned
Handler class is extracted from the exact source, not a handwritten Python
implementation. Neither branch runs real providers, user data, or browser UI.

## Scope and run

- Legacy revision: `J-X-C-Z/local-message-hub` at
  `c7052745fab3fa1880c6e3a496f40609185668b9`.
- Use the exact source archive, a Linux x86_64 ELF from the runtime ZIP, and
  a local copy of that Python tree. The harness checks the Git blob hashes of
  the actual Handler, store, account lock and capability files. Fixture
  scaffolding uses Python 3.12 stdlib; no HTTP client dependency, no network
  except loopback. Run with Python assertions enabled (the default).
- `PYTHONPATH=/path/to/pinned/python PYTHONDONTWRITEBYTECODE=1 python3 scripts/benchmark-legacy-read.py /path/to/local-message-hub-rs result.json`
- One seeded account, eight QQ conversations, 125 synthetic plain text rows
  per conversation, unique timestamps, mixed sender role and UTF-8 names/text.
  Both formats originate in one stock v9 detached SQLite snapshot; Rust uses
  supported guarded-v9 import, not the same physical DB or SQLite build.
- The same ordered 750-query plan (per engine), with fixed offset/limit
  parameter distribution, no search, concurrent clients, or cursor tokens.
  Before measuring, compare every distinct query's exact ten named fields
  and total; also check their canonical SHA-256 after every timed read.
  Full JSON/wire bodies, auth and pagination internals are **not equal**.
- 30 alternating paired fresh-process trials, one measured first request per
  engine after initialization/unlock (not start-up latency; OS cache not
  flushed and already preflighted). Eight alternating paired warm trials per
  engine, 24 untimed warmup requests in each new process followed by 90 timed
  serial reads. The clients open a new connection each time and share the
  same timing code. Pair order flips each trial. No CPUs pinned; repeat in
  the same controlled machine if comparing later versions.
- Wall timing measures request through entire body + HTTP connection close;
  JSON parsing and correctness checking occur outside the timer, but between
  requests in a warm batch. p50/p95 use nearest rank on individual samples,
  not trial means. Body bytes exclude HTTP framing/headers.
- Per-process sampled peak RSS is the largest Linux `/proc/PID/status`
  VmRSS across before/after plus 5 ms polling; report the median trial
  sampled peak. Also capture the Linux process high-water mark (VmHWM),
  which includes startup, and raw server CPU ticks from `/proc/PID/stat`
  deltas, coarse especially for the first read. Controller not included;
  short spikes can be missed and Linux RSS counters are approximate.
- Build/runtime hash, host CPU/kernel, sample plan, RSS/CPU and every
  observation are included in `benchmark-raw.json` with the sealed release.

This compares the two concrete paths as served, including differing response
size/shape, cookie vs bearer verification, connection runtime, schema and
SQLite library. It is not an algorithm speedup, workload capacity, general
memory saving, browser result, migration result, or whole-app performance.

## Rebuild-033 comparison and SQLite probe

Pass the exact extracted 032 ELF as a third positional argument for the
three-way run. The script checks its pinned SHA-256 and cycles through all
six process order permutations; other workload cardinalities are unchanged.
It requires byte-identical Rust response bodies for the same queries in both
runtimes, in addition to their ten-field equality with Python. Do not compare
the separately executed 032 report's absolute times against a new host.

The guarded legacy GET now reuses the same fresh, validated, read-only SQLite
connection opened at preflight for its SELECT. It closes before opening a
separate fresh postflight reader. There is no retained connection or
cross-request schema/session cache. Archive, sidecar, schema, identity and
verifier checks still run on fresh preflight and postflight; existing private
lease and HTTP generation fences remain. This also backs the legacy browser
message route, but no browser was tested. Query-only limits and deadline are
unchanged. Empty-scope requests and other routes use their original path.

To run the SQLite microprobe:
    cargo test --release --locked --offline --lib guarded_sql_read_stage_probe -- --ignored --nocapture

The probe alternates new and structurally old paths on a temporary synthetic
archive, discards 20 pairs, and times 200 pairs after warm-up. It isolates
validate/open/drop, count/select/normalize, and fresh validate/open/drop
phases (new preflight holds onto the handle; closing it is counted in the
query phase). It is a code-path microprobe in the 033 binary's test build,
not an instrumented 032 ELF, nor proof of syscall timing. The three-way
loopback benchmark uses the real uninstrumented optimized ELFs.
