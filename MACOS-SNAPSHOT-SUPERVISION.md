# macOS snapshot desktop supervisor

The macOS core binary adds a narrowly scoped process supervisor for a local,
read-only Hub snapshot. It is a native backend integration point, not a
desktop app or service. The launcher in the portable bundle remains a
foreground `serve` wrapper; do not use it as a supervisor.

The authorized desktop parent starts the trusted Hub binary from a private
local tree with:

```text
local-message-hub-rs desktop-supervise --snapshot INDEX.sqlite3 --grants GRANTS.json
```

Use absolute, trusted local paths and owner-private grants (restrict any macOS
ACLs too). The parent must create **separate anonymous** stdin/stdout pipes,
keep the sole stdin writer, continuously drain stdout, and avoid leaking pipe
ends to other processes or attaching them to the terminal, a shared file, or a
named pipe. A pipe-type check rejects terminals, files and sockets; it is not
an authentication check or a proof that a FIFO is anonymous. Do not expose the
protocol to untrusted peers. Never put plugin tokens in the command line,
environment or control pipes.

The supervisor spawns only its own executable in the internal snapshot-child
mode, with cleared environment, suppressed child stderr, and a separate
anonymous lifetime pipe. The child does not serve before receiving the single
startup byte on that pipe. It launches no provider or descendant process.
Closing the supervisor's writer (including abnormal supervisor termination)
lets the child's watchdog observe EOF and exit. This design relies on the OS
closing all copies of the writer, and on the child being scheduled. It is not
kernel-enforced instant parent-death signaling and does not guarantee cleanup
of a suspended/uninterruptible child. It is read-only only because lease loss
may interrupt in-flight responses immediately.

The supervisor accepts a size-bounded readiness event for a canonical
`http://127.0.0.1:<ephemeral-port>` origin and API version/mode matching the
snapshot path, checks that endpoint's bounded `/healthz` response and the
child's liveness, then writes a `ready` event with `backend_pid` to its own
stdout. The desktop sends exactly `stop\n` or closes the control pipe; the
supervisor closes the lifetime writer, polls the exact child for termination,
and escalates to killing that same child if needed. It reports a `stopped`
event with `forced` on normal control shutdown; errors close the writer and
attempt bounded cleanup. Drain stdout concurrently, as blocking writes to an
undrained desktop pipe are not covered by the startup/reap deadlines. The
readiness check is not authentication of the HTTP endpoint. Use existing
least-privilege bearer grants for archive access.

## Managed ordinary live

The same private-pipe launcher also accepts
`desktop-supervise --live PRIVATE_ARCHIVE --grants PRIVATE_GRANTS` on macOS
for an already initialized ordinary unguarded archive only. The foreground
path is unchanged. The supervised archive uses the same local directory,
mode, exact instance checks, SQLite WAL transaction and permission rules.
The supervisor does not own an archive-wide exclusive lock; SQLite still
coordinates ordinary access. It creates no provider process or helper.

Owner loss closes admission and asks the HTTP server to stop accepting.
Both native and compatibility SSE streams end rather than waiting for their
normal TTL. Already admitted requests may drain, including in-progress SQLite
transactions. There is a six-second independent watchdog; if a runtime or
peer is stuck, the process exits, and the supervisor independently bounds
cleanup of the same child. A nonzero live child stop or forced kill is not
reported as a clean stop. Do not treat EOF as proof that an in-flight write
failed: on forced termination a response may be lost after commit. Reconnect
to the same archive and use the original idempotency key or check current
revision before repeating an operation. WAL retains ordinary crash recovery;
there is no promise every request finishes before the deadline.

Snapshot EOF behavior is unchanged and remains immediate. No guarded,
browser or media-bundle supervision is added on macOS. Windows snapshot
supervision and Linux modes are unchanged. This source has not been built
or run on macOS; there is no platform qualification claim.

References: [Apple pipe(2)](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/pipe.2.html)
and [Rust ChildStdin](https://doc.rust-lang.org/std/process/struct.ChildStdin.html).
