# Windows Portable Package

The repository builds a self-contained Windows x64 portable package for Local
Message Hub. It is a bundled Python executable plus a separate stdio MCP
executable, not the retained WeFlow Electron application or an NSIS installer.
This package and its source-reader integrations are Windows-targeted; no
macOS/Linux build or runtime acceptance is provided, so the source bundle does
not imply support on those platforms.

## Build

From a Windows x64 checkout with PowerShell 5.1 or later, Python 3.12, Node/npm,
Rust 1.98.1 GNU, and MinGW-w64 GCC 16.2.0 available for build-time staging:

```powershell
npm ci --ignore-scripts --legacy-peer-deps
node .\node_modules\ffmpeg-static\install.js
if (-not (Test-Path .\node_modules\ffmpeg-static\ffmpeg.exe)) { throw 'Pinned FFmpeg binary is missing.' }
.\scripts\build-windows-portable.ps1 -OutputPath .\output\windows\LocalMessageHub-portable
Compress-Archive -Path .\output\windows\LocalMessageHub-portable\* `
  -DestinationPath .\output\windows\LocalMessageHub-portable.zip
```

The build pins Python dependencies and verifies the official QCE v6.3.2 Windows
NapCat shell by SHA-256, then builds the QCE server from the repository's vendored
source and Cargo.lock. The build rejects any GCC version other than 16.2.0. CI
updates its MSYS2 environment and installs GCC plus its matching dependencies
from the official signed package repositories, then checks the compiler version.
It stages selected project trees and excludes local runtimes,
account settings, databases, caches, and generated output. Its smoke test starts
the executable with temporary storage, then checks the WebUI, Lucide asset,
three platform entries, storage isolation, and read-only MCP endpoint. It also
launches the standalone MCP executable with a unique temporary storage
directory, sends `initialize`, `tools/list`, and `exit` over stdio, validates
JSON-RPC stdout and expected tools, captures stderr separately, and enforces a
15-second timeout. It does not read real source-app data. This runs in Windows
CI as part of the package build; do not bypass local application-control policy
if it blocks an unsigned executable. Use `-SkipSmokeTest` only if you intend to
run the package smoke separately before delivery.

After both package builds pass, CI writes `LocalMessageHub-artifact-manifest.json`
with a sorted inventory of every portable file plus the source archive hash and
size. It records the commit SHA, workflow run ID, and build context (`hosted-ci`);
paths are relative to package roots. A local manifest is explicitly marked
`local-worktree` and must not be presented as hosted release provenance.
The manifest detects byte changes relative to its recorded inventory, but is
not signed and does not authenticate who created or stored it.

## Runtime and Privacy

- The built package runs without installed Python, Node/npm, Rust, or a network
  installer. Build-time Python/Node/Rust/MinGW and network access are needed to
  fetch pinned dependencies, locked Cargo crates, and the verified NapCat shell.
- The portable package bundles the lockfile-selected `ffmpeg-static` Windows
  binary for offline WXGF/WXAM image and media decoding. Conversion output is
  capped at 64 MiB. Its upstream license and package notices ship under
  `licenses/ffmpeg-static/`.
- The included QCE shell, locally compiled QCE server, and matching GPL source/license are present in the
  package. On first use its runtime is copied under
  `%LOCALAPPDATA%\LocalMessageHub`, not into QQ's installation folder.
- Hub storage and integration settings default to
  `%LOCALAPPDATA%\LocalMessageHub`. Actual user data is created only after the
  user launches/configures the app and is never part of the build output.
- `.local`, development `.runtime`, integration venvs, `node_modules`, account
  credentials, private databases, and chat exports are not staged. The NapCat
  shell is fetched from its public release and verified independently; its QCE
  server is compiled from the vendored source.
- The package is unsigned. Windows SmartScreen may display an unknown-publisher
  warning. It is not an installer or intended for silent enterprise deployment.
- `package.json` still describes the upstream WeFlow Electron application; its
  `npm run build` does not produce this Hub package.

## Release Notes

The current local-worktree portable and source packages pass their privacy and
isolated startup checks, and a `local-worktree` manifest verifies their exact
file bytes. Hosted CI and artifact upload/download remain unverified. Public
release publication remains a separate review step: verify hosted CI, confirm
third-party notices and source-offer obligations for GPL QCE distribution,
scan the final release artifact, and sign the binaries if a trusted Windows
publisher identity becomes available.
