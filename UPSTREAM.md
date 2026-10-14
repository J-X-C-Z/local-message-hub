# Upstream Sources

## WeFlow

- Repository: https://github.com/hicccc77/WeFlow
- Commit: `2f23e72cebf7f309a0f4d129307d16d7b0cd41c3`
- Package version: `5.0.0`
- Imported on: 2026-10-10
- Import method: GitHub source archive, without upstream Git metadata.
- Verification: all 552 imported files matched the archive using SHA-256.
- License: original `LICENSE` retained (CC BY-NC-SA 4.0).

The upstream source is the initial application base in this repository. Importing
the source does not provide the native database/key/media components removed from
the upstream repository. Standalone data access must be implemented and verified
separately; the upstream component-path configuration is not a completed solution.

## QQ Chat Exporter / NapCat

- Repository: https://github.com/shuakami/qq-chat-exporter
- Release: `v6.3.2`, commit `7d154b847a0cb3f9cffbdb126598c4d1434995ba`
- Source: `integrations/qq/upstream/expanded/qq-chat-exporter-6.3.2`
- Original GPL-3.0 license retained with the source.
- Bundled Windows shell: `.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64`
- Archive SHA-256: `3fc093ba2332b97ac17e16160d5baa6ffe0a8def6c394d515a94777fe0ca492c`
- The integrated provider calls the upstream QCE read/history/resource APIs.
  QQ login is an explicit local user action. Runtime, access tokens and logs are
  ignored, not committed. Do not use the original BAT fallback to write into the
  installed QQ directory.

## QQNT Local Database References

- QQNT_Export: https://github.com/Tealina28/QQNT_Export, schema/cache reference
  revision `714d228dcd52f74b06bec91d4dba6ab856f16cd1`, GPL-3.0.
- ARK-1: https://github.com/star-picker/QQNT-Database-Export-Tool, AGPL-3.0.
  Its SQLCipher configuration confirms page size 4096, KDF iterations 4000,
  PBKDF2-HMAC-SHA512 and page HMAC-SHA1. It requires an existing pass key.
- QQBackup Windows key helper:
  https://github.com/QQBackup/qq-win-db-key. Its debugger extraction is not
  executed by the read-only local reader.

These are format and behavior references, not vendored source or bundled
executables. The local implementation uses existing Python/cryptography
dependencies. Android UID/header key derivation is not treated as a verified
Windows method. Existing upstream license obligations above still apply.

## Enterprise WeChat

The local reader is integrated under `integrations/wecom`. Account slots,
machine-specific paths, keys and chat exports are kept only in ignored local
configuration and are excluded from release packages. The adapter copies and
decrypts a single configured local account snapshot; it never writes the
original database.
The supplied local source has no standalone license statement. No right to
redistribute private data is implied.

## WeChat Native Reader

- Repository: https://github.com/fanyuantaier/wechatauto-replica
- Version: `1.2.6`, commit `d6e61bea7f9c75906faa8b71d37ef3572fba2454`
- Apache-2.0 source and license in `integrations/wechat/vendor`.
- Direct read-only Windows process/database integration; no external WeFlow
  instance or manually supplied native component required.
- Local SILK decoding uses `pilk-nogil==0.4.3`; nothing is uploaded.

## Unified Service

The WebUI is `webui/`; source databases, derived index and cached media remain
local. See `docs/LOCAL_HUB.md` for startup and acceptance boundaries.

This is local integration work, not a claim of unrestricted redistribution.
Retain all upstream licenses and assess their combined obligations before
publishing or commercially distributing a packaged derivative.

## Accepted Product Scope

The first delivery targets local Windows use with real WeChat, Enterprise WeChat
and QQ records. It supports selecting a platform and searching across all
platforms. Enterprise WeChat access is scoped to one explicitly configured
local account.

The first delivery covers conversations, contacts/groups, media viewing, filtered
search, export, synchronization and settings. Statistics, reports and
platform-specific features follow as part of the full WeFlow feature alignment.
The UI visual direction was informed by a local HTML reference; the reference
file and its machine-specific path are not part of this repository.
