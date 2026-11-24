# Local Linux managed Desktop acceptance

```sh
cargo test --locked --offline --test managed_sdk -- --test-threads=1
```

This gate launches the actual first-party Linux `desktop-supervise`, uses
the public Desktop SDK for launch options/readiness and grant generation,
and exercises the public typed Desktop client against the native-live
child. The runtime is executable built by Cargo from the selected sources.
Use only generated fixture archives; this test does not accept a user
profile or any real provider data. Archive writes in the fixture setup
use the local normalized event store so the Desktop under test does not
have producer authority. Audit user-visible work against this scope:

- Fixed readiness and manifest; wrong caller scope and a generated
  read-only grant attempting editor startup fail. Typed search and context
  keep the exact account even when another account has an identical message
  and conversation identifier and the same literal search text.
- The organizer uses an explicitly installed private hash-only grant.
  Two independent typed clients with the same original revision prove
  stale CAS denial, non-overwrite, normalized tags and explicit no-op.
  State objects are bound to their client session. The original stock
  browser/cookie/auth path is not used or altered.
- Stop the actual owned supervisor, add synthetic same-account and foreign
  arrivals while stopped, start a fresh managed process, and use typed
  bounded JSON journal replay from the saved applied cursor. The same-account
  gap is filled exactly once by advancing the returned cursor; the foreign
  item is not included, and foreign/old-session cursors are rejected.
  This does **not** exercise SSE or OS notification delivery. The test
  accounts for local application of events, not application ACK semantics.

- The typed notification client uses independently installed notification
  permissions. An editor starts disabled, sets scoped filters with explicit
  revision checks and enables routing without replaying history. A read-only
  grant cannot change preferences or acknowledge a pull. Filtered pages
  advance only on explicit ACK, repeated ACK is detected, and preference
  changes invalidate outstanding receipts. A receipt-backed local activation
  target checks the exact conversation and does not move the frontier.
  Restarting the owned supervisor preserves prior acknowledged progress and
  preferences; reconnect invalidates the old SDK settings and receipt, and
  a fresh pull recovers the remaining unacknowledged page. This covers local
  metadata only, not OS notification delivery, display, automatic activation,
  or the separate notification SSE route.

Keep original behavior comparisons separate: for exact protected read
and selected redacted field comparisons run
`scripts/check-legacy-http.py` with the pinned upstream Python tree,
and for original organizer field outcomes use
`stock_python_metadata_and_identity_mutations_match_native_user_state`
from `tests/live_events.rs`. They do not validate stock browser reuse or
make the native Desktop test a full replacement claim.

To exercise an independently preserved candidate ELF instead of Cargo's
newly compiled fixture executable, set both `LMH_MANAGED_TEST_BINARY` (an
absolute, trusted local file) and `LMH_MANAGED_TEST_SHA256` (the independently
recorded SHA-256 from that source's build receipt). The test verifies the
digest before launch; it does not infer a source commit from the ELF.
The SDK client library must still be compiled from the intended source.
