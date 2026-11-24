# Rust desktop connection SDK

`local_message_hub_rs::desktop_sdk` is production library source for the native
desktop side. It does not run programs, connect over HTTP, manage credentials on
disk, install anything, or implement a source adapter broker.

1. Select the mode and exact account. `Platform::supports` describes the native
   supervisor matrix: snapshots and ordinary native-live on Linux, macOS and Windows;
   imported-media supervision on Linux only. Guarded/browser modes and
   unsupported OS targets are not offered. New macOS/Windows live support is source-only
   and not OS-qualified.
2. Generate a `DesktopGrant` with a unique authorized ID and exact `AccountScope`.
   It validates the serialized grant with the same parser that the Hub uses. A new
   grant has only account-scoped conversation/message read. Text export, imported
   media access, and UI preferences require explicit typed options; the latter two
   are restricted to their respective modes. No provider/auth configuration, shell
   options, notifications, ingestion or legacy write access is generated.
3. Use `write_new` for a fresh, constrained lowercase `.json` filename inside an
   existing private config directory. It stages beside the target and refuses to
   replace an existing file. The file contains the token's SHA-256 digest, not the
   token itself. Unix checks group/world mode and writes at mode 0600; the caller
   remains responsible for ownership, private ACLs (including all ancestors), and
   Windows NTFS ACLs. This is no-clobber publication, not a power-loss guarantee.
4. Build a native `Launch` using trusted absolute executable, archive and grant
   paths. It produces separate OS-string arguments with a closed option vocabulary.
   It rejects symlinks, nonregular or wrong-kind leaves, and Windows reparse, device,
   UNC and alternate-stream paths. It does not verify executable provenance,
   prevent races in an untrusted directory, or itself open the snapshot. Windows'
   native supervisor additionally checks the volume type.
5. The desktop parent owns exclusive anonymous control/readiness pipes and enforces
   its own read deadline/1024-byte bound. Pass only the one LF-terminated readiness
   frame from that process to `accept_ready`. The SDK checks the exact version,
   expected mode, canonical IPv4 loopback origin, nonzero port and nonzero PID.
   Readiness is not endpoint authentication. Keep the supervisor/child liveness
   association and discard the connection on unexpected exit or stop.
6. `Connection::url` builds a URL only from an enumerated endpoint and validated
   origin. Use `with_token` to borrow the bearer briefly for authenticated requests to
   that exact origin, never health probes, a proxy, an arbitrary link or a redirect.
   Disable HTTP redirects and environment proxies and redact credentials. For
   typed native loopback reads including search, context and WeChat quote lookup,
   use the separate [desktop client](DESKTOP-LOOPBACK-CLIENT.md). Do not copy the token into an
   argument, environment, log, archive or persisted frontend store. The SDK zeroizes
   its token when dropped but cannot erase copies made by a caller or HTTP stack.

For lifecycle behavior see the [macOS](MACOS-SNAPSHOT-SUPERVISION.md) and
[Windows](WINDOWS-SNAPSHOT-SUPERVISION.md) supervisor notes. The Windows helper is
not Job Object parity. No platform execution, compilation, or compatibility
claims are made for this checkpoint.
