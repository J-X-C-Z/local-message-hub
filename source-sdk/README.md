# Local Message Hub source reference SDK, protocol 1

This optional reference tool is a **single-batch JSON Lines broker**, separate
from the native Rust service. It requires Linux and Python 3.9+ standard
library. The Hub executable does not require Python. It is not an installer,
autostart daemon, provider connector, sandbox or network policy engine.

## Operator contract

1. Initialize an ordinary private native-live archive; configure a distinct
   exact account `scoped-producer` with only `messages.ingest`. Start the
   Rust Hub with that private grants file. Use its confirmed origin verbatim,
   e.g. `http://127.0.0.1:12345`, and only on a trusted host. This is
   plaintext local HTTP. A different local listener at a misconfigured port
   could see the bearer. Never use someone else's listener.
2. Verify and extract the package to a trusted operator-controlled directory.
   Give the broker a trusted **absolute** executable path you own, not a
   symlink, group/world-writable executable or script from an unreviewed repo.
   Ensure its ancestors and interpreter are trusted, too. The executable
   gets no extra arguments. This is not loader race protection against
   another same-user process.
3. From your trusted launcher, start
   `python3 broker.py --origin http://127.0.0.1:12345 --plugin /private/verified/synthetic-source.py`
   with **anonymous private stdin** and pass exactly one JSON line:
   `{"token":"<64 hex>","grant_id":"my.source","platform":"qq","account_id":"synthetic-only"}`.
   Pass neither token nor control JSON on a command line, environment,
   shell history or URL. The FIFO check does not prove that a particular
   pipe is anonymous or private; the launcher is responsible for that.
   Operator stdout prints only status and sequence; stderr emits fixed
   errors, never raw Hub/child data. Archival fields still belong in a
   trusted private workspace.
4. The broker checks manifest grant identity/scope and uses only the fixed
   loopback source endpoints. A wrong scope fails before running the child
   or POSTing its records. Child stdin gets only protocol frames (no bearer,
   origin, CAS marker, account event checkpoint or operator config).
   The process gets a minimal env, temporary private cwd, and no inherited
   extra file descriptors. Its stderr is discarded.

## Writing an adapter

After reading one initialization line:

```json
{"protocol":1,"type":"resume","scope":{"platform":"qq","account_id":"synthetic-only"},"sequence":0}
```

send either `{"protocol":1,"type":"idle"}` and exit 0 or one batch:

```json
{"protocol":1,"type":"batch","sequence":1,"batch":{"idempotency_key":"synthetic-source-1","messages":[{"platform":"qq","account_id":"synthetic-only","conversation_id":"room","message_id":"msg","timestamp":1724123456,"is_self":false,"text":"synthetic"}]}}
```

For a batch, read the `committed` frame and then exit 0; unlike the Hub HTTP
receipt it only contains `protocol`, `type`, `sequence` and per-batch counts.
All JSON lines are UTF-8. Each run handles at most one record batch (1–100
records, 530 KiB JSON transport / 512 KiB normalized data). No external
queues, SDK storage or automated replay. Hub owns atomic persistence.
Derive stable IDs and idempotency keys, and use the numeric sequence to
resume, not as an inferred upstream provider token. On any error after
submit, the write outcome can be uncertain: restart, observe the durable
sequence and reconcile before retrying. The broker does not blindly resend
or claim child-side acknowledgment and Hub commit are atomic.

The broker bounds local I/O and will terminate/reap its trusted foreground
child process group on ordinary return, failure, protocol timeout, SIGINT
or SIGTERM. It cannot supervise work that spawns a new session, stop a
malicious executable with the same OS privileges, prevent it opening files
or networking, or guarantee cleanup after SIGKILL/the host disappears.
Never use an untrusted plugin. This reference only verifies the normalized
permission boundary against the Rust Hub. Python dependency and lifecycle
apply to the broker, not to the Rust service or other clients.

`synthetic-source.py` requires an explicit synthetic account configured by
the operator and emits one invented message on first run; later runs idle.
It does not collect, read or send platform data.
