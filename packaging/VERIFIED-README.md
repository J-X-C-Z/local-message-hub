# Local Message Hub core

This directory contains a native Hub core binary and, on macOS/Windows,
a foreground launcher. Treat it as untrusted until its complete inventory
and SHA-256 hashes have been verified using an independently trusted
`verify-portable-core` binary and manifest digest.

Do not run anything from an unverified package. Extract into a new private
local directory, preserving Unix executable modes. Do not add grants,
credentials, archives, or local settings to this distribution directory.
Once verified, supply your separately provisioned private snapshot and
grants to the trusted core using the documented `serve` command.
