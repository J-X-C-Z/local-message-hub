# Verified portable Hub core

Run the publisher/verifier from an independently trusted Hub build on the
native target. It reads or copies binaries but never executes them, builds
software, creates an installer, or downloads anything.

```text
local-message-hub-rs stage-portable-core ABSOLUTE_BINARY ABSOLUTE_NEW_DIR TRUSTED_BINARY_SHA256 SOURCE_REVISION
local-message-hub-rs verify-portable-core ABSOLUTE_EXTRACTED_DIR TRUSTED_MANIFEST_SHA256
```

The stage command requires the expected executable SHA-256 from your trusted
release process and a 40-character lowercase source revision label. This label
is an operator assertion, not proof that the binary was built from that revision.
Native platform gates are Windows x64 PE32+, macOS x64/arm64 thin executable Mach-O,
and Linux x64/arm64 ELF64. Format headers are sanity checks, not runtime ABI,
library-availability, build, or platform qualification. Cross-architecture
staging and verification are intentionally not supported.

Use a trusted local filesystem with private, operator-controlled paths and ACLs;
on Windows only ordinary fixed drive-letter paths are accepted. Inputs, ancestors,
destination and the final verification tree cannot rely on symlinks or reparse
points. The tool cannot defend against another writer with control over the tree.
The final destination must not exist. Copies, file digests, exact inventory and
manifest are checked in a private sibling staging directory, then promoted in one
native **no-replace** directory move. Unsupported native moves fail closed; no
copy-to-final or plain rename fallback. An interruption before publication may
leave a private hidden temp directory but not a partial final path. This is not
a filesystem power-loss durability guarantee.

For distribution mail or archives, keep the entire generated directory layout
and executable modes. This tool does not create/extract ZIP or tar files. Use a
trusted extractor that prevents path traversal and link writes, extract into a
fresh private directory, and verify that extracted package **before** executing
the shipped binary or launcher. The verifier must come from outside the archive.
Share the stage receipt's `manifest_sha256` over a separately authenticated
channel; the hash included in an untrusted archive or computed from an untrusted
manifest is not a trust anchor. Verification checks the pinned manifest bytes,
exact directory members, and each listed file size and SHA-256. It does not
authenticate ancillary filesystem metadata, platform signatures or transport.
Windows/macOS wrappers are the previously reviewed bytes embedded during staging;
Linux includes the bare core. No passwords, plugins, grants, data or providers
are included. Legacy staging scripts remain unverified recipes and do not
produce this manifest.

Publication APIs: [Apple XNU](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/syscalls.master)
and [Microsoft MoveFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefilew).
