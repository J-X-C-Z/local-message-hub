# Portable Hub core packaging

This is the staging recipe for Windows x64 and macOS. It packages a **prebuilt,
trusted Hub core executable supplied by the operator**. It does not build,
download, sign, notarize, install, archive, or run that executable. The format and
architecture checks do not establish the executable's identity, provenance,
runtime dependencies, or compatibility. Use only a known release build of this
repository on a trusted local filesystem.

## Stage the release folder

On Windows, from PowerShell 7.3 or later and the repository root:

```powershell
.\packaging\windows\stage-core.ps1 -Binary .\local-message-hub-rs.exe -Destination .\hub-core-windows-x64
```

The input ABI is `x86_64-pc-windows-msvc`. Windows ARM64 and x86 packages are not
supported. The launcher also needs PowerShell 7.3 or later on the destination
system. The packaging does not install a Visual C++ runtime or other system
dependencies; resolve any dependencies for the specific build separately.

On macOS, with Xcode Command Line Tools available for the staging step:

```sh
./packaging/macos/stage-core.sh arm64 ./local-message-hub-rs ./hub-core-macos-arm64
# Or stage a separate Intel bundle:
./packaging/macos/stage-core.sh x86_64 ./local-message-hub-rs ./hub-core-macos-x86_64
```

The inputs are thin native `aarch64-apple-darwin` and `x86_64-apple-darwin`
executables, respectively. Pick the bundle matching the host, not a universal or
Rosetta package. macOS may deny execution of an unsigned or quarantined copy;
normal release signing and notarization are separate from these scripts. Do not
bypass OS execution policy.

The destination must be a new directory under an existing, trusted local parent.
If a copy fails after creation, discard the partial folder; the scripts never
replace an existing distribution. No archive, database, plugin grants, secrets,
or OS service files are bundled. The layout is:

```text
hub-core-<platform>/
  README.md
  run-core.ps1                 # Windows, or run-core.sh on macOS
  bin/
    local-message-hub-rs.exe   # Windows, or local-message-hub-rs on macOS
```

To distribute a folder in an archive, preserve this layout and the executable
permission on macOS. Extract into a private, local directory controlled by your
account. Do not launch an executable from a public/shared folder, network share,
or an untrusted directory containing symlinks, junctions or reparse points. Leaf
path checks are not protection against an untrusted parent directory.

## Start the core

Use an existing Hub snapshot and a separately configured grants JSON. Keep the
grants and archive private; on macOS restrict the grants file to the owner. On
Windows create the archive and grants under an ACL that excludes untrusted users;
the native archive does not enforce Windows ACLs on your behalf. Do not copy a
browser profile, a provider database or account credentials into the bundle.

Once unpacked, call the foreground launcher from a terminal:

```powershell
.\run-core.ps1 -Mode Snapshot -Archive 'C:\private\index.sqlite3' -Grants 'C:\private\plugins.json'
```

```sh
./run-core.sh snapshot "$HOME/private/index.sqlite3" "$HOME/private/plugins.json"
```

`Snapshot` / `snapshot` selects an existing read-only snapshot; `Live` / `live`
selects an existing **unguarded** native archive directory
(with owner-only mode on macOS). The launcher never creates or converts it.
Live mode is not read-only: grants determine what clients may do.
Both wrappers call only `serve`, bind to IPv4 loopback via the core, and default
to a free port. Read the core's JSON readiness line for the origin. To select a
fixed port, append `-Port 8765` on Windows or `--port 8765` on macOS.
Stop the foreground process with Ctrl+C.

These foreground launchers intentionally reject any mode other than snapshot
or unguarded live. Linux-only guarded mode, browser, and media-bundle serving
are not provided on Windows or macOS. The source desktop integrations can
supervise snapshot and ordinary native-live modes by launching a suitably
built core directly using private anonymous pipes, not these wrappers. See
[macOS supervision](../MACOS-SNAPSHOT-SUPERVISION.md), [Windows snapshot
supervision](../WINDOWS-SNAPSHOT-SUPERVISION.md) and [Windows ordinary
native-live supervision](../WINDOWS-NATIVE-LIVE-SUPERVISION.md). The Windows
integration is a foreground single-child owner-pipe implementation, not Job
Object parity or a platform build/packaging result. The wrappers do not
provide a desktop app, background service, installer, client extractor,
provider integration, MCP launch, or Linux parity.
No binaries, installers, platform verification, or compatibility results are
included with the staging source.
