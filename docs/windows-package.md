# Windows Portable Package

The repository builds a self-contained Windows x64 portable package for Local
Message Hub. It is a bundled Python executable plus a separate stdio MCP
executable, not the retained WeFlow Electron application or an NSIS installer.

## Build

From a clean Windows x64 checkout with PowerShell 5.1 or later, Python 3.12, and
Node/npm available for build-time asset staging:

```powershell
.\scripts\build-windows-portable.ps1 -OutputPath .\output\windows\LocalMessageHub-portable
Compress-Archive -Path .\output\windows\LocalMessageHub-portable\* `
  -DestinationPath .\output\windows\LocalMessageHub-portable.zip
```

The build pins Python dependencies and verifies the official QCE v6.3.2 Windows
shell by SHA-256. It stages selected project trees and excludes local runtimes,
account settings, databases, caches, and generated output. Its smoke test starts
the executable with temporary storage, then checks the WebUI, Lucide asset,
three platform entries, storage isolation, and read-only MCP endpoint. It does
not read real source-app data. Use `-SkipSmokeTest` only if you intend to run the
same smoke check separately before delivery.

## Runtime and Privacy

- The built package runs without installed Python, Node/npm, or a network
  installer. Build-time Python/Node and network access are needed to fetch
  pinned dependencies and the verified upstream QCE archive.
- The included QCE shell and matching GPL source/license are present in the
  package. On first use its runtime is copied under
  `%LOCALAPPDATA%\LocalMessageHub`, not into QQ's installation folder.
- Hub storage and integration settings default to
  `%LOCALAPPDATA%\LocalMessageHub`. Actual user data is created only after the
  user launches/configures the app and is never part of the build output.
- `.local`, development `.runtime`, integration venvs, `node_modules`, account
  credentials, private databases, and chat exports are not staged. The bundled
  QCE runtime is fetched from its public release and verified independently.
- The package is unsigned. Windows SmartScreen may display an unknown-publisher
  warning. It is not an installer or intended for silent enterprise deployment.
- `package.json` still describes the upstream WeFlow Electron application; its
  `npm run build` does not produce this Hub package.

## Release Notes

The local package build passed its extracted-package smoke test. Public release
publication remains a separate review step: confirm third-party notices and
source-offer obligations for GPL QCE distribution, scan the final ZIP, and sign
the binaries if a trusted Windows publisher identity becomes available.
