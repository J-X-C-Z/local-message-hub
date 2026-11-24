# Windows foreground snapshot supervision (source only)

A trusted desktop integration can launch one trusted Hub core child in read-only
snapshot mode:

```text
local-message-hub-rs.exe desktop-supervise --snapshot "C:\private\index.sqlite3" --grants "C:\private\plugins.json"
```

Use absolute ordinary drive-letter paths on a fixed local volume. The source
rejects reparse-point components, non-regular executable/snapshot/grant
leaves, UNC and device path prefixes, and named-stream path syntax. This is
not ACL enforcement or a defense against an untrusted process changing those
paths. Put the trusted release binary, snapshot and grant file in a private
tree, with NTFS ACLs that prevent untrusted peers from reading grants or
modifying the files and ancestors. Grant credentials never traverse the
control pipe or appear in the environment or command line; the child gets
the private grant-file path and a cleared environment.

The caller supplies separate, private anonymous stdin and stdout pipes. Keep
only the intended endpoints, never pass spare ends to other processes, and
drain stdout concurrently. The Windows standard handle check rejects terminal
or file handles but cannot distinguish an anonymous pipe from a named pipe or
authenticate the peer. Do not attach untrusted input. The portable PowerShell
`run-core.ps1` remains a regular foreground launcher, not this protocol.

Before reporting readiness, the parent validates the child's bounded frame
and probes its ephemeral IPv4 loopback `/healthz` response. It reports a
`ready` JSON line with the origin and `backend_pid`; authenticate archive
requests with a scoped bearer grant, not with this readiness event. To stop
the child, send exactly `stop\n` or close the control writer. The parent
closes the child's separate lifetime writer, waits a bounded interval, and
attempts to kill and reap only the child it directly created if needed. The
child watchdog exits on owner EOF, including after supervisor exit if no
other copies of the writer remain open. A timed-out or invalid startup does
not publish readiness. Blocking writes to an undrained outer stdout pipe
are not covered by the readiness/stop timers.

This is not a Windows Job Object, a security sandbox, documented
handle-list-only pre-exec association, instant kill-on-close, or a
process-tree cleanup guarantee. A suspended child cannot observe EOF until
it runs. Run this directly from a cooperating desktop process, not via an
untrusted shell or a service wrapper. Ordinary native-live now has a separate bounded-drain owner contract described in
[Windows native-live supervision](WINDOWS-NATIVE-LIVE-SUPERVISION.md). Guarded,
browser and media supervision remain unavailable. This is an implementation in source only;
no Windows compilation, execution, installer or compatibility result is
claimed.

See Microsoft’s [GetFileType reference](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfiletype)
and [CreateProcessW handle inheritance reference](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw).
