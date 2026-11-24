# Windows ordinary native-live supervision (source only)

The existing private-pipe `desktop-supervise` entry now accepts an already
initialized **ordinary Rust-owned** native archive on Windows:

```text
local-message-hub-rs.exe desktop-supervise --live "C:\private\native" --grants "C:\private\plugins.json"
```

It does not initialize, migrate, install or register a service, change an ACL,
launch a provider, or enable guarded/browser/imported-media supervision. The
ordinary `EventStore::open` path still refuses guarded archives and validates
native identity and files. Keep grants outside the archive. The existing
snapshot mode retains its immediate EOF exit behavior.

## Trust and admission

The trusted desktop must own separate private anonymous input/output pipes,
keep the sole control writer, and continuously drain output. As in snapshot
mode, Windows' pipe-type check cannot authenticate a peer or distinguish an
anonymous pipe from a named pipe. No grant token traverses the control pipe,
command line or environment; the child inherits a cleared environment and an
explicit private grant-file path. Its HTTP permissions are unchanged.

Executable and grants must be regular files; the archive must be an existing
directory. Both parent and child check the archive's absolute ordinary drive
path on a fixed volume, every ancestor and the leaf, refusing reparse points,
UNC/device prefixes, alternate streams, trailing spaces/dots, and wrong kinds.
This is not an ACL audit, executable attestation or race-free handle-based
filesystem confinement. The operator must protect the binary, grants, archive
and all ancestors with private NTFS ACLs. No current working directory or
relative-path fallback is used.

The child serves only after the existing one-byte lifetime-pipe handshake.
The parent validates the exact bounded native-live readiness frame, ephemeral
IPv4 loopback address and bounded health response before forwarding readiness.
Readiness is not HTTP authentication. Continue using the exact scoped grant
and discard a desktop connection when its owning process/pipe ends.

## Owner loss and bounded drain

A normal `stop\n` or outer control EOF closes the child's lifetime writer.
Child-side EOF, unexpected post-handshake bytes, or a non-interrupted read
error publishes the closed watch. Interrupted reads are retried. New requests
are refused with `backend_draining`, native and compatibility SSE waits end,
and HTTP stops accepting while already admitted requests drain. A closed watch
sender also fails closed. This uses the same ordinary-live request middleware
as macOS; it does not grant any additional capability.

The independent watchdog permits six seconds of drain and then exits with a
nonzero code if the process is still alive. The parent waits that limit plus
its existing 900 ms margin before attempting to kill only its directly owned
child, with another bounded reap interval. Nonzero live exit or a forced kill
is reported as `forced: true`, never as a clean stop. A write admitted before
owner loss may have committed even if its receipt was lost. Use the original
idempotency key or inspect current revision before retrying; EOF is not proof
of rollback. An uninterruptible/suspended child or undrained supervisor stdout
can exceed these practical bounds; neither a Windows Job Object nor a
process-tree/instant kill-on-close guarantee is implemented.

## Source coverage and remaining validation

This change adds Windows-only source tests for live owner EOF/unexpected
control bytes, invalid/missing handshake, interrupted/failed reads, fixed-path
kind checks, and two owner-drain middleware tests. A portable SDK matrix test
keeps imported-media limited to Linux. **None of these tests, any build, or
any runtime was executed for this source-only lane.** Windows compilation and
real owner process/pipe, in-flight write, SSE drain, ACL/reparse and shutdown
qualification remain required before claiming operational Windows support.
No performance or cross-platform equivalence result is asserted.
