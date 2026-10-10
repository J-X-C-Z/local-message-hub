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

## Enterprise WeChat

The user-authorized local reader source is integrated under `integrations/wecom`.
Private account slots, paths, keys and chat exports were removed from source and
kept only in ignored local configuration. The adapter copies and decrypts a
single selected local account snapshot; it never writes the original database.
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

The first delivery runs on Windows locally and connects real WeChat, Enterprise
WeChat and QQ records. It supports selecting a platform and searching across all
platforms. The Enterprise WeChat source is the user's own local account.

The first delivery covers conversations, contacts/groups, media viewing, filtered
search, export, synchronization and settings. Statistics, reports and
platform-specific features follow as part of the full WeFlow feature alignment.
The UI reference is the user's local `noema_mimo_motion_showcase.html`.
