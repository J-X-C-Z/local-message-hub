# Development Log

## 2026-10-15 - P13 Offline WeChat MCP Status

- HTTP-generated MCP configurations now keep
  `get_wechat_send_status` available when the WeChat client is offline, while
  `prepare_wechat_send` remains gated on client readiness.
- Added one configuration regression covering both offline and ready states.
  No request was prepared and no message was sent.

## 2026-10-15 - P14 Documentation Commit Hosted Verification

- Windows runs `38672652376` (`main`) and `38672652404`
  (`codex/roadmap-p8-p14-20261014`) passed for commit
  `2860ce8ba87d69be1b473e4dfbfe8a39d648ad73`, including the full gate,
  portable/source packaging, smoke checks, and artifact download byte and
  provenance verification.
- These runs predate the current uncommitted source-media pagination changes.

## 2026-10-15 - P11 Unindexed WeChat Media Pagination

- Replaced the 10,000-row source-media scan ceiling with cross-shard keyset
  pagination. The Hub keeps only the requested page and continues filtering
  indexed rows as it advances.
- Added cursor ordering, null sequence, deep-page, and file-variant normalization
  regressions. Real-account completeness remains unverified.
- Removed dynamic local send-queue state from the public roadmap. Personal
  WeChat MCP prepare/status capability is now recorded with its gate.

## 2026-10-15 - P11 WeCom Local Conversation Avatars

- Added read-only extraction of WeCom conversation avatar blobs from the exact
  `Data/avatar_store_v3.db` snapshot schema and exact `conversation_id` matches.
  Reads are capped at 2 MiB per image and 32 MiB per sync; the Hub validates
  image signatures and stores accepted bytes in its own local avatar cache.
- The WebUI now accepts the local WeCom avatar handle. No remote image request
  is made, and WeCom source databases remain read-only. Focused schema, sync,
  cache, offline-serving, and render regressions pass.
- Other client schemas, contact/member photo bytes, and real visual acceptance
  remain open.

## 2026-10-15 - P14 Package Refresh After P11 Media Changes

- Rebuilt the Windows portable package (9,783 files; 586,789,162 bytes) and
  sanitized source ZIP (690 files). Portable executable,
  extracted source Hub, three-source UI, read-only MCP and standalone MCP stdio
  smoke checks passed, as did package privacy scans.
- Portable executable SHA-256:
  `9ec003cc141fc582d04a104bb9c1b9ad4efa293a1af0effb3c1d41d176929782`.
  Manifest run ID: `local-019-p11-variants`.
- The manifest verifies exact package inventories and bytes against base commit
  `75fa73185b5b1acf960da35ef331901c3284deef`. Hosted CI and artifact
  round-trip remain separate and open.

## 2026-10-15 - P11 WeChat Variant Cache Fallback

- The Hub now resolves WeFlow `_b/.b/_w/.w/_c/.c` DAT variants as the lowest
  image tier. Best-available preview/export can use these local files; an
  original-only request still refuses to downgrade. The mapping follows the
  vendored WeFlow `getCachedPathTier` rule without assigning guessed dimensions.
- A focused synthetic regression covers all six suffixes, best-available
  fallback, and the original-only boundary. Broader real-cache and
  resource/decryption parity remain open.

## 2026-10-15 - P9 WeChat Preparation Gate and P14 Source Archive Refresh

- The local Hub started through the repository launcher and responded on its
  loopback endpoint. WeChat request preparation was refused while the
  `wechat_send` capability remained disabled; no request was created and no
  message was sent. Real delivery still requires a separate local approval.
- Refreshed the sanitized source ZIP after genericizing device-specific public
  documentation and removing local media-sample counts. Its 690 entries passed
  the source privacy scan and isolated WebUI, three-source and read-only MCP
  startup checks. The portable runtime was unchanged and reused; the regenerated
  `local-worktree` manifest verified both package byte inventories.
- Hosted CI and the uploaded/downloaded artifact round trip remain unverified
  for this worktree.

## 2026-10-14 - P14 Current Worktree Package Refresh

- Rebuilt the portable Windows package (9,783 files; 586,760,039 bytes) and
  sanitized source archive (690 files; 25,102,137 bytes). Isolated Hub/UI/MCP
  startup, source privacy scans, and manifest byte verification passed. The
  manifest uses `local-worktree` provenance and run ID
  `local-currentworktree-20261014-008`.
- Portable executable SHA-256:
  `ceba334b9b7c47bf65639429913b3e3affd22450fd81d81137b32171db902b0b`.
  Source ZIP SHA-256:
  `34586f51f7142f354ebc621268637fb19f15392e23f004cdb481d85fae71b027`.
  Manifest SHA-256:
  `2d93a91cf820c3ca17d540b28c289109e0ea71711e348b8123b50a7cd9a297eb`.
- This is a current-worktree package, not hosted release evidence; hosted CI
  and artifact round-trip still refer to the committed revision.

## 2026-10-14 - P11 WeChat and QQ Cached Image Parity

- WeChat local image lookup recognizes the pinned WeFlow reader's original and
  thumbnail suffix variants and selects the largest cache file within each
  tier. Its focused media suite passed 59 tests.
- QQ local parsing now exposes QCE `market_face` items as account-scoped image
  resources for the existing preview and export path. The focused parser/cache
  test and QQ rich-content WebUI regression passed. Real device cache naming
  remains unverified.

## 2026-10-14 - P12 Notification Payload Minimization

- The body-free SSE message payload now includes only platform, account,
  conversation ID, title, and conversation kind. Sender name, timestamp,
  message type, and internal row ID are omitted from JSON; the row ID remains
  only as the SSE event ID for reconnect. A focused HTTP regression passed.
- Real desktop-notification delivery remains unverified.

## 2026-10-14 - P14 Current Worktree Package Refresh

- Rebuilt the portable Windows package (9,783 files, 586,751,885 bytes) and
  sanitized 690-file source ZIP (25,101,179 bytes). Isolated Hub startup,
  standalone MCP protocol, source privacy scans, and local manifest verification
  all passed. The manifest uses `local-worktree` provenance and run ID
  `local-currentworktree-20261014-006`; it is not hosted artifact evidence.
- Portable executable SHA-256:
  `1fef6a05655abfcba6e395c64996d52b0b28eac635d36b04451e77ae2bc208f`.
  Source ZIP SHA-256:
  `ebc1a6cbbce0dc8d70d0e5ffbf3b91451b856b607b14fad0339457da82d7c714`.
  Manifest SHA-256:
  `9860a2b6f39a60377587db7dc658de5e1d2823a1e8d354bf7365b4388a6e4888`.
- The package includes current uncommitted source changes but no `.local`
  account data. Hosted CI and artifact round-trip for this exact worktree remain
  unverified.

## 2026-10-14 - P9 QQ Readiness Receipt Validation

- QQ login readiness now requires `retcode` to be an exact integer zero, matching
  the existing delivery receipt check. Synthetic `false` and `0.0` responses
  report the API unavailable and do not expose an account identity.
- The focused readiness regression passed. No NapCat endpoint was contacted.

## 2026-10-14 - P11 QQ Cache Gallery Thumbnails

- Visible QQ global-cache image tiles now join the existing lazy preview queue
  and resolve through account-scoped opaque resource IDs. Concurrency remains
  bounded and page changes cancel pending reads; no source download was added.
- The focused media gallery regression passed for URL, MIME, account, resource
  identity, and tile update. Real cached-media acceptance remains open.

## 2026-10-14 - P8 MCP Client Capability Mapping

- MCP client config now exposes `search_messages` through the core search
  capability and gates `list_sources` through statistics. A focused HTTP
  regression verifies search stays available with chat browsing disabled and
  the source-list tool follows the stats switch.
- A prior operation reported the exact user-provided WeChat request queued for
  local approval; a later sanitized probe reported no request queued. With the
  Hub stopped and private storage uninspected, current persisted status is
  unverified. No message was delivered; request contents and identifiers stay
  out of this public log.

## 2026-10-14 - P11 Single Image Best-Tier Preview

- The media gallery now offers an explicit local best-tier read for one WeChat
  chat image. It updates only the selected tile; a cache miss leaves its current
  preview intact, and ordinary viewport previews still request `mid`.
- The gallery regression covers selected-message identity, tile-local updates,
  and cache-miss preservation. No source-client/network download path was added.
- At this point a local operation reported a text-only WeChat test request as
  `pending_approval`. A later sanitized probe reported no request queued. The
  Hub is stopped, so current persisted status is unverified; no message was
  delivered. Request content and identifiers stay out of public docs.

## 2026-10-14 - P11 Stable MCP Cache Reads

- Cached-image and Moments media reads now compare the path's regular-file
  identity with the opened file handle, then recheck that handle after the
  bounded read. A cache replacement before open is rejected by regression
  coverage; the complete Hub server suite passed 208 tests.
- A same-inode writer that restores modification time can still evade the
  metadata check. Real cache/decryption coverage remains open.

## 2026-10-14 - P13 Localized WeChat Window Detection

- The sender now recognizes the exact localized main-window title `微信` as
  well as `Weixin`, while continuing to require the expected window class and
  an exact title. A sanitized live readiness probe reports the account/window
  ready and desktop input available; the main window is not foreground. The
  focused sender suite passed all 25 tests.
- That probe found no composed or queued request and no delivered message. The
  Hub was stopped and `wechat_send` was disabled in local settings. Current
  persisted status is unverified because the Hub is stopped. Delivery still
  requires the Hub, the user enabling sending, the target window, and
  per-request approval.

## 2026-10-14 - P8-P14 Local Gate and Package Refresh

- The full local gate passed: 795 Python tests, four source-mutation gate
  checks, 32 WebUI checks, package/privacy and deterministic ZIP checks, and
  the 25,000-message performance run. Ingest took 1.712s, first search 0.035s,
  50-page pagination 1.585s, backup 0.107s, and restore 0.274s; WAL reported
  zero failures.
- Rebuilt the Windows portable package (7,554 files, 288,298,893 bytes) and
  sanitized source ZIP (690 files, 25,055,029 bytes). Isolated startup, MCP,
  and package privacy checks passed. The local-worktree manifest verifies the
  package bytes for base commit `75fa73185b5b1acf960da35ef331901c3284deef`;
  these packages include uncommitted worktree changes and are not a release.
- A sanitized probe at that time found no WeChat request queued; a later
  operation reported one prepared. The Hub is stopped, so current persisted
  status is unverified. No content was entered through the desktop and nothing
  was delivered. QQ/NapCat live receipt, real-cache parity,
  desktop notification/native mutation acceptance, and clean hosted artifact
  verification for the current worktree remain open.

## 2026-10-14 - P9 Exact QQ Delivery Receipt Validation

- NapCat OneBot receipts now require `retcode` to be an exact integer. Boolean
  and floating-point lookalikes cannot be reported as sent or rejected.
- All 17 focused sender tests passed. No QQ or NapCat endpoint was contacted;
  live delivery acceptance remains open.

## 2026-10-14 - Stable media ZIP reads

- Resource export now rejects a source cache file that changes identity, size,
  or modification time during its streamed copy, and removes the partial item.
- Synthetic same-size mutation, growth-after-stat, and cancellation checks
  passed. This does not close real-device cache coverage.

## 2026-10-14 - P14 Review Package p371

- Rebuilt the portable package and sanitized source ZIP from the current
  worktree. The portable app and standalone MCP passed isolated startup checks;
  both package inputs passed privacy scanning, and the 690-file source ZIP
  passed its isolated Hub/MCP smoke.
- Wrote and verified a `local-worktree` manifest against both package inputs;
  every recorded size and SHA-256 matched. The current Git-visible-file privacy
  scan passed. This local evidence does not verify hosted CI or artifact
  upload/download, and nothing was committed or published.

## 2026-10-14 - P11 Live Media Resolution Recheck

- Repeated the bounded local media probe with the WebUI's exact parameter
  mapping, including `media_entry.resource_index` for indexed attachments.
  Across 12 chat-image candidates per platform, two WeChat items resolved to
  local media URLs; the remaining WeChat items and all sampled WeCom/QQ images
  returned the ordinary not-cached result. No HTTP errors occurred.
- Corrected the prior `message_missing` observation: that probe omitted the
  nested attachment index and sent image-tier options to non-WeChat media. The
  error came from the probe parameters, not evidence of a resolver defect.
  No identifiers, paths, message content, or media bytes were recorded.

## 2026-10-14 - QQNT High-Index Attachments

- Aligned the local QQNT protobuf decoder and media resolver with the existing
  bulk-export limit: message attachments at indices 0 through 100 are now
  preserved and accepted; index 101 is rejected. WeCom's independent 0–49
  limit is unchanged.
- Focused checks passed for decoding all 101 synthetic attachments and for
  routing index 100 to the QQ local provider while rejecting 101. No source
  database was changed.

## 2026-10-14 - QQ Discovery Request Reused Verified Key

- After the user's one-time approval, submitted one confirmed key-discovery
  request through the local Hub. The Hub rejected a new scan because the local
  QQNT database was already readable with a verified, locally protected key;
  no process memory was scanned and no key/configuration was changed.
- Live status remains connected with no sync error or active progress.
  Key-discovery status retains its completed message from the prior
  verified-key sync; this request did not start a new import or write to the
  source database. No account identifiers or personal message totals are
  recorded here.

## 2026-10-14 - QQ Local Send Target Resolution

- QQ history is read from the local QQNT database while sending still goes
  through NapCat OneBot, so the two sides disagreed on identifiers: private
  conversations are indexed by QQNT uid (`u_...`) but OneBot 11 accepts only
  a numeric uin/group code. The Hub now resolves that at prepare time.
  `integrations/qq/send_target.py` holds the single rule set taken from the
  runtime: a 5-20 digit id is already a target, a private uid is mapped
  through the reader's own uid tables, and a non-numeric group id is refused.
  Groups need no mapping because NapCat uses the group code as the group peer.
- The resolved number is what is stored, reviewed and delivered; the local id
  stays visible in the review dialog as `source_conversation_id`. Unresolvable
  identities fail closed with a specific error code before any request is
  queued, so no send can target the wrong chat. Delivery still re-checks that
  the online NapCat account matches the request's account.
- Focused validation passed: `integrations.qq.test_send_target`,
  `integrations.qq.test_local_provider`, `hub.test_sending`, `hub.test_mcp`,
  the new WebUI composer/review assertions, and the full 446-test Python suite.
  No live NapCat runtime, QQ client or real QQ account was contacted; real
  message delivery remains an explicit user-run acceptance step.

## 2026-10-14 - QQ Send Target Integration Coverage

- The resolver and its UI were wired into the repository's own check instead of
  running only as ad-hoc test files. `scripts/check-local-hub.ps1` now runs
  `integrations.qq.test_send_target` with the other QQ modules, includes
  `webui/test-send-compose-local-uid.cjs` in the WebUI suite, and compiles
  `integrations/qq/send_target.py` alongside the other imported modules.
- `hub.test_sending` gains a service-level case proving the review-only
  `source_conversation_id` never becomes part of the durable request: it is
  absent from `send_requests`, changing it does not make a replayed prepare a
  different message, a non-string value is ignored, and status reads keep
  returning the resolved number as the target.
- Validation: the whole `scripts/check-local-hub.ps1` Python set (773 tests)
  and the 28 WebUI test files pass in the local environment with the interpreter that
  has pytest-free `unittest` and no QQ client contact. Real message delivery
  still needs a fully exited QQ client, an online NapCat account matching the
  request's account, and an explicit approval per message.

## 2026-10-14 - Personal WeChat Text Sending

- `integrations/wechat/sender.py` adds `WeChatGUISender`, a text-only
  sender for the logged-in local WeChat client. It uses public Win32
  `ctypes` calls plus `PrintWindow` and DIB pixel statistics to locate the
  target conversation window, confirm it is focused with a readable input
  area, enter the text, and press the send control. It never injects into
  the client, hooks DLLs, or writes to its database.
- Identity is proven from the window title (exact match for a private
  conversation, or `@chatroom` suffix for a group); several windows with
  the same title are refused as `wechat_conversation_ambiguous` instead of
  guessing a target.
- A delivery is only marked `sent` when a read-back of the conversation's
  cached message rows shows the exact same text sent by the local account
  with an ID that was not present before the send. Anything else becomes
  `unknown` and is never retried.
- `hub/sending.py`, `hub/capabilities.py`, `hub/server.py`,
  `hub/mcp_server.py` and `webui/app.js` wire the channel into the shared
  pending-approval flow as the default-off `wechat_send` capability, with
  its own routes (`/api/wechat-sends`), readiness probe and settings tile.
  MCP only exposes `prepare_wechat_send` and `get_wechat_send_status`.
- `integrations/wechat/test_sender.py` (19 tests), plus new `hub.test_sending`,
  `hub.test_server` and `hub/test_mcp` regressions and the WebUI
  send-history render suite cover capability gating, the chatroom suffix
  rule, cross-platform isolation, receipt disambiguation and the failure
  codes. Real delivery on a real conversation has not been verified.

## 2026-10-14 - MCP Import-Job Pagination

- `list_imports` now pages import jobs in SQLite instead of slicing only the
  first 100 rows in memory, and reports the full job count for every page.
- The MCP regression creates 101 synthetic jobs and verifies the first page,
  the page crossing the 100-row boundary, the total, and an empty terminal
  page. `python -m unittest hub.test_mcp -q` passes (37 tests).

## 2026-10-13 - WeChat Avatar Cache Freshness

- Local WeChat avatar sync orders duplicate `head_image` rows by
  `update_time` and keeps the first signature-valid image per exact username,
  preventing a stale duplicate from replacing a newer cached avatar.
- A synthetic SQLite regression confirms newest-first selection even when the
  older row was inserted later. The focused WeChat contact/provider suite
  passes; no private account data or remote avatar source was accessed.

## 2026-10-13 - Private WeChat Image-Key Setup and QQ Media Lookup

- WeChat image keys can be entered through the local source settings, validated
  against an existing cached image, then stored per account with current-user
  Windows DPAPI. The key is absent from ordinary WeChat configuration, API
  responses, status text, logs, and documentation. The existing WeChat memory
  scanner stays disabled; no source-client file is modified.
- QQ attachment lookup now binds cached search results to the exact attachment
  match criteria as well as account and cache roots, preventing one message's
  candidates from being reused for another. Directory changes invalidate the
  index; path, account, file size and MD5 checks remain in force.
- Focused QQ/WeChat tests and the WeChat configure-route privacy test pass.
  Real-account image/decryption acceptance and full P11/P14 acceptance remain
  open; this does not change roadmap status.

## 2026-10-13 - QQ Local-Only Direction and Key Attempt

- Default QQ history/source controls use local database discovery/configuration,
  with no account/path/key in public status. Online roaming is disabled; the
  compatibility start route only syncs local data. Sending remains separate.
- Added bounded authenticated database/WAL snapshots and QQNT element parsing;
  local attachments feed existing UI/MCP/export cache paths. New snapshots scan
  from zero, reconciling stable message IDs rather than reusing old offsets.
- Authorized read-only key discovery checked four own-user QQNT processes.
  First text pass tested 310 candidates without a match; expanded text/UTF-16/
  SQLCipher-context pass tested 1,267 and read 344 MiB across passes, without a
  verified key. An intermediate context heuristic hit its candidate cap and was
  tightened using page authentication. No key was saved or memory dump created.
  QQ was not modified, suspended, injected or closed.
- Reader/WAL/key checks passed (14 synthetic tests). Real encrypted history,
  attachments and avatars remain unaccepted without a key. The running 8772
  backend and portable p341 have not been updated with this new reader.
- All 35 QQ-focused Hub checks, three local-source/navigation/sync UI checks,
  and package/Git privacy regressions pass. No full rebuild or hosted CI run was
  performed for this key-discovery attempt.

## 2026-10-13 - P11 WeCom avatar cache acceptance

- Added synthetic UI assertions for allowlisted WeCom avatar rendering in
  conversation and group-member rows. The endpoint regression now exercises a
  synthetic `wework.qpic.cn` URL, serving only a preseeded signature-checked
  local cache entry and rejecting a cache miss while `urlopen` is patched to
  fail. This verifies offline cache behavior only; real WeCom avatar-byte
  acquisition remains unimplemented and unverified.

## 2026-10-13 - P14 p277 package refresh

- Refreshed the sanitized source package after the P11 avatar regression.
  Archive privacy scanning and extracted empty-storage WebUI/MCP startup
  passed. Manifest `local-20261013-p277` verifies p275 portable (10,365 files,
  426,847,838 bytes) and p277 source (24,956,770 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. Hosted Actions, artifact transfer, and real QQ account acceptance
  remain open.

## 2026-10-13 - P9 QCE child recovery

- Added a bounded QCE child-process restart policy in the in-repository
  NapCat plugin: 1/2/4/8/16-second backoff, retry-budget reset after five
  stable minutes, and pause after five consecutive attempts. The Windows
  portable builder now stages this reviewed plugin runtime into the pinned
  QCE release bundle. Module syntax and assert-based backoff boundary checks
  pass. The upstream TypeScript test runner is unavailable because `tsx` is
  not installed; no dependency was downloaded. No running QQ/NapCat process
  was restarted, and packaged-runtime plus real QQ history acceptance remain
  outstanding.

## 2026-10-13 - P9 p279 packaged recovery runtime

- Built portable p279 with the reviewed QCE plugin runtime staged over the
  hash-pinned release bundle. Isolated Hub and standalone MCP startup smokes
  passed; the packaged plugin file contains the bounded recovery policy and
  has SHA-256
  `52ca5b5aa7f175181e505221b07a0967d12cfff025d72e87cdf94c3601681e76`.
  The vendored QCE server hash is
  `c6814a1b51df1293d2002b51e90c025b75cf7b90dc661f8d4269c4363f9f1cd8`.
  This does not verify real child-crash recovery or QQ account history sync;
  no live QQ/NapCat process was restarted.

## 2026-10-13 - P9 QCE recovery integration test

- Added a dependency-free Node integration test that launches a fake loopback
  QCE child, forces its first process to exit, observes the bounded-backoff
  restart restoring the API, and confirms plugin shutdown stops the child.
  The regression is included in the upstream plugin's `test` and `test:unit`
  globs. The direct Node test passed; the TypeScript suite remains unavailable
  in this checkout because `tsx` is not installed. No QQ/NapCat or source data
  was touched.

## 2026-10-13 - P14 p279/p281 manifest verification

- Manifest `local-20261013-p281` verifies portable p279 (10,365 files,
  426,881,302 bytes) and sanitized source p281 (24,959,470 bytes), with
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Source p281 includes the QCE
  recovery integration regression. Hosted Actions and artifact transfer are
  still open; the worktree remains uncommitted.

## 2026-10-13 - P14 p272/p273 verified release inputs

- Refreshed the sanitized source archive as p273 after building the p272
  portable and passing the full local gate. Extracted source smoke and privacy
  scanning passed; manifest `local-20261013-p273` verifies p272 portable
  (10,365 files, 426,869,590 bytes) and p273 source ZIP (24,954,794 bytes)
  against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with
  `local-worktree` provenance.
- The full local gate passed 568 Python tests, four source-mutation gates,
  WebUI tests, syntax checks, package privacy/manifest/smoke checks, and the
  synthetic 25,000-message performance/recovery benchmark. Hosted Actions and
  artifact upload/download remain unverified; this verification note is in the
  current worktree, not inside p273, and the worktree is uncommitted.

## 2026-10-13 - P14 p274 source refresh

- Refreshed the source ZIP as p274 so it includes the current P9/P11 fixes and
  p272/p273 verification records. Privacy scanning and extracted empty-storage
  Hub/WebUI/MCP smoke passed. Manifest `local-20261013-p274` pairs p274 with
  portable p272 (10,365 files, 426,869,590 bytes; source ZIP 24,954,912 bytes)
  and verifies package bytes against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, `local-worktree` provenance.
- This p274 verification note is recorded in the worktree, not embedded in the
  p274 archive. Hosted Actions and artifact upload/download remain unverified.

## 2026-10-13 - P14 p272 current-worktree package pair

- Rebuilt the Windows portable bundle and sanitized source ZIP from the current
  worktree after the P9 login and P11 gallery fixes. The package includes the
  vendored-source QCE runtime; Hub/MCP isolated startup, source-package startup,
  privacy scans, and deterministic manifest byte verification passed.
- Manifest `local-20261013-p272` verifies 10,365 portable files
  (426,869,590 bytes) and source ZIP p272 (24,954,642 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. The QCE executable SHA-256 is
  `9abb4e22dd79ba11018bbbc108b4e676a1a15fc31d5645a2e4206c61d2ab1ccc`.
  Hosted Actions and artifact transfer remain unverified.

## 2026-10-13 - P9 QQ QR login-state accuracy

- `/api/qq/login-status` now uses NapCat's account-authenticated `isLogin`
  field instead of `coreReady`, which only indicates that NapCat initialized.
  This prevents the WebUI polling flow from closing the QR modal and starting
  sync before the user has confirmed login. The QR reader uses that same field,
  so an initialized but unauthenticated runtime still requests its QR code.
- Synthetic HTTP regressions cover both `coreReady=true/isLogin=false` and the
  authenticated state while retaining the existing secret/QR redaction check;
  provider coverage verifies QR retrieval before authentication and suppression
  after it. Real QQ login and QR scanning remain unverified.

## 2026-10-13 - P11 QQ gallery image thumbnails

- Added indexed QQ roaming images to the existing visibility-triggered,
  three-request-bounded local media resolver. Preview URLs stay on the
  resource entry and include the QQ message/account/resource identity; offscreen
  rows still do no work.
- Synthetic gallery coverage checks lazy scheduling, identity/index forwarding,
  resource URL writeback and existing page-change cancellation. Real QQ media
  availability and refreshed-app preview remain unverified.

## 2026-10-13 - P14 p270/p271 package pair verified

- Repacked sanitized source p271 after recording the p270 current-worktree
  portable build. Source privacy scanning and extracted empty-storage startup
  passed; its manifest with p270 verifies 10,365 portable files
  (426,846,282 bytes) and source p271 (24,954,085 bytes), against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. This verification note is recorded in the worktree, not inside
  p271. Hosted Actions and artifact transfer remain unverified.

## 2026-10-13 - P14 refreshed current-worktree portable package

- Built portable p270 from the current worktree, including the latest P11/P13
  changes. QCE compiled from vendored source with the locked Cargo dependencies
  and GCC 16.2.0; packaged Hub and standalone MCP isolation smokes passed, as
  did the final package privacy scan.
- The deterministic manifest verifies 10,365 portable files (426,846,282
  bytes) and source p270 (24,953,881 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. Hosted Actions and hosted artifact transfer remain unverified.
- QCE SHA-256:
  `e0b707ed7a550f312639ed247411592ae2005eafad607d746d6ad1644b75f762`.

## 2026-10-13 - P13 WeCom staged-media cleanup outcome

- A staged attachment cleanup error after bot delivery no longer escapes the
  sender's `finally` block and overwrite a successful or ambiguous delivery
  state as retryable `failed`. Leftover staged blobs remain covered by the
  existing startup orphan cleanup.
- Added a synthetic service-level regression for both `sent` and
  `delivery_unknown` with cleanup raising `OSError`; existing credential and
  media-revalidation cleanup tests still pass. No live delivery was attempted.

## 2026-10-13 - P11 WeCom media gallery thumbnails

- Extended the existing visibility-triggered, three-request-bounded thumbnail
  resolver to WeCom image attachments. It passes the attachment's resource
  index to the same-origin resolver and stores the returned local URL on that
  attachment; it does not resolve offscreen tiles or add a media cache.
- The focused gallery test verifies the WeCom platform and attachment index,
  along with the existing WeChat preview, poster, concurrency, and page-change
  cancellation behavior. Real refreshed-WebUI preview remains unverified.

## 2026-10-13 - P14 complete local gate on current worktree

- `scripts/check-local-hub.ps1` passed from the current uncommitted worktree:
  package privacy, deterministic manifest, extracted-package smoke, 566 Python
  tests, four source-mutation gate tests, 25 WebUI tests, syntax checks, and the
  synthetic 25,000-message performance/recovery benchmark.
- Two Python `ResourceWarning` messages reported closed socket objects during
  the suite; the gate exited successfully. This does not verify hosted Windows
  Actions or a hosted artifact upload/download round-trip.

## 2026-10-13 - P11 real WeCom cached-image resolver acceptance

- Ran the updated WeCom reader against the configured local source using an
  isolated temporary Hub storage. A cached image was discovered through the
  native CacheMapping/file-table join, resolved under the account cache, and its
  image signature matched. The source reader copies databases before decrypting
  them and opens cache bytes read-only; no source-client mutation was made.
- The temporary config, copied database snapshots, and decrypted test storage
  were removed after the check. Only boolean acceptance results were retained;
  no account identifiers, paths, filenames, message content, or volume metrics
  were recorded. The running WebUI still predates this code, so visual preview
  acceptance remains open.
- A later read-only directory-presence check found `Cache/Image` and
  `Cache/File`, but no dedicated `Cache/Video`, `Cache/Voice`, or `Cache/Audio`
  directories. No filenames or cache contents were inspected; dedicated
  video/voice candidates remain synthetic-only, while the existing generic
  cache fallbacks remain available.

## 2026-10-13 - P11 native WeCom media-cache resolution

- Added an in-repository read-only join from copied `CacheMapping.mapping`
  records to decrypted `file_table4.md5` message IDs, so CacheMapping-linked
  attachments no longer require a separately generated `media_index.json`.
  Existing files are resolved only under the configured account cache; absolute
  paths and local cache locators remain provider-internal and are checked again
  before serving. No attachment is copied or uploaded.
- Integrated only the mapping logic from the user-provided extractor. Its
  hard-coded account paths and destructive export cleanup were not copied.
  The source mapping category is retained as the attachment type and selects a
  bounded `Cache/Image`, `Cache/Video`, `Cache/Voice`/`Cache/Audio`, or
  `Cache/File` candidate for bare filenames; existing candidates and cache-root
  containment checks remain. Twenty-one focused WeCom adapter/engine, HTTP
  resolver, and media-type filter tests pass, including synthetic coverage for
  all four categories. Real preview in the refreshed running Hub remains
  unverified.

## 2026-10-13 - P11 WXGF graceful fallback

- WeChat media resolution now labels local `.wxgf` originals; the WebUI presents
  them as downloadable source attachments instead of broken image previews.
  Focused resolver and render regressions pass. No conversion dependency or
  source-client mutation was added; real-account cache coverage remains open.

## 2026-10-13 - P9 QQ QR retry

- The requested fresh QR attempt through the running Hub returned the
  sanitized `QQProviderError`; login status still reports the local QQ service
  unavailable. No QR URL was produced, so the prior QR remains unusable and no
  live account/history acceptance is claimed.

## 2026-10-13 - P14 p248 package refresh

- Source p248 (597,894-byte ZIP) includes the current roadmap and
  passes the package privacy scan and isolated Hub/WebUI/three-source/read-only-
  MCP startup smoke. Portable p248 contains
  10,361 files (407,427,797 bytes) and passes bundled UI, three-source,
  isolated-storage, standalone MCP, and pinned NapCat/QCE checks.
- Manifest p248 verifies those package byte inventories with
  `local-worktree` provenance at base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` and run
  `local-p248-20261013`. The worktree has uncommitted changes; hosted CI and
  artifact upload/download remain unverified.

## 2026-10-13 - P14 complete local gate rerun

- Added `hub.test_startup` to the documented local gate so the bundled QCE
  runtime-path regression is no longer omitted. The complete gate passes 535
  Python tests, 4 source-mutation tests, 23 WebUI tests, package privacy,
  manifest and isolated startup fixtures, syntax checks, and the 25,000-message
  benchmark. Hosted CI/artifact round-trip and real-device acceptance remain
  unverified.

## 2026-10-13 - P14 refreshed sanitized source artifact

- Source package p242 (128 files, 597,037 bytes) passes privacy scanning and
  extracted Hub/WebUI/three-source/read-only-MCP startup smoke with isolated
  empty storage. Manifest p242 verifies portable p238 and source p242 byte
  inventories and `local-worktree` provenance at base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This worktree contains
  uncommitted changes; hosted Actions and upload/download round-trip acceptance
  remain unverified.

## 2026-10-13 - P9 pin bundled runtime in the local launcher

- `start_hub.py` now passes the repository-managed QCE bundle path to its Hub
  child, preventing an inherited stale `QCE_BUNDLE_DIR` from disabling the
  login API. The focused launcher test passes, and an isolated Hub reports a
  waiting QR when QCE history is offline. The existing port-8772 process still
  reports unavailable and was not restarted.

## 2026-10-13 - P14 local package refresh

- Rebuilt Windows portable p238 and sanitized source p239 after the QQ stale-QR
  presentation and provider-status changes. The portable executable passed
  bundled UI, three-source, isolated-storage and read-only MCP checks; the
  extracted source passed privacy and startup checks. Manifest
  `local-p239-20261013` verifies both package inventories and byte hashes with
  explicit `local-worktree` provenance. Hosted Actions and hosted artifact
  upload/download remain unverified.

## 2026-10-13 - P9 QQ status fallback regression

- Added isolated provider regressions for NapCat QR login status when QCE is
  offline, the fully unavailable fallback, and exclusion of QR/token data from
  the status response. Both focused tests pass without contacting QQ or reading
  chat history.

## 2026-10-12 - P9 stale QQ QR presentation fix

- The QQ login modal now replaces any prior QR with a service-unavailable state
  when its status API is unavailable, and offers a retry. The focused WebUI
  regression and JavaScript syntax check pass; it does not restart QQ/NapCat or
  change the backend's current runtime mismatch.

## 2026-10-12 - P9 QQ login/runtime diagnosis

- The bundled NapCat loopback login API is reachable and reports
  `waiting_qrcode`; the Hub on port 8772 reports the login service unavailable.
  NapCat generated a fresh local QR, but the QCE history service ports 40653 and
  40654 are not listening, so QQ history sync is not ready. The Hub runtime
  context differs from a direct provider check; no QQ/NapCat process was
  restarted and no chat database was read. Account login and history acceptance
  remain pending.

## 2026-10-12 - P14 local package manifest refresh p236

- Reverified p236's portable directory and sanitized source ZIP against its
  `local-p236-20261012` / `local-worktree` manifest. The manifest matches HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, package inventories, sizes and
  SHA-256 values; the portable bundle contains 10,359 files (407,418,881 bytes)
  and the source ZIP is 595,264 bytes. Hosted Actions and hosted
  upload/download remain unverified.

## 2026-10-12 - P12 scheduled-export local-path boundary

- Automatic export rejects UNC shares and checks Windows drive type to reject
  mapped network drives, keeping chat archives on local disks. The seven
  focused auto-export tests and `git diff --check` pass; only UNC rejection is
  exercised by a synthetic path on this host, not a live Windows mapped drive.
- The complete local gate also passes: 532 Python tests, 4 source-mutation gate
  tests, 23 WebUI tests, package privacy/manifest/startup fixtures, syntax
  checks, and the 25,000-message performance/backup benchmark. No real export
  destination or account data was accessed.

## 2026-10-12 - P14 paired local package refresh p235

- Rebuilt the Windows portable package and sanitized 127-file source archive
  after the automatic-export local-path change. The portable executable served
  the UI and Lucide, reported all three source entries, used isolated storage,
  and completed standalone read-only MCP stdio initialization; the source
  archive passed privacy and empty-storage startup checks.
- Manifest p235 verifies package inventories, SHA-256 values, and local-worktree
  provenance. No upload, push, hosted CI, or artifact download round trip ran.

## 2026-10-12 - P14 full local gate and P11 report accessibility

- `scripts/check-local-hub.ps1` passed 531 Python tests, 4 source-mutation gate
  tests, 23 WebUI tests, package privacy/manifest/startup fixtures, syntax
  checks, and the 25,000-message performance/backup benchmark with zero WAL
  failures. Portable p231, sanitized source p232, and manifest p232 separately
  passed their package smoke/privacy/provenance checks. This supersedes the
  earlier 509-test worktree check; no hosted workflow or artifact round trip
  was run.
- Annual and pair-report hourly charts now expose all 24 hour/count values to
  assistive technology; the focused WebUI regression and JS syntax check pass.

## 2026-10-12 - Rechecked hosted CI failure locally

- The read-only log for public run 38101275283 showed two missing WeChat test
  imports (`cryptography`) and one settings assertion that assumed the local
  Chinese checkout path. The dirty worktree now pins `cryptography` in CI and
  uses a path-independent assertion; the exact affected test plus WeChat
  cursor/media suites passed (65 tests). This does not update the public run or
  verify the later package/artifact steps; no push or workflow rerun occurred.

## 2026-10-12 - P9 QCE message-cache byte budget

- QCE message cache now estimates nested JSON memory, skips entries above
  128 MiB, and evicts the oldest cached entries to keep the combined estimate
  within 128 MiB. This is not a hard process-memory limit: allocator overhead
  and concurrent cache-hit clones are not included. Returned history remains
  complete; only cache retention is reduced.
- Added Rust unit coverage for nested-size estimates and oldest-entry eviction.
  The Cargo/rustfmt shims are broken, but the installed stable toolchain can be
  invoked directly; all four focused Rust cache tests pass offline. No QCE
  restart, live sync, or account-data read was performed; the live process
  memory cause remains unproven.

## 2026-10-12 - QQ login status invalid-config handling

- `/api/qq/login-status` now folds invalid local QCE URL configuration into the
  existing unavailable response instead of leaking a generic HTTP 500. A
  focused regression covers the route contract; no QQ runtime was started.

## 2026-10-12 - P11 QQ forwarded WMA attachments

- QQ merged-forward attachments now recognize WMA only when the bounded voice
  signature matches the ASF Header Object GUID. Cached files retain the
  `audio/x-ms-wma` MIME and flow through the existing explicit download and MCP
  attachment paths; malformed ASF-like bytes remain rejected.
- The QQ provider suite (52 tests), scoped Hub/MCP forward-attachment tests (2),
  and `webui/test-qq-forward-attachments.cjs` pass. This is synthetic format
  coverage only; real-account cache availability and browser WMA codec support
  remain unverified. No source database or live chat content was read.

## 2026-10-12 - P14 sanitized source package p218

- Built the 127-file source package from the current worktree. Archive staging
  and extracted-package privacy scans passed; isolated startup served the
  WebUI and three source entries with empty storage and read-only MCP.
- This refresh does not include a matching portable bundle or provenance
  manifest and does not verify hosted Actions or artifact download. P14 remains
  in progress; no upload or push was performed.

## 2026-10-12 - Bound QQ provider prefetch

- The QQ adapter now fetches a 500-message Hub page as five 100-message QCE
  requests and sets QCE's underlying `batchSize` to 100. This lowers the
  estimated per-cache-miss prefetch window from about 5,500 to 1,100 messages;
  it does not prove the live QCE process memory is caused only by Hub requests.
- Synthetic provider, QQ sync-resume, and bounded Web-search regressions pass.
  No live sync was started, no source data was read for this check, and no
  running process was restarted.

## 2026-10-12 - P13 multi-chunk media protocol regression

- Added a synthetic upload crossing the 512 KiB chunk boundary. It verifies
  ordered chunk indexes, byte-perfect reassembly, and a single final send with
  the returned `media_id`; no network, credentials, or real message delivery
  was used. `integrations.wecom.test_bot_media` passes.
- P13 live bot delivery, target reachability, and official acknowledgement
  acceptance remain open.

## 2026-10-12 - Bound Web search result bodies

- The Web search route now asks SQLite for a 1,200-character text preview per
  hit. Search matching still uses full stored text; MCP and export queries
  retain complete bodies. With the UI page size of 60, returned text is capped
  at 72,000 characters per page, independent of source message size.
- A synthetic 2 MiB message regression verifies the preview bound and confirms
  the default full-message store search remains unchanged. Focused store/server
  tests passed; no real chat data or full performance gate was used.

## 2026-10-12 - P14 source-only refresh p216

- Built a 127-file sanitized source archive from the current worktree. The
  package privacy scan passed; the extracted package served the WebUI and all
  three source entries using isolated empty storage, and its MCP endpoint
  reported read-only mode.
- This does not refresh the paired portable bundle or provenance manifest:
  portable p213 and manifest p215 predate current roadmap edits. A portable
  rebuild is deferred until the current machine has adequate free memory.
  P14 remains in progress.

## 2026-10-12 - P11 MCP roster and P13 approval isolation

- MCP now exposes the existing local group-member reader as `list_group_members`,
  with exact platform/account/conversation scope, bounded paging, and the chat
  capability gate. The tool does not trigger sync; unavailable readers fail
  closed. `hub.test_mcp` passes 34 tests and focused MCP/approval configuration
  regressions pass 3 tests.
- WeCom bot credential changes now revoke only bot-platform approval tokens;
  disabling sending still revokes every pending challenge. Focused approval
  regressions pass. No message was sent and no real credential was changed.
- Real-account sync counters, login state, local ports, and process-memory
  snapshots are intentionally excluded from public development notes. Live
  device acceptance remains open; no chat bodies were displayed or transmitted.
- `scripts/check-local-hub.ps1` passed: 509 Python tests, 4 source-mutation
  gate tests, 23 WebUI tests, package privacy/manifest/smoke fixtures, syntax
  checks, and the 25,000-message benchmark (0.030 s first search, 1.450 s for
  50 cursor pages, 0.087 s backup, 0.237 s restore, zero WAL failures). Free
  physical memory was 0.75 GiB at the post-gate check, so the current-worktree
  package rebuild was deferred rather than risk exhausting the host.
- A content-free no-match query on the live 8772 index returned zero rows in
  103 ms cold and 5/2 ms warm (three runs, `limit=1`); this is a narrow lookup
  probe, not representative result rendering or full-search acceptance.
- Hosted run 38101275283 remains failed before package steps: CI lacked
  `cryptography` and one test assumed the local Chinese checkout path. Both
  corrections are in the dirty worktree, but still require a reviewed commit
  and explicitly authorized push before hosted CI/artifact verification.

## 2026-10-12 - P11 QQ cached WMA attachment support

- MCP QQ cache-resource reads now recognize WMA audio only when the opaque
  gallery handle resolves for the active account to an audio `.wma` item and
  the cached bytes carry the ASF signature. Caller-supplied type metadata
  cannot reclassify another resource; invalid type, extension, or content is
  rejected.
- Focused MCP/provider regressions pass for the audio response, forged type,
  wrong extension, and account-bound gallery metadata. This adds one locally
  resolvable QQ audio format; it does not close P11 resource/decryption parity.
- The local media route also permits the validated WMA MIME inline, and the
  WebUI preview keeps a direct download fallback in case the browser lacks a
  WMA codec. Focused HTTP and WebUI regressions cover both paths; playback on
  an actual browser/device remains unverified.

## 2026-10-12 - P12 source-mutation gate audit

- Audited message edit/insert/delete and Moments trigger/delete paths across
  Electron IPC, HTTP routes, renderer controls, and provider call sites. They
  remain fail-closed; the four focused synthetic gate tests pass and confirm
  the provider operations are not called. No real database was opened or
  changed. The independent Hub backup-restore flow was not in scope.
- Anti-recall and Moments interception remain unimplemented and unavailable;
  they still require a separately approved, inspectable native implementation
  and isolated install/check/uninstall/rollback validation.
- The latest paired package remains p213/p215; p218 is source-only. A current
  portable rebuild is deferred during QQ runtime/login and memory pressure.
  No service restart or artifact upload was performed.

## 2026-10-12 - P11 offline media resolution follow-up

- QQ MCP attachment reads now accept cached video, voice, and file resources
  through the existing account-bound local cache resolver; no QCE fetch or
  network request was added. WeChat MCP can return a signature-checked cached
  WXGF container with a dedicated MIME type when local image conversion is
  unavailable. It is the original container, not a decoded preview.
- WeCom Chinese labels for image, voice, video, and file now normalize to the
  canonical media types used by bubbles, gallery filters, and preview routing.
- Focused Python regressions passed (57 across QQ provider, WeChat MCP/image,
  and WeCom store filters); `webui/test-wecom-resource-render.cjs` and
  `git diff --check` passed. Real-account cache availability remains unverified.

## 2026-10-12 - P14 source-only package p220

- Built the sanitized 127-file source archive from the current worktree. The
  package privacy scan passed; the extracted package served the WebUI and all
  three source entries with isolated empty storage, and its read-only MCP
  endpoint responded.
- Portable packaging remains deferred while qce-server is live under memory
  pressure. This source archive has no matching portable bundle or provenance
  manifest, so it is not a complete P14 release pair. No upload or push was
  performed.

## 2026-10-12 - P14 artifact workflow contract regression

- The package-manifest fixture now asserts that the hosted workflow uploads
  the expected package and manifest paths, downloads the same named artifact,
  and verifies the downloaded bytes/provenance in that order. The local
  manifest fixture passes; this does not substitute for a hosted Actions run.

## 2026-10-12 - P8 MCP statistics capability enforcement

- Closed the `stats` capability bypass in MCP: `list_sources` is hidden and
  denied on direct calls when disabled; `lmh://sources` is no longer advertised
  and its direct read fails. Chat conversation and message resources remain
  available independently.
- Focused MCP regression passes across tools/list, tools/call, resources/list,
  resources/read, and an enabled chat query. No chat body was read from a live
  source.

## 2026-10-12 - Source-message write gate closure

- Routed inherited WeChat message edit, insert, and delete IPC through the
  source-mutation deny gate. Main-process handlers return the existing explicit
  unavailable result without calling `chatService`; read-only chat queries are
  unchanged. This intentionally keeps source-client databases read-only until a
  separately reviewed and approved backup/rollback flow exists.
- The focused mutation gate suite passes all four checks, including the three
  message-write channels and the existing anti-recall/Moments boundaries. No
  source database was touched. P12 native trigger work remains NOT_STARTED.

## 2026-10-12 - Local sync and roadmap acceptance continuation

- Real-account sync counters, per-platform connection state, local ports, and
  process identifiers are intentionally excluded from public development notes.
  No chat bodies were opened or transmitted.
- Re-ran the focused QQ pagination, P8 capability-denial, and WeChat quote
  identity regressions: 53 tests passed. The P13 bot approval/attachment cleanup
  and send-capability revocation regressions also passed (3 tests); no real
  delivery or credential use occurred.
- Closed a Moments source-mutation bypass: post deletion now fails closed through
  Electron IPC and HTTP, and the UI control is disabled. Three mutation-gate
  tests and renderer/Electron typechecks passed. Refreshed p203 portable and
  p205 sanitized source packages passed isolated startup, read-only MCP, privacy,
  and manifest hash/provenance checks. No source DB was modified.
- The p205 local-worktree manifest was verified against both package byte sets
  and provenance. Hosted artifact upload and download remain unverified.
- P11 now resolves only WeChat image tiles within 240px of the viewport, using
  the existing cache-only `mid` image path, a three-request concurrency cap,
  and abort/disconnect on page changes. QQ, WeCom, stickers, video, and original
  image retrieval are excluded from automatic previews. Full resource/decryption
  parity and visible real-cache acceptance remain open.
- The latest `scripts/check-local-hub.ps1` gate passed 499 Python tests, 3
  source-mutation gate tests, 22 WebUI tests, package privacy/manifest/startup
  fixtures, syntax/compile checks, and the 25,000-message benchmark (0.038 s
  first search, 1.391 s for 50 cursor pages, 0.074 s backup, 0.229 s restore,
  zero WAL failures). Portable p206 and sanitized source p208 (126 files) passed
  isolated startup/read-only MCP checks and privacy scanning; manifest p208
  matches both package byte sets and local-worktree provenance. Hosted Actions
  and artifact upload/download remain unverified.
- WeCom first sync still requires a local database directory, 32-hex database
  key, and account-slot identifier; none were guessed or exposed.

## 2026-10-12 - P8 quoted-message capability enforcement

- Added `/api/quoted-message` to the server-side `chat` capability route map;
  previously this direct HTTP route bypassed the disabled-module gate.
- A focused HTTP regression verifies 403 plus stable capability error fields
  when chat is disabled. Existing exact-identity quote-context test also passes.

## 2026-10-12 - P8/P9 gates, p199/p202 package, and live QQ sync

- P8 re-audit found `/api/quoted-message` missing from the server-side `chat`
  capability map. The route is now gated, with a regression proving a disabled
  module returns 403; the route gate and canonical quote-context tests pass.
- QQ history pagination now stops when QCE returns the identical complete
  `msgId`/`msgSeq` page twice consecutively with `hasNext=true`. A synthetic
  regression verifies no duplicate rows are appended and no third request is
  made; the existing short-page continuation contract is unchanged.
- Provider, P8 route, and quote-context focused tests passed (50 QQ provider
  tests and 2 HTTP regressions). The portable p199 and sanitized source p202
  passed isolated startup/UI/three-source/read-only MCP checks, package privacy,
  and manifest byte/provenance verification; this is local-only evidence.
- Historical real-account sync counters, connection states, and local process
  details are intentionally excluded from public development notes. Live
  account, cache, and media acceptance remains open; no message bodies were
  inspected.
- No message bodies were inspected, no messages were sent, and no source-client
  database was modified. QQ retry completion and P9 delivery acceptance remain
  open.

## 2026-10-12 - P11 quoted-media preview, P14 p195/p198 refresh

- WeChat quoted image/video/voice previews are restricted to an exact indexed
  source-message match in the same account and conversation. They reuse the
  local cache resolver, return a same-origin media token, and do not trigger
  source sync/download. The UI escapes quote text and rejects non-local media
  URLs; cache misses and unsupported types remain placeholders.
- The integrated local gate passed 497 Python tests, 2 source-mutation gate
  tests, 21 WebUI tests, and a 25,000-message benchmark (0.029 s first search,
  1.862 s for 50 cursor pages, zero WAL failures). One regression found during
  the gate was fixed: the new quote local had shadowed `urllib.parse.quote`,
  causing Moments export filename encoding to raise `UnboundLocalError`; the
  existing export and quote regressions now pass.
- Portable p195 passed isolated EXE/UI/three-source/MCP and standalone MCP
  stdio checks. Sanitized source p196 (125 files) passed archive privacy and
  extracted startup checks; portable privacy and manifest byte/provenance
  checks passed for `local-20261012-p198` (`local-worktree`, base
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). This is not hosted CI evidence.
- Real-account sync progress and indexed-message counts are intentionally
  excluded from public development notes. Full-history acceptance remains
  unverified. No message body was opened/transmitted, no message was sent, and
  no source-client database was modified.
- Hosted Actions/artifact round-trip, real-account/cache parity, remaining full
  WeFlow resource parity, and separately gated native mutation and real sending
  remain open.

## 2026-10-12 - P11 WeChat quoted-media previews

- WeChat quote cards now include inline image, video, or voice previews when the
  quoted source ID uniquely resolves to an indexed message in the same platform,
  account, and conversation. The WebUI only receives a same-origin Hub media
  token; the existing local resolver is reused and no source sync/download is
  requested. Cache misses, ambiguous references, disabled media, and other
  quoted types remain placeholders/text.
- Focused HTTP and WebUI regressions cover exact identity, account isolation,
  cache miss, escaping, and source URL/path redaction.
- Real WeChat cache/device acceptance and full WeFlow resource parity remain
  open.

## 2026-10-12 - Local connection and integrated gate refresh

- The local WeChat sync and its real-account counters are omitted from this
  public log. No message body was opened or transmitted by the agent; the
  source database remains read-only.
- MCP `list_media` now lists WeCom and QQ attachments attached to text-parent
  messages, preserving safe resource indices and parent message URIs while
  redacting paths and URLs. The send toolbar now labels the selected WeCom bot
  identity distinctly from QQ.
- The integrated gate passed 492 Python tests, 2 source-mutation tests, 20
  WebUI tests, package privacy/startup/MCP checks, and the 25,000-message
  benchmark (0.029 s first search, 1.416 s for 50 cursor pages, zero WAL
  failures). Portable `p193`, sanitized source `p193` (124 files), and
  manifest `p193` passed local privacy, extracted startup/read-only MCP smoke,
  and byte/provenance verification. Hosted artifact upload/download remains
  unverified.

## 2026-10-12 - MCP attachment discovery

- `list_media` now emits individual WeCom and QQ roaming attachment entries,
  including attachments on text messages. Each entry includes its safe
  resource index and parent-message URI for an explicit `get_attachment` call.
- The MCP listing remains metadata-only, strips attachment paths and URLs, and
  leaves Moments and QQ cache gallery resources on their dedicated tools.
- Synthetic MCP tests cover attachment pagination/type filtering, ordinary
  WeChat media compatibility, URI identity, redaction, and indexed reads.

## 2026-10-12 - MCP media date filtering

- `list_media` now accepts inclusive UTC+08 start/end calendar dates, matching
  the local media library. It still returns bounded metadata only.
- Synthetic boundary, one-sided-range, invalid-date, and account-scope tests
  passed: `python -m unittest hub.test_mcp -q` (30 tests).

## 2026-10-12 - P11 QQ streaming cache and hourly report chart

- QQ gallery and message-media downloads now stream in 1 MiB reads to a
  same-directory temporary file, hash while writing, enforce the 50 MiB limit,
  verify gallery-declared size, and atomically publish only complete cache
  entries. Existing loopback path validation, bearer auth, and redirect
  rejection remain in place.
- Annual reports now render all 24 UTC+08 hourly buckets, including empty
  hours, with an accessible chart label. The focused QQ/WeChat provider and
  media suite passed 104 tests; `node webui/test-pair-report-export.cjs`, JS
  syntax, Python compilation, and `git diff --check` passed.
- These checks use synthetic fixtures. NapCat/QQ and real WeChat account/cache
  acceptance remain open.

## 2026-10-12 - P14 integrated gate and portable p163

- `scripts/check-local-hub.ps1` passed on the current tree: 478 Python tests,
  two source-mutation gate tests, 20 WebUI tests, package privacy/manifest,
  extracted startup and read-only MCP checks, syntax/compile checks, and
  `git diff --check`. The 25,000-message benchmark reported 0.064 s first
  search, 3.255 s for 50 cursor pages, and zero WAL failures. Logged
  `RuntimeError` lines are intentional failure injection in passing tests.
- Portable p163 passed bundled-executable UI/source/isolated-storage checks
  and standalone MCP initialize/tools/list/exit over stdio with clean JSON-RPC
  stdout. Sanitized source p164 passed its local privacy and extracted-startup
  checks. Manifest p165 binds the final packages
  to commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` and is verified locally.
- These are local checks only. Hosted Actions and GitHub artifact
  upload/download remain unverified; no live messaging account or source-client
  database was accessed, and no message was sent.

## 2026-10-12 - P14 WeCom media package privacy audit

- Source and portable package staging now require all three WeCom bot modules.
  Both package filters and the shared audit exclude the DPAPI credential file,
  media outbox directory, and `.blob` payloads; the source archive audit also
  rejects private outbox entries.
- `scripts/test-package-privacy.ps1` passed with synthetic credential, blob,
  and outbox fixtures. The package itself was not rebuilt in this check, so
  prior package manifests do not cover this source change; hosted artifact
  upload/download remains unverified.

## 2026-10-12 - P11 QQ cache gallery size labels

- QQ global-cache media tiles now show the bounded API-provided file size; absent
  or zero sizes stay hidden. The UI does not display MIME, remote URLs, or paths.
- `node webui/test-wecom-resource-render.cjs` passes with a synthetic 32 KiB
  gallery item.

## 2026-10-12 - P11 WXGF/resource cancellation and P14 local gate refresh

- WXGF conversion now retries bounded WeFlow-style HEVC VPS-group candidates;
  WXGF containers also prefer embedded JPEG/PNG signatures within the same
  bounded prefix window before HEVC conversion. Synthetic coverage verifies
  candidate fallback, both image signatures, and unchanged cache behavior.
- Resource ZIP compression streams each staged attachment in 1 MiB chunks and
  checks cancellation between chunks. A regression test cancels during ZIP
  compression and verifies job workdir cleanup.
- `scripts/check-local-hub.ps1` passed on the current tree: 451 Python tests,
  two source-mutation gate tests, 19 WebUI tests, package privacy/manifest and
  extracted startup/MCP checks, and syntax/compile checks. The 25,000-message
  benchmark reported 0.066 s first search, 2.586 s for 50 cursor pages, and
  zero WAL failures. The qq/Moments RuntimeError lines are intentional failure
  injection in passing tests.
- Rebuilt portable v159 and the 117-file source v160 locally after these
  changes. Isolated Hub/UI/three-source and standalone MCP stdio smokes passed;
  the source archive privacy scan and extracted startup smoke passed. Manifest
  v161 verified exact package bytes with `local-worktree` provenance at
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and GitHub
  artifact upload/download remain unverified. No QQ/WeChat account or source
  database was accessed; no real message was sent and no source-client
  mutation ran.

## 2026-10-12 - Fail closed on source-client mutation entrypoints

- Routed anti-recall and Moments trigger install/uninstall IPC through a
  default-deny main-process gate. Moments HTTP install/uninstall routes now
  return 403 in the existing JSON error shape. Read-only status checks remain.
- The synthetic gate test exercises four IPC handlers and both HTTP routes and
  confirms zero provider/native calls. `node --experimental-strip-types --test
  scripts/test-source-mutation-gate.cjs` passed 2 tests; `git diff --check`
  passed. No real database or native provider was used.
- Full Electron/WCDB typecheck was not run because this local workspace lacks
  `tsc` and `esbuild`; hosted validation remains pending.

## 2026-10-12 - QQ offline status and bounded WeChat voice export

- `/api/qq/login-status` now returns HTTP 200 with `available: false` when the
  local NapCat login API is unavailable, rather than a generic HTTP 500. The
  normal response exposes only login phase, online state, and availability.
- WeChat cached SILK voice export streams the SQLite BLOB in 1 MiB chunks to a
  same-directory temporary file, then fsyncs and atomically replaces the
  destination. It no longer loads an additional full voice BLOB in memory.
- Verification: `python -m unittest hub.test_server -q` passed 137 tests;
  `python -m unittest integrations.wechat.test_media -q` passed 42 tests;
  `git diff --check` passed. No real account or source database was queried.
- P14 portable/source/manifest local copy round-trip verification passed.
  Hosted Actions and GitHub artifact download remain unverified.

## 2026-10-12 - QQ readiness diagnostics and send settings gate

- NapCat/QCE readiness timeout now retains the last allowlisted error reason;
  unknown error text is reduced to its exception type so QR payloads, tokens and
  URLs are not returned to the UI. Synthetic tests cover repeated readiness
  failures and sensitive URL redaction.
- Settings only requests the pending QQ send queue when `qq_send` is both
  available and enabled. Disabled capability clears stale queue state without
  blocking backups, MCP or operation history; a focused UI regression test
  passes.
- QQ provider, WebUI syntax and targeted UI checks pass. Real NapCat/QQ startup
  and device acceptance remain unverified; no messages were sent.

## 2026-10-12 - Local Windows package v140

- Rebuilt the portable Windows package and sanitized source bundle from the
  current local worktree. Both isolated Hub startup smokes passed; portable MCP
  initialize/tools/list/exit stdio smoke passed, and package privacy scanning
  found no current-user or local-data paths.
- Local manifest v140 binds the portable directory and source ZIP to base commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree` provenance;
  manifest round-trip and deterministic manifest fixture checks pass. This is
  not hosted GitHub Actions or upload/download acceptance; P14 remains open.

## 2026-10-12 - P11 WeChat offline location cards

- WeChat `local_type=48` payloads now parse bounded `<location>` XML into
  normalized place name/address and finite in-range coordinates stored only in
  the Hub index. The WebUI displays escaped details without map links, remote
  tiles, or network access; malformed/empty payloads retain a generic card.
- Focused parser and WebUI rendering checks pass for XML bounds/entity
  rejection, invalid coordinates, HTML escaping, and absence of external links.
- Real-account schema/display acceptance remains open; no source database was
  modified or live account accessed. P11 remains IN_PROGRESS.

## 2026-10-12 - P11/P12 follow-up and local v128 refresh

- QQ/WeCom attachment metadata now appears across CSV, HTML, XLSX, JSON, SQL,
  ChatLab JSON/JSONL, Markdown and TXT exports via an allowlist that excludes
  local paths and temporary URLs. SQL resources use a separate table so old
  message-table schemas remain executable.
- WebP/BMP image completeness checks honor declared container lengths; Moments
  reads supported `WITHOUT ROWID` schemas without relying on implicit row IDs.
- Full local gate: 418 Python tests, 16 WebUI tests, package privacy/manifest
  fixtures, syntax checks and the 25,000-message benchmark all passed.
- Portable and sanitized source v128 passed isolated startup/MCP and privacy
  checks. Manifest hashes verified for `local-20261012-p14-v128` with
  `local-worktree` context and base commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
  Source ZIP SHA-256: `3089005E76FE04544FCEE9997544D2A706CB675ABD5E20C5CD2A635CBB87738C`.
- No live-account data was used. Real-device/cache acceptance and hosted Actions
  artifact upload/download remain open.

## 2026-10-12 - P14 local v127 package refresh

- Rebuilt portable v127 with the WXGF output-file limit; Hub/WebUI, three
  source entries, read-only MCP, and standalone MCP protocol smoke passed.
- Sanitized source v127 passed privacy and extracted-service checks. Its local
  manifest passed provenance and byte verification for
  `local-20261012-p14-v127` (`local-worktree`, base commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`).
- Source ZIP SHA-256:
  `02EB26FA0E137122326BEA972F2DA6BA779C5820818CBEDB3FDF1E056DB4DB82`.
  Hosted Actions and uploaded-artifact round-trip remain unverified.

## 2026-10-12 - WXGF subprocess output bound and P13 API audit

- WXGF conversion now constrains FFmpeg output with `-fs` and retains the
  existing bounded readback/rejection path; the WeChat media suite passes.
- The full local gate passed 415 Python tests, all 16 WebUI tests, package
  privacy/manifest fixtures, syntax checks, benchmark and diff checks.
- P13 remains disabled: official app/customer-service/public-account send
  routes do not authorize sending as the signed-in personal WeChat/WeCom
  desktop account to arbitrary existing chats. No account API was called and no
  message was sent.
- Real QQ/NapCat and WeChat/WeCom cache/device acceptance plus GitHub-hosted
  Actions/artifact round-trip are still not verified.

## 2026-10-12 - P14 local v126 release candidate

- Rebuilt portable v126 after the WeCom media-context and private analytics
  validation fixes. The packaged WebUI/source list, read-only MCP endpoint,
  and standalone MCP initialize/tools/list/exit smoke passed.
- Sanitized source v126 passed the archive privacy scan and extracted startup
  smoke. The local byte/provenance manifest verified for
  `local-20261012-p14-v126` against worktree commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- Source ZIP SHA-256:
  `77CDEDF27C403E8B81E9ED5BE80C1724B9E9F4C802B9267ACE33C30D0B00F0B7`.
  This local build does not represent GitHub-hosted CI or artifact upload.

## 2026-10-12 - WeCom media and private analytics validation

- WeCom media-context parsing now ignores malformed index rows and safely skips
  non-string MD5 values rather than aborting the whole local snapshot.
- Relationship achievements now fail closed for unknown conversation kinds;
  only explicitly supported private conversations are aggregated.
- The combined focused suite passed 161 tests (`integrations.wecom.test_adapter`,
  `hub.test_analytics`, `hub.test_server`). No account databases, caches, or
  external platform APIs were accessed.

## 2026-10-12 - P14 local v125 package refresh

- Rebuilt portable v125 after removing the unverified bundled FFmpeg binary;
  executable Hub/UI and standalone MCP initialization/tool-list smokes passed.
- Sanitized source v125 passed current-user/local-data privacy checks and its
  extracted Hub/API smoke. Local manifest provenance and package-byte checks
  passed for `local-20261012-p14-v125` (`local-worktree`, commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Source ZIP SHA-256:
  `034F831EF54E9EB2BA7B975E77909B549386042813F9415FDEB67A87142060AC`.
- This is local evidence only; GitHub-hosted CI/artifact round-trip and live
  platform-device acceptance remain outstanding.

## 2026-10-12 - P8 live MCP capability revocation

- Closed a dynamic revocation gap for MCP resources and prompts: each list/get
  endpoint refreshes current settings, message resource templates disappear
  when chat access is revoked, and all four endpoints reject requests when MCP
  is disabled.
- `python -m unittest hub.test_mcp -q` passed (23 tests); module compilation
  and `git diff --check` passed. HTTP route and background task capability
  checks were separately audited with no additional bypass found.
- No account/client was started and no private runtime data was read.

## 2026-10-12 - P14 complete local gate and package round-trip

- The local gate passed 406 Python tests, 16 WebUI tests, py_compile, the
  25,000-message benchmark, package privacy/manifest fixtures, and diff checks.
- Portable and sanitized source packages passed isolated Hub/WebUI/three-source
  and read-only MCP checks; standalone MCP stdio initialize/tools/list/exit also
  passed. Copying both artifacts to a separate directory and verifying manifest
  provenance and file hashes passed.
- Local outputs: `output/windows/LocalMessageHub-portable-p14-smoke`,
  `output/windows/LocalMessageHub-source-p14-local.zip`, and
  `output/windows/LocalMessageHub-artifact-manifest-p14-local.json`. This does
  not verify GitHub-hosted Actions or its artifact upload/download round-trip.

## 2026-10-12 - Cached emoji and roster export integrity

- Resolved WeChat cached type-47 emoji remain inline in the chat bubble after
  local resolution and can use the existing image zoom action. Unresolved
  stickers retain the local-cache action; unsafe remote URLs stay rejected.
- Group roster UI now binds responses and exports to the exact platform/account/
  conversation identity, discarding a late response after selection changes.
- The complete gate passed 406 Python and 16 WebUI tests; benchmark first search
  was 0.068 s, 50 pages 2.586 s, backup 0.078 s, restore 0.216 s, and zero WAL
  failures. Negative-path source/Moments errors are expected fixtures.
- Both regressions are included in `check-local-hub.ps1`; no live accounts or
  source-client changes were involved.
- Portable v120 and sanitized source v120 passed their local startup/API/MCP
  smoke checks. The package privacy scan and byte/provenance manifest also
  passed; these are local-worktree artifacts, not hosted CI outputs.

## 2026-10-12 - Avatar cache coverage audit

- Reconciled avatar docs with the current WeChat provider: sync reads only an
  exact-schema, keyed `head_image.db`, joins by username, accepts bounded JPEG
  bytes, and writes to Hub-owned cache.
- QQ QCE/NapCat and WeCom expose avatar URL metadata but no verified local
  image-byte reader/cache layout. Their uncached images remain initials; no URL
  fetch or guessed local path was added.
- No user account files, source client, or network avatar endpoint was touched.
- Portable v121 and sanitized source v121 were rebuilt after this documentation
  correction. Their isolated startup/MCP smokes and local byte/provenance
  manifest passed; hosted CI and upload/download remain unverified.

## 2026-10-12 - QQ mentions and annual report contacts

- QQ roaming normalization now preserves QCE member mentions and `@all` as
  bounded rich-content elements while retaining the original text for search;
  numeric type checks reject booleans masquerading as integer mention codes.
- Annual reports now include the top 10 inbound contacts, scoped to the selected
  platform/account/year. WebUI and CSV show sanitized names/counts only; account
  IDs, contact IDs, and message bodies are not returned in this breakdown.
- The complete local gate passed 406 Python tests and 14 WebUI tests, package
  privacy/manifest fixtures, syntax checks, and the 25,000-message benchmark.
  Synthetic QQ/Moments failure logs are expected negative-path fixtures.
- Rebuilt portable/source v119 before the later P11 UI fixes; isolated EXE/UI/MCP and extracted-source smokes,
  package privacy scans, and manifest byte/provenance verification passed with
  `build_context=local-worktree`. These are not hosted CI artifacts.
- P9 live NapCat, P11 full WeFlow resource/decryption parity, P12 real-account
  rendering/cache acceptance, P13 authorized sender availability, and hosted
  P14 Actions/artifact round-trip remain open.

## 2026-10-12 - P9 markdown history and P11 bounded media lookup

- QQ normalization now maps QCE `markdownElement` content into searchable text
  and rich-content output, capped at 4,096 characters and without forwarding
  private raw payload fields. The focused regression and all 31 QQ provider
  tests passed.
- WeChat cached video/file resolution now uses the message timestamp's exact
  `YYYY-MM` cache directory instead of recursively walking every month. Missing
  or invalid timestamps fail closed; wrong-month duplicate and no-walk coverage
  passed. All 31 WeChat media tests passed.
- The unified local gate passed 402 Python tests and 14 WebUI tests. Synthetic
  failures printed by negative-path fixtures were expected; command exit was 0.
- Rebuilt portable/source v116; EXE/UI/standalone-MCP and extracted-source
  smokes, package privacy scan, and local-worktree manifest verification passed.
  These packages remain local, not hosted release artifacts.
- P9 live NapCat acceptance, broader P11 parity, P12 real-account/browser
  acceptance, and hosted P14 remain open; P13 remains unavailable without a
  verified authorized WeChat/WeCom delivery route.

## 2026-10-12 - MCP cached contact avatar tool

- Added the read-only `get_contact_avatar` tool. It requires exact platform,
  account, and indexed contact identity; it returns only an existing Hub-cached,
  signature-checked image as MCP image content. It never fetches remote URLs,
  and is hidden when the MCP instance has no avatar reader.
- Synthetic MCP coverage verifies image bytes/MIME, account isolation, cache
  miss, missing contact, capability denial, and metadata without paths or raw
  avatar URLs. The full local gate passed 400 Python and 14 WebUI tests.
- Refreshed portable/source v115; isolated executable/WebUI/MCP smokes,
  source privacy scan, and manifest byte/provenance verification passed. These
  are local-worktree results; hosted Actions and artifact upload remain open.

## 2026-10-12 - P13 sender feasibility audit

- Rechecked the unified send validator and the pinned local WeChat/WeCom
  integrations. The validator accepts QQ only; the WeChat provider declares
  itself read-only, while the WeCom adapter parses local snapshots and has no
  send operation or delivery transport.
- No fake sender was added: a synthetic endpoint would not establish an
  authorized platform route and could misrepresent a platform as send-capable.
  P13 remains NOT_STARTED pending a supported authorized route and explicit
  real-device acceptance. No source client was changed and no message sent.

## 2026-10-12 - P12 local WeChat avatar cache

- A cached-key-only, read-only audit on this Windows installation verified the
  `head_image(username, md5, image_buffer, update_time)` table and JPEG-signature
  blobs. WeFlow's `getAvatarsFromHeadImageDb` passes contact usernames directly
  to the reader, so Hub now matches exact usernames without inferring `md5`.
- Sync checks every contact; a verified local JPEG takes precedence over an
  avatar URL and is copied atomically to Hub-owned storage. Reads require the
  cached key and exact schema, cap each image at 2 MiB and returned bytes at
  32 MiB, and reject non-JPEG signatures. Unsupported schema remains unavailable.
- Provider/Hub avatar tests, `hub.test_server` and `py_compile` passed; the
  focused provider/server run passed 137 tests. Real WebUI rendering on this
  account and other WeChat schema variants remain unverified.
- Sanitized source v92 passed archive privacy scanning and extracted isolated
  startup/MCP smoke. Portable v101 passed isolated EXE/WebUI and standalone MCP
  smoke. Manifest v15 verified both package byte inventories and local-worktree
  provenance; hosted Actions/upload remain unverified.

## 2026-10-12 - P11 cached WeChat stickers

- Type-47 messages now retain only a validated 32-hex MD5 locator. Preview,
  explicit MCP reads, and ZIP export may resolve an existing GIF/PNG/WebP/JPEG
  in the WeFlow account-hashed emoji cache; lookup rejects traversal, symlinks,
  and other account namespaces. It never opens the emoticon database, contacts
  the network, downloads a missing sticker, or changes the source database.
- Synthetic provider and Hub checks passed for GIF/PNG, malformed identifiers,
  traversal, cross-account access, and missing files. Full resource/decryption
  parity remains open.

## 2026-10-12 - P14 manifest reparse-point rejection

- Package manifest generation and verification now reject reparse points at
  package roots and within portable trees rather than omitting them from the
  hash inventory. This prevents junction/symlink content from being silently
  left outside the manifest's byte binding.
- The manifest fixture creates an external-directory junction and verifies
  both writer and verifier reject it. `scripts/test-package-manifest.ps1`
  passed; hosted Actions and artifact upload remain unverified.

## 2026-10-12 - P9 ambiguous HTTP timeout classification

- OneBot HTTP 408 now maps to `unknown` rather than `failed`: the adapter may
  receive a timeout response after the request body was accepted, so claiming
  definite failure could invite duplicate delivery. Re-approval of that request
  remains read-only. Synthetic loopback coverage verifies the state and exactly
  one HTTP request; no NapCat or QQ account was started or contacted.
- Focused sender tests for HTTP 408, HTTP 401/transport timeout, and interrupted
  response reads passed. P9-02 remains in progress pending live NapCat/runtime
  and real-account acceptance.

## 2026-10-12 - P14 local package refresh

- Portable v40 passed staged privacy scanning, isolated Hub/UI and read-only
  endpoint smoke, three-source status checks, and standalone MCP stdio
  initialize/tools/list/exit. Sanitized source v89 (112 files) passed its
  archive audit, extracted startup, isolated storage, and read-only MCP smoke.
- Local manifest v89 verified all portable/source package bytes and provenance
  against HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with
  `build_context=local-worktree`. Hosted Actions and artifact upload remain
  unverified; no account data or runtime config was packaged.

## 2026-10-12 - P9/P14 follow-up delivery

- OneBot HTTP 408 now resolves to `unknown`, not a definite failure; repeated
  approval does not resend. Sender, Hub-sending and server verification passed
  138 tests. No NapCat runtime or QQ account was used.
- Portable v41 and sanitized source v91 were built from the updated worktree.
  Both passed privacy checks; portable startup and standalone MCP protocol
  smoke, and source extraction/startup smoke, passed. A byte-bound manifest
  records both package hashes and local-worktree provenance.
- P13 remains NOT_STARTED: no authorized current-account WeChat/WeCom sender
  path is verified. The available WeCom application-send API is not evidence
  of sending as the logged-in user in an existing conversation.

## 2026-10-12 - Clean-runner package dependency fix

- Removed `npm ci --offline` from the Windows package builder. GitHub-hosted
  runners do not promise a populated npm cache; locked install now fetches the
  pinned public WebUI dependency when needed. PowerShell syntax and a clean
  lockfile install passed locally.
- Hosted CI still requires an authorized push. The source bundle and manifest
  were refreshed to include this build-script change; the portable v41 binary
  contents are unchanged.

## 2026-10-12 - P12 isolated browser acceptance

- Fresh headless Edge profile and temporary Hub storage used synthetic QQ data
  only. Unlock worked; notification permission was requested only after the
  explicit toggle, the body-free notification navigated to its conversation,
  wheel activity refreshed the idle timer, and locking changed the SSE response
  from 200 to 423. No user profile, account or private messages were accessed.
- This verifies browser behavior only; real Moments schema/media/avatar
  acceptance remains open. Temporary synthetic browser/storage files were kept
  under `%TEMP%` because recursive cleanup was blocked.

## 2026-10-12 - Unified local gate after P11-P14 follow-up

- The updated local gate passed 370 Python tests and all 14 WebUI regression
  scripts, package privacy and manifest fixtures, JavaScript/Python syntax and
  compile checks, and `git diff --check`. The 25,000-message benchmark recorded
  0.067 s first search, 2.696 s for 50 pages, 0.073 s backup, 0.216 s restore,
  and zero WAL failures. These are synthetic host-local timings, not an RSS or
  real-account guarantee.
- This verifies the local gate only; current-worktree hosted Actions, live QQ
  acceptance, real Moments/media/avatar schema, full WeFlow resource parity,
  and any authorized WeChat/WeCom send route remain unverified.

## 2026-10-12 - P14 local WebUI gate coverage

- Added all 14 existing `webui/test-*.cjs` scripts to the local gate; five
  previously existing checks were not being invoked. All 14 passed. Hosted
  Actions has not run on this worktree, so P14 remains in progress.

## 2026-10-12 - Batch Moments page media lookup

- Replaced the per-post Moments media query with one account-scoped query for
  the requested page. A page of up to 500 posts now performs at most one media
  lookup instead of up to 500, while retaining post and per-post media order.
- Synthetic SQLite trace coverage verifies one media query and stable result
  ordering. Real-account schema/media/avatar and device acceptance remain open;
  no live account or source client was accessed.

## 2026-10-12 - P11 focused verification refresh

- Disabling the media capability now cancels and joins active attachment ZIP
  jobs; re-enabling it resumes job acceptance. The synthetic server regression
  verifies cancellation during a blocked resolver and confirms no downloadable
  archive remains.
- Re-ran `hub.test_analytics` and `hub.test_resources` (20 tests), the annual/pair
  report CSV browser self-check, JavaScript syntax validation, and
  `git diff --check`; all passed. This confirms synthetic analytics/report and resource
  behavior only; it does not satisfy full WeFlow media/decryption parity or
  real-device acceptance. No source account was accessed.

## 2026-10-12 - P11 exports/reports and P12 Moments search

- Annual report CSV now includes first/last message times and the busiest
  day/hour metrics already shown in the report UI. QQ batch attachment export
  accepts provider-supported resource indexes 0-100 (and continues rejecting
  101). Moments keyword search now matches post and comment content only, not
  usernames or media URLs.
- Fixed SQLite NULL handling in the shared media predicate: WeChat image rows
  with no `raw_type` remain counted, while explicit type 47 stickers stay
  excluded. Static P13 review found no authorized WeChat/WeCom delivery route;
  WeFlow's local message insert is not remote delivery, so P13 remains
  NOT_STARTED.
- Focused coverage passed for six analytics/store/resource cases, four Moments
  catalog cases, annual/pair report CSV, plus JS syntax and `git diff --check`.
  No live account was accessed and no message was sent.
- Portable v38 passed isolated Hub and MCP stdio startup smokes; sanitized
  source v87 passed privacy and extracted-app smoke checks, and manifest v87
  verified package hashes. Hosted CI is still required for P14 release
  acceptance.

## 2026-10-11 - Parallel P11/P12/P14 follow-up

- WeChat SILK media cache writes now stage to a same-directory temporary file,
  flush and atomically replace the cache entry; failures clean up the temporary
  file and preserve any prior complete entry. Account-lock activity now includes
  wheel scrolling, preventing scroll-only reading from expiring the idle timer.
- P14 package-manifest review found no reproducible defect; the existing
  Windows PowerShell manifest self-test passed. A dedicated reparse-point
  fixture remains a possible package-audit follow-up, not a verified bug.
- P9 now classifies interrupted OneBot response reads as `unknown`, not
  `failed`; retrying the same request ID remains read-only and does not send a
  second HTTP request.
- WeChat provider/media tests (33), the account-activity UI check, JavaScript
  syntax, P9 sender/Hub-sending tests (12), and `git diff --check` passed. No
  real account data was accessed and no message was sent.
- Rebuilt portable v37 (10,329 files) with Hub and MCP stdio smoke tests;
  sanitized source v85 (112 files) passed archive privacy and extracted-app
  smoke checks. Local manifest v85 verified all package hashes. Hosted CI was
  not run.

## 2026-10-11 - Export, Moments cache, and CI reproducibility follow-up

- Shared CSV formula protection now detects formula markers after leading
  spaces, tabs, newlines, or BOM for message, contact, and group exports.
- Moments text catalogs still refresh when the local cache root disappears;
  attachment rows remain indexed but marked unavailable. CI pins
  `cryptography==45.0.7` to match the Windows package dependency lock.
- The package manifest verifier now keeps single-file ZIP inventories as arrays
  under Windows PowerShell 5 and reports expected/actual counts on mismatch.
- Seven focused export/Moments tests and 20 combined focused store/HTTP/MCP
  tests passed. The v36 portable package passed isolated Hub and MCP stdio
  smoke tests; source v82 passed archive privacy and extracted-package smoke;
  manifest v82 verified every byte/hash. Hosted CI was not run.

## 2026-10-11 - Media library account scoping

- Added a composite platform/account picker for the media library and exact
  account filters through chat/Moments SQLite queries, the QQ cache gallery,
  and MCP `list_media`. Added a local account-inventory endpoint. Fixed SQL
  three-valued logic that had hidden WeChat media rows whose `raw_type` is NULL.
- Eleven focused store/HTTP/MCP tests, two MCP attachment tests, fourteen WeCom
  adapter tests, JavaScript syntax, Python compile, and
  `git diff --check` passed. Full suites and real-account data were not used.

## 2026-10-11 - Atomic QQ media cache writes

- QQ media downloads now write to a same-directory temporary file and atomically
  replace the content-addressed cache entry, cleaning up partial files on error.
- One focused provider test passed. No live QCE/QQ run was performed.

## 2026-10-11 - WeCom cached SILK playback

- Cached WeCom SILK attachments now use the shared local voice decoder after a
  seven-byte header check. No source data is modified; playback still depends on
  the optional local decoder being installed.
- Fourteen WeCom adapter tests passed. No real account was accessed.

## 2026-10-11 - MCP attachment contract regression tests

- Updated stale assertions for the expanded MCP tool list and the supported
  bounded file-attachment response. The test now verifies a selected PDF returns
  as an embedded resource after validating its index.
- Two focused MCP attachment tests passed.

## 2026-10-11 - MCP QQ cache gallery

- Added read-only MCP tools to page authenticated QCE QQ cache metadata and
  explicitly return a selected local image/audio/video/file by opaque ID.
  Account binding, local cache containment, signature checks, and the existing
  8 MiB response cap are reused; paths and QCE URLs are not exposed.
- One focused MCP synthetic test and Python syntax checks passed. The full suite
  and live QCE/account acceptance were not run.

## 2026-10-11 - Contact profile lookup

- Contact search now matches indexed IDs and the stored nickname, alias, and
  remark fields in addition to display names. The list shows distinct alternate
  names as escaped secondary text; it adds no export fields or network lookups.
- One focused contact-store test, WebUI syntax check, and `git diff --check`
  passed. The full suite was not run.

## 2026-10-11 - Contact account scoping

- Contact browsing and JSON/CSV export now support an exact platform/account
  scope; MCP `list_contacts` accepts the same optional account ID. The UI picker
  uses platform/account pairs, so equal IDs on different platforms stay distinct.
- Focused store/MCP checks, WebUI syntax, and `git diff --check` passed. No full
  suite or real-account contact data was checked.

## 2026-10-11 - On-demand full group activity ranking

- Group analytics keeps the compact top-12 default, with an explicit per-group
  action to load up to 5,000 observed message senders. The API requires platform,
  account, and conversation scope for this expanded query; the UI caches the
  result for collapse/reopen rather than refetching it.
- A focused UI/API check covers the default cutoff, scoped request, full list,
  and collapse behavior. Two targeted Python checks, the UI check, JavaScript
  syntax, and `git diff --check` passed; the full suite was not run.

## 2026-10-11 - QQ rich sync projection and package manifest

- Regular QQ sync now stores the existing renderer's compact structured
  projection for text, link/system cards and market faces instead of retaining a
  duplicate raw QCE message object. Chat type and peer identity remain available
  to local media lookup. Focused provider/UI checks and syntax validation passed.
- The portable/source package manifest now verifies path membership through a
  single ordinal path index rather than rescanning the full inventory per file.
  The deterministic manifest fixture and current 10,319-file portable package
  hash verification passed; source ZIP SHA-256 is
  `D19B821A21317E6D9DED1D36A4227E61FCF34AC3AA1CF46BBDD03E3C9074513E`.
- No full test suite was run. Real QQ account payloads and Hosted Actions remain
  unverified.

## 2026-10-11 - QQ roaming rich message rendering

- QQ history chat bubbles now render indexed structured text, mentions, faces,
  reply previews, nested forwarded-message snippets, locations and link/share cards.
  Regular QQ sync now stores a compact structured projection instead of a duplicate
  raw QCE message payload; media lookup retains peer/chat-type fields. Cached media
  continues through the existing local resolver; upstream HTML is not rendered
  and element text is escaped.
- `node webui/test-qq-rich-content.cjs` and `node --check webui/app.js` passed.
  Real QQ account rendering and unsupported element types remain unverified.

## 2026-10-11 - Pair report CSV export

- Pair-report dialogs now export the selected private conversation's overview,
  date bounds, hourly counts, and message-type counts. Export contains no message
  bodies, uses the existing formula-safe CSV cell encoder, and omits personal
  names from the filename.
- A focused browser self-check covers CSV formula neutralization and body-free
  output; `node --check webui/app.js` passed. No full test suite was run.

## 2026-10-11 - MCP document attachment MIME

- Locally cached file attachments now expose PDF, ZIP, RTF, and legacy OLE
  MIME types when their file signatures match; unknown content remains opaque.
  The reader returns original bytes and does not inspect/extract archives.
- One synthetic cached-file check covers PDF/ZIP/RTF. Real files and MCP client
  rendering were not tested.

## 2026-10-11 - WeChat binary avatar reader investigation

- Confirmed that the repository contains the WeFlow TypeScript caller and C ABI
  declaration for `wcdb_get_head_image_buffers`, but not its optional native
  implementation. Public format descriptions alone do not verify a pinned
  reader contract, so no schema/encoding guess or user database read was made.
- Updated avatar capability wording to match the provider's current
  `reader_unavailable` status. Binary avatar bytes remain a documented P12
  gap until the native implementation/source and a synthetic fixture are
  available for verification.

## 2026-10-11 - Open chat context from media library

- Chat-backed media tiles now have a tooltip-labeled action to jump to the
  source conversation and message context, reusing the global-search navigation
  path. Moments and QQ global-cache entries do not show the action.
- `node webui/test-media-open-chat.cjs` and `node --check webui/app.js` passed.
  No full test suite, package build, or real account data was used.

## 2026-10-11 - MCP attachment content types

- The explicit MCP attachment tool now returns validated cached voice data as
  MCP audio and indexed video/files as bounded embedded resources; image
  responses retain their existing image content. Reads remain local-cache only
  and capped at 8 MiB.
- Added focused synthetic MCP audio-content and local WAV-signature regressions.
  The targeted tests and source syntax checks passed; real-account media and
  client rendering remain unverified.

## 2026-10-11 - Annual report CSV export

- Added a local CSV download to the annual report view for overview, monthly,
  hourly, and message-type aggregates. It contains no message bodies; fields
  are quoted and formula-leading text is neutralized.
- `node --check webui/app.js` and `git diff --check -- webui/app.js` passed.
  No full suite or real-account data was used.

## 2026-10-11 - Media type classification

- WeChat `raw_type=47` remains searchable and visible in the expanded media
  library as `sticker`; image filtering and media counts now exclude it, and
  overview type totals classify it separately from ordinary images.
- The focused synthetic store regression, `node --check webui/app.js`, and
  `git diff --check` passed. No full suite or package rebuild was run for this
  narrow change. Broader P11 media/decryption parity remains open.

## 2026-10-11 - WeChat emoji media boundary

- WeChat local type 47 now normalizes to the safe searchable placeholder
  `[表情]` instead of parsing sticker XML as image metadata. Chat UI identifies
  the unsupported decoder; media preview and batch selection are disabled, and
  media/resource resolvers return an explicit unsupported-format result before
  provider access. No cache path was guessed and no network fetch was introduced.
- Added regression coverage for normalization, provider non-access, resource
  export rejection, and disabled WebUI batch selection. The full local gate
  passed 353 Python tests and 8 WebUI tests, plus privacy/manifest fixtures,
  syntax/compile checks, and `git diff --check`. The 25,000-message benchmark
  recorded 0.068 s first search, 3.521 s for 50 pages, 0.149 s backup, 0.412 s
  restore, and zero WAL failures.
- Sanitized source bundle v78 contains 107 files; staged privacy scan and
  extracted Hub/WebUI/three-source/read-only-MCP smoke passed. SHA-256:
  `9054EE33BCC38AAF6EB5BB47D1F830AE6557965431578D78754CF1DC57059A04`
  (459,573 bytes). Local manifest
  `output/windows/LocalMessageHub-artifact-manifest-local-v34-v78.json` binds
  portable v34 and source v78 to `local-worktree`; SHA-256:
  `6AD40EA6B4707B56E5A40DBBCBAD68B341464C5D9DFED8A08C74564FBBDD72DA`
  (2,365,921 bytes). Hosted CI remains unverified; no push or workflow dispatch
  was performed.

## 2026-10-11 - WeChat image export quality tiers

- Added a batch-export quality selector for ordinary WeChat chat images:
  best available (default), mid, thumbnail, or cached original only. Server and
  export-job validation rejects unsupported values and rejects this option for
  Moments, WeCom, QQ, indexed attachment slots, and non-image messages. The
  provider reads only existing cache entries; a missing original fails that
  item without requesting a source download. ZIP manifests retain only the
  message identity and selected tier, never a local path.
- Synthetic server/job/provider/UI tests cover accepted tier forwarding,
  invalid combinations, default behavior, and the WeCom UI boundary. No live
  account or source database was accessed.
- The full local gate passed **351 Python tests and 8 WebUI tests**, package
  privacy and manifest fixtures, syntax/compile checks, and `git diff --check`.
  The 25,000-message benchmark recorded 0.067 s first search, 2.658 s for 50
  pages, 0.081 s backup, 0.224 s restore, and zero WAL failures.
- Sanitized source bundle v75 contains 107 files. Staged path/privacy scan and
  extracted Hub/WebUI/three-source/read-only-MCP smoke passed. SHA-256:
  `32AF4CE7F042021E1314BD36B76B7D1C14910447F9EEA8A542F3197F60F30ECF`
  (458,218 bytes). Local manifest
  `output/windows/LocalMessageHub-artifact-manifest-local-v34-v75.json` binds
  the existing portable v34 and source v75 to `local-worktree`; manifest
  SHA-256: `913E3D8D6206F96A0F7F19CE874F70D46C704042E5C4EF4A2A78E1E02693CAFD`
  (2,365,923 bytes). No push or hosted workflow dispatch was performed.
- Updated the WeFlow comparison matrix to record the quality-tier scope and
  acceptance boundary, without changing P11 parity status. Rebuilt the source
  bundle and local manifest as v76. Source bundle has 107 files and passed the
  staged privacy scan plus extracted Hub/WebUI/three-source/read-only-MCP smoke.
  SHA-256: `A7E88EA4135D155756927E0A62A1913B1E179B8534D8C73D7EA71B7F2A2606A4`
  (458,481 bytes). Manifest
  `output/windows/LocalMessageHub-artifact-manifest-local-v34-v76.json` binds
  portable v34 and source v76 to `local-worktree`; SHA-256:
  `EE80E17742F4D6E546A55A1256686F63DCB356240E1BA87C596E006247E81F0E`
  (2,365,923 bytes). No push or hosted workflow dispatch was performed.

## 2026-10-11 - Current-user path privacy regression

- Extended the package privacy negative fixtures to put synthetic normalized
  `USERPROFILE` and `LOCALAPPDATA` paths into temporary text files and require
  the shared staged-package scanner to reject them. No account data or profile
  files were read; the focused privacy test passed.
- The full local gate passed 350 Python tests and 8 WebUI tests before this
  fixture-only extension; its 25,000-message benchmark recorded 0.066 s first
  search, 3.565 s for 50 pages, 0.172 s backup, 0.443 s restore, and zero WAL
  failures. The updated privacy test passed independently, and sanitized source
  v72 passed staged privacy and extracted Hub/WebUI/three-source/read-only-MCP
  smoke. Its SHA-256 is
  `368E6C4251165614CA280286CB9DCFAB758C3D56764E2D1951C3E27D80B38687`
  (456,829 bytes).
- Refreshed local provenance manifest
  `output/windows/LocalMessageHub-artifact-manifest-local-v34-v72.json` with
  `local-worktree` context and the existing portable v34 plus source v72.
  Manifest SHA-256:
  `C157E43614627762275E18E4721A2F4EE6CC8D907BBB7464025795FE7AE5A8F0`
  (2,365,926 bytes). Hosted CI remains unverified; no push or workflow dispatch
  was performed.

## 2026-10-11 - Voice identity boundary and current release artifacts

- WeChat voice-index lookup now distinguishes absent `local_id` from valid
  zero, preventing an exact voice playback diagnostic from widening into an
  unfiltered media query. A temporary SQLite regression covers zero-ID
  filtering and the legacy schema without `download_status`.
- The current full local gate passed 350 Python tests and 8 WebUI tests, along
  with package privacy/manifest fixtures, syntax/compile checks, and
  `git diff --check`. The 25,000-message benchmark recorded 0.071 s first
  search, 2.667 s for 50 pages, 0.079 s backup, 0.223 s restore, and zero WAL
  failures.

## 2026-10-11 - Bind release artifacts to build provenance

- The Windows CI workflow now writes a deterministic JSON manifest after both
  package stages pass and uploads it with the packages. It records every file
  in the portable directory and the source bundle's SHA-256/size, plus commit
  SHA, workflow run ID, and whether the build came from hosted CI or a local
  worktree. Paths are package-relative; local absolute paths are not emitted.
- Added a synthetic fixture verifying stable manifest bytes, exact file counts,
  file hash/size, provenance fields, and path redaction. The refreshed portable
  v34 contains 10,319 files / 406,304,225 bytes; Hub EXE SHA-256 is
  `B8B00D35C66AA4DD548CF21025FC3D959EB626A2629D2E1D9B15B8E7556C3243`, and
  standalone MCP EXE SHA-256 is
  `141AEA2B24C112AF54398AAAC9DBB766652FC064E326362126BD8506D272BE33`.
  The local manifest records `local-worktree`, not hosted provenance; its
  source archive v71 SHA-256 is
  `195FFD71630682D1AD892DA622839BBCB12FF728A8BC2BD4924A0DD0DB700D76`
  (456,529 bytes). The extracted v71 package passed privacy scan and isolated
  Hub/WebUI/source/read-only-MCP smoke. Its local manifest is listed below and
  explicitly declares `local-worktree` provenance.
- Local manifest
  `output/windows/LocalMessageHub-artifact-manifest-local-v34-v71.json` has
  SHA-256 `A802563D18BD1A18D621096137467E8CE3783ED0BFF8332A4797E0F354EA91D8`
  (2,365,915 bytes); it inventories 10,319 portable files / 406,304,225 bytes
  and one source archive / 456,529 bytes. The manifest itself is not staged in
  either package and makes no hosted-build claim.
- Hosted Actions and uploaded artifact remain unverified; no push or external
  workflow dispatch was performed.

## 2026-10-11 - Moments reply rendering and roadmap package refresh

- The Moments WebUI now follows parsed `refCommentId` links and labels the
  referenced author's reply; dangling references render as ordinary comments.
  Names and content remain HTML-escaped, and absent likes/comments default to
  empty lists. The synthetic UI regression covers linked, dangling, and escaped
  comment data.
- Full local gate passed: 349 Python tests, 8 WebUI tests, package privacy
  negative fixtures, syntax/compile checks, and `git diff --check`. The
  25,000-message benchmark reported 0.074 s first search, 2.860 s for 50 pages,
  0.082 s backup, 0.229 s restore, and zero WAL failures.
- Portable v32 passed privacy scan, WebUI/source/isolation/read-only-MCP smoke,
  and standalone MCP stdio smoke. Sanitized source v65 passed its extracted
  package smoke and privacy scan.
- This advances the synthetic WeFlow Moments parity only. Real-account Moments
  schema/media/avatar validation, remaining P11 gaps, and P9/P12/P13/P14
  acceptance requirements remain open as noted in the roadmap.

## 2026-10-11 - On-demand WeChat voice cache diagnostics

- When an indexed voice playback request misses local audio, the resolver now
  queries the media index on demand and binds a duplicate local message ID to
  its exact database shard. HTTP/UI receives only an allowlisted reason and
  byte count; source paths and server IDs remain private. Ordinary message
  pages do not trigger the status scan.
- Focused verification passed for provider/media/HTTP tests (142 tests),
  including shard collision, uncached reason mapping, and redaction. Full gate
  and refreshed package results are recorded after integration below.
- A follow-up exact-identity audit found that voice-index filtering treated
  `local_id=0` as absent and could widen the query. The query now tests against
  `None`, and a temporary SQLite fixture covers zero-ID precision plus the
  legacy schema without `download_status`; the full 350-test gate passed.

- Regular message pages do not scan the voice index. The on-demand diagnostic
  performs an exact message/shard lookup and returns only an allowlisted reason
  plus byte count; source paths and server IDs are not exposed.

## 2026-10-11 - Portable package runtime acceptance

- Built `output/windows/LocalMessageHub-portable-20261011-v31-roadmap-p9-tests`
  on Windows 11 / Python 3.12.10 with the pinned bundle dependencies. Static
  privacy scan passed. Inventory: 10,319 files, 406,286,963 bytes. Hub EXE
  SHA-256: `04D2D5447D9C13E526A7E4B324DF883D8A8B791DD51F1403088832BC072526CD`;
  standalone MCP EXE SHA-256:
  `EE2434D73334F45D288B096CB2D512062F4E414EB28FFB2E3CEBA693789E434F`.
- The built Hub EXE served the WebUI, Lucide assets, three source entries,
  isolated temporary storage and read-only MCP endpoint. The standalone MCP
  EXE completed initialize/tools/list/exit over stdio with valid JSON-RPC
  stdout. P6-06 package acceptance is now complete. This does not verify QQ
  login or real delivery, and it does not substitute for hosted P14 CI.

## 2026-10-11 - WeChat contact avatar metadata

- Extended the local WeChat contact reader to inspect `contact` table columns
  and read avatar URLs only from the field-name allowlist in the pinned WeFlow
  reader. URLs are restricted to supported HTTPS avatar hosts; no network
  request is made. Hub rendering remains cache-only, and binary avatar bytes
  from `head_image.db` remain unavailable without WeFlow's optional WCDB native
  export.
- Added three synthetic provider tests and added the module to the explicit
  local CI/check script. The full local gate passed 342 Python tests, 7 WebUI
  tests, privacy negative fixtures and syntax checks. The 25,000-message
  benchmark recorded 0.067 s first search, 2.634 s for 50 pages, 0.081 s
  backup, 0.217 s restore, and zero WAL failures.
- Built portable package v30 with this provider change; its static package
  privacy scan passed. It contains 10,317 files totaling 406,264,194 bytes.
  Hub EXE SHA-256: `99004735104DEA9915C033519AD67DD4733292F58DDC1DC43958E8A647FC52A7`;
  standalone MCP EXE SHA-256:
  `A552BE723084E84774CF8C94D42D44F634E5EDB0CBCD2373D23CDABA6D6E169B`.
  EXE runtime smoke remains pending on an allowed Windows runner.
- Rebuilt the sanitized source bundle as v56 (106 files); privacy scanning and
  extracted-package WebUI, three-source, isolated-storage, and read-only MCP
  smoke passed.
- This exposes verified URL metadata only; it does not claim that uncached
  WeChat avatars or `head_image.db` binary extraction are supported.

## 2026-10-11 - QQ cache ZIP export end-to-end acceptance

- Added an HTTP integration test that sends an opaque QQ cache resource identity
  through the real Hub resolver and cancellable ZIP exporter, then verifies the
  exported bytes, account-bound identity manifest, source immutability, and
  rejection of caller-supplied URLs and local paths.
- The full local gate passed 339 Python tests, 7 WebUI tests, package privacy
  negative fixtures, syntax/compile checks, and the 25,000-message benchmark:
  0.066 s first search, 2.616 s for 50 pages, 0.076 s backup, 0.220 s restore,
  and zero WAL failures.
- The portable package remains v29; EXE runtime smoke is still pending on a
  permitted Windows runner. Refreshed sanitized source archive v54 (105 files);
  archive privacy scanning and extracted-package WebUI, three-source, isolated
  storage, and read-only MCP smoke all passed.
- This closes synthetic QQ cache gallery-to-ZIP coverage only; it does not
  claim QCE historical recovery or full WeFlow resource/decryption parity.

## 2026-10-11 - QQ cached resource gallery in the media library

- Added a paged QQ cache gallery backed by the authenticated local QCE endpoint.
  The browser receives only safe metadata and random in-memory IDs; IDs are
  account-bound, cache paths are reconstructed from validated type/name pairs,
  each item is limited to 50 MiB, and downloaded bytes must match QCE metadata.
- Wired gallery previews and multi-select ZIP export through existing local
  media/resource APIs. No QCE URL, local path, or credentials are returned.
- Added provider, HTTP, and WebUI regressions for page/filter mapping, path and
  account validation, metadata redaction, opaque preview/export identities.
- The full local gate passed 338 Python tests and 7 WebUI tests, privacy
  negative fixtures and syntax/compile checks. The 25,000-message benchmark
  recorded 0.067 s first search, 2.647 s for 50 pages, 0.079 s backup, 0.218 s
  restore, and zero WAL failures.
- Refreshed sanitized source archive v53; its archive privacy audit and
  extracted-package startup/WebUI/three-source/read-only-MCP smoke passed.
- QCE pagination metadata is accepted only in its documented integer/boolean
  types; malformed totals fall back to the validated page count.
- This adds a bounded view of files currently exposed by QCE, not historical
  recovery, complete WeFlow resource discovery, or all-format decryption.
- Built portable Windows package v29 with the QQ cache gallery and the
  hash-verified official QCE v6.3.2 runtime. Static package privacy scan passed;
  10,315 files total (406,243,610 bytes). Hub EXE SHA-256:
  `EC68EBA48E8CAA329C0065E73CC66357B766E4F3934F6F283DBCC3214FAF4251`;
  standalone MCP EXE SHA-256:
  `51D0C6E4B54D18DD6B89C75518F9D7686BC9A3A506E15239FC4BCDE0190E690C`.
  EXE runtime smoke was skipped under the known host execution restriction;
  this build does not close P6-06 runtime acceptance.

## 2026-10-11 - Bounded WeChat image decryption reads

- Capped encrypted `.dat` image input at 64 MiB and used a bounded read after
  the size check, so a growing file cannot bypass the allocation limit. The
  source cache remains untouched; oversize media is reported unavailable.
- Added synthetic regressions for an oversized file and a file that grows
  between metadata check and read. All 25 focused WeChat media tests pass.
- The full local gate passed 335 Python tests, 7 WebUI tests, package privacy
  fixtures, syntax/compile checks, and the 25,000-message benchmark: 0.069 s
  first search, 2.630 s for 50 pages, 0.078 s backup, 0.221 s restore, and
  zero WAL failures.
- Rebuilt sanitized source archive
  `output/windows/LocalMessageHub-source-roadmap-20261011-v51.zip` (105 files).
  Archive privacy audit and extracted-package WebUI/three-source/isolated-
  storage/read-only-MCP smoke passed. SHA-256:
  `8B55EBDE33492EB36DDFDAE8238DF807CFE6E154EFE1CED85327442DAE1F3E7B`.
- Full WeFlow resource discovery/decryption parity remains open; this closes
  only an unbounded-memory edge in the supported offline image path.

## 2026-10-11 - Contact export payload minimization

- Tightened contact JSON/CSV exports to retain only normalized, scalar profile
  fields (nickname, alias, remark, UIN and safe local counts/timestamps). Raw
  provider payloads, unknown fields, nested objects, URLs and token/credential-
  like values are omitted; the indexed contact data itself is unchanged.
- Added renderer and HTTP regressions with nested QQ-style avatar URLs, access
  tokens, unknown private fields and formula-leading profile values. CSV formula
  escaping and platform/account provenance remain intact.
- Corrected the WeChat integration README and WeFlow gap matrix to document
  explicitly requested cached MP4/WebM delivery through the MCP resource path.
- The full local gate passed 333 Python tests and 7 WebUI tests, privacy
  fixtures, syntax/compile checks, and diff validation. The 25,000-message
  benchmark recorded 0.068 s first search, 2.620 s for 50 pages, 0.076 s
  backup, 0.226 s restore, and zero WAL failures.

## 2026-10-11 - Bounded group roster export

- Added a capability-gated JSON/CSV export action to the three-platform group
  roster dialog. The endpoint reuses the indexed account/group and local reader
  checks, emits only allowlisted roster fields plus platform/account/group
  provenance, omits avatars/provider extras, and applies existing CSV formula
  escaping.
- The current reader contract exposes at most 1,000 members per group request;
  larger rosters now fail with HTTP 413 instead of silently exporting a partial
  list. Synthetic tests cover account provenance, field/token omission, CSV
  hardening, capability denial, and the explicit row limit.
- Focused backend verification passed: 168 `hub.test_store` and
  `hub.test_server` tests.
- The full local gate passed 333 Python tests and 7 WebUI tests, privacy
  fixtures, syntax/compile checks, and diff validation. The 25,000-message
  benchmark recorded 0.066 s first search, 2.630 s for 50 pages, 0.081 s
  backup, 0.220 s restore, and zero WAL failures.
- Rebuilt sanitized source archive
  `output/windows/LocalMessageHub-source-roadmap-20261011-v48.zip` (105 files).
  Its denylist/current-profile path audit and extracted-package WebUI, three-
  source, isolated-storage and read-only MCP smoke passed. SHA-256:
  `5EF355E6696EF1FF158107F99BF306E44A29F057FE57AF78B55B565056441DDC`.
- Rebuilt sanitized source archive v49 after the contact export privacy fix
  (105 files). Denylist/current-profile path checks and extracted WebUI, three-
  source, isolated-storage and read-only MCP smoke passed. SHA-256:
  `95B1F1751782E97062BA4D2F3559B63FAAC06D48DB653D3380C6D9B074D1A236`.

## 2026-10-11 - Standalone MCP package protocol smoke

- Extended the Windows portable package smoke to launch the bundled standalone
  MCP executable using a GUID-scoped isolated storage directory. It sends MCP
  `initialize`, `notifications/initialized`, `tools/list`, and `exit` through
  stdin; checks both JSON-RPC responses, core search/conversation/attachment
  tools, database isolation, a clean JSON-only stdout stream, and successful
  exit; stderr is captured separately and the run is bounded to 15 seconds.
- The normal Windows CI package-build step runs this executable test after
  packaging. The local Python gate does not launch unsigned executables; this
  host's policy remains respected, so P6-06 runtime and P14 hosted acceptance
  remain IN_PROGRESS until an allowed Windows Actions run verifies the build.
- Source-level validation only in this change: PowerShell parser and diff checks;
  the frozen EXE was not launched locally.

## 2026-10-11 - Local-only avatars and one-time send approval

- Changed avatar rendering to same-origin paths only. `/api/avatar` now serves
  a signature-checked image only when its URL hash is already in the Hub's
  local avatar cache; misses make no DNS or HTTP request. Existing cached
  avatars remain visible, while uncached platform avatars fall back to initials.
- Replaced the fixed `APPROVE_SEND` delivery string with a 60-second, request-
  bound, one-time challenge. Missing, incorrect, expired and replayed tokens
  do not call the delivery adapter. This is a confirmation workflow guard, not
  isolation from processes running as the same local Windows user.
- Added focused server regressions for avatar cache-only behavior and send
  challenge expiry, request binding, and replay; focused cases and
  `node --check webui/app.js` pass.
- Added read-only WeChat Moments Live Photo parsing and MP4 cache resolution.
  The WebUI pairs a cached still image with its cached video through short-lived
  same-origin tokens; cache misses, invalid signatures and symlink escapes are
  covered without contacting a media host. MCP remains explicit-image-only.
- Added default-off local voice transcription with identity-bound provider cache
  resolution and an optional already-installed SenseVoice runtime/model. It
  never downloads a model or persists transcripts; the UI tests use an injected
  recognizer. Actual inference remains unavailable on this host because the
  optional runtime/model are not installed.
- The full local check passes 242 Python tests, two UI rendering tests, video
  byte ranges, syntax/compile checks, a 25,000-message benchmark, WAL and
  backup/restore.
- Rebuilt the 90-file source package at
  `output/windows/LocalMessageHub-source-v14-roadmap-privacy-voice.zip`;
  extracted-package smoke passed with isolated storage. Rebuilt the portable
  Windows package at
  `output/windows/LocalMessageHub-portable-20261011-v13-local-privacy-voice`.
  Static inventory covered 10,249 files / 405,305,177 bytes with no database,
  private account settings, key, certificate or log files and no exact workspace
  path marker. Three upstream `onebot11.json` templates have empty tokens. Hub
  SHA-256 is `E52BD52CC9A10F2919AD503DB0C2482A74E71E36C0AC44423DB34C172FF9141B`;
  MCP SHA-256 is `8926BABB81E1734328C0EE4555DD40922F28CAE4D53FDC1C0A03CB3243A69C9F`.
  The unsigned portable EXE smoke remains skipped due to host policy.
- Started an isolated empty-data preview on a loopback port; HTTP 200,
  all three source entries, and latest approval / Live Photo UI assets verified.

## 2026-10-11 - V2 release verification

- Re-ran `scripts/check-local-hub.ps1`: **215 tests passed**. The 25,000-message
  benchmark passed search, 50-page traversal, WAL concurrency and backup/restore
  (ingest 1.23 s, first search 0.07 s, 50 pages 2.636 s, backup 0.08 s,
  restore 0.216 s).
- Closed P12-02 against its stated objective, content-free private-chat milestone
  acceptance. Current package privacy inventory and platform-limit docs pass
  local checks. The build smoke test now also checks the packaged relationship
  milestone stylesheet.
- P6-06 remains open because local application-control policy blocks launching
  the unsigned final EXE. Real QQ send, real Moments schema/avatar/media, complete
  WeFlow resource/media parity, and a verified authorized WeChat/WeCom sender are
  still explicit account/device or implementation gaps; no synthetic test is
  represented as their acceptance. P14 remains open until hosted CI runs on the
  reviewed branch; this worktree is still uncommitted and was not pushed.

## 2026-10-11 - Local WebUI account lock

- Added an opt-in passphrase lock for the loopback WebUI and HTTP API. The
  passphrase is stored as a salted PBKDF2-HMAC-SHA256 verifier; sessions are
  in-memory, idle for 15 minutes, rate limited after repeated failures, and
  cleared by manual lock or process restart.
- Locked requests receive a dedicated unlock page; direct APIs, exports, local
  media, avatars and event streams are gated server-side. The setting UI can
  set, manually lock, or disable the passphrase. The MCP stdio process remains
  an independently configured local trust boundary and is not covered by this
  lock.
- Added deterministic session-expiry and HTTP bypass tests. The project's full
  check script passed **202 tests**, JavaScript syntax, Python compilation,
  25,000-message search/pagination, WAL concurrency and backup/restore.

## 2026-10-11 - Windows portable package audit

- Built `output/windows/LocalMessageHub-portable-20261011-notifications` from
  the pinned isolated CI environment. It contains the WebUI service, standalone
  MCP executable, and hash-verified QCE v6.3.2 runtime; no installer or runtime
  dependency download is required.
- Static inventory: 10,222 files (about 405 MB); no local database, account
  configuration, credential, or key files were present. The only filename
  heuristic matches were upstream QCE documentation and test assets containing
  the word `token`.
- SHA-256: Hub `9166CA3D184A41AEDD6872E9088BB6924C87FB583DFB9267B576508791609A13`;
  MCP `CBFE1292B60BB5DF65E0E7BA49F4FF8F9DDA00A48AD49F468356AF3EE7A5DBB4`.
  Both executables are unsigned. The isolated startup smoke test remains
  unverified because this Windows environment blocks unsigned executable launch.

## 2026-10-11 - Account-lock portable rebuild

- Rebuilt the current source into
  `output/windows/LocalMessageHub-portable-20261011-account-lock`, including
  the WebUI unlock assets and account-safety backend in both bundled runtime
  trees, the standalone MCP executable, and pinned NapCat/QCE runtime.
- Static inventory: 10,232 files (405,034,944 bytes); no SQLite database,
  account settings, private-key/certificate files, or scanned machine/user
  path markers. Required lock, Hub, MCP, and NapCat files are present.
- SHA-256: Hub `D84C3377B4270D2B7A1CA58C923C55DD5856E593D2C3210F25501CC5E2FB80C6`;
  MCP `FE08022D7B5FEDD69BC5EFC092D3D4BC02C892743A1A72BCD73D2B251A64C193`.
  Executables are unsigned; the build completed, but startup smoke testing was
  skipped because Windows application control blocks launching unsigned builds.
- The full isolated check passed **202 tests**, including lock rate limiting,
  disable flow, HTTP route gating, frontend syntax, Python compilation,
  25,000-message performance checks, WAL concurrency, backup/restore, and
  `git diff --check`.

## 2026-10-11 - Local desktop notifications

- Implemented the default-off `notifications` capability with a loopback-only
  SSE route. New connections baseline at the current SQLite rowid; events include
  platform/conversation/sender metadata only, exclude self-sent messages and body
  text, and obey persisted platform filters plus per-conversation mute settings.
- WebUI requests browser notification permission only from the user's toggle,
  shows a privacy-minimal notification while the page is hidden, and opens the
  matching conversation when clicked. Leaving the page or disabling the module
  closes the stream.
- Synthetic store and HTTP tests cover ordering, no-body payloads, default gate,
  platform/mute filtering, and settings validation. P12 remains in progress for
  Moments and app lock; this is not equivalent to remote or source-client push.
- `scripts/check-local-hub.ps1` passed with the existing isolated CI venv:
  **199 tests passed**, JavaScript syntax, Python compilation, synthetic
  25,000-message performance, WAL concurrency, backup/restore, and diff checks.

## 2026-10-11 - P11 local attachment batch export

### Scope

- Added bounded background ZIP jobs for resolver-supported local attachments.
  Jobs accept message identity only (never a client path), report progress,
  tolerate per-item failures, support cancellation, and include a provenance
  manifest without message text. Limits are 100 items, 50 MiB per item, and
  512 MiB per archive; the media capability gates create, status, cancel, and
  download routes.
- Added media-library selection, page select/clear, progress, cancellation,
  and direct ZIP download controls. QQ selections carry the indexed roaming
  resource number when available; WeChat and WeCom continue through their own
  local providers. Uncached/unsupported media is reported as unavailable.
- This does not complete WeFlow's full resource library or all-platform bulk
  decryption. It exports only files the existing local readers can resolve.

### Verification

- `hub.test_resources` and HTTP export/cancel tests pass with synthetic files;
  the API capability gate, provenance manifest, partial failures and archive
  cleanup were verified.
- `scripts/check-local-hub.ps1` passed **190 tests**, JavaScript syntax,
  Python compilation, 25,000-message search/pagination, concurrent WAL writes,
  backup/restore, and `git diff --check` in the isolated CI virtualenv.
- Built the Windows portable Hub and standalone MCP packages into
  `output/windows/LocalMessageHub-portable-20261011-p11`; both executable
  archives contain `hub.resources`. Static scan covered 10,222 files and
  found no chat databases, account settings, or private key/certificate files.
  Startup remains unverified here because local application control blocks
  launching the unsigned executables; build smoke launch was intentionally
  skipped.

## 2026-10-11 - Local scheduled exports

### Scope

- Implemented P12 local automatic export using the existing unified SQLite
  index and export renderers. The feature is default-off and independently
  capability-gated; users choose platform, interval, format, and a local output
  directory. Scheduling resumes from persisted timestamps after restart.
- Added bounded paging (250,000 messages per run), task cancellation,
  same-directory temporary output followed by atomic replacement, and
  sanitized persisted status. It never reads source-app databases or uploads
  data. The WebUI exposes plan configuration, immediate export, and cancel.
- Kept Moments, notifications, app lock, anti-recall, and WeChat/WeCom real
  sending unavailable pending their data source, OS/security design, or
  authorized-device acceptance.

### Verification

- `hub.test_auto_export`: **6 tests passed** for local output, persisted plan,
  restart recovery, capability denial, invalid configuration, cancellation,
  and size bounds.
- HTTP integration test verified default-off denial, explicit enablement,
  configuration and successful synthetic-index export. JavaScript syntax and
  Python compilation passed.
- `scripts/check-local-hub.ps1`: **184 tests passed**; synthetic 25,000-row
  search/pagination, concurrent WAL writes, backup/restore, and diff checks
  passed. See the remaining real-device/package gates below.

## 2026-10-11 - V2 send, analytics and release integration

### Scope

- Added a durable text-only QQ send queue with explicit numeric account and
  target, request-ID deduplication, audit states, local approve/reject, and
  interrupted/timeout `unknown` handling. NapCat delivery is restricted to an
  authenticated loopback OneBot endpoint and rechecks the online account
  against the approved account immediately before delivery. MCP can prepare
  and inspect requests but cannot approve or deliver them.
- Added private/group profiles, annual/pair reports, and XLSX/SQL/ChatLab JSON
  and JSONL exports to the unified UI and API. Added a default-off relationship
  footprint view and UTC+08 daily timeline; the derived output contains counts
  only, not message bodies. Moments, notifications, account lock, and client
  interception have explicit unavailable reasons; scheduled exports are now a
  separate default-off local capability.
- Fixed Windows portable staging/hidden imports for `integrations.qq.sender`.
  Added analytics to the reproducible local check. Updated the MCP contract,
  parity matrix, and roadmap to distinguish synthetic verification from
  real-device and source-data limits.

### Verification

- Clean temporary Python 3.12 environment: `scripts/check-local-hub.ps1` passed
  **177 tests**, JavaScript syntax, Python compilation, synthetic 25,000-message
  search/pagination, concurrent WAL writes, backup/restore, and `git diff
  --check`.
- Built both Windows x64 portable executables with pinned dependencies and the
  hash-verified bundled QCE v6.3.2 runtime. PyInstaller archive inspection
  confirmed both executables include `hub.sending` and `integrations.qq.sender`.
  Scanned 10,214 packaged files; no chat database, account settings, source
  credentials, or key files were present.
- The Windows application-control policy blocked launching the built EXE, so
  the isolated package startup/WebUI smoke test remains unverified. No attempt
  was made to bypass that policy.
- QQ adapter/service tests use synthetic state and a fake HTTP opener. No
  real QQ account, private chat database, WeChat/WeCom source database, or
  local account credentials were read, and no message was sent.

### Remaining acceptance

- P9-02 requires the user to run the QR login and explicitly test a private and
  group message on their own QQ account; P13 has no verified WeChat/WeCom send
  adapter. Real sends are not performed by automated tests.
- P11 still lacks full WeFlow resource management, bulk decryption, and some
  media-specific workflows. P12 Moments, desktop notifications, account lock,
  and high-risk source interception remain unavailable with reasons.
- P14 still needs the portable EXE startup smoke test on a machine where local
  application-control policy permits running this unsigned build.

### 2026-10-11 release recheck

- Rebuilt the current source into
  `output/windows/LocalMessageHub-portable-20261011-v2-final` with bundled
  Hub and standalone MCP executables plus the pinned QCE/NapCat runtime.
- Static package audit covered 10,218 files. It found no database files,
  account/settings configuration, or private key/certificate files; the Hub,
  analytics, auto-export, sending, QQ sender, and MCP executable payloads are
  present.
- Re-ran the complete local check: 184 tests passed. The 25,000-message
  benchmark completed in 1.305 s ingest, 0.070 s first search, 2.759 s for 50
  pages, 0.085 s backup, and 0.230 s restore; concurrent WAL writes had no
  failures.
- The current machine's application-control policy still prevents a package
  startup smoke test, so the rebuilt EXE is not runtime-accepted here.

## 2026-10-11 - P8 persisted capability management

### Scope

- Added a shared capability registry with independently persisted switches for
  supported Hub modules and explicit unavailable reasons for WeFlow features
  not yet migrated. Global search and Settings remain core and cannot be
  disabled; the settings page exposes both available switches and unavailable
  modules.
- HTTP handlers gate restricted read/write routes before work begins. Sync,
  import, QQ roaming and MCP entry points also check capabilities; disabling
  imports requests cancellation without deleting staged or indexed data.
- MCP filters unavailable tools, checks permissions again on direct tool and
  resource calls, and refreshes persisted permissions while the stdio process
  is running. The generated client allowlist follows the same switches.
- Closing a module changes settings/entry-point availability only; it does not
  delete indexed messages, contacts, attachments or backups.

### Verification

- `python -m py_compile hub\capabilities.py hub\server.py hub\mcp_server.py hub\test_server.py hub\test_mcp.py`: passed.
- `node --check webui\app.js`: passed.
- Six focused P8 tests passed, covering settings persistence/reopen, unavailable
  reasons and rejected enablement, preserved index data, denied direct HTTP
  metadata/sync calls, and MCP hidden-tool/direct-call/runtime permission checks.
- `python -m unittest discover -s hub -p 'test_*.py'` ran 123 tests but the
  system Python environment lacked the declared `qrcode` test dependency;
  two QQ QR tests errored for that environment limitation. This does not affect the focused P8
  suite, but the full suite should be rerun in the documented CI environment.
- No real chat data, local source databases or account credentials were read.

## 2026-10-11 - V2 WeFlow parity baseline and CI dependency audit

### Scope

- Compared WeFlow `main` (`2f23e72cebf7f309a0f4d129307d16d7b0cd41c3`, package
  5.0.0) with Hub `main` (`8f45f2525c432ba81d6125b8d728f0ae5517ef5d`) and the
  current workspace. Added `docs/weflow-gap-matrix.md` with actual unified Hub
  status, upstream entrypoints, reuse direction and synthetic acceptance for
  each V2 capability.
- Explicitly distinguished WCDB local message insertion/edit/deletion, WeFlow
  HTTP SSE event push, and real IM delivery. The first two do not send a message
  to a chat recipient; Hub source readers remain read-only.
- Clean-checkout dependency review found `integrations.wechat.test_cursor` and
  `test_media` import `cryptography` and `pilk-nogil`, but
  `requirements-ci.txt` omitted them. Added bounded public test dependencies;
  no private account/runtime data is required by CI.
- Updated the roadmap with P7-P14 acceptance IDs. Only P7 is closed; capability
  management, QQ sending and feature parity remain unstarted.

### Verification

- Initial invocation with system Python failed because that environment had
  none of the project test dependencies installed. The first clean temporary
  environment then exposed the missing `pilk-nogil` decoder dependency. With
  the bundled project Python, `scripts/check-local-hub.ps1` passed: **143
  tests**, JavaScript syntax, Python compilation, 25,000-message synthetic
  benchmark, concurrent WAL write, backup/restore and `git diff --check`.
- Reinstalled `requirements-ci.txt` into a clean temporary Python 3.12
  environment and reran `scripts/check-local-hub.ps1`: **143 tests passed**;
  JavaScript syntax, Python compilation, synthetic benchmark and
  `git diff --check` passed. The expected simulated QQ source-failure diagnostic
  appeared, but tests and the script exited successfully.
- Clean-environment benchmark: 25,000 synthetic messages ingested in 1.319 s;
  initial FTS search 0.069 s; 50 pages/25,000 rows 2.670 s; 200 concurrent QQ
  writes without WAL errors; backup 0.082 s and restore 0.226 s.
- No real chat data, local source databases or account credentials were read.

### Remaining roadmap work

- P8 persisted capability registry and server-side permission enforcement.
- P9/P10 simulated-first QQ text sending and MCP prepare/approval/status.
- P11-P14 feature migration, platform validation and release acceptance.

## 2026-10-10 - Durable import jobs

### Scope

- Added the v3 `import_jobs` table with source-file SHA-256 fingerprints,
  normalized validation summaries, durable states, progress counters and
  committed-row checkpoints.
- Confirmed JSON/CSV imports now stage normalized records under the local Hub
  storage directory, then ingest in 500-message transactions. Cancellation is
  observed at batch boundaries; a crash replays only the last uncheckpointed
  batch, which is safe because message ingestion is idempotent.
- The Hub resumes queued/running jobs at startup, retains failed/cancelled
  staging data for diagnosis, and blocks backup restore while imports are active.
- Added local status/list/cancel APIs, a task history/progress UI, and a read-only
  MCP `list_imports` tool. No import content is logged or sent externally.
- Expanded the existing statistics view into the overview dashboard with live
  source counts/status, local SQLite+WAL size and the five latest import jobs.

### Verification

- Full focused backend/source test suite: **95 tests passed**, including restart
  recovery, cancellation before the first batch, HTTP job state, MCP status and
  existing WeChat/WeCom/QQ reader tests.
- `node --check webui/app.js`, Python `py_compile`, and `git diff --check`: passed.

### Remaining roadmap work

- Stable cursor pagination, dashboard, Hermes client example, and concurrent
  MCP/WAL access are now verified. MCP settings remain open, along with local
  metadata/audit, source checkpoints, Telegram import and reproducible Windows
  packaging.

## 2026-10-10 - Stable cursor pagination and MCP client coverage

### Scope

- Added opaque URL-safe continuation cursors to unified search and per-
  conversation message listing while preserving offset pagination for existing
  clients. Cursors use timestamp plus SQLite row id and are bound to the query
  scope; using one with different filters is rejected.
- Wired cursor state through HTTP search/message endpoints, WebUI next/previous
  search pages and load-earlier messages, and MCP `search_messages` /
  `list_messages`. Exports intentionally ignore page cursors and remain complete.
- Added regression tests for same-timestamp ordering, inserts between pages,
  cursor scope validation, HTTP continuation, MCP continuation and concurrent
  MCP reads during SQLite WAL writes.
- Documented the standalone MCP server for Hermes, including an explicit
  read-only tool allowlist and opaque cursor behavior.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_store hub.test_server hub.test_mcp -q`
  - Result: **74 tests passed**, including concurrent MCP reads while a second
    process committed 80 synthetic messages, malformed JSON handling and a
    check that message text is absent from MCP stderr.
- Full import/search/MCP/WeChat/QQ focused backend suite:
  - Result: **100 tests passed**.
- `node --check webui/app.js`, Python compilation, and `git diff --check`:
  passed.

### Remaining roadmap work

- MCP settings UI, local metadata and write authorization/audit, Telegram
  import, adapter-level incremental checkpoints, delete-local-data workflow,
  clean Windows packaging and recorded performance/recovery measurements.

## 2026-10-10 - MCP settings and privacy-preserving operation history

### Scope

- Added schema v4 with a bounded MCP operation table. Calls store only tool
  name, outcome and timestamp; arguments and chat content are never persisted.
- Added `/api/mcp` to report bundled server availability, stdio/read-only mode,
  a machine-local Hermes config, and the latest operation records.
- Added a Settings section with the MCP lifecycle/permission state, one-click
  config copy, and recent call history.

### Verification

- Store, HTTP and MCP tests cover operation-log bounds/content omission, local
  Hermes settings response, schema migration, and successful/failed call records.
- Full import/search/MCP/WeChat/QQ focused backend suite: **102 tests passed**.
- `node --check webui/app.js`, Python compilation and `git diff --check`:
  passed.

## 2026-10-10 - Platform capability matrix

### Scope

- Documented per-platform history boundaries, cached-media availability,
  adapter checkpoint status and export limits in one comparison matrix.
- Explicitly distinguished idempotent indexing from true incremental source
  acquisition; only QQ's bounded roaming job currently persists scan progress.

### Verification

- Reviewed the matrix against the documented local readers and the current
  adapter/checkpoint roadmap. No source data was read or changed.

## 2026-10-10 - Local metadata schema

### Scope

- Advanced the owned SQLite schema to v5 with separate conversation metadata
  (notes, tags and archive state), contact identity links and a general
  operation log. Imported message columns remain unchanged.
- Kept the bounded MCP read-operation history distinct from the future
  attributable management audit trail.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_store -q`
  - Result: **41 tests passed**, including assertions that metadata and audit
    tables are separate from message rows.
- Full import/search/MCP/WeChat/QQ focused backend suite after schema v5:
  **103 tests passed**.
- `node --check webui/app.js`, Python compilation and `git diff --check`:
  passed.

## 2026-10-10 - Roadmap, recovery and JSON/CSV import

### Scope

- Inspected the existing Electron/WebUI + Python hub and local WeChat,
  WeCom and QQ readers; did not inspect `.local`, `.runtime`, private databases,
  keys or live chat records.
- Kept the current architecture. The Rust/Tauri default in the taskbook does
  not fit this repository's already integrated Electron and Python source
  readers; a parallel rewrite would not improve the user's current app.
- Added `docs/roadmap.md` with acceptance-based IDs and truthful statuses.
- Added local SQLite online backup/download and upload/restore controls in
  Settings. Restore validates integrity, version and required schema columns,
  waits for active sync/history jobs, and snapshots the current index first so
  a failed restore rolls back. Backups contain only the Hub index, not source
  application databases, credentials or media cache.

- Added JSON/CSV upload preview and normalization for the Hub's own export
  schema and common English/Chinese field aliases. Invalid records are
  isolated and reported, writes are idempotent by the existing composite
  message key, and the UI requires a fresh preview after changing file/platform/
  account. Uploads are currently synchronous and capped at 256 MiB; persistent
  jobs, cancellation, restart recovery and streaming JSON remain open work.
- Added an optional SQLite FTS5 trigram index with transactional triggers and
  rebuild-on-upgrade, retaining escaped literal LIKE as the final matcher and
  the short-term fallback. Search results now open an 8-message neighborhood
  on either side of the selected hit; the hit is highlighted in the chat view.
- Added an independent read-only MCP stdio process with bounded tools for
  search, conversations, messages, contexts, contacts and indexed source counts.
  It uses the shared SQLite file, emits protocol-only stdout, and has a Codex
  setup guide. Import-job status and stable cursor pagination are not exposed
  yet; no write tools exist.

### Verification

- `integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_imports hub.test_mcp hub.test_store hub.test_server integrations.wechat.test_media integrations.qq.test_provider -q`
  - Result: **92 tests passed**.
- `node --check webui/app.js`
  - Result: passed.
- `integrations/wechat/runtime/venv/Scripts/python.exe -m py_compile hub/imports.py hub/mcp_server.py hub/store.py hub/server.py`
  - Result: passed.
- `git diff --check`
  - Result: passed.
- `npm run typecheck`
  - Result: not run successfully; this checkout has no `node_modules`, so `tsc`
    is unavailable. The root package currently describes the upstream WeFlow
    Electron frontend, not the standalone `webui/` app.
- Rust toolchain check:
  - Result: `cargo` and `rustc` are not installed. No Rust workspace/build is
    claimed.

### Known gaps

- Source adapter incremental checkpoints, MCP write authorization, local-data
  deletion and reproducible Windows packaging are not implemented.
- Telegram Desktop JSON currently has a tested parser, but Telegram is not
  registered as a hub platform and cannot yet be imported end to end.
- Performance/recovery measurements and a private-data-free CI workflow remain
  open.
- QQ roaming history is bounded to the available private-chat endpoint and
  cannot prove server-side completeness. WeChat/WeCom show local snapshots and
  locally cached media, not unavailable server archives.

### Next

1. Add a deliberate MCP write opt-in with validated tools and durable audit.
2. Add true per-source incremental checkpoints and recovery evidence.
3. Finish deletion, Telegram end-to-end registration, Windows packaging and CI.

## 2026-10-10 - Local conversation/contact management

### Scope

- Connected v5 local metadata to conversation and contact queries. The WebUI
  now edits conversation notes, tags and archive state and links/unlinks
  contacts through a shared identity ID.
- Added local same-origin management endpoints and a bounded operation history.
  Audit entries record action/entity/time only; note text and chat content are
  excluded. These operations change only Hub-owned metadata tables.
- Added a read-only Telegram Desktop JSON parser for chat/message/date/sender
  and media metadata. Unsafe attachment paths are omitted; exported content is
  never opened or executed. It remains unregistered in the Hub platform model.

### Verification

- Store and HTTP management tests: **79 passed**, including source-message
  immutability, metadata roundtrip, identity link/unlink, audit-content privacy
  and cross-origin rejection.
- Telegram adapter tests: **4 passed**.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **112 tests
  passed**.
- Python `py_compile`, `node --check webui/app.js`, and `git diff --check`:
  passed.

## 2026-10-10 - Reproducible checks and local data deletion

### Scope

- Added a Windows GitHub Actions workflow and one PowerShell entry point for
  synthetic backend tests, WebUI JavaScript syntax, Python compilation and
  whitespace checks. CI installs only public test packages and does not need
  local runtime state or account data.
- Added a two-step, same-origin local index deletion flow. It pauses automatic
  sync, requests active import/QQ roaming cancellation and removes only the
  Hub SQLite index, owned import/backup files and generated QQ roaming folders.
  Platform source data, account settings and unrelated storage files are
  retained and reported.

### Verification

- Clean-checkout PowerShell script: **94 tests passed**; static checks passed.
- Local deletion API/storage tests: **2 passed**, including confirmation,
  index recreation, backup/import/roaming cleanup, preservation of unrelated
  files/configuration, and retained-data reporting.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite after the deletion
  workflow: **117 tests passed**.

## 2026-10-10 - Opt-in MCP metadata writes

### Scope

- Kept MCP read-only by default and added a local Settings opt-in that is read
  by each newly started stdio process. The generated client allowlist changes
  with the switch.
- When enabled, three bounded tools can edit Hub-owned conversation metadata
  and contact identity links. They cannot alter imported message rows or
  platform databases. Every mutation writes an action/entity/time audit record;
  the separate MCP log stores tool name and outcome only.
- Settings labels the effective permission and notes that an MCP client must
  restart after changing its tool surface.

### Verification

- Added tests for hidden-by-default tools, denial without side effects,
  persisted local opt-in, tool annotations, allowed metadata mutations and
  unchanged source message content.
- Focused MCP/server/store tests: **85 tests passed**.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **115 tests
  passed**.
- Python `py_compile`, `node --check webui/app.js`, and `git diff --check`:
  passed.

## 2026-10-10 - Telegram end-to-end import

### Scope

- Registered Telegram as an index/query platform while keeping native sync and
  the connected-source list limited to WeChat, WeCom and QQ.
- Added a dedicated Telegram Desktop JSON import path to the shared import API
  and WebUI. Preview exposes unsafe attachment-path warnings; safe relative
  paths remain metadata and are never opened from the export directory.
- Added HTTP preview/job tests and proved imported Telegram messages appear in
  both Telegram-scoped and all-platform search. The import's account override
  takes precedence over exported personal metadata.

### Verification

- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **121 tests
  passed**.
- This completes P6-02. P6-03 remains open because native readers still lack
  persistent source cursors. P6-06 remains open because the current Electron
  package is still the upstream WeFlow app and excludes the Hub and local-only
  QQ runtime; see the roadmap gate before making any release claim.

## 2026-10-10 - Synthetic performance and recovery baseline

### Scope

- Added `scripts/benchmark-local-hub.py`, a reproducible benchmark over a
  temporary database with 25,000 synthetic bilingual messages. It measures
  initial FTS search, all stable-cursor pages, a second SQLite connection
  writing while the reader searches, and online backup/restore.
- Existing tests cover interrupted import resumption, backup rejection and
  rollback. The benchmark verifies recovered row counts; no source chat data is
  read or written.

### Verification

- Windows 11, Python 3.12.10, SQLite 3.49.1 with FTS5 trigram: 25,000-row
  ingest **1.347 s**; FTS search **0.074 s**; 50 cursor pages of 500 rows
  **3.577 s**; online backup **0.149 s**; restore **0.391 s**.
- Concurrent WAL writer committed **200** synthetic QQ rows with no errors;
  backup restore recovered all **25,000** WeChat rows and the **200** pre-backup
  QQ rows. Post-backup data was correctly absent after restore.
- `scripts/check-local-hub.ps1`: **98 tests passed**, plus JavaScript syntax,
  Python compilation and `git diff --check`.
- P6-07 remains in progress until P6-03 source-cursor interruption/replay is
  implemented and measured.

## 2026-10-10 - Sync recovery and Windows source bundle

### Scope

- Added durable per-conversation offsets for WeChat and QQ. Each message page
  and its next offset commit in the same SQLite transaction, so a failed page
  does not advance the checkpoint. A restarted Hub resumes an incomplete scan.
- These providers expose offset pagination rather than stable source cursors;
  a completed sync still starts over, and insertions during a partial scan can
  shift offsets. WeCom remains a full materialized snapshot. This is recovery
  progress, not proof of true incremental synchronization.
- Added an explicit Windows source-portable ZIP staging script with runtime and
  private-state exclusions. It extracts to a temporary path and smoke-tests the
  Hub UI, source list, isolated storage and read-only MCP API.
- The ZIP is not a self-contained app: it requires system Python, Node/npm and
  first-run network access, and does not include NapCat/QCE. The Electron
  builder still targets the retained upstream WeFlow app.

### Verification

- `scripts/check-local-hub.ps1`: **102 tests passed**; JavaScript syntax,
  Python compilation, synthetic benchmark and `git diff --check` passed.
- Benchmark: 25,000 synthetic rows ingested in **1.276 s**; FTS search
  **0.066 s**; 50 cursor pages **3.584 s**; WAL writer committed 200 rows;
  backup **0.150 s** and restore **0.409 s** with expected recovery counts.
- Hub restart tests prove WeChat and QQ resume at their last committed offset;
  storage tests prove checkpoint isolation and message/checkpoint rollback.
- Full import/search/MCP/store/HTTP/WeChat/QQ/Telegram suite: **125 tests
  passed**. Rejected short POST bodies are drained within the existing size
  bound before returning 403, avoiding intermittent Windows connection resets;
  cross-origin and form-post rejection tests pass in the full suite.
- P6-03, P6-06 and P6-07 remain open; see `docs/roadmap.md` for release gates.

## 2026-10-10 - Self-contained Windows package and durable WeChat cursor

### Scope

- Added a Windows x64 package build that embeds the Hub, UI assets, standalone
  MCP executable, and SHA-256-verified official NapCat/QCE v6.3.2 shell. Runtime
  startup uses app-local storage and performs no package install or download.
- Added WeCom first-run configuration and moved integration settings/media
  defaults under `%LOCALAPPDATA%\LocalMessageHub` for the packaged application.
- Persisted WeChat's stable `(sort_seq, shard, local_id)` cursor atomically with
  each imported batch. Completed syncs continue from the committed cursor.
- Defined and tested source-specific reconciliation: QQ's successful full scan
  restarts from page zero; WeCom replays its local snapshot and does not delete
  rows absent from a potentially incomplete cache. No zero-rescan promise is
  made for either source.
- Roadmap acceptance is complete: P6-03, P6-06, and P6-07 now have evidence,
  with source-specific limitations recorded in the capability matrix.

### Verification

- `scripts/check-local-hub.ps1`: 135 tests passed; WebUI syntax, Python compile,
  synthetic benchmark, and `git diff --check` passed.
- Windows portable package smoke test passed against the built executable:
  HTTP UI + Lucide, three platform entries, isolated storage, and read-only MCP.
- A separate delivery audit reran the packaged Hub against an empty temporary
  store and completed `initialize`/`exit` through the bundled standalone MCP
  executable over stdio. No real account or chat data was used.
- QQ reconciliation tests prove a fresh full pass starts at page zero and picks
  up new messages; WeCom tests prove ID-based update/insert behavior without
  deleting an indexed row omitted from a later local snapshot.
- Build runs from a temporary staging tree that excludes private state. No real
  account credentials, chat database, or chat records were accessed.
- The final portable ZIP contains 10,258 entries and is 161,431,721 bytes;
  SHA-256: `9D5EA3386213ADD4B04EAED2854E69B6E296BF5BFAD20AE92E56B450CE1313BC`.
  Archive enumeration and required-file checks passed, and a content scan found
  no local account/data files or known machine/user-specific markers. The smoke
  test was run against the packaged directory before ZIP creation, not after
  extracting the ZIP.

## 2026-10-11 - QQ resource export and attachment cleanup

- Flattened QQ roaming resources into individual media-library entries and
  retained stable selections across pages, capped at the export service's 100
  item limit. Search, platform, and view changes clear selection.
- Resource jobs now resolve QQ roaming attachments by validated resource index,
  including resources attached to a text message. Local index cleanup pauses
  and joins active archive jobs, removes Hub-managed resource job folders, and
  preserves unrelated files in that directory.
- Added regression coverage for QQ text-message attachments, privacy cleanup,
  preserved unrelated files, and paused job admission.
- Focused resource/server checks: **8 tests passed**. The full isolated check
  ran **192 tests** and passed frontend syntax, Python compilation, synthetic
  25,000-message search/paging, WAL concurrency, backup/restore, and
  `git diff --check`. Benchmark: ingest 1.338 s, first FTS search 0.071 s,
  50 cursor pages 2.733 s, backup 0.088 s, restore 0.233 s.
- Rebuilt `output/windows/LocalMessageHub-portable-20261011-p11-final2`;
  static scan covered 10,222 files, found no database/config/key/certificate
  files, and confirmed both Hub/MCP executables plus the bundled
  `hub.resources` module. Executable smoke test was skipped because Windows
  the environment's application-control policy blocks launching the unsigned build.
- A separate isolated synthetic preview verified that two QQ roaming resources
  attached to one image message appear as two independently selectable media
  entries and the library reports message and attachment counts separately; its
  server and browser tab were closed after verification.

## 2026-10-11 - Offline WeChat image decryption boundary

- Kept the repository's WeChat V1/V2 image decryption implementation, but the
  Hub media adapter now disables image-AES process-memory scanning and
  client-driven original-image downloads.
- Persisted image keys are account-scoped under the Hub's ignored runtime cache;
  they are no longer written to `.local/wechat.json`. Source databases and media
  remain read-only; the general WeChat reader's separate SQLCipher key reading
  behavior is documented accurately.
- Added synthetic encrypted V1/V2 fixtures covering XOR/AES handling, PKCS7
  full-block padding, and container footer stripping. No real account paths or
  media were used.
- `scripts/check-local-hub.ps1`: **196 tests passed**, plus WebUI syntax, Python
  compilation, 25,000-message search/pagination, WAL concurrency,
  backup/restore, and `git diff --check`. Final rerun benchmark: ingest 1.215 s,
  first FTS search 0.066 s, 50 cursor pages 3.485 s, backup 0.145 s, restore
  0.531 s.
- P11 remains in progress: offline WeChat image decoding is now covered, while
  full cross-platform resource-library/decryption parity and real-cache
  acceptance remain open.
- Rebuilt the portable bundle at
  `output/windows/LocalMessageHub-portable-20261011-p11-wechat-offline` and
  statically audited 10,222 files: no SQLite databases, account configuration,
  private keys, or certificates; Hub, standalone MCP, QQ sender, resource, and
  WeChat provider modules are present. Runtime smoke testing remains blocked by
  the environment's application-control policy for unsigned executables.

## 2026-10-11 - Read-only WeChat Moments

- Added schema-checked parsing of the local `SnsTimeLine` table from `sns.db`,
  with bounded XML payload, row and aggregate scan-byte budgets. It parses text,
  author, timestamps, comments, likes, location and attachment references;
  source databases are opened through the existing read-only decrypted runtime
  copy, and remote media URLs are never fetched or rendered.
- Added a default-off capability, local HTTP endpoint, WebUI filters/paging and
  capability-gated MCP `search_moments` tool. Missing/locked/unknown local data
  returns unavailable rather than presenting synthetic or partial data as live.
- Added synthetic in-memory SQLite/XML tests and HTTP/MCP capability tests.
  Focused run: **84 tests passed** across WeChat Moments, MCP and Hub HTTP;
  full local acceptance: **208 tests passed**, plus JS syntax, Python
  compilation, performance, concurrency, recovery and diff checks.
- Real-account WeChat schema and local attachment resolution remain unverified;
  P12 stays in progress. Account-lock browser credential entry remains a
  user-run handoff, and no source-client mutation or message delivery occurred.
- Rebuilt `output/windows/LocalMessageHub-portable-20261011-moments` with the
  updated Moments backend/MCP/WebUI. Static audit covered 10,236 files and
  405,094,938 bytes; found no database/account config/key/certificate/log files
  or scanned user/temp-path markers. Hub SHA-256:
  `8C14304AA6304FCD458127BF806754B4126F396D7B18DE96BF915F269ADA7375`; MCP
  SHA-256: `F9D492AC680431CBFA9E59A67F0070BBA4072F4A29B695D6DBEF7DCE499CDC76`.
  Portable EXE smoke test was skipped because local application control blocks
  unsigned executable startup; package build and static audit passed.

## 2026-10-11 - Moments filtered export

- Added a Moments-specific JSON export endpoint and WebUI action that exports
  the full filtered result, not only the visible page. It reuses the existing
  local-only schema reader, `moments` capability gate, 100,000-row and 32 MiB
  source scan limits, and enforces a 32 MiB serialized output ceiling.
- Preserves post text, author, timestamp, likes, comments and location while
  removing media URLs, thumbnails, keys, tokens, encryption indices and hashes
  from downloaded JSON. No remote media is fetched.
- Updated the WeFlow gap matrix and P12 acceptance notes. Real account schema,
  avatar resolution and local Moments media resolution remain open.
- Synthetic HTTP regression coverage verifies capability denial, full-result
  request bounds, filter propagation, retained fields, credential stripping,
  and rejection above the 32 MiB output ceiling.
- `scripts/check-local-hub.ps1`: **210 tests passed**, plus WebUI syntax,
  Python compilation, 25,000-message search/pagination, WAL concurrency and
  backup/restore. Benchmark: ingest 1.197 s, first search 0.071 s, 50 pages
  2.597 s, backup 0.078 s, restore 0.217 s. `git diff --check` passed.
- Rebuilt `output/windows/LocalMessageHub-portable-20261011-moments-export`
  with the updated export API/UI. Static audit covered 10,236 files and
  405,107,818 bytes; no database/account config/key/certificate/log files or
  scanned user-path markers. Hub SHA-256:
  `150EE08B87786B7DEDB24A60741EED26C00AD3E8C04ADEC72513854C777227E1`; MCP
  SHA-256: `A655E60FA78D142DF8FDC89F66D7AA3DDF219D210C8C8292641111D32C3BEE03`.
  Runtime smoke test was skipped because local application control blocks
  launching unsigned executables.

## 2026-10-11 - Moments multi-format export

- Added dedicated JSON, CSV, Markdown, TXT and standalone HTML renderers for
  Moments. They retain local source provenance, author, timestamp, location,
  likes and comments while stripping all remote media URLs and credentials.
- CSV export escapes spreadsheet formula prefixes and HTML export escapes all
  user-controlled fields. The Moments view now lets the user select a format;
  the server validates it, preserves the 32 MiB output limit and keeps the
  existing capability gate.
- Added renderer and HTTP tests for content types/filenames, unsupported format
  rejection, provenance, formula injection, HTML escaping and media-secret
  stripping. Focused run: **79 tests passed** across Moments exports, reader
  and Hub HTTP suite; the full check passed **211 tests**. Benchmark: ingest
  1.220 s, first search 0.067 s, 50 pages 2.612 s, backup 0.079 s, restore
  0.216 s. JavaScript/Python checks, WAL concurrency and backup/restore passed.
- Fixed the portable build's Python version probe to avoid PowerShell stripping
  quoted format-string arguments; Python 3.12 is still strictly required.
- Built `output/windows/LocalMessageHub-portable-20261011-moments-multiformat`.
- Rebuilt final package at
  `output/windows/LocalMessageHub-portable-20261011-moments-multiformat-v3`.
  Static inventory covered 10,240 files (405,140,803 bytes); no account/database,
  key/certificate/log files or scanned machine/user path markers. Hub SHA-256:
  `586C6BBA58B5573726E90112B09A3D812F5ECC82E95A3620545BFD4F71332DAB`; MCP
  SHA-256: `35F2263AFBAB1332FCC8C47041A7D6D337CB5818CA5E3C936EC3B603E9CDD241`.
  Runtime smoke test remains unverified because application control blocks
  unsigned executable startup.

## 2026-10-11 - Objective relationship milestones and resource bounds

- Added capability-gated, read-only relationship milestone aggregation for
  private chats. It reports only objective counts from the current local index
  (messages, active days, streaks, media, and calendar span); groups are excluded
  and no message bodies or semantic journey inference are returned.
- The relationship timeline dialog now shows milestone progress alongside its
  daily activity view. UTC+08 calendar dates, account/platform scope and module
  default-off behavior remain explicit.
- Attachment ZIP copies now enforce per-file/aggregate limits during each read
  chunk and check cancellation between chunks. Synthetic regression tests cover
  a source that grows after `stat` and cancellation mid-copy.
- NapCat OneBot configuration updates now use a flushed same-directory
  temporary file and atomic replacement; write failures preserve the prior
  configuration and remove the temporary file.
- Focused verification: **92 tests passed** across analytics, resources, HTTP
  and QQ sender; JavaScript syntax, Python compilation and `git diff --check`
  passed. The full `scripts/check-local-hub.ps1` check then passed **215 tests**,
  25,000-message search/pagination, WAL concurrency and backup/restore. Benchmarks:
  ingest 1.257 s, first search 0.070 s, 50 pages 2.626 s, backup 0.080 s,
  restore 0.219 s.
- Real QQ private/group delivery, WeChat/WeCom sending, Moments account schema,
  avatar/media resolution, full WeFlow media parity and unsigned EXE runtime
  acceptance remain external account/device or system-policy gates; none are
  claimed complete by synthetic tests.
- The first updated portable build exposed absolute stage paths in PyInstaller-
  generated bytecode caches. The builder now disables bytecode-cache writes
  during analysis. Rebuilt `output/windows/LocalMessageHub-portable-20261011-
  relationship-resources-v5`; static inventory covered 10,233 files and
  405,055,463 bytes, with no account/database/key/certificate/log files and no
  scanned username/workspace path markers. Hub SHA-256:
  `B06A2CAB47BCB15653CFC4F2825F572EB802F509F8AC040607C5236D968912E8`; MCP
  SHA-256: `B4F2AF18726290D88DC0826D8CB84B1B4762A8DFA7BC725C9F0649DB864659B5`.
  EXE runtime smoke remains unverified because local application control blocks
  unsigned executable startup.
## 2026-10-11 - Media library date filters

- Added UTC+08 start/end date filtering to the unified media library, reusing
  the same inclusive whole-day bound parser as cross-platform message search.
- The date bounds now flow from the WebUI through `/api/media` into the
  SQLite media query and combine with platform, attachment type, and
  conversation-title filters.
- Synthetic store and HTTP tests cover inclusive day boundaries, combined
  filters, and empty ranges. The full `scripts/check-local-hub.ps1` check passed
  **216 tests**, frontend syntax, Python compilation, and 25,000-message
  search/pagination, WAL concurrency, backup and restore checks.
- Full WeFlow media/decryption parity and real-device/source-cache acceptance
  remain open; this addition does not change the read-only source policy.

## 2026-10-11 - Portable package with media date filters

- Rebuilt the Windows x64 portable Hub and standalone MCP with the updated
  WebUI, pinned dependencies, official QCE v6.3.2 runtime, and source/license
  notices at `output/windows/LocalMessageHub-portable-20261011-media-date-v6`.
- Static inventory covered 10,233 files (405,057,647 bytes). No database,
  account/settings config, key, certificate, or log files were present, and a
  scan found no absolute workspace path marker. Hub SHA-256:
  `6A4F792BE7FEED3EC7080716A8934B5277B00E2EA6514EB75C4C777667160F03`;
  MCP SHA-256: `53CDBF6222DCBFD642BD34E822E467121B678DFFC82D245D5F503E62020AE465`.
- Startup smoke was skipped because unsigned executable startup is blocked by
  this host's application-control policy; this package is not claimed runtime-
  verified.

## 2026-10-11 - Offline Moments cache media and MCP image return

- Added a read-only WeFlow-compatible Moments cache resolver. It removes
  volatile `token`/`idx` query values for cache-name matching, accepts only
  verified image/video signatures inside the configured existing cache, and
  never fetches missing remote content. WebUI uses same-origin 15-minute
  tokens; source media credentials and URLs are stripped from results.
- Added default-off, Moments-capability-gated MCP `get_moment_media`. It accepts
  only a local token from `search_moments`, rechecks cache boundary and image
  signature, caps payloads at 8 MiB and returns images only on explicit request.
  Tokens expire after 15 minutes in both HTTP and MCP. Reads stay bounded if a
  file changes during access. Videos remain WebUI-only. Focused tests pass.
- The full local gate passed **220 tests**, Python compilation, WebUI syntax,
  `git diff --check`, and the 25,000-message benchmark with WAL concurrency,
  backup and restore. Metrics: ingest 1.207s, first search 0.067s, 50-page
  traversal 2.601s, backup 0.079s, restore 0.219s.
- Rebuilt `output/windows/LocalMessageHub-portable-20261011-moments-cache-v10`.
  Static inventory: 10,237 files / 405,121,890 bytes; no database, account
  state, key, certificate, log or scanned workspace path. Two duplicated
  `config.json` files are upstream NapCat plugin defaults without user data.
  Hub SHA-256: `98EE861E80DBE2E0E61F2E9A93AB641D2C1CF49562436D8EC9438DA9192C578D`;
  MCP SHA-256: `F91E5ADADD508E3114A063757975C0E1AA06545AB6AFFF03AA60649C76E2D13B`.
- Startup smoke was skipped because this host blocks the unsigned executable.
  Hosted CI, authorized real-account/device acceptance, and remaining full
  WeFlow media/resource parity remain open; this update does not close P11,
  P12 or P14.

## 2026-10-11 - Repair source-bundle smoke and inspect hosted CI failure

- The extracted Windows source-bundle smoke initially failed because its
  curated include list omitted `integrations/qq/sender.py`, now imported by
  `hub.server`. Added the sender module to both required-file validation and
  the package allowlist so future omissions fail before startup.
- Rebuilt `output/windows/LocalMessageHub-source-v3-ci-repro.zip` (81 included
  files). The extracted package served the WebUI and all three source entries,
  used isolated temporary storage, and reported read-only MCP configuration.
- Inspected public Actions run `38101275283` through GitHub's unauthenticated
  run/job/check annotations: dependency installation passed, `Run local hub
  checks` failed, and the source-bundle step was skipped. The annotation gives
  only exit code 1; the job log API returns HTTP 403 without authentication.
  The local source-bundle omission found here is a current-worktree defect and
  is not claimed as the cause of that older hosted run.
- P14 remains open pending a passing hosted run on reviewed changes and a
  permitted runtime smoke of the unsigned standalone EXE.

## 2026-10-11 - Reconcile media/avatar matrix and rerun local release gates

- Corrected the WeFlow comparison matrix to include the implemented offline
  Moments cache image/video preview and explicit MCP image return. Clarified
  that WeChat contact avatars from `head_image.db` are still unavailable: the
  upstream reader uses WCDB native APIs, and this integration has no verified
  equivalent schema reader. The Hub does not guess that encrypted schema or
  fetch avatar CDN content for this gap.
- Re-ran `scripts/check-local-hub.ps1`: all 220 tests passed, Python and
  frontend checks passed, and the synthetic 25,000-message benchmark completed
  without WAL failures. Backup took 0.078s and restore 0.214s.
- Repeated the CI check in a fresh Python 3.12 virtual environment containing
  only `requirements-ci.txt`; all 220 tests and benchmark checks passed. The
  extracted 81-file source package also passed its WebUI/source/MCP smoke with
  this clean interpreter. This confirms current dependency completeness but
  does not identify the failure in the older hosted run.
- Rebuilt and smoke-tested the Windows source bundle at
  `output/windows/LocalMessageHub-source-v4-final-check.zip` (81 files). Its
  extracted WebUI, three source entries, isolated storage, and read-only MCP
  endpoint all responded successfully.
- These local checks do not close P14 hosted CI or unsigned-EXE runtime smoke;
  nor do they substitute for authorized real-account acceptance under P9/P12.

## 2026-10-11 - Portable backup of Hub-managed QQ media

- Added an opt-in ZIP backup containing a consistent Hub index snapshot and
  only the QQ roaming files referenced by that snapshot. It excludes source
  platform databases/caches, QCE temporary JSON, and account configuration or
  keys; the bundle is explicitly treated as sensitive local chat data.
- Restore validates regular-file entries, archive budgets, canonical managed
  paths, that every bundled file is referenced by its bundled index, manifest
  counts, file sizes, and SHA-256 digests before adding files.
  It merges identical existing files, refuses conflicting overwrites, and
  rolls back newly added media if index restoration fails.
- The focused backup and HTTP download/restore tests passed (5 tests). The full
  `scripts/check-local-hub.ps1` gate passed 225 tests, Python compilation,
  frontend syntax, the 25,000-message benchmark, WAL concurrency, backup and
  restore checks. Measured ingest at 1.207s, first search at 0.066s, 50-page
  traversal at 2.590s, database backup at 0.076s, restore at 0.216s, with no
  WAL failures. The synthetic QQ provider's expected failure-path diagnostic
  (`Source failure: qq RuntimeError`) appeared while its tests passed.
- P11 remains in progress: complete media/resource parity and real-cache
  acceptance remain open. This work does not close P14 hosted CI/unsigned-EXE
  smoke or the authorized real-account gates under P9/P12/P13.
- Rebuilt and smoke-tested the 83-file source bundle at
  `output/windows/LocalMessageHub-source-v5-media-backup.zip`; its extracted
  WebUI, three source entries, isolated storage and read-only MCP endpoint
  responded successfully.

## 2026-10-11 - Refresh the Windows portable build

- Rebuilt the current source as
  `output/windows/LocalMessageHub-portable-20261011-contact-export-v11` with
  the pinned Python 3.12 dependencies and official QCE v6.3.2 archive/runtime
  after SHA-256 verification. The unsigned executable smoke was skipped due
  to the host's known application-control restriction.
- Static inventory covered 10,241 files (405,220,104 bytes): no database,
  account configuration, key, certificate or log files; no exact workspace
  path markers. Hub SHA-256:
  `FAEF170E370C2B276DB8229E98B3AC5AC3983F54AC8976E306A37B891161E1EF`; MCP
  SHA-256: `2844E7F7DB60D819BE8E90639188F7B2566980B0FAC9894C2CC10E9D5B8FA8FF`.
- This closes the current static package audit only. The executable still needs
  isolated runtime smoke on a Windows host that permits unsigned programs.

## 2026-10-11 - Diagnose the public CI failure

- Retrieved the full log for Actions run `38101275283`. The clean runner lacked
  `cryptography`, and an MCP configuration test assumed the checkout path
  contained the Chinese product name. The current `requirements-ci.txt` adds
  `cryptography`; the test now validates stable config structure and exercises
  a neutral checkout path fixture.
- The focused MCP configuration test passed. The full local gate passed 228
  tests, Python compilation, frontend syntax and 25,000-message search,
  pagination, backup and restore checks (ingest 1.194s, first search 0.072s,
  50-page traversal 2.609s, backup 0.077s, restore 0.218s; no WAL failures).
- The fix has not been pushed, so hosted CI remains unverified. No external
  repository changes were made.
- Rebuilt the 83-file source bundle at
  `output/windows/LocalMessageHub-source-v9-ci-path-fix.zip`; its extracted
  WebUI, three platform source entries, isolated storage and read-only MCP
  smoke passed.

## 2026-10-11 - Contact and group export

- Added a contacts-view export action for JSON and CSV, respecting the active
  platform and name filter. Exports retain platform, account, unified identity
  and local profile fields; temporary avatar URLs/tokens are excluded and CSV
  formula values are hardened. Export remains behind the existing capability.
- Fixed contact ingestion so explicit `extra` profile metadata is persisted
  alongside inferred provider fields instead of being silently discarded.
- Focused HTTP/renderer tests passed. The full local gate passed 228 tests,
  Python compilation, frontend syntax, 25,000-message search/pagination and
  backup/restore checks (ingest 1.209s, first search 0.070s, 50-page traversal
  2.641s, backup 0.074s, restore 0.218s; no WAL failures). `git diff --check`
  passed.
- Rebuilt and smoke-tested the 83-file source bundle at
  `output/windows/LocalMessageHub-source-v8-contact-export.zip`; extracted
  WebUI, three platform source entries, isolated storage and read-only MCP all
  responded.
- Real-account avatar completeness, Moments schema/cache acceptance, hosted
  CI and unsigned Windows executable runtime acceptance remain outstanding.

## 2026-10-11 - Notification allowlist, blacklist and presentation controls

- Extended the existing default-off notification settings with an all-platform-
  filtered mode or an explicit conversation allowlist. Per-conversation mutes
  remain a denylist and always take precedence. Settings validation bounds and
  normalizes both lists; new SSE rows pass both platform and conversation rules.
- Added session-detail actions to allow or mute a conversation, plus settings
  controls for notification scope and standard/silent browser presentation.
  Notification payloads remain body-free, and all filters stay in local
  settings storage.
- The focused event-stream and settings tests passed. The complete
  `scripts/check-local-hub.ps1` gate passed 225 tests, frontend syntax, Python
  compilation and the 25,000-message/WAL/backup benchmark (ingest 1.196s, first
  search 0.066s, 50-page traversal 2.595s, backup 0.078s, restore 0.219s;
  no WAL failures). `git diff --check` passed.
- Rebuilt and smoke-tested the latest 83-file source bundle at
  `output/windows/LocalMessageHub-source-v7-notification-filters.zip`. The
  extracted WebUI, three platform source entries, isolated storage and
  read-only MCP endpoint responded. A dedicated browser-driven UI check could
  not start because Playwright's configured Chrome binary is absent; no browser
  installation was performed.
- This closes synthetic notification filter acceptance only. Real browser
  permission/click behavior, real-account Moments/QQ acceptance, full P11
  media parity, WeChat/WeCom sending and hosted release gates remain open.

## 2026-10-11 - Stream local media and record release gate

- Local media responses now stream files in bounded 1 MiB chunks instead of
  reading the complete attachment into memory, while retaining byte-range
  support for playback and previews. Tests cover prefix and suffix ranges,
  unsatisfiable ranges, and unsupported multi-range requests.
- The full local gate passed **216 tests**, Python compilation, frontend syntax,
  and the 25,000-message index/search/pagination, WAL concurrency, backup and
  restore checks. Measured ingest at 1.205s, first search at 0.068s, 50-page
  traversal at 2.612s, backup at 0.078s and restore at 0.215s.
- Recorded the latest public Actions run (`38101275283`, commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`) as failed at `Run local hub
  checks`; detailed logs require repository admin access.
- Runtime smoke of the unsigned Windows package remains blocked by the host
  application-control policy; local package behavior is not claimed verified.

## 2026-10-11 - Portable package with bounded media streaming

- Rebuilt the Windows x64 portable Hub and standalone MCP at
  `output/windows/LocalMessageHub-portable-20261011-media-stream-v7`, bundling
  official QCE v6.3.2 and upstream/license notices.
- Static inventory covered 10,233 files (405,064,933 bytes). No database,
  account/settings config, key, certificate, or log files were present, and a
  scan found no absolute workspace path markers. Hub SHA-256:
  `C299D718DE28827384FFFA187A227CC2FC4B75CC62DCAF50C7464FEB0D08D21D`;
  MCP SHA-256:
  `9A7718A82E96C11E55292BA7F99F3612974A998F572BB2907B7CDB4AF306D483`.
- Startup smoke was skipped because unsigned executable startup is blocked by
  this host's application-control policy; this package is not claimed runtime-
  verified.

## 2026-10-11 - Default-off local voice transcription

- Added a default-off `voice_transcription` capability with identity-bound
  `POST /api/voice/transcribe` and status API. The service only accepts an
  indexed voice message and reads audio through its platform provider; callers
  cannot submit a path or URL. Transcripts are returned to the current UI and
  are not stored in the index or written to a transcript file.
- Added an optional offline SenseVoice adapter for 16-bit PCM WAV. It uses only
  an already-installed Node/Sherpa package and the existing local WeFlow model
  directory; it never downloads either. When those optional components are absent,
  component, so status returns an explicit unavailable reason. A fake
  recognizer verifies the complete API/UI flow without external networking.
- Verification: `python -m unittest hub.test_voice_transcription -v`,
  `node webui/test-voice-transcription-render.cjs`, Python compilation and
  `node --check webui/app.js`.

## 2026-10-11 - Revoke notification streams and display cached avatars

- Existing message-event SSE connections now recheck the `notifications`
  capability before each poll and close after permission is revoked. A server
  regression test establishes a stream, disables notifications, ingests an
  event, and verifies that no event is delivered.
- The WebUI now maps only the fixed QQ, WeChat, and WeCom avatar hosts through
  the same-origin avatar cache API. It never requests those external hosts
  directly; other URL hosts, protocols, credentials, and explicit ports are
  rejected. Cache misses continue to use the initials fallback.
- Added the avatar rendering test to the documented local/CI gate. In a fresh
  Python 3.12.10 virtual environment installed only from `requirements-ci.txt`,
  `scripts/check-local-hub.ps1` passed 243 Python tests, all three UI render
  tests, compilation/syntax checks, and the 25,000-message benchmark. Benchmark
  results: ingest 1.197s, first search 0.069s, 50-page traversal 2.605s, WAL
  failures 0, backup 0.078s, restore 0.217s. `git diff --check` passed with
  only Git's LF-to-CRLF notices.
- Remaining P14 hosted CI is not run because the reviewed local worktree is not
  published. Real QQ delivery, real WeChat Moments schema/avatar/media,
  unsigned executable startup under a permissive Windows policy, and WeChat/
  WeCom local-account sending still require external acceptance or a verified
  authorized path; no such operation was attempted.

## 2026-10-11 - Rebuild and audit roadmap packages

- Rebuilt the source archive as
  `output/windows/LocalMessageHub-source-v15-roadmap-privacy-voice-avatars.zip`
  (91 entries; SHA-256
  `16969BC82EB65CAA8DF42F545D4B62EEFBD315E3CB21AE4359678A4635445C11`). Its
  extracted WebUI/source/MCP smoke passed with isolated temporary storage.
- Rebuilt the portable package as
  `output/windows/LocalMessageHub-portable-20261011-v14-roadmap-privacy-voice-avatars`.
  Static inventory covered 10,251 files (405,312,026 bytes); no database,
  SQLite, log, key, certificate, or exact workspace-path marker files/content
  were found. The three generic `onebot11.json` templates contain empty tokens.
  Hub SHA-256: `B00706B4A0CE53326607AC0AF5640AC4F1B025D459B32921D11EFB6D5691B348`;
  MCP SHA-256: `C20B3C46CB08BCF55B2BE064F65095D6048686985C69EC8436BD6E22ABE8F199`.
- Portable startup smoke remains skipped because the host application-control
  policy blocks unsigned executables. This is a static privacy audit, not a
  runtime verification; hosted CI and real-device acceptance remain open.
- The source package build ran its extracted-package smoke after generation:
  WebUI, all three source entries, isolated temporary storage, and read-only
  MCP all passed. Archive SHA-256:
  `B41E152CFD039F4AD171B72A0068430F7F281E147D4475ADE92EF2BB4894D692`.

## 2026-10-11 - Cross-platform group rosters and cached MCP images

- Added one account- and conversation-scoped `/api/conversation-members` route
  and inspector view for WeChat, WeCom, and QQ groups. Each adapter returns
  normalized member metadata with local filtering and a 1,000-member result
  cap. QQ QCE returns a full roster in one local response, so only Hub-side
  filtering/paging is possible; no claim of upstream paging is made.
- MCP `get_attachment` now validates WeCom and QQ attachment indexes and can
  return a single explicitly requested cached image. QQ cache misses do not
  trigger QCE export/download; cache paths reject traversal, symlinks, and
  files outside the configured local roots. MCP images remain explicit calls,
  capped at 8 MiB and signature checked.
- Verification: the local gate passed 267 Python tests, four UI render tests,
  compilation/syntax checks, `git diff --check`, and the 25,000-message
  benchmark (ingest 1.325s, first search 0.071s, 50-page traversal 2.952s,
  WAL failures 0, backup 0.081s, restore 0.235s). Focused roster/MCP/provider
  tests also passed. All tests use synthetic fixtures; no real account was
  queried.
- Source v19 passed extracted WebUI/API/MCP smoke, with 93 public source files
  and no forbidden private-state paths. Its SHA-256 is
  `F39185856847395346774AA43BC757C47C8D324A626816E6DC0EE1085881FD6C`.

## 2026-10-11 - Query performance before further roadmap work

- Prioritized the user's reported memory pressure in the query application.
  Backend, integrations and WebUI were inspected in parallel; unrelated desktop
  processes and user source databases were not modified.
- Indexed deep cursors, lightweight source counters, bounded WeCom query
  selection, post-ingestion snapshot release, streaming QCE JSON and common
  exports, and bounded WebUI history/transcript/token retention are implemented.
- Reproducible synthetic benchmarks and scope/tradeoffs are recorded in
  `docs/performance.md`; component allocation savings are not presented as a
  whole-process RSS reduction. Real-device roadmap acceptance remains open.
- Full gate: 285 Python tests and five UI tests passed; 25k messages traversed
  in 50 pages with matching totals, WAL failures zero, backup/restore counts
  correct. Measured ingest 1.307s, search 0.072s, traversal 3.942s, backup
  0.170s and restore 0.463s. Browser synthetic tests exercise long history.
- The existing 8767 preview was restarted with the optimized query/UI core.
  The primary 8765 service automatic-restart command was blocked by the
  execution policy; its running process was not stopped or replaced. Updated
  backend modules take effect on the next normal service restart. No attempt
  was made to bypass the policy or alter unrelated applications.
- Isolated Edge acceptance: 100 pages retain at most 500 message DOM nodes;
  Latest/context exit and desktop/mobile clicks pass. CDP native/DOM heap after
  GC was 216,404,992 -> 14,218,768 bytes, not whole-process RSS. Corrected fixed
  grid tracks to prevent the footer obscuring the Latest button.
- Resumed P11/MCP attachment work after performance acceptance: the outer
  cache-root check now accepts indexed QQ roaming images in Hub-owned
  `qq-roaming`, in addition to the normal media cache. A real provider synthetic
  regression proves no QCE/network calls occur on that path.
- After that narrow fix, 38 focused MCP/provider/path tests pass. The complete
  285-test gate preceded that last regression; five UI tests and desktop/mobile
  browser checks remain passed. No additional full-suite success is implied.

## 2026-10-11 - Continue roadmap: Moments author avatars and source audit

- Moments authors now receive an avatar only from an indexed WeChat contact
  whose platform, account ID and username all match. Missing/unknown contacts
  retain the initial fallback. The WebUI passes that URL through the existing
  strict same-origin avatar proxy; it performs no direct network fetch.
- Synthetic server tests cover exact-account matching and missing-avatar cases;
  the Moments render test verifies local proxy URL mapping. The complete local
  gate passed 287 Python tests, five UI tests, syntax/compile checks and the
  25,000-message benchmark (ingest 1.518s, search 0.084s, 50-page traversal
  2.973s, WAL failures 0, backup 0.086s, restore 0.233s). This is synthetic
  coverage; real WeChat schema/cache/avatar acceptance remains open.
- Rebuilt the current sanitized source archive at
  `output/windows/LocalMessageHub-source-roadmap-20261011-v2.zip`; extracted
  WebUI/API/MCP smoke passed using empty temporary storage (101 files).
  Static archive scan found no exact current workspace path or username marker.
  SHA-256: `3DFC6726752CBA0A99BC3C8F843F947483B58C056CB741A68EC42B1A7CF2EBC4`.

## 2026-10-11 - Continue roadmap: bounded WeChat attachment staging

- Replaced whole-file reads for locally cached WeChat videos/files with 1 MiB
  buffered copies to a same-directory temporary file followed by flush, fsync
  and atomic replacement. Staging rejects symlinked roots/files, paths outside
  the account media subtree, and output-name traversal; source cache files are
  never changed.
- Four synthetic regressions cover video chunk size/atomic cleanup, outside
  symlink rejection, cache-file copying and output path sanitization. All 18
  WeChat media tests pass.
- The complete local gate passed **291 Python tests** and **5 UI tests**.
  The 25,000-message benchmark completed in 1.271 s ingest, 0.069 s first
  search, 3.734 s for 50 pages, 0.158 s backup and 0.436 s restore; WAL had
  zero failures. WebUI synthetic retention traversed 10,000 rows with 500
  retained messages, reducing serialized payload from 6,648,891 to 332,501
  bytes. This is not a whole-process RSS measurement.
- Refreshed the sanitized source bundle as
  `output/windows/LocalMessageHub-source-roadmap-20261011-v3.zip`; extracted
  WebUI/API/MCP smoke and static private-path scan passed. Portable EXE smoke,
  hosted CI on a reviewed branch, and real-device/source-data acceptance remain
  open as listed in the roadmap. Archive SHA-256:
  `9FAB6EA4FB930EE90D7EABE3AF7B49B362A2ADE169E0FAD77ADBB148F0E18431`.

## 2026-10-11 - Continue roadmap: WeChat image format preservation

- WeChat decrypted WebP and BMP images now keep their recognized filename
  extensions so the existing local preview/MIME path can identify them. The
  legacy unsigned XOR fallback also recognizes WebP signatures. No media bytes
  or real account files were used.
- Synthetic tests verify format-specific output names/content, WebP recovery
  from legacy XOR, and rejection of non-WebP RIFF payloads. All 21
  `integrations.wechat.test_media` tests pass.
- This closes a narrow preview-format gap only. Full WeFlow media/resource
  discovery, decryption formats, and real-account acceptance remain open.

## 2026-10-11 - Roadmap verification after image format audit

- The full local gate passed **294 Python tests** and **5 UI tests**. The
  25,000-message benchmark completed in 1.211 s ingest, 0.068 s search,
  3.739 s for 50 pages, 0.164 s backup and 0.476 s restore; WAL had zero
  failures. This includes the strengthened WebP signature regression.
- Refreshed the sanitized source bundle as
  `output/windows/LocalMessageHub-source-roadmap-20261011-v4.zip`; package
  smoke, private-data scan and SHA-256 are recorded after the build.
- Real QQ private/group delivery, a verified WeChat/WeCom sender, full media
  parity, hosted CI on a reviewed branch, and unsigned EXE runtime smoke remain
  external or unsupported acceptance gates.

## 2026-10-11 - Portable package and source privacy audit

- Built `output/windows/LocalMessageHub-portable-20261011-v16-roadmap-image-formats`
  with the updated WebP/BMP handling. Static inventory covered 10,313 files
  (405,910,256 bytes); no database, SQLite, log, key, certificate, username or
  workspace-path markers were found. All three bundled `onebot11.json` tokens
  are empty. Hub SHA-256:
  `5E239B95FABF1C559762492C03CC86F4C06606A8BBB0418955A756DE9E35A2ED`; MCP
  SHA-256:
  `8FD6087477EF01815E79774BF9BBA87AFFF99AD5AB48ED050D64D101BF9DA0E7`.
- The unsigned EXE runtime smoke was skipped because host application-control
  policy blocks it. Source archive v6 (101 files) passed extracted-package smoke and its
  text-content scan found no username, local path, or private-checkout marker;
  SHA-256:
  `66DD6B0F076DE4EC4669AC294F5828BB9C53D8AF0268EE40055257910ADFC749`.
- P12 audit confirms the synthetic capability/SSE gates; real Moments schema,
  avatar and media acceptance remain open. Anti-recall remains unavailable.

## 2026-10-11 - MCP relationship footprint tools

- Added default-off, `relationship_footprint`-gated MCP tools for per-
  conversation content-free counts, UTC+08 daily timelines, and objective
  private-chat milestones. The tools use the existing normalized-index
  analytics service; disabled tools are hidden and direct calls are denied.
- Synthetic MCP tests cover capability revocation, output privacy, daily totals
  and date validation. `python -m unittest hub.test_mcp -q` passes all 16 tests.

## 2026-10-11 - Query performance and attachment export follow-up

- Full local gate passes **298 Python tests** and **6 UI tests**, including the
  regression test that asserts WeChat attachment export requests the best
  already-cached image tier while ordinary preview remains mid-tier, and the
  WeCom multi-attachment render/export-index test.
- The latest 25,000-message synthetic benchmark completed with 2.682 s ingest,
  0.123 s initial search, 5.622 s for 50 pages with pagination rechecks,
  0.128 s backup and 0.399 s restore; concurrent WAL writes had zero failures.
  Timings are host-dependent and are not whole-process RSS measurements.
- Existing query-memory bounds remain covered: browsing 100 synthetic pages
  retains at most 500 messages (332,501 serialized bytes versus 6,648,891
  before). These figures measure the WebUI fixture cache, not process memory.
- WeChat explicit export uses a complete locally cached original when available
  and safely falls back to mid/thumbnail; WeCom attachments are individually
  selectable in the media library and batch ZIP export. Full WeFlow media
  parity and real-source acceptance remain open.
- Rebuilt sanitized source archive v7 with 102 files; the package script's
  extracted WebUI/three-platform/isolated-storage/read-only-MCP smoke passed.
  SHA-256: `DEB6FFCF3FA04EE9715690B24975A83423FA77932F46D3D65D1E1D0E8E7D7D90`.

## 2026-10-11 - Roadmap transport and byte-bound verification

- Added synthetic loopback HTTP transport tests for the QQ sender using an
  ephemeral server bound only to `127.0.0.1`. They verify bearer auth, exact
  text-only group payload, successful receipt, HTTP 401 classification, and a
  one-request timeout with no retry. No NapCat or QQ client was started and no
  real message was delivered.
- Moments XML size coverage now proves the 2 MiB limit is enforced on UTF-8
  bytes even when multibyte text has fewer characters than the limit.
- The full local gate passed **300 Python tests** and **6 UI tests**. The
  25,000-message benchmark measured 0.077 s initial search, 3.185 s for 50
  pages, 0.090 s backup, 0.326 s restore, and zero WAL failures.
- Rebuilt portable output v17 and performed static privacy inventory: 10,315
  files / 405,938,109 bytes, zero forbidden database/log/key/config files, no
  username/path markers, and three generic OneBot configs with empty tokens.
  SHA-256 Hub `9F8852E2878641AAF7AA58BD0AC763AF0180F2CFD8FAE063BEA6EA715AFA0232`;
  MCP `48CAA0CF74A4DB1FA580CEC9815B3A77BB2DEB1115E9313A3836997A4576A31C`.
  Unsigned EXE startup smoke was skipped because of host application-control
  policy; static package audit does not close runtime acceptance.
- Sanitized source archive v8 (102 files) passed extracted-package smoke and a
  text privacy scan with zero username/path markers. SHA-256:
  `0A81081463674BA44FB82B9CB6576ADC927439B059C6A3BA387C256C9845FE89`.
- Rebuilt v9 after synchronizing the roadmap's byte-limit acceptance text;
  extracted-package smoke passed. SHA-256:
  `58D30D06BA9C0DA44A612047D0A30FB1DBDC3ACBE933C011AB7437D41A9C2AC6`.
- Hosted CI on reviewed current changes, live NapCat/private/group acceptance,
  Moments real schema/avatar/cache validation, complete WeFlow media/resource
  parity, authorized WeChat/WeCom sending, and permitted-host portable runtime
  smoke remain open. No external push or real-account action occurred.

## 2026-10-11 - WeCom chat attachment rendering

- Chat bubbles now render WeCom `extra.attachments` individually alongside
  the existing QQ roaming-resource path, including escaped names, type glyphs,
  bounded metadata labels, and the original per-item `resource_index` action.
  Opening an item continues through the local read-only resolver; no source
  client, network fetch, or filesystem path is exposed to the UI.
- Extended the synthetic WeCom UI fixture to assert mixed image/file entries,
  distinct sizes, and distinct attachment indices. The focused UI test passes.
- This closes the chat-timeline visibility gap only; complete WeFlow resource
  parity and live source/cache acceptance remain open.
- The full local gate passed 300 Python tests and 6 UI tests, syntax/compile
  checks, and the 25,000-message benchmark (0.109 s first search, 4.186 s for
  50 pages, 0.160 s backup, 0.500 s restore, zero WAL failures).

## 2026-10-11 - Attachment-level media filtering and paging

- The WebUI media API now projects WeCom `attachments` and QQ
  `roaming_resources` through SQLite JSON1 before applying type filters,
  counts, and stable timestamp/message/resource-index pagination. Ordinary
  WeChat media remains one row per message; the existing MCP message-level
  `list_media` contract remains unchanged.
- Synthetic coverage verifies image/file type separation, mixed-platform totals,
  stable pages crossing parent messages, JSON1 availability, HTTP response
  shape, and that WebUI does not re-expand server-projected items or lose the
  selected `resource_index` for preview/export.
- Full WeFlow resource parity and real source/cache acceptance remain open.
- Pair reports now validate the indexed conversation kind against the same
  private-conversation allowlist used by private analytics, before aggregating.
  Synthetic service and HTTP tests verify private reports still work, groups get
  HTTP 400, and unknown conversations remain 404.
- The full local gate passed 304 Python tests and 6 UI tests, syntax/compile
  checks, and the 25,000-message benchmark (0.081 s first search, 3.566 s for
  50 pages, 0.111 s backup, 0.333 s restore, zero WAL failures).
- Rebuilt the current sanitized source archive at
  `output/windows/LocalMessageHub-source-roadmap-20261011-v11.zip` (102 entries).
  Extracted-package WebUI/source/MCP smoke passed; archive text scan found zero
  local username/workspace path markers. SHA-256:
  `5A229E63877DA8AF19E5818BC2B73D794FA740B24D8E8EFB57F3BB10B90EFB61`.
- Rebuilt portable output v18. Static inventory: 10,315 files / 405,966,131
  bytes, zero database/SQLite/log/key/certificate/config files and no personal
  username/path markers. Hub SHA-256:
  `B6FDD900BD286D74116E75EA21BB018D019D932A910AEE595B9A3665BE4A7B24`; MCP
  SHA-256: `A88F7CCC2FAC7A203FC7BF77488647B7BC7BF6A7AB08368B34E610C6484A21C1`.
  Unsigned EXE smoke remains skipped under this host's application-control policy.
- Group analytics now loads all sender aggregates for the requested page in one
  SQL query instead of issuing one query per group. A traced multi-group fixture
  verifies the fixed query count while preserving the complete member arrays.
- After that optimization, the full local gate passed 305 Python and 6 UI tests;
  the 25,000-message benchmark measured 0.082 s first search, 3.239 s for 50
  pages, 0.102 s backup, 0.309 s restore, and zero WAL failures.
- Rebuilt portable output v19. Static inventory: 10,315 files / 405,970,195
  bytes, zero database/SQLite/log/key/certificate/config files and no personal
  username/path markers. Hub SHA-256:
  `B8DBB57EBE9F0E2FCAED82B91630B2972826E63831F4DF8B2B87A9C9C61A4CF6`; MCP
  SHA-256: `4D6AE093C65F6CBDBE0ACFBE81DAD674F8BB0620B974D60ECC95C5EA613FCB51`.
- Rebuilt the sanitized source archive at
  `output/windows/LocalMessageHub-source-roadmap-20261011-v12.zip` (102 entries).
  Extracted-package WebUI/source/MCP smoke passed; text scan found zero local
  username/workspace path markers. SHA-256:
  `9CE352B1D83ABCF13DAF1C89D82F2F38EF50368A344CB5413F103E038E2F6516`.
- Group analytics accepts an optional `member_limit`; the WebUI requests its
  displayed top 12 while the SQL window retains the full observed-member count.
  The default API response remains unbounded for compatibility. This avoids
  materializing every sender row in large groups for the statistics screen.
  Synthetic analytics and HTTP tests cover truncation, stable ranking, full
  counts, and the unchanged unbounded default.
- The full local gate passed 307 Python tests and 6 UI tests. The 25,000-message
  benchmark measured 0.070 s first search, 3.725 s for 50 pages, 0.156 s backup,
  0.435 s restore, and zero WAL failures.
- Rebuilt the sanitized source archive at
  `output/windows/LocalMessageHub-source-roadmap-20261011-v13.zip` (102 entries,
  422,502 bytes). Extracted-package WebUI/source/MCP smoke passed. SHA-256:
  `F0F2BEDE5B54413F6E1DAC10949C765B3362DBB2809F1AF486EC25420A834289`.
- Rebuilt portable output v20. Static inventory: 10,315 files / 405,977,219
  bytes, no database/SQLite/log/key/certificate/config files or scanned personal
  username/path markers; all three bundled OneBot templates have empty tokens.
  Hub SHA-256: `DF8A1AA2368F327A43A6D784C0DB02DDCACC9190D0227046ABA2CB028BB29674`;
  MCP SHA-256: `1D0B4D72318F8C78471276A39F3895C52BA3186FD65FB75500FE99C8E39D0BFD`.
  Unsigned EXE runtime smoke remains blocked by the host policy.
- Media-library keyword filtering now matches conversation titles, ordinary
  message filenames, and indexed WeCom/QQ attachment names in SQLite before
  COUNT and pagination. Synthetic store coverage verifies exact filename hits,
  QQ roaming filtering, and unchanged attachment paging. A naive Moments-to-
  media API merge was rejected because it would rescan decrypted source
  `sns.db` on each page; P11 still needs an explicit-refresh catalog in the Hub
  index before Moments can join a combined media query.
- Verification after the media search change: all 307 Python tests and 6 WebUI
  tests passed. The 25,000-message benchmark measured 0.070 s first search,
  3.808 s for 50 cursor pages, 0.164 s backup and 0.455 s restore, with zero
  WAL failures. `git diff --check` passed; only existing LF/CRLF warnings were
  emitted.
- Rebuilt portable v21. Inventory: 10,315 files / 405,978,795 bytes, with no
  database, credential, log, certificate, or user-specific config files; the
  three bundled OneBot templates have empty tokens and scanned text has no
  personal path markers. Hub SHA-256:
  `DFF5E7230F9F0EBDEBBB77F8A336C103EF25385879B12923A5C1B19188BEAC9C`; MCP
  SHA-256: `D28F009CE13BB683879DAC215AECA9EDBAEC556B7BE9E3E5A0BEABAC5B7623D5`.
  Unsigned EXE smoke was skipped under this host's application-control policy.
- Rebuilt sanitized source archive v14 (102 entries, 422,709 bytes); extracted
  package WebUI/source/MCP smoke passed and local-path scan found zero matches.
  SHA-256: `4BFCC703B518A2BC21105352F25DD80B6D042034B35438248C09DB3C2B42FAF9`.
- After adding the v21 inventory results above, rebuilt the sanitized source
  archive as v15 (102 entries, 422,709 bytes); extracted package smoke passed
  and privacy scan again found zero local path markers. SHA-256:
  `76484D34F9A1E6882F235FF42B9957B493396490231FDF709A91E6204601DDBC`.
- Updated `.github/workflows/local-hub-ci.yml` so hosted Windows CI also builds
  the portable package, runs its isolated runtime smoke test, and uploads the
  result as a seven-day artifact. The source package and test workflow remain
  in the same job. This workflow edit is locally reviewed only; hosted Actions
  has not run because no push was requested.
- Rebuilt sanitized source archive v16 after the CI/documentation update (102
  entries, 422,803 bytes). Extracted WebUI/source/MCP smoke passed; privacy scan
  found zero local path markers. SHA-256:
  `1B4E88FE4F4D1D29D6EA0177C20657AE5275C9D22B23D7BD82F82BD9B388215A`.
- Added schema-v8 per-account Moments catalogs to the Hub SQLite store. A
  successful explicit WeChat sync atomically replaces the sanitized catalog;
  API/UI/MCP paging and filtered exports query that local catalog rather than
  rescanning/decrypting `sns.db` on each page. Cache locators are validated and
  kept internal; media tokens are minted only when an explicit result resolves
  to a file inside the configured local cache. A missing media-cache directory
  no longer prevents text-only indexing. This does not yet combine Moments
  media with chat media-library filtering or batch ZIP jobs, and real-device
  cache/avatar acceptance remains outstanding.
- Full local gate after the catalog change: 311 Python tests and 6 WebUI tests
  passed; `py_compile`, `node --check`, and `git diff --check` passed. The
  25,000-message synthetic benchmark measured 0.079 s first search, 4.143 s for
  50 cursor pages, 0.176 s backup, 0.473 s restore, and zero WAL failures.
- Built portable package v22 at
  `output/windows/LocalMessageHub-portable-20261011-v22-moments-catalog`.
  Static inventory: 10,315 files / 406,024,340 bytes; no database, runtime log,
  private key, credentials file, or account settings, and no scanned personal
  path markers. The filename scan's account-related matches were only the
  `account_safety.py` source module and its test. Hub SHA-256:
  `D93E23AF95ABE02B8CC882DC446A89B90AB30FBD91B0BF210D0E8FC335BDA425`; MCP
  SHA-256: `EDB7C657AECFD5E98BD3248B3EE60057939295D2DDCA7D5D052F7123BFD57679`.
  EXE runtime smoke was skipped because this host blocks unsigned executables.
- Built sanitized source archive v17 (102 files); extracted-package WebUI,
  source/API, isolated storage and read-only MCP smoke passed. Personal path
  scan found zero matches. SHA-256:
  `55B69788512D6766436999B6017A82061E583E6E11C987D2165105EA784BA307`.
- Refreshed the sanitized source archive as v18 after recording the v22 package
  evidence (102 entries). Archive scan found zero sensitive data/config entries
  and zero personal path markers; extracted-package smoke passed. SHA-256:
  `2B4576F25ED36E56D264181CE000688AC86C9BB8CB3E2FDD796116080527211D`.
- Refreshed `output/windows/LocalMessageHub-source-roadmap-20261011-v19.zip`
  so the archive also contains the v18 archive record; its isolated
  extraction/WebUI/API/MCP smoke passed. SHA-256:
  `3ADA4BB3237C08DA4E1E574AE57F4DDC52132CC3FED6E5AC01B1E20389BB4B4B`.

## 2026-10-11 - Unify Moments media with the attachment library

- Extended the SQLite media query with a `source=all|chat|moments` filter and a
  single UNION-based filter/count/order/page across chat attachments and the
  indexed Moments catalog. The media view now has a source selector, includes
  cached Moments images/videos and maintains independent cross-page selection.
- Added discriminator-based Moments resource identities for ZIP export. The
  server re-reads the catalog locator by account/post/media ordinal/variant,
  verifies the configured cache root and rejects symlink escapes; the browser
  receives only a short-lived same-origin media token, never a cache path.
- Added synthetic store, HTTP preview/capability, ZIP identity/manifest and UI
  projection coverage. P11 resource/decryption parity and real-device acceptance
  remain open.
- Full local gate passed **315 Python tests and 6 WebUI tests**, including
  `py_compile`, `node --check`, and `git diff --check`. The 25,000-message
  benchmark recorded 0.070 s first search, 3.590 s for 50 pages, 0.151 s
  backup, 0.415 s restore, and zero WAL failures.
- Rebuilt portable v24 after the Moments capability control landed in the source
  selector. Static inventory: 10,315 files / 406,083,770 bytes. Hub SHA-256:
  `EA78AE98DD5402505D75AC93B9D851149FAEC86FFACCD58DAE5616935EB1A784`;
  MCP SHA-256:
  `4C044A85E267A62CD138F1E347F18DB67F11C970CC3F1CE7604CB58B9E71683D`.
  Runtime smoke remains skipped because this host blocks unsigned executables.
- Built sanitized source v22 with 102 entries at
  `output/windows/LocalMessageHub-source-roadmap-20261011-v22.zip`. Extracted-
  package WebUI/API/MCP smoke passed; scanning archive contents for the current
  user profile, `.codex`, Documents/ChatGPT and Downloads markers found zero
  matches. SHA-256:
  `9BCAA801CE152B3B2CF43E2C825BBDD2FFF271EBD9F10A14E0D3340490D779A3`.
- Refreshed source v23 after recording the portable v24 inventory and hashes.
  The 102-entry archive passed extracted-package WebUI/API/MCP smoke and the
  same personal path marker scan returned zero hits. SHA-256:
  `5C8F67CA2C3A98C677D40A2CC8BA528A4BC14AD97923B73306D5C30DE4B92A93`.

## 2026-10-11 - Verify Moments catalog refresh through explicit sync

- Added a synthetic integration test through `Hub.sync_wechat()` that exercises
  the provider Moments reader and per-account SQLite catalog replacement.
- Verified unavailable, truncated and throwing readers preserve the last good
  catalog; the disabled Moments capability does not call the reader. Also
  verified account isolation and that stored post/media output omits absolute
  cache paths and remote media URLs.
- The focused test passed. Live `sns.db` schema/media/avatar acceptance remains
  open and was not attempted.

## 2026-10-11 - Rebuild sanitized source package after sync coverage

- Built source archive v24 with 102 entries after adding the explicit-sync
  Moments integration test. Extracted-package startup served the WebUI, all
  three platform source entries, isolated temporary storage and read-only MCP.
- Archive checks found no private-state directories or database/key/log files;
  scanning contents for the current user's absolute profile and Documents path
  found zero matches. SHA-256:
  `BEA304F0E181AC795EFC24559C2737F65841831E2DA65C9F5F396EF5E05188DE`.

## 2026-10-11 - Make automated exports snapshot-consistent

- Automatic export now pages a transactionally consistent SQLite backup rather
  than repeatedly querying the live index with OFFSET. Concurrent sync/import
  writes continue against the live store without shifting export pages; the
  temporary snapshot is closed and deleted after the task.
- Added a deterministic regression that inserts a newest message between
  export pages and verifies both original rows appear exactly once while the
  concurrent row is excluded. Cancellation and message-limit tests now exercise
  the snapshot-backed reader. `python -m unittest hub.test_auto_export -v`
  passed all 7 tests.
- The full local gate passed **317 Python tests and 6 WebUI tests**, plus
  `py_compile`, `node --check` and `git diff --check`. The 25,000-message
  benchmark recorded 0.072 s first search, 3.867 s for 50 pages, 0.152 s
  backup, 0.446 s restore, and zero WAL failures.
- Refreshed source archive v25 after this change. Its 102-entry extracted
  bundle passed WebUI/API/MCP startup smoke; it contains
  `hub/test_auto_export.py`, and the exact current-profile path and
  private-artifact scans found zero matches. SHA-256:
  `BE87126C0BB6B0B437087E45D791E37BDB0913336892E31168DEFAA94024627F`.

## 2026-10-11 - Rebuild current portable package

- Built portable v25 from the snapshot-export source with the locked Python and
  npm dependency set and verified QCE v6.3.2 archive/binary checksums. The
  dependency audit reported zero vulnerabilities.
- Static inventory: 10,315 files / 406,095,821 bytes; forbidden private-state
  files and exact current-profile path markers: zero. Hub SHA-256:
  `C7AED63925EBDC6C60FBAE555627D9A484B4DF5B4A7C4A38156BBF2163659838`;
  MCP SHA-256:
  `334A3A40B6E36BF2DB77F6F013EAF84F0013EAB5E7E961F2696F12250FC97B04`;
  bundled QCE server SHA-256:
  `066D9FF15C3E6CB7EFE23ACC564B5B7B34D6DA2849FEC77A7DCC890290BB7745`.
- EXE runtime smoke was skipped because the host blocks unsigned executables;
  no policy bypass was attempted.

## 2026-10-11 - Bound lock sessions and revoke live notification streams

- App-lock sessions now reap expired entries on unlock and keep no more than 64
  in-memory sessions, preventing abandoned browser sessions from accumulating.
- An already-open body-free SSE notification stream now explicitly closes its
  keep-alive connection when app-lock authorization is revoked. Synthetic HTTP
  coverage verifies EOF after lock; a companion test checks the 64-session cap
  and expired-session cleanup with a deterministic clock.
- Focused acceptance passed: 5 account-lock/SSE tests. The full local gate then
  passed **319 Python tests and 6 WebUI tests**, syntax/compile checks, and the
  25,000-message benchmark: 0.067 s first search, 3.56 s for 50 pages, 0.15 s
  backup, 0.419 s restore, zero WAL failures. WebUI retention remained capped
  at 500 messages after browsing 10,000.

## 2026-10-11 - Close the three-platform attachment ZIP integration gap

- Added a synthetic HTTP end-to-end export test that leaves Hub's real resource
  resolver in place for WeChat, WeCom and QQ, then inspects downloaded ZIP bytes
  and the provenance manifest. It verifies WeChat requests best-tier media,
  local source files remain unchanged, and manifest entries contain logical
  account/message identity but no absolute cache paths.
- Moved portable artifact upload after both portable and sanitized-source
  package smoke steps, so a source-bundle failure cannot leave an artifact on a
  failed workflow run.
- Focused three-platform ZIP acceptance passed; the full local gate is recorded
  above after the added regression: **320 Python tests and 6 WebUI tests**,
  `py_compile`, `node --check`, `git diff --check`, 0.067 s first search,
  3.567 s for 50 pages, 0.151 s backup, 0.417 s restore, and zero WAL failures.
- Refreshed sanitized source bundle v30 with 102 entries. Its extracted
  WebUI/API/MCP startup smoke passed; an archive scan found no current-user
  profile path markers. Portable artifact upload in CI now follows both package
  build/smoke steps. Hosted CI is still unverified because these changes remain
  uncommitted and this host has no `gh` CLI.

## 2026-10-11 - Rebuild the current Windows portable package

- Built portable v26 from the current source using the pinned Python/npm
  dependencies and verified QCE v6.3.2 runtime. Static inventory: 10,315 files
  / 406,110,435 bytes; account config files, SQLite databases and exact current
  user profile path markers: zero.
- Hub SHA-256: `A79CAD102097E7F26D709752ACFB3FF9EFC087F0E504C4BC8FD101C6E1DEDAD0`;
  MCP SHA-256: `46FAA9FA850946E18326CBC696A4447D401AC19DD076DA79B162CEA48B934221`;
  bundled QCE server SHA-256:
  `066D9FF15C3E6CB7EFE23ACC564B5B7B34D6DA2849FEC77A7DCC890290BB7745`.
- EXE runtime smoke was skipped under the host's unsigned-executable policy;
  no policy bypass was attempted.

## 2026-10-11 - Refresh sanitized source bundle after package inventory update

- Refreshed the roadmap's source-archive pointer and included this portable-build
  inventory in the source bundle; the v31 archive is the current documented
  source package. Extracted-package WebUI/API/MCP startup smoke passed for v31;
  its 102-entry archive scan found zero forbidden runtime/private files and
  zero exact current-user profile-path markers.

## 2026-10-11 - Refresh source bundle with verified v31 smoke results

- Updated the source bundle pointer and this log to record the completed v31
  extracted-package smoke and privacy scan. Build v32 to carry these verified
  results in the current source package.

## 2026-10-11 - Keep persisted Moments readable offline and harden package audits

- Moments queries now read an existing per-account Hub catalog when the local
  WeChat reader cannot initialize. Multiple offline catalogs require explicit
  account selection in HTTP/MCP and the WebUI; accounts are never merged, and
  cached media URLs stay unavailable without the matching live reader/cache.
- Added HTTP ZIP end-to-end coverage for the Moments-specific catalog resolver,
  checking attachment bytes, identity-only manifest, no cache path/remote token,
  source immutability, and rejection of caller-supplied paths.
- Added a shared staged-package text scan for current-user profile/local-data
  paths, excluded common cache directories and SQLite WAL/SHM sidecars from both
  packages, and made portable smoke require the standalone MCP executable.
- The complete local gate passed **322 Python tests and 6 WebUI tests**,
  `py_compile`, `node --check`, and `git diff --check`. The 25,000-message run
  recorded 0.066 s first search, 3.528 s for 50 pages, 0.153 s backup,
  0.416 s restore, and zero WAL failures. Memory fixture retained 500 messages
  after browsing 10,000; payload/markup sizes are not whole-process RSS.
- Sanitized source bundle v33 passed its staged profile/local-data path scan and
  extracted WebUI/API/read-only MCP startup smoke with 103 files.
- Built portable v27 from the current source with pinned dependencies and the
  hash-verified QCE v6.3.2 runtime. Inventory: 10,315 files / 406,126,849 bytes;
  account configs, SQLite/sidecar databases, key files and current profile/local-
  data path hits in text: zero. The standalone MCP executable is present.
- Hub SHA-256: `1D81D0395A5F58C94818B33B946B3C54F94E21AFBB496A2E50FBBC984313BC47`;
  MCP SHA-256: `EA83DA926740767B34A2D56094984660D16CF1EA9DBC81E1F572F7CAA8802751`.
  Runtime smoke was skipped because local policy blocks unsigned EXEs; no bypass
  was attempted.

## 2026-10-11 - Verify current sanitized source archive

- Built `LocalMessageHub-source-roadmap-20261011-v35.zip` with 103 files.
  Staged text privacy scan passed, and extracted-package smoke served the WebUI,
  all three source entries, isolated storage, and the read-only MCP endpoint.
- Archive SHA-256: `0AA65CDBC612E11AC57BD997E0E163A8462944561FCCC71743001443601AE1D9`.
- `git diff --check` passed; only Git's expected LF-to-CRLF working-copy notices
  were emitted.

## 2026-10-11 - Treat ambiguous QQ delivery receipts as unknown

- NapCat success without a message ID, malformed success responses, HTTP 5xx,
  transport interruption, and timeout now produce `unknown`; explicit OneBot
  rejection remains `failed`. Re-approving a completed request never delivers
  again, preventing the UI from suggesting a potentially duplicate resend.
- Added service-level idempotency coverage and an end-to-end synthetic loopback
  HTTP test proving missing-receipt delivery remains `unknown` after a second
  approval and results in exactly one HTTP request. Updated P9-02 acceptance.
- Full local gate passed **324 Python tests and 6 WebUI tests**, compile/syntax
  checks and `git diff --check`. The 25,000-message benchmark recorded 0.067 s
  first search, 3.432 s for 50 pages, 0.147 s backup, 0.409 s restore, and zero
  WAL failures. The memory fixture retained 500 messages after browsing 10,000.
- Source bundle v36 passed the staged privacy scan and extracted-package
  WebUI/API/MCP smoke with 103 files; SHA-256:
  `3719F0C43AAA9883483EEAB38EAEF132BD1899F9CC29B053128FB14723C9AC8D`.
- The updated portable artifact pointer is v28. Runtime smoke remains skipped
  because this host blocks unsigned executables; no policy bypass was attempted.
- Portable v28 static inventory: 10,315 files / 406,130,027 bytes; staged
  profile/local-data path scan passed and standalone MCP EXE is present.
- Hub SHA-256: `D969A3A7770F72171308ADEF79914F78810560532CE7BD91F242FDA84013D389`;
  MCP SHA-256: `8A6F24B49A4B8F035CC36F1E0DE6CCD046665FE373031BCD35F7EF87145A9A8D`.
- Rebuilt source bundle v37 with 103 files; staged profile/local-data scan and
  extracted WebUI/API/three-source-entry/read-only MCP smoke all passed.
- Refreshed sanitized source bundle v38 after the current gate/documentation
  update; the same staged privacy scan and extracted WebUI/API/MCP smoke passed
  with 103 files. SHA-256: `91EFE87BC15675A390A334EFD4FAD43A7E72461490AB080569635B376D465EE2`.
- Hardened the common package audit to reject named account/NapCat config files,
  databases, WAL/SHM sidecars, logs, keys and compiled Python files in staged
  artifacts; only the pinned upstream NapCat config template paths are allowed.
  Source and portable copy filters now share the same config denylist. Added
  negative fixture coverage for `wechat.json`, `onebot11.json`, `.db-wal` and
  `.key`; the new scanner also passed against the existing portable v28 tree.
- Fixed the local gate to invoke the PowerShell scanner tests without checking
  stale `$LASTEXITCODE`; reran the full gate successfully: 324 Python and 6
  WebUI tests, privacy fixtures, syntax/compile checks, and `git diff --check`.
  The latest 25,000-message benchmark recorded 0.066 s first search, 3.424 s
  for 50 pages, 0.155 s backup, 0.422 s restore, and zero WAL failures.
- Source bundle v40 then passed the denylist/path audit and extracted-package
  WebUI, three-source, isolated-storage and read-only MCP smoke with 104 files.
  SHA-256: `1326C5975E66942BB9C6C6D3A613DC6F96B15745568523BECCC9EE58124334DA`.
- Settings now reloads the latest 100 QQ send requests, retains terminal
  outcomes after refresh, and makes sent/failed/unknown/rejected entries
  read-only; unknown warns users to verify in QQ before making another request.
  A focused UI test confirms no review/retry control is rendered for terminal
  outcomes.
- Fixed ZIP export of indexed WeCom attachments on text-type parent messages;
  the synthetic end-to-end archive fixture now exercises that case using the
  real resolver and verifies exported bytes and identity-only manifest.
- Moments exports now query SQLite in 50-record pages and serialize each item
  into a byte-capped output buffer, stopping at 32 MiB without reading later
  pages; the response shares that bounded buffer rather than making a second
  full-size copy. Added format-equivalence and early-stop tests.
- Full local gate passed: **327 Python tests and 7 WebUI tests**, privacy
  fixtures, syntax/compile checks, and `git diff --check`. The 25,000-message
  benchmark recorded 0.067 s first search, 2.633 s for 50 pages, 0.077 s
  backup, 0.221 s restore, and zero WAL failures.
- Rebuilt the sanitized source archive as
  `output/windows/LocalMessageHub-source-roadmap-20261011-v41.zip` (105 files).
  Its denylist scan, current-profile/local-
  data path scan, extracted WebUI/three-source smoke, isolated storage and
  read-only MCP smoke passed. SHA-256:
  `A77793D5B4AED65D1471236185A87EA2B49C92EA4092366B6824877211D87F10`.
- Unified the media-message counter predicate across overview and analytics:
  WeCom indexed attachment arrays and QQ roaming-resource arrays on text parent
  messages now count once per parent message, while malformed JSON remains
  safely ignored. Synthetic tests cover multi-attachment WeCom/QQ messages in
  private profiles, relationship footprint/timeline, and overview stats.
- Full local gate passed after the media-counter fix: **328 Python tests and 7
  WebUI tests**, package privacy negative fixtures, syntax/compile checks, and
  `git diff --check`. The 25,000-message benchmark recorded 0.070 s first
  search, 2.663 s for 50 pages, 0.076 s backup, 0.217 s restore, and zero WAL
  failures.
- Rebuilt sanitized source bundle v43 with 105 files; privacy/path audit and
  extracted WebUI, three-source, isolated-storage and read-only MCP smoke passed.
- Added an explicit malformed-JSON regression assertion; the focused store and
  analytics suite still passes (71 tests), and `git diff --check` is clean.
- Rebuilt sanitized source bundle v45 with 105 files; privacy/path audit and
  extracted WebUI, three-source, isolated-storage and read-only MCP smoke passed.
  SHA-256: `F158857DD08C70076091EC349C8767AE08EBB3CA703226BDBDED7576D06B97DB`.
- Updated Windows Actions artifact collection to include both the portable
  package and sanitized source ZIP, still gated behind successful completion of
  both build/smoke steps; the hosted workflow has not yet run on this worktree.
- Extended explicit Moments MCP media retrieval from cached images to cached
  MP4/WebM videos. Videos are returned as embedded MCP resources after the same
  15-minute token, cache-root containment, signature, and 8 MiB checks; no URL
  fetching or caller-supplied paths were added. The MCP and server test modules
  pass (122 tests), including existing attachment/image MCP behavior.
- After integrating the standalone MCP EXE smoke, expanded Moments media reads,
  analytics attachment counters and release-artifact changes, the full local
  gate passed **330 Python tests and 7 WebUI tests**, privacy fixtures,
  syntax/compile checks and diff validation. The 25,000-message benchmark
  recorded 0.068 s first search, 2.620 s for 50 pages, 0.079 s backup, 0.220 s
  restore and zero WAL failures.
- Rebuilt sanitized source bundle v46 with 105 files; its privacy/path audit and
  extracted WebUI, three-source, isolated-storage and read-only MCP smoke passed.
- Updated the WeFlow gap matrix and MCP contract to reflect explicit cached
  Moments MP4/WebM resource responses. Rebuilt sanitized source bundle v47 with
  105 files; its privacy/path audit and extracted-package smoke passed. SHA-256:
  `F4F337A97C558EC916FB0D7851D66FDCD88044820970B6ECD9897FD014C37B3D`.
- Added a synthetic HTTP end-to-end test for WeChat image, voice, video, and file
  resolution from indexed message identity through same-origin playback tokens.
  It also verifies unavailable-cache behavior, that responses do not disclose
  local paths, and source-cache bytes remain unchanged. The full local gate
  passed **343 Python tests and 7 WebUI tests**, privacy fixtures, syntax/compile
  checks, and `git diff --check`; the 25,000-message benchmark recorded 0.086 s
  first search, 4.382 s for 50 pages, 0.159 s backup, 0.452 s restore, and zero
  WAL failures. This is synthetic resolver coverage only, not real-account
  media acceptance.
- Rebuilt the sanitized source bundle as
  `output/windows/LocalMessageHub-source-roadmap-20261011-v57.zip` with 106
  files. The staged privacy audit and extracted WebUI/source-entry/isolated-
  storage/read-only-MCP smoke passed. SHA-256:
  `CFE65A1E2CA5F34B1A2629C56A001C50F405870CF08D60B243A756737364F842`.
- Rebuilt after recording the package result and refreshed roadmap pointer as
  `output/windows/LocalMessageHub-source-roadmap-20261011-v58.zip`, also with
  106 files; its privacy audit and extracted-package smoke passed. SHA-256:
  `426E911E6DA4CBAAE0E11C6E716BFFC5A5BA8C075689603006301544D5C7E4A4`.
- Added synthetic coverage for the WeChat avatar-database status row, including
  HTML escaping and WeChat-only rendering. The provider status remains metadata-
  only and does not open the avatar database. The full local gate passed
  **345 Python tests and 8 WebUI tests**, privacy fixtures, syntax/compile
  checks, and `git diff --check`; the 25,000-message benchmark recorded 0.070 s
  first search, 2.700 s for 50 pages, 0.080 s backup, 0.225 s restore, and zero
  WAL failures. A Python `ResourceWarning` for a closed socket was emitted by
  the test process but did not fail the gate.
- Built sanitized source bundle v59 with 107 files; staged privacy/path audit
  and extracted WebUI, three-source, isolated-storage, and read-only MCP smoke
  passed. SHA-256:
  `2CEFB5363D160F56535CD00B18B2DFA7CF0B8F4E2B33E715120AC830FE94F252`.
- Added a synthetic send-service restart test: an in-flight request recovers as
  `unknown/interrupted`, records `recovered_unknown`, and cannot be approved into
  a second adapter call. Corrected avatar documentation to state that
  `schema_unverified` only means a reader key is present; the DB is not opened.
- Added a blocking-adapter race test proving concurrent approvals produce one
  adapter call while the second receives the in-flight status.
- The latest full local gate passed **347 Python tests and 8 WebUI tests**,
  privacy fixtures, syntax/compile checks, and `git diff --check`. The 25,000-
  message benchmark recorded 0.067 s first search, 3.666 s for 50 pages,
  0.083 s backup, 0.228 s restore, and zero WAL failures. Synthetic benchmark
  times are host-load sensitive; the pagination result varied between runs.
- Rebuilt sanitized source bundle v62 with 107 files; privacy/path audit and
  extracted startup, three-source, isolated-storage, and read-only MCP smoke
  passed. SHA-256: `72CA6D6C11911A4BC14C1C0AB8914336E0DCCDAE4F831FCE4A9B9BD12C3D1F87`.
- Rebuilt after recording portable runtime acceptance as sanitized source
  bundle v63 with 107 files; staged privacy audit and extracted-package smoke
  passed. SHA-256: `61A99B0F2B16A2BB262B935C013274F5C2446DD542DD8A34921FA6C8DBDFD0B9`.
- The updated full local gate passed **346 Python tests and 8 WebUI tests**,
  privacy fixtures, syntax/compile checks, and `git diff --check`. The 25,000-
  message benchmark recorded 0.069 s first search, 3.586 s for 50 pages,
  0.149 s backup, 0.415 s restore, and zero WAL failures. Synthetic benchmark
  timings vary by host load and are not a performance guarantee.
- Rebuilt sanitized source bundle v60 with 107 files; package privacy/path
  audit and extracted startup, three-source, isolated-storage, and read-only
  MCP smoke passed. SHA-256:
  `B1548B1459E76187D68DD7BEB01A1AD044626914C38F0F69C4F9FDD016605B47`.
## 2026-10-12 - Fix clean-runner WebUI dependency install

- Removed `npm ci --offline` from the Windows portable builder: hosted Actions
  starts with a fresh npm cache and the package script has no cache warmup, so
  the offline install could not fetch the locked Lucide dependency. It now uses
  the committed WebUI lockfile with npm audit/funding lookups disabled.
- Updated the P14 acceptance row. Hosted Actions still must run on a reviewed
  push before P14 can close.

## 2026-10-12 - Search media by message and sender

- Extended the existing media-library keyword filter to match indexed chat
  message text and sender name/ID in addition to conversation title, filenames,
  and attachment names. This applies before SQLite count/pagination for both
  chat-only and unified chat/Moments results; platform/account/date filters are
  unchanged.
- Added synthetic WeCom multi-attachment assertions for body, sender name, and
  sender ID matches. The focused `hub.test_store` test passed.
- Targeted P14 clean-environment checks passed: `npm ci` installed the locked
  WebUI dependency from a brand-new isolated npm cache; package privacy negative
  fixtures and deterministic manifest/hash-mutation fixtures passed; a fresh
  112-file source ZIP passed its archive/privacy audit and extracted isolated
  Hub/read-only-MCP smoke (SHA-256 `7360B83B397A15C2CB9109E5003E52425BD6FE13FBAEEF27F25003ACEDD6DF5C`).
  The portable builder also completed both PyInstaller targets and isolated
  Hub plus standalone MCP stdio smokes with an empty npm cache using
  `-OmitQceRuntime`. This checks builder/dependency plumbing only, not the
  official QCE runtime; no full clean GitHub runner or hosted Actions run was
  performed. Temporary fixtures and packages were kept under `%TEMP%` and did
  not use real account data.

## 2026-10-12 - P11 media search and full local package refresh

- Media-library keyword filtering now also matches indexed chat body text and
  sender name/ID before count and pagination. The 65-test `hub.test_store` suite
  passed; synthetic WeCom multi-attachment cases cover body and sender matches.
- Rebuilt the sanitized source bundle at
  `output/windows/LocalMessageHub-source-release-v2.zip`; staged and archive
  privacy checks plus extracted isolated Hub/read-only-MCP smoke passed.
- Rebuilt the full Windows portable directory at
  `output/windows/LocalMessageHub-portable-20261012-roadmap-v95` with the
  hash-verified official QCE v6.3.2 runtime. The 10,329-file package passed
  staged privacy scanning, isolated Hub/UI smoke, and standalone MCP stdio
  initialize/tools/list/exit smoke. Its paired manifest
  `output/windows/LocalMessageHub-artifact-manifest-release-v2.json`
  binds both packages to HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, run
  `local-20261012-roadmap-release-v2`, build context `local-worktree`; byte/hash
  verification passed. Hosted Actions and release artifact upload remain open.
- The complete local gate passed **371 Python tests and 14 WebUI tests**, package
  privacy/manifest fixtures, syntax/compile checks, and `git diff --check`.
  The 25,000-message synthetic benchmark recorded 0.066 s first search, 2.605 s
  for 50 pages, 0.083 s backup, 0.218 s restore, and zero WAL failures.

## 2026-10-12 - P11 resource archive integrity

- Resource ZIP manifests now include a streaming SHA-256 for each successfully
  exported attachment, alongside its source identity, filename, and byte count.
  Hashing shares the existing bounded copy loop; it adds no extra file read and
  does not expose local paths. Synthetic WeChat and Moments fixtures verify the
  digest against archived bytes.

## 2026-10-12 - P14 artifact round-trip verification

- CI now downloads the release artifact after upload and re-runs the manifest
  verifier against the downloaded portable directory, source ZIP, and manifest.
  The local round-trip fixture mirrors the workflow's
  `LocalMessageHub-portable-ci` directory and
  `LocalMessageHub-artifact-manifest.json` filename, and rejects wrong commit
  provenance and mutated downloaded bytes.
- `scripts/test-package-manifest.ps1` passed. A clean Python 3.12 environment
  installed only `requirements-ci.txt` and passed the full local gate: 375
  Python tests, 14 WebUI tests, package privacy/manifest fixtures, syntax and
  compile checks, and the 25,000-message benchmark (0.066 s first search,
  2.592 s for 50 pages, 0.079 s backup, 0.214 s restore, zero WAL failures).
- Portable v96, built with hash-verified official QCE v6.3.2, passed isolated
  Hub/UI and standalone MCP initialize/tools/list/exit smokes. This remains local
  evidence; hosted Actions upload/download behavior has not run on GitHub.
- Security review of Hub-owned Python/JavaScript boundaries found no reproducible
  high/medium issue. Five focused tests passed for cross-origin and rebinding
  rejection, form-post rejection, account-lock gates, and local-cache-only avatar
  serving. Vendored QCE and runtime-only configuration were outside this review.
- Package docs now state that the portable build and source-reader integrations
  are Windows-targeted; macOS/Linux build/runtime acceptance is not claimed.
- Re-ran `scripts/check-local-hub.ps1` after these audits and the P11 contact
  increment: 376 Python tests,
  14 WebUI tests, privacy/manifest fixtures, syntax/compile checks, and the
  25,000-message benchmark passed (0.066 s first search, 2.609 s for 50 pages,
  0.076 s backup, 0.217 s restore, no WAL failures). Hosted Actions still has
  not run for this worktree.
- Rebuilt sanitized source release v10 and Windows portable v98 after the code
  and documentation updates; its
  archive privacy scan and extracted Hub/UI/read-only MCP smoke passed (112
  files). Manifest v10 binds it with verified portable v98 bytes to the local
  worktree commit SHA; this is local provenance, not a hosted release.

## 2026-10-12 - P11 WeChat official account contacts

- The WeChat reader now follows the pinned WeFlow contact rule (`gh_` username
  plus `local_type == 1`) and maps the local `biz_info.type` values to a fixed
  official-account enum. API/UI filtering and JSON/CSV export expose only the
  normalized classification, not source-only raw columns.
- Five focused provider/store/API/export tests and `node --check webui/app.js`
  passed. No real WeChat account was connected; absent optional columns keep
  the existing ordinary-contact behavior. Full contact details remain open.

## 2026-10-12 - P8 sync revocation, P11 conversation media, and P14 packaging

- Capability updates now serialize with source sync ingestion and Moments
  catalog replacement. Turning sync off signals active cancellation and blocks
  any later indexed write; turning Moments off blocks a catalog read already in
  progress. Deterministic server tests cover both races and passed in the full
  gate.
- The media library can be opened from a conversation and constrained to its
  exact conversation ID. Store and HTTP API tests verify the filter applies
  before paging/counting and excludes Moments/unrelated chats.
- Windows Actions upload now includes hidden files covered by the manifest;
  the local manifest fixture exercises a hidden file. Hosted upload/download
  behavior remains unverified until GitHub Actions runs.
- `scripts/check-local-hub.ps1` passed: 381 Python tests, 14 WebUI tests,
  package privacy and manifest fixtures, syntax/compile checks, and the 25,000
  message benchmark (0.068 s first search, 2.620 s for 50 pages, 0.076 s
  backup, 0.216 s restore, zero WAL failures).
- Sanitized source release v13 passed archive/privacy audit and extracted Hub,
  WebUI, three-source-entry and read-only MCP smoke. Windows portable v99 passed
  isolated Hub/UI and standalone MCP initialize/tools/list/exit smokes. Both
  packages exclude account/runtime state; local build provenance is not hosted
  release provenance.
- Manifest v12 verifies every portable/source package byte and records the
  local worktree context against current HEAD; it does not represent a commit
  containing these uncommitted workspace changes.
- P12 avatar schema acceptance remains blocked on verified distributable
  schema/fixtures or upstream implementation; no speculative blob decoder was
  added. P9 live QQ, P12 real-account acceptance, and P13 authorized sender
  verification remain unperformed.

## 2026-10-12 - Parallel roadmap follow-ups

- P8 capability revocation now serializes imports and QQ roaming-history writes
  with capability changes, preventing a late batch from entering the index
  after the module is disabled. Targeted race checks and the full gate passed.
- P12 Moments parsing now reads WeFlow-style URL/thumb XML attributes for
  token, key, md5, and enc_idx while retaining child-text fallback. Synthetic
  tests cover the attribute format; no network fetch or private decoder was
  added.
- Parallel P9 review found the current bounded QQ pagination, rich payload
  ingestion, local resource validation, and capability/cancel gates covered by
  synthetic tests; no evidence-backed code change was needed. Real NapCat/QQ
  history/media acceptance remains outstanding.
- Read-only audit of hosted Actions run 38101275283 found two prior failures:
  CI lacked the WeChat `cryptography` dependency and one test expected a
  machine-specific path. Both fixes are present in the current worktree; no
  workflow was triggered. The corrected hosted run remains unverified.
- The full local gate passed 384 Python tests and 14 WebUI tests, privacy and
  manifest fixtures, compile/syntax checks, and the 25,000-message benchmark
  (0.067 s first search, 2.609 s for 50 pages, 0.079 s backup, 0.216 s restore,
  zero WAL failures). Portable v100 and sanitized source are rebuilt and
  isolated Hub/MCP smoke-tested; a local-worktree manifest verifies package
  bytes.
- Package provenance is local-worktree state, not a hosted release. Live
  source-account acceptance, binary avatar-cache decoding, and WeChat/WeCom
  authorized sending remain unverified and are not simulated.

## 2026-10-12 - P9 pagination and refreshed local packages

- QQ history pagination now follows QCE's boolean `hasNext` when present, so a
  short page can continue to the next page; empty or invalid batches still stop
  to avoid looping on malformed responses. A focused synthetic regression,
  provider syntax check, and `git diff --check` passed. No NapCat or QQ account
  was contacted.
- The latest local artifacts are portable v101 and sanitized source v92,
  bound by manifest v15. The latest full local gate remains 384 Python tests,
  14 WebUI tests, package privacy/manifest fixtures and the recorded benchmark.
  Hosted Actions has not been triggered and remains unverified.
- P12's synthetic avatar path was verified through the local HTTP endpoint and
  JPEG decoder. A later read-only audit verified cached keys against encrypted
  page headers for the exact `sns.db` and `head_image.db` basenames without
  reading SQLite payload rows or printing identifiers. Real-account WebUI
  avatar rendering remains open; no process scan or source-client write was
  attempted.

## 2026-10-12 - P9 rich history, P11 SQL identity, and P12 avatar rendering

- QQ history normalization now preserves QCE face and reply elements using
  allowlisted fields, restoring emoji/reply previews in the existing renderer
  without exposing unknown raw payload. Focused provider tests passed.
- SQL message export now writes the canonical message `id` into the SQL
  `message_id` column. Executing the generated export verifies identity
  preservation; the focused regression passed.
- Local WeChat avatar handles are now accepted only in the exact
  `hub-avatar://wechat/<64 lowercase hex>` form and mapped to the same-origin
  avatar endpoint. URL validation/rendering, avatar HTTP checks, JavaScript
  syntax, and focused Python tests passed.
- These changes do not close the real-account browser/media, full WeFlow
  resource parity, NapCat runtime, or hosted CI acceptance gates.
- Rebuilt local portable v102 and sanitized source v102 after these edits.
  Package privacy scans, isolated Hub/UI and standalone MCP smokes passed;
  manifest v102 verifies both package byte inventories and local-worktree
  provenance. Hosted Actions and upload/download round-trip remain unverified.
- Repacked the sanitized source ZIP as v103 after synchronizing the roadmap;
  its isolated startup/API and privacy scans passed. Manifest v103 binds that
  source archive to portable v102 and was re-verified byte-for-byte. Provenance
  remains local-worktree, not hosted release provenance.
- Repacked the source ZIP as v104 after replacing the roadmap's version-specific
  latest-artifact wording with manifest-based provenance; source privacy and
  isolated startup/API checks passed. Manifest v104 binds it to portable v102
  and its byte verification passed. No further roadmap edits followed this
  package build.
- Audited the v104 source/v102 portable payloads and found they omitted the
  latest QQ bounded merge-forward normalization and WeChat WeFlow file-localType
  resolver fix. Updated the roadmap to distinguish cached type-47 sticker
  support, clarify MCP avatars are metadata-only, and identify the 384/14 count
  as the latest full gate rather than coverage of these later edits. Built
  portable v106 and sanitized source v106
  (`LocalMessageHub-artifact-manifest-local-v106.json`); isolated Hub/UI and
  standalone MCP portable smokes, source privacy/API smoke, focused QQ/WeChat
  regressions, and manifest byte/provenance verification passed. Hosted CI/upload remain
  unverified; the full 384/14 gate was not rerun.
- P11-02 now preserves exact `conversation_id` and message `id` in CSV/HTML/XLSX
  columns and Markdown/TXT message headers, without moving existing columns.
  `hub.test_store.ExportTestCase` passed 17 focused tests, including the
  injection/XSS/literal-cell regressions. P12 offline audit found its remaining
  gaps require real account/browser acceptance; focused Python and WebUI checks
  passed without launching a source client or triggering sync.
- Refreshed both deliverables after the P11 export provenance update: portable
  v107, sanitized source v107, and
  `LocalMessageHub-artifact-manifest-local-v107.json`. Portable isolated Hub/UI
  and standalone MCP smokes, source privacy/API smoke, and manifest byte and
  provenance verification passed. The full 384/14 gate and hosted upload were
  not rerun.
- P9 QQ history now renders bounded voice/video call-record summaries from the
  pinned QCE element contract, omitting timestamps and raw payload. P12-02
  relationship milestones now include WeCom and QQ media attachments, using
  per-message `EXISTS` semantics so multiple matching attachments do not
  inflate counts. Focused analytics (12 tests), QQ provider/UI (3 provider and
  1 UI tests), export (17 tests), and the WeChat file-type regression passed.
  No source client, QQ account, or real cache was accessed.
- Refreshed deliverables after these additions: portable v108, sanitized source
  v108, and `LocalMessageHub-artifact-manifest-local-v108.json`. Isolated
  portable Hub/UI and standalone MCP smokes, source privacy/API smoke, and
  manifest byte/provenance verification passed. Hosted CI, upload round-trip,
  and the full 384/14 gate remain outstanding.
- P12 avatar audit found that WeCom group rosters discarded avatars already
  present in the local contact directory. The adapter now joins that field by
  exact user ID when the roster has none; the focused roster regression passed.
  No remote avatar fetch or real account was used. Missing QQ/WeCom cache bytes
  still render the initials fallback; real browser/account acceptance remains
  open.
- Refreshed the Windows portable package and sanitized source archive as v109
  after the P12 fix. Portable isolated Hub/UI and standalone MCP smokes passed;
  source privacy and extracted API/MCP smokes passed. Manifest
  `LocalMessageHub-artifact-manifest-local-v109.json` verifies both packages;
  the source ZIP SHA-256 is
  `A92E7A1E6B55A458271C750314A74A5B4E031905B56CE6006879E0923D326C8A`.
  Hosted CI and artifact upload/download remain unverified.
- QQ QCE `walletElement` now normalizes to a 256-character-capped summary and
  uses the existing escaped rich-card renderer; the provider fixture confirms
  amount/token fields are not retained. MCP cached QQ audio signature checks
  now recognize AMR-NB, AMR-WB, and SILK v3 and return audio content. Focused
  provider/UI and MCP tests passed; no NapCat, source client, or real account
  was used. The full local gate then passed 398 Python tests and 14 WebUI tests,
  privacy/manifest fixtures, syntax/compile checks, and the 25,000-message
  benchmark (0.066 s first search, 2.595 s for 50 pages, 0.076 s backup,
  0.217 s restore, zero WAL failures). An independent review found QCE's
  `0x02#!SILK_V3` cache-header variant; MCP now accepts it, and the focused
  AMR/SILK regression passed again. Hosted CI/upload remains unverified.
- Refreshed v110 after the QQ wallet and cached-audio additions: the portable
  EXE and standalone MCP smokes passed, as did sanitized source privacy and
  extracted Hub/API/MCP smokes. Local manifest
  `LocalMessageHub-artifact-manifest-local-v110.json` verifies both package
  byte inventories and local-worktree provenance. The source ZIP SHA-256 is
  `9D63B55833796188C76C625B418CA191470BF927ECDC7535C94B187ED9DD2841`.
  No package upload or hosted CI run was performed.
- Follow-up after v110: cached QQ AAC/ADTS is now identified as `audio/aac`
  before generic MPEG, following QCE's declared resource MIME mapping; the
  avatar UI removes a failed local image so initials fallback remains visible.
  Focused synthetic tests passed; the full local gate then passed 398 Python
  and 14 WebUI tests, privacy/manifest fixtures, syntax/compile checks, and the
  25,000-message benchmark (0.069 s first search, 2.594 s for 50 pages,
  0.077 s backup, 0.215 s restore, zero WAL failures). v110 predates these
  changes and is not the current build. No real account or remote media was
  used.
- Refreshed local deliverables as portable/source v111 after AAC and avatar
  fallback changes. Portable executable/UI and standalone MCP stdio smokes,
  source privacy and extracted API/MCP smokes passed. Manifest
  `LocalMessageHub-artifact-manifest-local-v111.json` verifies local-worktree
  provenance and both package byte inventories. Source ZIP SHA-256:
  `07D42A0C710BEB918ED177C2D271929BE20BDF1A1EF9B091BB662F2E221905BC`.
  No hosted upload or real-account acceptance was performed.
- P9 runtime follow-up: NapCat runtime JSON updates now use same-directory
  atomic replacement, matching the existing OneBot config handling. A synthetic
  replacement-failure test confirms the prior config survives and temporary
  files are removed; focused NapCat config, credential-cache, and QR launch
  tests passed. This is offline reliability coverage only: NapCat was not
  launched and no QQ account/device was used.
- P11 QQ cached audio now recognizes QCE's M4A-family ISO-BMFF audio brands as
  `audio/mp4` in MCP while rejecting generic `isom` video; synthetic MCP tests
  also retain coverage for ADTS AAC, AMR and SILK. No account media was read.
- The full local gate passed 399 Python and 14 WebUI tests, package privacy and
  manifest fixtures, syntax/compile checks, and the 25,000-message benchmark
  (0.067 s first search, 2.584 s for 50 pages, 0.076 s backup, 0.218 s restore,
  zero WAL failures). Refreshed portable/source v113; portable EXE/UI/MCP
  smokes and source privacy/API/MCP smokes passed. Local manifest
  `LocalMessageHub-artifact-manifest-local-v113.json` verifies package bytes
  and provenance. Hosted CI/upload and live platform acceptance remain open.
- P11 WeChat V2 image parsing now validates the complete AES section and XOR
  tail against the container boundary before slicing. Synthetic regressions
  reject truncated AES data and overlong XOR tails while preserving the valid
  explicit-key decrypt fixture. `python -m unittest integrations.wechat.test_media
  -q` passed 32 tests; module compilation and `git diff --check` passed. No
  account cache or source client was accessed. Full WeFlow/real-device media
  acceptance remains open.
- P9 QQ history paging now rejects bool/float `limit` and `offset` values in
  the shared provider before any QCE request. The QQ provider suite passed 34
  tests, and Python compilation plus `git diff --check` passed. No NapCat,
  QQ account, or source-client state was accessed; live history/media
  acceptance remains open.
- P11 WeChat V2 image parsing validates the complete AES section and XOR tail
  against the container boundary before slicing. Synthetic regressions reject
  truncated AES data and overlong XOR tails while preserving the valid
  explicit-key decrypt fixture; the focused media suite passed 32 tests. No
  account cache or source client was accessed.
- The full local gate passed 408 Python tests and 16 WebUI tests, privacy and
  manifest fixtures, compile checks, and the 25,000-message benchmark (0.066 s
  first search, 2.587 s for 50 pages, 0.077 s backup, 0.216 s restore, zero
  WAL failures). Portable/source v123 passed isolated Hub/UI/API/MCP smokes and
  privacy checks; the local manifest also verified after copying packages into
  a simulated artifact-download directory. Manifest run
  `local-20261012-p14-v123` is local-worktree evidence only. Hosted CI/upload
  remains unverified. A non-fatal ResourceWarning for an already-closed socket
  was emitted by the test suite.
- WXGF/HEVC conversion now caps decoded JPEG output reads at 64 MiB and rejects
  oversized output; an offline synthetic regression and the focused WeChat media
  suite (34 tests) pass. The conversation-list avatar regression now checks the
  local cached-avatar handle through the actual WebUI render path. These are
  focused checks only. License review could not verify the bundled FFmpeg
  binary's build/configuration, so its package dependency was removed; portable
  WXGF conversion now requires system FFmpeg. Hosted CI remains unverified.
- After the QQ location-card and media/avatar follow-ups, the complete local gate
  passed 412 Python tests and 16 WebUI tests, privacy/manifest fixtures,
  syntax/compile checks, and the 25,000-message benchmark (0.066 s first search,
  2.586 s for 50 cursor pages, 0.075 s backup, 0.213 s restore, zero WAL failures).
  Expected failure-path logs appeared in QQ/Moments tests; the suite exited 0.
  Portable v123 predates these changes and was not refreshed in this gate.
- Refreshed portable v124 and a sanitized 114-entry source ZIP after the QQ
  location-card and avatar/WXGF bounds follow-ups. Both passed isolated
  Hub/UI/API/MCP smokes and package privacy scans. Manifest
  `LocalMessageHub-artifact-manifest-local-v124.json` verifies package bytes
  with local run `local-20261012-p14-v124` and baseline commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. No hosted workflow or upload was
  run; the manifest is integrity metadata, not a signature.
- Parallel P9/P12 follow-up: QCE `avRecordElement` call records now normalize as
  system messages, and WeFlow Moments location parsing retains coordinates,
  POI address name, and label separately. Focused QQ/Moments suites passed 43
  tests. The full local gate passed 419 Python tests and 16 WebUI tests,
  privacy/manifest fixtures, compile checks, and the 25,000-message benchmark
  (0.066 s first search, 2.583 s for 50 pages, 0.077 s backup, 0.215 s restore,
  zero WAL failures). No source-client data was accessed; P9/P12 real-device
  acceptance and hosted CI remain outstanding. Existing v124 packages do not
  contain this follow-up and were not refreshed.
- P9 merged-forward content now retains a maximum of 50 nested attachment
  metadata entries across the bounded preview, excluding QCE URLs, local paths,
  and raw IDs. The WebUI shows the escaped type/name/size and `未缓存` without
  presenting a fake resolver. P12 Moments posts without a nickname now use the
  WeChat ID for the author name and avatar initial; its render regression still
  verifies the existing cached-avatar URL path. Full local gate: 420 Python
  tests, 16 WebUI tests, privacy/manifest checks, syntax checks, and benchmark
  (0.068 s first search, 2.606 s for 50 pages, 0.077 s backup, 0.216 s restore,
  zero WAL failures). Portable v129 passed isolated Hub/UI/three-source/read-only
  MCP and standalone MCP stdio smokes; sanitized 114-file source ZIP passed
  privacy and extracted startup checks. Manifest verified for
  `local-20261012-p14-v129` (`local-worktree`, base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`);
  source ZIP SHA-256 is
  `B35BBEE5248F16AEB2BF528C52BAE71C440919A8AB9821C3E1C32B07A1BE8EAF`.
  The nested QQ resource model is metadata-only until a local resolver is added;
  real QQ/Moments device/cache acceptance and hosted Actions remain unverified.
- Rebuilt v130 after updating the roadmap's local-gate evidence. Portable v130
  passed isolated Hub/UI/three-source/read-only MCP and standalone MCP stdio
  smokes; sanitized source v130 passed privacy and extracted startup checks.
  Manifest provenance and bytes verify for `local-20261012-p14-v130`
  (`local-worktree`, base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Source
  ZIP SHA-256:
  `B4C71624512B1B7367C94E4F74EB44C8B9682D135EBCD46BBC5D69E0779E8FC7`.
  Hosted Actions/upload remain unverified.
- Fixed WeChat media dispatch for the three WeFlow file `localType` variants
  beyond `49` (`34359738417`, `103079215153`, and `25769803825`). They now use
  the existing read-only account/cache-validated file downloader. Synthetic
  provider coverage verifies all four type codes; `integrations.wechat.test_media`
  passes 36 tests. No source database or cache was modified. Hosted Actions logs
  for the initial public commit confirm it failed because `requirements-ci.txt`
  omitted `cryptography` and one test assumed a Chinese temporary-directory
  component; both defects are corrected in the current local worktree. Local
  packages v130 predate this fix and require refresh; hosted verification still
  requires publication of the reviewed worktree.
- Added explicit QQ merged-forward resource expansion and on-demand attachment
  download through fixed local NapCat bridge calls. Responses expose opaque
  short-lived handles only; account/message identity, cache containment, file
  signatures, 50-resource, 50 MiB/file, and 512 MiB active-cache limits are
  enforced. The UI now surfaces truncation, caps image fetch concurrency at four,
  aborts requests when leaving the chat, and supports CSP-authorized local blob
  image previews. Unknown/Silk formats remain unsupported. The full local gate
  passed 425 Python tests, 17 WebUI tests, privacy/manifest checks, syntax and
  diff checks, and the 25,000-message benchmark (0.068 s first search, 2.609 s
  for 50 pages, zero WAL failures). Real NapCat/account acceptance was not run;
  v130 packages predate this feature and need a local refresh. P9/P11 device and
  full-resource-parity work plus hosted Actions remain open.
- Refreshed local artifacts as v131. Portable package passed isolated Hub/UI,
  three-source, read-only MCP, and standalone MCP stdio smokes. The sanitized
  115-file source archive passed privacy and extracted startup checks. Manifest
  verification passed for `local-20261012-p14-v131` (`local-worktree`, base
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`); source ZIP SHA-256:
  `8B4F67A23ACB9F3A6AF904765D68AB6B845A52C3861850FB5BF15AF227500F54`.
  This does not verify the hosted runner or GitHub artifact upload/download.

## 2026-10-12 - Documentation status correction

- Updated the roadmap's current local-gate summary to 425 Python and 17 WebUI
  tests and corrected the WebUI regression-script count. This is local evidence;
  hosted Actions and artifact upload/download remain unverified.
- Corrected the platform overview: the Python WeChat adapter reads bounded JPEG
  avatars from the exact supported `head_image.db` schema during explicit sync,
  matching contacts by exact username and caching bytes locally. Unsupported
  schemas fail closed; live-account/browser acceptance remains open.
- Before implementation, reviewed the MCP contract and confirmed QQ
  merged-forward content had no MCP tool. The contract now documents the new
  explicit, one-item, 8 MiB `get_qq_forward_attachment` reader separately from
  cache-only `get_attachment`.

## 2026-10-12 - Local v133 refresh

- Built the portable package with the QQ forward MCP tool and passed isolated
  Hub/UI, three-source, read-only MCP and standalone MCP stdio smokes. The
  sanitized 115-file source bundle passed privacy and extracted startup checks.
- Manifest byte/provenance verification and portable privacy scan passed for
  `local-20261012-p14-v133` (`local-worktree`, base
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). These artifacts include local
  worktree changes and are not evidence of hosted Actions or GitHub artifact
  upload/download acceptance.
- The final source archive is p134; manifest v134 binds it to the already
  smoke-tested portable p133 and verifies both package byte sets.

## 2026-10-12 - QQ merged-forward MCP media

- Added media-capability-gated `list_qq_forward_attachments` and
  `get_qq_forward_attachment` MCP tools. The first returns safe metadata and
  short-lived opaque handles; the second reads one selected attachment after
  revalidating account/conversation/message scope. Source names are reduced to
  basenames and control characters removed; bridge IDs, paths and URLs are not
  serialized.
- MCP response size is limited to 8 MiB, with declared-size rejection before
  download and a second bound after download. Added focused protocol and Hub
  identity/size tests; 62 MCP and QQ-provider tests pass. No real NapCat account
  was queried. Existing p133 packages predate this final two-tool flow.

## 2026-10-12 - Current roadmap gate and package refresh

- Re-ran `scripts/check-local-hub.ps1`: 427 Python tests and 17 WebUI tests
  passed, along with package privacy/manifest fixtures, syntax/compile checks,
  `git diff --check`, and the 25,000-message benchmark. Search took 0.070 s on
  the first query and 2.596 s across 50 cursor pages; backup/restore took
  0.077/0.220 s, with zero WAL failures.
- Re-verified manifest v137 against portable p135 and sanitized source p137;
  package hashes, sizes, commit SHA, run ID, and `local-worktree` context match.
  The final QQ `list_qq_forward_attachments` / `get_qq_forward_attachment`
  MCP flow is included in these artifacts.
- This is local evidence only. Hosted GitHub Actions and post-upload artifact
  download verification, real QQ/NapCat acceptance, real WeChat/WeCom account
  acceptance, and full WeFlow resource/decryption parity remain open.

## 2026-10-12 - Anti-recall safety gate

- Audited the pinned WeFlow anti-recall wrapper and current Hub capability. The
  upstream wrapper delegates trigger operations to a WCDB native component not
  included in this repository; its SQL effects, rollback and version behavior
  cannot be verified from the wrapper API.
- Added P12-03 as an explicit `NOT_STARTED` acceptance item. `anti_recall` stays
  default-off and server-forced unavailable. No source database was opened or
  modified. Reconsider only with an inspectable native implementation, isolated
  database backup/install/check/uninstall/rollback verification, and separate
  user approval before any real source-client mutation.
- The V2 scope also names Moments anti-delete/hide interception separately from
  chat anti-recall. Added P12-04 as its own `NOT_STARTED` safety gate; the feature
  remains unimplemented and unavailable pending the same independent review,
  isolated-database verification and approval requirements. The roadmap now has
  45 task rows: 37 DONE, 5 IN_PROGRESS and 3 NOT_STARTED (82.2% by row).

## 2026-10-12 - QQ login recovery, MCP gates, avatar rendering, and v141

- QQ login now reads the current local login phase before requesting another QR.
  If the phone has already scanned and the confirmation window was closed, the
  Login button reopens a status modal and keeps polling instead of requesting an
  unavailable replacement QR. A focused UI regression covers the scanned state.
- MCP now reports a missing `get_moment_media` identity as an argument error and
  filters `cross_platform_search` and `inspect_attachment` prompts according to
  the live `chat` and `media` capabilities. The MCP suite passes 25 tests.
- Synthetic avatar rendering now checks conversation rows, message senders,
  inspector details, and group rosters. This verifies generated same-origin
  image nodes without reading a real account or asserting browser pixels.
- The complete local gate passes 431 Python tests and 19 WebUI tests, package
  privacy/manifest fixtures, syntax/compile and diff checks. The 25,000-message
  benchmark reports 0.069 s first search, 2.683 s for 50 cursor pages, and zero
  WAL failures. A ResourceWarning for one closed test socket remains non-fatal.
- Portable p141 and the 117-file source bundle pass isolated startup, three-
  source UI, read-only MCP and standalone MCP stdio smokes, privacy scanning,
  and manifest provenance/byte verification (`local-20261012-p14-v141`,
  `local-worktree`, base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`). Hosted
  Actions and uploaded-artifact round-trip remain unverified.

## 2026-10-12 - Parallel roadmap follow-up

- P9 OneBot transport no longer follows redirects. HTTP 3xx is `unknown`, so
  authorization is not forwarded and no automatic retry occurs; a loopback 307
  regression confirms its target receives no request.
- P11 relationship achievements count WeChat `raw_type=50` records toward the
  upstream five-call milestone without parsing message bodies. P8/MCP tool
  discovery now hides local attachment, QQ gallery, and Moments media tools
  when their required readers are not configured; the safety/capability gates
  remain unchanged.
- Fixed the MCP client-configuration test to decode JSON and compare actual
  paths, rather than asserting a specific Unicode escape representation.
- The merged local gate passed 434 Python tests and all 19 WebUI tests,
  package privacy/manifest fixtures, syntax/compile and diff checks. The
  25,000-message benchmark reported 0.066 s first search, 2.580 s across 50
  cursor pages, and zero WAL failures. Expected synthetic QQ/Moments failure
  logs appeared; one closed-socket `ResourceWarning` remains non-fatal.
- Read-only remote audit found latest Local Hub CI run 38101275283 failed at
  commit `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`: missing CI `cryptography`
  and the old MCP path assertion; package build was skipped and there are no
  remote artifacts/releases. The local uncommitted CI workflow already pins
  `cryptography==45.0.7` and defines package upload/download verification; the
  path assertion is now fixed locally. No push or workflow was triggered, so
  hosted re-verification and artifact round-trip remain open.
- No real accounts, message sends, or source-client mutations were used. The
  latest local packages predate these changes and must be rebuilt before use.

## 2026-10-12 - QQ redirect delivery ambiguity

- The NapCat OneBot sender now refuses HTTP redirects. A 3xx response is
  classified as `unknown`, preventing authorization-header forwarding and
  avoiding an automatic second delivery attempt. A synthetic loopback 307 test
  verifies the redirect target receives no request; 12 sender tests pass.
- No QQ account was contacted. Live NapCat/device delivery acceptance remains
  open. Remote Actions repair and artifact upload/download verification remain
  separate P14 work.

## 2026-10-12 - WeChat file dispatch and v143 package acceptance

- Removed an unintended 32-bit mask from WeChat media `local_type` dispatch.
  Three large file-type values were already allowlisted but could never reach
  `download_file`; synthetic coverage now verifies all four supported variants.
- Package builders now keep portable/source outputs in temporary staging until
  privacy, startup and protocol smoke checks pass. A failed build no longer
  leaves a final-named artifact that blocks the next build.
- Portable v143 passed staged privacy scanning, isolated Hub/WebUI/three-source
  checks and standalone MCP initialize/tools/list/exit. Sanitized source v143
  (117 files) passed archive privacy and extracted startup checks. Manifest
  provenance and bytes passed for `local-20261012-roadmap-v143` with
  `local-worktree` context and base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- The current full gate passed 435 Python tests and 19 WebUI tests, fixtures,
  syntax/compile and diff checks. The 25,000-message benchmark reported 0.066 s
  first search, 2.573 s across 50 pages, 0.074/0.215 s backup/restore, and zero
  WAL failures. A closed-socket ResourceWarning remains non-fatal.
- P12-01's remaining evidence is real-device/cache and browser pixel acceptance;
  its nine focused synthetic/API tests passed, and the current account-safety
  endpoint reports unconfigured/unlocked. No real account was accessed.
- Hosted Actions has not rerun the known failed public CI run; package upload /
  download evidence is still absent. No push, message delivery or source-client
  mutation was performed. Local packages do not prove hosted acceptance.
- After this entry, the source bundle was refreshed to include these records as
  v144 and its manifest was re-verified against portable v143; the manifest run
  ID is `local-20261012-roadmap-v144` with `local-worktree` provenance.

## 2026-10-12 - P13 authorized identity feasibility

- Read the official WeCom CLI README and official team messaging instructions.
  They document bot-identity outbound messages to the bot owner's/recent bot
  sessions, including media; this is not evidence of sending as the logged-in
  employee identity required by P13. The CLI issue requesting employee-identity
  multimedia support is a feature request, not a supported contract.
- No supported local personal-WeChat send route was verified. P13 therefore
  remains NOT_STARTED; robot-identity sending is not substituted for the
  roadmap's employee-identity acceptance. No CLI/API call or real delivery was
  made. References: https://github.com/WecomTeam/wecom-cli,
  https://github.com/WecomTeam/wecom-unified/blob/main/skills/wecom-unified/references/wecomcli-message.md,
  https://github.com/WecomTeam/wecom-cli/issues/153.

## 2026-10-12 - WeChat quoted-message preview

- Added bounded type-57 quote extraction for WeChat type-49 messages and carry
  it through both cursor and offset sync. The renderer escapes both sender and
  content; unsupported quoted media is a placeholder. Synthetic parsing covers
  plain text, image placeholder, unsupported types, DTD and oversized XML.
- Verification: `python -m unittest hub.test_server -q` passed 136 tests,
  `node webui/test-wechat-location-render.cjs` passed, and `git diff --check`
  passed. No real account or source-client data was accessed.
- Remaining limit: exact quote navigation is implemented in the follow-up below;
  inline quoted media/card previews remain open, as does broader WeFlow resource
  parity.

## 2026-10-12 - QQ partial sync after per-conversation provider errors

- QQ history sync now catches a QCE provider error at the individual message
  fetch boundary, keeps that conversation's existing checkpoint, and continues
  with later conversations. The source is marked partial and exposes only the
  number of failed conversations, never their identities or error payloads.
- Added a synthetic good/failure/good Hub regression covering continued fetch,
  retained messages, partial state, count-only status, and unchanged checkpoint.
- A bounded read-only local probe confirmed QCE health/login readiness, one
  sampled first-page fetch success, and a provider error on the next sampled
  fetch. It was not a full sync; no chat content or account identifiers were
  recorded here. Real-account full history acceptance remains open.

## 2026-10-12 - QQ group history and WeChat avatar compatibility

- Kept QQ group history requests on the group endpoint when a normalized
  conversation exposes `kind: group`; added regression coverage. WeChat
  `head_image` avatar queries now accept additive database columns.
- Integrated validation: 488 Python tests, 2 source-mutation gates, 20 WebUI
  tests, and the 25k-message benchmark (0.029 s first search, 1.825 s / 50
  cursor pages, zero WAL failures).
- Real-account connection state and local endpoints are intentionally excluded
  from public development notes. Device-side group-history and avatar rendering
  acceptance remains open.
- Final sanitized source archive p189 (124 files), portable p190, and manifest
  p191 passed privacy, isolated-startup/read-only MCP smoke, and byte/provenance
  verification against base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.

## 2026-10-12 - Local WeChat connection and P14 package refresh

- The local WeChat connection and indexing result are omitted from this public
  log. No conversation body was opened or transmitted.
- Integrated verification passed 486 Python tests, 2 source-mutation gates,
  20 WebUI tests, Python/JavaScript syntax checks, and the 25,000-message
  benchmark (0.029 s first search, 1.384 s for 50 cursor pages, 0 WAL failures).
- Built Windows portable `p179`, sanitized source `p182` (124 files), and
  manifest `p183`; package privacy scans, extracted startup/read-only MCP
  smokes, and manifest byte/provenance verification passed. Provenance is
  `local-worktree` on base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- WeCom/QQ live accounts and hosted Actions/artifact round-trip remain
  unverified; the local packages are not hosted CI releases.

## 2026-10-12 - Moments media signatures and WeCom cache redirection

- Moments local cache parsing now recognizes AVIF/AVIS and HEIC-family
  ISO-BMFF brands separately from MP4 and rejects truncated/invalid `ftyp`
  box lengths. The classification follows the pinned WeFlow `snsService.ts`
  behavior; synthetic coverage also keeps forced image/video resolution scoped.
- WeCom bot attachment storage rejects an existing or runtime-swapped
  `wecom-bot-outbox` symlink before staging, loading, removal, or orphan cleanup.
- Verification: 31 focused Python tests passed across Moments, WeChat provider,
  WeCom bot media, and bot credentials. No network, real credentials, live send,
  or source-client mutation was used. A concurrent symlink replacement between
  validation and filesystem operation is still a residual race.

## 2026-10-12 - P11 query index and P13 attachment cleanup

- Added the schema-v9 `(platform, timestamp)` index used by platform-scoped
  newest-first search. On 90,000 synthetic rows, 30 searches took 0.034 s with
  the index versus 0.491 s without; a query-plan regression confirms the index
  also avoids a temporary sort. The extra index consumes local disk space.
- WeCom bot send cleanup now covers credential lookup and attachment
  revalidation failures. Synthetic checks prove staged blobs are removed in
  both cases; nothing is sent.
- `scripts/check-local-hub.ps1` passed 483 Python tests, two source-mutation
  gates, 20 WebUI tests, package privacy/manifest/startup/MCP checks, syntax and
  compile checks, and the 25,000-message benchmark (0.028 s first search,
  1.961 s for 50 cursor pages, zero WAL failures). RuntimeError failure-injection
  lines and closed-socket ResourceWarnings were logged despite the passing gate.
- The local UI responded on 8767 but had zero connected sources. No live
  account, avatar/media cache, NapCat, bot credentials or source DB was used.
  GitHub Actions and artifact round-trip still require hosted verification.
- Portable p174 and sanitized source p175 passed local isolated startup, source
  privacy and MCP smokes. Manifest p176 verifies both package inventories and
  `local-worktree` provenance on base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
  GitHub Actions and actual artifact download remain unverified.

## 2026-10-12 - P11 QQ audio signatures and report chart accessibility

- QQ forward-resource validation now accepts QCE-exported AMR/AMR-WB, ADTS AAC,
  and M4A-family audio signatures, while generic `isom` video remains rejected
  as voice. The QQ provider suite passed 48 tests.
- Annual and pair-report hourly charts expose a UTC+08 accessible name and mark
  individual decorative bars hidden from assistive technology. The focused
  annual/pair export and range regression passed.
- These are synthetic/local checks only; real-device media and browser
  accessibility acceptance remain open, as does full WeFlow media parity.

## 2026-10-12 - Send capability approval revocation

- Disabling either QQ or WeCom bot sending now clears outstanding one-time
  approval challenges, preventing a token from surviving an off/on cycle.
- Verification: the focused HTTP regression covers both sender routes, rejects
  stale challenges without delivery, and confirms a fresh challenge remains
  usable. The existing QQ and WeCom approval route regressions also pass.
- `scripts/check-local-hub.ps1` passed: 479 Python tests, two source-mutation
  gate tests, 20 WebUI tests, privacy/manifest fixtures, isolated startup and
  MCP smoke checks, syntax/compile checks, and the 25,000-message benchmark
  (0.066 s first search, 3.305 s for 50 cursor pages, zero WAL failures).
- Portable p166 passed bundled UI/source/isolated-storage and standalone MCP
  stdio smokes; sanitized source p167 passed archive privacy and extracted
  startup checks. Local manifest p168 verified both package byte inventories
  and `local-worktree` provenance. Hosted Actions and upload/download remain
  unverified.
- No live accounts or message delivery were used.

## 2026-10-12 - QQ cache gallery audio preview

- Normalized QQ gallery `audio` to the shared `voice` preview kind. Cached
  audio now opens in an HTML audio player; other media preview/download paths
  are unchanged.
- A synthetic WebUI regression verifies audio-player markup. Real QCE/cache
  playback was not exercised; P11 resource parity remains incomplete.

## 2026-10-12 - P13 WeCom bot media preparation and delivery

- Extended the opt-in independent WeCom bot UI from text to image, file, AMR
  voice and video. A loopback binary upload accepts bytes, never a caller path;
  extension/signature/size are checked on upload and the opaque staged item is
  revalidated immediately before protocol upload. Review shows attachment name
  and type; approval stays local and one-time. Attachments are removed after
  send attempt or rejection.
- Implemented the documented WebSocket media flow in the in-repo stdlib
  adapter: matching media type, init/chunk/finish, 512 KiB chunks, at most 100,
  then send the returned media_id. Media uses a bounded 300-second total
  deadline; text retains its 8-second deadline. MCP remains text-only and
  cannot deliver.
- Synthetic verification: focused sender/media, SendService, MCP and WeCom HTTP
  tests (50 cases), `node webui/test-wecom-bot-media-compose.cjs`, and
  `node webui/test-send-history-render.cjs` passed; JS syntax check passed. No
  account, credential, device, network delivery or package
  build was used. Real WeCom media constraints and receipt behavior remain
  subject to on-device acceptance.

## 2026-10-12 - Resolve WeChat quote targets through MCP

- Added `get_quoted_message_context`, keyed by exact platform/account/
  conversation/reference-message identity. MCP derives the local source ID from
  indexed quote metadata, returns only the normalized target ID and bounded
  same-conversation context, and fails closed for absent, ambiguous, or
  mismatched targets.
- Verification: focused synthetic MCP test passes, including cross-conversation
  rejection, duplicate-ID ambiguity, and rejection of caller-supplied raw source
  IDs. No real data was accessed.

## 2026-10-12 - WeChat quoted-message navigation

- Retained source `server_id` as an exact string through both WeChat sync paths
  and indexed it only as Hub-internal metadata. Type-57 `refermsg.svrid` now
  resolves only when the referencing message and one unique target both belong
  to the requested WeChat account and conversation; the UI then opens the
  existing context view. Missing, mismatched, or duplicate IDs fail closed.
- Focused parser/provider/HTTP and renderer/navigation regressions passed. No
  source account was read or modified. Existing indexes need a local sync to
  acquire source-ID mappings; full quoted media/card previews remain open.

## 2026-10-12 - Opt-in WeCom bot text sender

- Added a separate, default-off WeCom bot-identity Markdown text route with
  current-user Windows DPAPI credentials, request-bound local approval, and
  conservative unknown/no-retry handling. It is explicitly not the logged-in
  employee identity; personal WeChat, media sends, and MCP bot-send tools remain
  out of scope.
- Verification: focused adapter, credential-vault, send-service and HTTP route
  tests plus all 138 `hub.test_server` tests pass using synthetic
  transports/credentials only. JS syntax, Python compile and `git diff --check`
  pass. No real bot was configured and no message was sent; target reachability
  and acknowledgement semantics remain unverified on a real WeCom device.

## 2026-10-12 - WeCom bot MCP prepare/status tools

- Added capability- and configured-identity-gated MCP prepare/status tools for
  WeCom bot requests. Preparation requires the configured bot ID, status is
  restricted to bot requests, and MCP cannot approve, reject, or deliver. This
  supersedes the earlier sender entry's note that MCP bot-send tools were out
  of scope; personal WeChat and media sending remain unavailable.
- Verification: focused MCP tests cover the configured bot ID, request
  platform isolation, no delivery during preparation, disabled capability, and
  missing credentials. They use synthetic identities and make no live send.

## 2026-10-12 - Validate OneBot delivery receipts

- OneBot `message_id` must now be a JSON integer within signed int32 range.
  Malformed success receipts raise the delivery-unknown outcome so the send
  ledger cannot falsely mark them sent or retry them automatically.
- Verification: `python -m unittest integrations.qq.test_sender -q` passed 13
  tests; `git diff --check` passed. No NapCat runtime or real account was used.

## 2026-10-12 - Report time-range selection

- Annual and pair reports now support both all-time and single-year queries.
  The UI passes no year for all-time, and the report subtitle/CSV metadata and
  filename label the selected range consistently.
- Verification: 154 `hub.test_analytics` + `hub.test_server` tests passed;
  `node webui/test-pair-report-export.cjs` passed. No real account was used.

## 2026-10-12 - P14 local package refresh

- Portable v164 passed the staged privacy audit and isolated packaged Hub/UI,
  three-source, read-only MCP, and standalone MCP stdio checks. The sanitized
  117-file source v172 passed archive privacy and extracted startup checks.
- Local manifest v172 binds both packages to `local-worktree` provenance on
  base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; exact byte verification
  passed. Hosted Actions and uploaded-artifact download remain unverified.

## 2026-10-12 - P14 final local refresh

- Portable v164 passed package privacy and isolated executable/MCP startup
  checks. Sanitized source v174 passed archive privacy and extracted startup;
  manifest v174 passed exact byte/provenance verification.
- The working tree remains uncommitted on `main` at initial commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; no push or hosted Actions run
  was triggered. Hosted artifact upload/download is still unverified.

## 2026-10-12 - Synthetic avatar browser acceptance

- An isolated Hub with one synthetic conversation/contact/message and a
  synthetic PNG avatar returned `200 image/png`; Edge headless rendered the
  conversation row, message, and inspector avatars. Screenshot pixel samples
  confirmed visible non-empty avatar pixels at all three locations.
- No source, real account, or existing local server was accessed or changed.
  Real-account avatar acceptance remains open. The temporary synthetic data
  directory could not be removed because local policy denied deletion; it is
  isolated under `%TEMP%` with no running service.

## 2026-10-12 - Parallel QQ and package privacy follow-up

- QQ merged-forward media validation now accepts standard and QCE
  `0x02`-prefixed SILK V3 signatures. Package privacy scanning includes
  redirected `APPDATA`, with a synthetic path regression outside
  `USERPROFILE`.
- Re-audited WeChat media parity; made no additional format claim without an
  authoritative format reference.
- Verification: `python -m unittest integrations.qq.test_provider -q` passed
  46 tests; `scripts/test-package-privacy.ps1` passed the redirected-APPDATA
  regression; `git diff --check` passed (line-ending warnings only). No
  NapCat/live account or hosted Actions run was started. Real-device media and
  hosted upload/download acceptance remain outstanding.

## 2026-10-12 - Export privacy and account-lock concurrency

- JSON message export removes raw top-level attachment aliases and emits only
  allowlisted attachment metadata, preventing local cache paths and URLs from
  leaking through `attachments`/`resources` fields.
- Account-lock disable holds the shared lock while verifying and removing the
  passphrase, preventing concurrent passphrase rotation from being erased.
- Verification: `hub.test_store.ExportTestCase` (19),
  `hub.test_account_safety` (4), the account-lock HTTP gate regression (1), QQ
  provider tests (46), package privacy fixture, and `git diff --check` passed.
  No real accounts, client databases, NapCat, or hosted CI were touched.

## 2026-10-12 - Local package refresh v163/v169/v170

- Rebuilt the Windows portable package with the current source and bundled
  hash-verified QCE v6.3.2. Its privacy scan, isolated Hub/UI/three-source,
  read-only MCP, and standalone MCP stdio protocol smokes passed.
- Rebuilt and extracted the 117-file sanitized source ZIP (v169); its privacy and
  isolated Hub startup/read-only MCP checks passed.
- Manifest v170 passed byte and provenance verification for both packages,
  using local-worktree provenance on base commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This does not replace hosted
  Actions or actual GitHub artifact upload/download acceptance.

## 2026-10-12 - Parallel P11/P12/P14 hardening

- QQ forwarded media now recognizes standard BMP signatures. Avatar URL checks
  reject backslash paths that browser URL normalization could send cross-origin.
  Standalone MCP package smoke now isolates user/app/platform paths and disables
  the loopback QCE endpoint, with assertions for environment restoration.
- Verification: the complete local gate passed 452 Python tests, two source-
  mutation gate tests, and 19 WebUI tests. The 25,000-message benchmark passed
  at 0.067 seconds for first search and 2.591 seconds for 50 cursor pages, with
  zero WAL failures. Portable v161, sanitized source v166, and local manifest
  v167 are rebuilt after this record and verified against package bytes with
  `local-worktree` provenance. This does not replace live QQ, avatar-browser,
  real-device media, hosted CI, or uploaded-artifact checks.

## 2026-10-12 - QQ/WeChat media and P12 render follow-up

- QQ normalization now preserves `faceIndex=0`. WXGF embedded payload scan also
  recognizes GIF, WebP and BMP within the existing bounded prefix. Avatar
  rendering reuses one validated source URL per render instead of parsing it
  twice. No account databases, caches, or client runtime were accessed.
- Verification: `integrations.qq.test_provider` and
  `integrations.wechat.test_media` passed 91 tests; avatar-cache rendering,
  package privacy/manifest/smokes, and the full local gate passed. Current gate:
  454 Python tests, two source-mutation gate tests, 19 WebUI tests; benchmark
  first search 0.067 seconds, 50 cursor pages 2.667 seconds, zero WAL failures.
  `npm run test:wcdb` passed 44 tests and `npm run typecheck` passed both
  renderer and Electron configurations.
- Portable v162, sanitized source v167, and manifest v168 are built after this
  record and byte/provenance verified with `local-worktree` provenance. Hosted
  CI and upload/download round-trip, live QQ, and real-device WeChat acceptance
  remain open.

## 2026-10-12 - QQ long-message privacy and WeChat V1 image validation

- QQ `structLongMsgElement` is normalized to a bounded summary; QCE resource
  IDs and XML payload are intentionally omitted.
- WeChat V1 DAT XOR output must match a supported image signature before it is
  accepted, preventing wrong-key bytes from being cached as an image.
- Verification: 94 focused QQ/WeChat tests and two QQ UI regressions passed;
  16 account-safety/auto-export/Moments tests passed. `git diff --check` passed.
  No real account or source-client data was accessed.
- P14 local refresh v149/v152 with manifest v153 passed isolated package,
  privacy, manifest and smoke-fixture checks. Hosted Actions and artifact
  upload/download remain unverified.

## 2026-10-12 - WeChat original-image integrity and gated feature audit

- `_image_complete` now accepts JPEG/PNG footer bytes only within the existing
  32-byte container-footer rule, then requires the complete format end marker.
  Synthetic valid-footer and invalid-trailing-payload cases pass.
- Audited the pinned WeFlow WCDB wrapper path and current repository: neither
  contains the native trigger implementation. Anti-recall and Moments
  interception remain unavailable until source/build/license are auditable,
  isolated lifecycle and rollback tests pass, and source mutation is separately
  approved. No real source database was accessed.
- P13 audit confirmed the documented WeCom AIBot sender is a robot identity;
  it does not meet current-employee identity acceptance. No supported local
  personal-WeChat send route is verified; no message was sent.
- Verification: 111 focused QQ/WeChat and P12 Python tests passed. Hosted
  Actions and real-account acceptance remain outstanding.
- P14 local v150/source v154 refresh passed isolated startup, privacy and
  manifest verification (v155, local-worktree). The source archive and
  manifest are refreshed after this entry to include it. Hosted CI and
  artifact upload/download remain unverified.

## 2026-10-12 - QQ live-gift rendering and WeChat XOR fallback

- QQ `liveGiftElement` is now reduced to a bounded summary and rendered as an
  escaped card; synthetic coverage confirms recipient/token payloads do not
  reach the UI.
- Legacy WeChat XOR decoding honors the explicit key or the local database
  `cfg_dword` low byte before legacy fallback candidates.
- V2 image decryption rejects outputs that fail the supported image signatures,
  preventing a wrong AES key from being cached as a successful image.
- Verification: 90 focused Python QQ/WeChat media tests and two QQ WebUI tests
  passed. No real QQ or WeChat account data was accessed.

## 2026-10-12 - P14 local refresh v148/v151

- Portable v148 passed staged privacy and isolated Hub/UI/three-source and
  standalone MCP stdio checks. Sanitized source v150 passed archive privacy and
  extracted startup checks.
- Manifest v151 verified package bytes and local-worktree provenance; privacy
  and manifest fixtures passed. Smoke subprocess environment isolation was
  checked with synthetic paths. Hosted Actions and upload/download remain open.

## 2026-10-12 - P14 v146 local package refresh

- Rebuilt Windows portable v146 after the WeChat quote implementation. It
  passed staged privacy checks, isolated Hub/UI/three-source and read-only MCP
  checks, and standalone MCP initialize/tools/list/exit.
- Rebuilt the 117-file source v146; archive privacy and extracted startup
  checks passed. Manifest byte/provenance verification, package privacy, and
  manifest fixtures passed for `local-20261012-roadmap-v146` with
  `local-worktree` provenance on base `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- After recording this entry, source v147 and manifest v147 were refreshed
  around the unchanged portable v146 to include the current documentation.
  Hosted Actions and upload/download round-trip remain unverified.

## 2026-10-12 - WeChat quoted-message preview

- Added bounded type-57 quote extraction for WeChat type-49 messages and carry
  it through both cursor and offset sync. The renderer escapes both sender and
  content; unsupported quoted media is a placeholder. Synthetic parsing covers
  plain text, image placeholder, unsupported types, DTD and oversized XML.
- Verification: `python -m unittest hub.test_server -q` passed 136 tests,
  `node webui/test-wechat-location-render.cjs` passed, and `git diff --check`
  passed. No real account or source-client data was accessed.
- Remaining limit: quote navigation and full media/card previews are not
  implemented; broader WeFlow resource parity remains open.

## 2026-10-12 - QQ live sync attempt

- A local QQ sync attempt exposed a Hub/runtime readiness mismatch. Account
  login state, counters, and local endpoints are intentionally excluded from
  this public log. No message bodies were opened or transmitted, and QQ/NapCat
  was not restarted or reconfigured.

## 2026-10-12 - QQ and WeChat local connection continuation

- Real-account connection states, sync counters, and local endpoints are
  intentionally excluded from this public log. No message bodies were opened
  or transmitted. QQ/NapCat was not restarted or reconfigured. Hosted Actions
  and artifact upload/download were not exercised.

## 2026-10-12 - QQ message-page timeout diagnosis

- A bounded read-only probe showed QQ message-page requests can outlast the
  provider's general request timeout.
- QQ `/api/messages/fetch` now has an independent 30-second timeout cap;
  unrelated QCE requests retain the existing timeout. All 49 focused QQ
  provider tests pass, including timeout-scope regression coverage.
- The running Hub has not loaded the updated code, so no retry/full QQ sync was
  started. No message content was opened or output, and NapCat/QQ was not
  restarted or reconfigured.

## 2026-10-12 - P9/P12 live account and current local gate

- Real-account login state, sync counts, and local endpoints are intentionally
  excluded from public development notes. Full-history and real-cache avatar
  acceptance remain open.
- No chat body was opened or transmitted; no message was sent and no source
  database was modified. P11 fixed-version WeFlow JS fallback audit found no
  demonstrable synthetic format gap in the Hub implementation; all 46 focused
  WeChat media tests passed. Opaque native-addon formats and real cache parity
  remain unverified.
- `scripts/check-local-hub.ps1` passed: 492 Python tests, 2 source-mutation
  gate tests, 20 WebUI tests, privacy/manifest/package-smoke fixtures, and the
  25,000-message benchmark (0.030 s first search, 1.602 s for 50 pages,
  0.080 s backup, 0.238 s restore, zero WAL failures). `npm run typecheck`
  passed for renderer/Electron; `npm run test:wcdb` passed all 44 tests.
- Current Windows portable/source packages have not yet been rebuilt. Hosted
  Actions and artifact upload/download remain unverified; the newest public
  Actions run is still the failed run on the unchanged remote commit.
- QQ chat detail now offers a per-conversation sync retry. The HTTP/API path
  rejects non-QQ, malformed and overlong targets, matches the ID against the
  currently online QCE account, and scans only that conversation through the
  existing checkpoint transaction path. The targeted retry API tests and
  `webui/test-qq-targeted-sync.cjs` pass. The 2026-10-12 full local gate now
  passes 496 Python tests, 2 source-mutation gate tests, 21 WebUI tests and the
  25,000-message benchmark (0.028 s first search, 1.393 s pagination recheck,
  0.076 s backup, 0.230 s restore, zero WAL failures). Windows packages still
  need refresh after this addition; hosted artifact round-trip remains open.

## 2026-10-12 - P11 sticker query parity and P13 bot identity binding

- Chat-source media queries now include indexed WeChat type-47 stickers and
  classify them as stickers, while ordinary image filtering continues to
  exclude them. A focused synthetic store regression covers all/chat/image/
  sticker query combinations.
- WeCom bot preparation now requires the requested account ID to match the
  configured bot ID. Delivery rechecks that binding against current
  credentials, so credential rotation after approval fails closed. Focused
  HTTP and delivery regressions verify rejection and no transport call.
- Four focused store/server tests pass. No real bot delivery or source-client
  mutation was performed; P11 resource parity and P13 live acceptance remain
  open.

## 2026-10-12 - P11 MCP sticker filtering

- MCP `list_media` now advertises and accepts the existing `sticker` category.
  A synthetic WeChat type-47 message is returned by the sticker filter and
  remains excluded from ordinary image results. The capability is metadata
  listing only and does not read attachment bytes.
- The focused MCP/store test passes; full P11 resource parity remains open.

## 2026-10-12 - P14 current-worktree Windows package refresh

- Rebuilt the Windows portable package (`p211`) and 127-file sanitized source
  archive (`p212`) from the current worktree. The portable package
  served its UI, three-source API and read-only MCP endpoint from isolated
  storage; its standalone MCP executable completed initialize/tools/list/exit
  over stdio. The source archive passed privacy scanning and extracted startup
  smoke checks.
- Package-manifest (`p212`), package-privacy, package-smoke and staged portable
  privacy checks pass. A `local-worktree` manifest verifies all package bytes against
  the recorded base commit and local run label; it does not claim the dirty
  worktree itself is a committed revision.
- Focused P11 MCP/store regressions pass (71 store/MCP tests). Hosted CI and
  upload/download round-trip remain unverified; P14 stays IN_PROGRESS.

## 2026-10-12 - P11 account-scoped WeChat cache backup

- WeChat Hub media cache identities now include the account ID. Optional media
  backups derive cache directories only from indexed media rows in the SQLite
  snapshot, include their regular files, and restore only after path, index
  identity, size, and SHA-256 validation. Old unscoped caches, unreferenced
  cache directories, and source-client files are excluded.
- Focused backup/provider tests pass (17 tests). Real-account cache portability
  and cross-device media availability remain unverified; P11 stays IN_PROGRESS.

## 2026-10-12 - P11 MCP analytics tools

- Added bounded MCP tools for private/group chat profiles, annual reports, and
  exact-scope private pair reports. Each reuses `AnalyticsService` and its
  existing capability gate; group/member pagination is capped at 50 and
  conversation-level annual reports require platform plus account identity.
- Synthetic MCP coverage verifies account isolation, body-free results,
  group rejection for pair reports, and hidden/direct-denied tools after
  capability revocation. The stale backup wording in the feature matrix now
  reflects account-scoped WeChat Hub-cache inclusion and excluded legacy/source
  caches. Bounded group roster listing is available; broader resource parity
  and live-device acceptance remain open.

## 2026-10-12 - P14 local package refresh p213/p215

- Rebuilt the portable Windows bundle as p213 and the 127-file sanitized
  source ZIP as p215 after the MCP analytics changes and final documentation
  update. Both isolated startup smoke paths passed; the portable executable
  also completed standalone MCP stdio initialization and tool listing.
- Local-worktree manifest p215 binds both artifacts and verifies their SHA-256
  and sizes. Package manifest, privacy, and smoke fixtures pass. Hosted CI and
  hosted artifact upload/download round-trip remain unverified.

## 2026-10-12 - QQ login diagnostics and public fixture cleanup

- `/api/qq/start` now returns the QQ provider's allowlisted launch diagnostic
  with HTTP 503 instead of replacing it with a generic local-operation error.
  Unknown errors still reduce to their exception type; paths, credentials and
  QR URLs are not returned. The focused server/provider suite passed 51 tests.
- Replaced personal-looking QQ identities, group names, export times and one
  chat-text example in vendored test/demo fixtures with synthetic values. A
  targeted residual scan and `git diff --check` passed. These are fixture-only
  edits; Rust formatting/Cargo tests were not run (`rustfmt` and target dirs
  are unavailable), and the upstream plugin test could not load its missing
  `tsx` dependency.
- Runtime QR login and device history acceptance remain open. No second QQ
  client was launched. Windows packages need refresh after the current
  worktree changes; hosted Actions and artifact upload/download remain open.

## 2026-10-12 - P9 local QQ QR handoff

- Restarted the local Hub through the repository-managed runtime after its
  inherited QCE configuration produced an unavailable login status. The
  loopback login endpoint now reports the waiting-for-QR state, and the local
  UI renders its QR modal.
- The user still needs to scan and confirm on their phone. No account login,
  roaming-history acceptance, message send, or source-client mutation is
  claimed. The QR and account-specific state are intentionally omitted here.

## 2026-10-12 - P12 Moments Live Photo media classification

- Normal-media lookup excludes the nested `livePhoto` subtree so Live Photo
  URLs and thumbnails are not duplicated as ordinary media. The separately
  parsed `livePhoto` object remains available.
- `python -m unittest integrations.wechat.test_moments` passes (9 tests).
  Real-account Moments media/cache acceptance remains open.

## 2026-10-12 - P11 Markdown/TXT account provenance

- Human-readable message exports now include the exact `account_id`, keeping
  identical conversation/message IDs distinguishable across accounts.
- The focused two-account collision regression passes. No real chat data was
  used; full WeFlow export/resource parity remains open.

## 2026-10-12 - P11 cached WeChat video posters

- WeChat media-gallery video tiles now lazy-load existing cached JPEG posters
  or thumbnails via the read-only provider, Hub resolver, and same-origin media
  token. Poster state remains separate from MP4 playback; a missing poster does
  not make the video unavailable.
- Focused provider/API and browser regressions pass. No remote fetch or source
  write was added; complete resource/decryption parity remains open.

## 2026-10-12 - P9 QCE message-cache retention

- Latest-page QCE cache keys are stable instead of embedding the current time,
  and each fetch removes expired entries across peers before lookup.
- Two focused Rust cache tests pass. This reduces avoidable retained entries;
  it does not establish the cause of the live QCE process's large memory use.
  The running QCE process was not restarted, preserving the pending QR session.

## 2026-10-12 - P11 avatar-source and P13 staged-media audit

- The P11 audit confirmed that only WeChat currently has an offline avatar-byte
  reader. QQ and WeCom provide avatar URLs, but the Hub deliberately does not
  fetch them; six focused existing avatar/MCP tests pass. Three-platform avatar
  parity remains open until an approved local source is identified.
- P13 startup cleanup now retains staged attachment files only for live
  pending/sending requests and runs after interrupted sends are recovered.
  `python -m unittest hub.test_sending` passes all 12 tests. No real delivery or
  remote WeCom media-retention behavior was exercised.

## 2026-10-12 - P11 cached AVIF/HEIC MCP images

- The bounded local-cache signature reader now recognizes AVIF and HEIC brands
  in ISO-BMFF images, allowing explicit MCP attachment reads to return them as
  image content with the corresponding MIME type.
- `python -m unittest hub.test_server.ServerTest.test_mcp_image_read_accepts_cached_avif_and_heic hub.test_server.ServerTest.test_mcp_image_read_returns_cached_bmp_as_image_content -q`
  passes. Synthetic QQ cache fixtures only; no real account data or network.

## 2026-10-12 - P13 media approval parity

- WeCom bot media requests now reject accompanying text at the shared send
  validation boundary. The composer clears and disables text when media mode
  is selected, keeping the approval preview equal to the delivered payload.
- `python -m unittest hub.test_sending.SendServiceTest.test_wecom_media_request_rejects_unreviewed_text -q`
  and `node --test webui/test-wecom-bot-media-compose.cjs` pass. No real bot
  delivery was attempted.
- The first offline Rust cache test run exposed an incorrect size-difference
  assertion in the new estimator test; corrected it to assert growth and reran
  the focused cache suite successfully (4 tests). Cargo emitted one unrelated
  existing unused-import warning in `resources.rs`.

## 2026-10-12 - P11 QQ forwarded AVIF/HEIC signatures

- QQ merged-forward image resources now accept bounded AVIF/HEIC ISO-BMFF
  brands and continue rejecting generic MP4 brands.
- Synthetic provider signature tests cover AVIF, HEIC, and MP4 rejection; no
  QCE request or real account media was accessed.

## 2026-10-12 - P14 source-only package refresh

- The current 127-file sanitized source archive passed the privacy scan and
  extracted-package WebUI, three-source, isolated-storage, and read-only MCP
  extracted-package Hub/WebUI/three-source/read-only-MCP smoke.
- This source-only result does not match a current portable bundle or
  provenance manifest; no package upload or publication was performed.

## 2026-10-12 - P11 pair-report year boundary coverage

- Added a synthetic pair-report regression spanning 2024-12-31 23:30 and
  2025-01-01 00:00 UTC+08, proving each yearly report includes only its own
  calendar-year messages.
- The focused test and all 18 `hub.test_analytics` tests pass. No analytics
  implementation change or real chat data access was needed.

# 2026-10-12 QQ login status clarification

- The QQ login modal now distinguishes an explicitly unavailable login-status
  endpoint from an active verification phase. Added focused assertions for the
  unavailable and waiting-for-scan labels. The local NapCat instance responds
  to direct status/QR reads, but the running Hub at `127.0.0.1:8765` currently
  reports its login-status endpoint unavailable; no account scan or history
  sync has been completed.

# 2026-10-13 QQ QR handoff status

- A direct read-only call to the repository-managed NapCat provider returned a
  fresh `waiting_qrcode` challenge. The Hub at `127.0.0.1:8772` still reports
  login status unavailable; QCE history ports `40653` and `40654` are not
  listening. The challenge URL is intentionally not recorded here. No scan,
  history read/sync, or process restart was performed.

## 2026-10-12 - P9 NapCat QR refresh fallback

- When explicit QR refresh fails, QQ launch now tries to retrieve the existing
  QR before entering the new-process launch path. A provider regression proves
  this fallback returns without starting or reconfiguring QQ.
- This addresses the observed retry shape but does not explain why the active
  Hub login-status API differs from direct provider status, nor establish a
  successful device scan.

## 2026-10-12 - P11 pair-report hour buckets

- Pair-report charts now use 24 fixed hourly buckets, so absent hours render as
  zero-count bars and the UTC+08 hour labels stay aligned with sparse data.
- `node webui/test-pair-report-export.cjs` and `node --check webui/app.js` pass;
  this synthetic render check does not complete the remaining P11 media and
  real-cache acceptance work.

## 2026-10-12 - P14 refreshed source bundle

- Pair-report and P13 approval-review regressions pass alongside the package
  manifest, privacy, and isolated source-smoke scripts on the current worktree.
- Source-only package `LocalMessageHub-source-roadmap-20261012-p227.zip`
  includes those changes and passed the package privacy scan plus
  extracted Hub/WebUI/three-source/read-only-MCP smoke using isolated empty
  storage. It is not paired with a portable bundle or provenance manifest;
  hosted CI and artifact upload/download remain unverified.

## 2026-10-12 - P13 send approval review coverage

- The send-history WebUI regression now renders QQ text and WeCom bot
  attachment approval reviews and checks visible identity, target, content,
  request ID, and request-bound approve/reject actions. It does not invoke a
  delivery endpoint.

## 2026-10-12 - P9 QQ QR handoff resumed

- The prior NapCat process was no longer running, so the bundled QQ login flow
  was restarted and a fresh QR was generated. The Hub was restored at loopback
  port 8772 with the prior temporary storage and automatic synchronization
  disabled. `/api/sources` returned three providers and
  `/api/qq/login-status` returned `waiting_qrcode`, offline, and available.
- The user has not confirmed a scan; no history/media sync or message delivery
  was performed.

## 2026-10-12 - P14 local Windows package set

- The first portable build was correctly blocked by the privacy scanner after
  staging generated Rust `target` metadata. The portable package copier now
  excludes `target` directories; the rebuilt Windows x64 package passed the
  pinned QCE v6.3.2 hash check, privacy scan, packaged Hub/WebUI/three-source
  smoke, and standalone MCP stdio smoke.
- Source bundle and portable output were bound by a `local-worktree` SHA-256
  manifest; verification passed for all package bytes. Hosted Actions and a
  real hosted artifact upload/download round trip remain unverified.

## 2026-10-12 - P14 current-worktree package refresh

- Rebuilt source archive p232 and portable package p231 after the pair-report
  year-boundary test was added. The source package privacy scan and isolated
  startup smoke passed; portable startup and standalone MCP stdio smokes passed.
- A `local-worktree` manifest verified exact package bytes and provenance.
  The extracted source archive's roadmap and analytics regression match the
  worktree. Hosted Actions and uploaded-artifact download remain unverified.
## 2026-10-13 - P11 QQ avatar host parity

- Added QCE's exact private-chat (`q1.qlogo.cn`) and group (`p.qlogo.cn`)
  avatar hosts to the WebUI URL validator and Hub's local-cache reader.
- UI and Hub tests cover same-origin proxying, signature-valid synthetic local
  cache hits, cache misses, and hostile lookalike rejection; the network fetch
  function is patched to fail during cache tests.
- This does not download avatars or prove that real QQ avatar bytes exist in
  the local Hub cache.

## 2026-10-13 - P12 notification click target regression

- Added a synthetic WebUI test proving a body-free notification click selects
  the exact platform, account, and conversation, then focuses and loads it.
- The focused test passes. Real browser/system notification permission and
  click behavior still require local runtime acceptance.

## 2026-10-13 - P14 local package refresh and hosted CI dependency audit

- The current portable and sanitized source packages passed isolated startup
  and privacy checks. Their local-worktree manifest verified both package
  inventories and bytes against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- A clean-checkout workflow review confirmed six package/test helper scripts
  are required by `.github/workflows/local-hub-ci.yml` but remain untracked in
  this worktree. Hosted CI and artifact round-trip cannot be verified until
  those scripts enter a reviewed commit; no commit or push was performed.

## 2026-10-13 - P11 QQ cache date filtering

- Added inclusive UTC+08 date controls to the QQ global-cache media view.
- Hub passes validated millisecond bounds to QCE. Vendored QCE source applies
  them before count and pagination in one local directory scan and returns an
  explicit capability marker. The provider fails closed if an older runtime
  omits that marker; it never presents unfiltered rows as date-filtered results.
- Provider, HTTP integration, and WebUI regression checks cover boundary
  conversion and visible controls. The bundled QCE binary predates this source
  change, and this environment has no `cargo`; rebuilding and runtime
  acceptance remain outstanding. The API does not expose cache paths or URLs.

## 2026-10-13 - P11 QQ forwarded MP4 preview

- QQ merged-forward attachments identified as `video/mp4` are now read through
  the existing bounded attachment loader and shown in a native controls-enabled
  video player, with a separate download action retained. Preview Blob URLs use
  the existing rerender cleanup lifecycle.
- `webui/test-qq-forward-attachments.cjs` covers automatic MP4 retrieval,
  inline player markup, download action, and URL cleanup. This is synthetic UI
  coverage only; real cached MP4 availability and browser playback remain
  unverified. No source database or live chat content was read.

## 2026-10-13 - P11 Original SILK download fallback

- When local decoding is unavailable or fails, the identity-bound media
  resolver now serves the original SILK through its random local media token;
  successful decoding continues to serve the WAV instead.
- Fallback requires a SILK signature and containment in the platform's managed
  cache root (WeChat, WeCom, or QQ). Account/message mismatch and path escape
  do not issue a token. Synthetic HTTP tests verify the bytes and boundaries;
  no source database or live chat content was read.
- The WebUI now downloads the original `.silk` instead of presenting an
  unsupported browser audio player. Existing WMA and playable-audio behavior is
  unchanged; focused Hub and WebUI regressions cover the returned type/name.

## 2026-10-13 - P12 automatic-export cancellation boundary

- Added a regression that cancels after the temporary export has been written,
  flushed, and fsynced but before it is atomically published.
- The focused test confirms the job reports `cancelled` and leaves neither a
  `.part` file nor a final export. No production behavior changed; the existing
  `finally` cleanup passed this late-cancellation boundary.

## 2026-10-13 - P13 staged WeCom attachment cleanup

- A failed bot-send prepare now removes the staged attachment only when no
  pending/in-flight request retains its opaque ID. A failure after the request
  commits leaves its attachment available for review, avoiding a broken pending
  request.
- Synthetic HTTP tests verify both pre-commit cleanup and post-commit retention;
  neither path delivers a message. Real bot credentials and delivery were not
  used.

## 2026-10-13 - P13 Windows DPAPI round-trip

- Added and ran a Windows-only integration test against the real current-user
  DPAPI APIs. It uses synthetic bot credentials under a temporary directory,
  checks the on-disk bytes do not contain the secret, verifies decrypt/readback,
  and deletes the temporary file. Both credential tests pass on this host.
- This does not verify authorization with a real enterprise bot or message
  delivery.

## 2026-10-13 - P14 local package p251

- The current full local gate passed 545 Python tests, 4 source-mutation gate
  tests, 23 WebUI tests, and the synthetic 25,000-message performance check.
- Rebuilt portable p251 and sanitized source p251. Hub/UI/three-source and
  standalone MCP smokes, extracted-source startup, package privacy scanning, and
  SHA-256 manifest verification passed. The manifest records local-worktree
  provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`;
  it is not a hosted CI or artifact-upload result.

## 2026-10-13 - P11 SILK preview fallback and P14 p252

- The identity-bound media resolver now labels undecoded original SILK as
  `audio/silk` and returns `voice.silk` as a download name. Message previews and
  the QQ resource gallery offer download without a nonfunctional `<audio>`;
  WAV/MP3 playback and WMA playback/download behavior remain unchanged.
- The full local gate passed 546 Python tests, 4 source-mutation gate tests, 23
  WebUI tests, package privacy/manifest/smoke fixtures, syntax checks, and the
  synthetic 25,000-message benchmark. Portable p252 and sanitized source p252
  passed isolated Hub/UI/three-source/MCP or extracted-source smokes, privacy
  checks, and manifest byte/provenance verification.
- Portable p252 contains 10,363 files (407,468,080 bytes); source p252 has 129
  files (603,209 bytes). Manifest `local-p252-20261013` binds both package
  inventories to their byte hashes.
- The packages include dirty local worktree content and are not represented by
  base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. A clean `git archive`
  check confirmed the current helper scripts, implementation, and tests are
  absent from the tracked base; hosted CI and artifact upload/download remain
  unverified. No real account or source-client data was accessed.

## 2026-10-13 - P12 notification permission boundary

- Extended the existing notification click-target test to verify page setup and
  disabling never request browser permission, only an explicit enable prompts
  from the `default` state, denied permission leaves the capability off, and
  pre-granted permission is not requested again. Focused WebUI regression passes.
- No real notification was sent; real-browser/account acceptance remains open.

## 2026-10-13 - P11/P12/P14 p253 local-worktree refresh

- The full gate passed 547 Python tests, 4 source-mutation gate tests, 23
  WebUI tests, package privacy/manifest/smoke fixtures, syntax checks, and the
  synthetic 25,000-message benchmark. The new P11 integration test exercises
  WeChat original/mid/thumb image resolution through ZIP export, explicit
  original-tier failure, archive hash, and source-cache immutability.
- Portable p253 contains 10,363 files (407,481,472 bytes); the refreshed
  sanitized source package contains 129 files. Bundled Hub/UI/source/MCP smokes,
  extracted source startup, privacy scanning, and manifest byte verification
  passed. Its manifest records local-worktree provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, not a clean committed checkout.
- Hosted Actions and hosted artifact transfer remain unverified. No real
  account data or source-client mutation was used.

## 2026-10-13 - P11 pair-report reply metrics and P14 gate coverage

- Pair reports now show counterpart-to-self reply count, average latency, and
  fastest latency for replies within a one-hour session window. Year filtering,
  selected counterpart scope, MCP/API results, WebUI, and CSV include the new
  values. Analytics, MCP, HTTP, and WebUI focused regressions pass.
- Added the notification click-target privacy regression to the aggregate local
  gate so CI exercises it. The first concurrent full-gate run overlapped the
  analytics implementation and failed four pair-report checks; affected
  analytics/MCP/HTTP suites passed after the implementation settled, followed
  by a clean complete gate: 550 Python, 4 source-mutation, and 24 WebUI tests,
  syntax/compile checks, package fixtures, and a 25,000-message benchmark with
  zero WAL failures. No fresh portable package or hosted Actions run was made.
- The local QQ scan page is available at `http://127.0.0.1:8773/#qq-login`;
  it uses the existing local data store and disables duplicate automatic sync.
  Real-account scan/sync acceptance remains pending.

## 2026-10-13 - P14 p254 local-worktree package refresh

- Rebuilt the portable Windows application and sanitized source bundle. The
  portable output has 10,363 files (407,503,630 bytes); the source archive is
  607,710 bytes and contains 129 files.
- Bundled Hub/UI/three-source and standalone MCP stdio smokes passed. The source
  package passed privacy scanning and isolated extracted-startup checks.
  Manifest `local-20261013-p254` verifies both byte inventories and records
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- These packages include uncommitted worktree changes, not just the recorded
  base commit. Hosted Actions/upload, real QQ scan/sync, real-account media,
  and browser notification delivery remain unverified.

## 2026-10-13 - P13 WebSocket protocol validation and P14 p255

- The WeCom bot WebSocket reader rejects RSV1/2/3 when no extension was
  negotiated. Synthetic malformed-frame tests cover each reserved bit; all 11
  WeCom sender/media tests pass.
- The full local gate passed 551 Python tests, 4 source-mutation tests, and 24
  WebUI tests, along with package privacy/manifest/smoke fixtures, syntax checks,
  and the synthetic 25,000-message benchmark.
- Portable p255 has 10,363 files (407,505,178 bytes); source p255 has 129 files
  (608,050 bytes). Both packages passed their startup/source/MCP checks, privacy
  scanning and manifest byte verification. Provenance is `local-worktree` at
  base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the packages include
  uncommitted changes and are not reproducible from that commit alone.
- No live WeCom delivery, QQ scan/sync, hosted Actions/upload, or real-account
  media was exercised.

## 2026-10-13 - P13 control-frame conformance and P14 p256

- The WeCom WebSocket reader now ignores valid Pong heartbeat frames and rejects
  control frames whose payload exceeds 125 bytes. Synthetic tests cover both;
  the sender/media suite has 13 passing tests.
- The full local gate passed 553 Python tests, 4 source-mutation tests, and 24
  WebUI tests, plus package privacy/manifest/smoke fixtures, syntax checks, and
  the synthetic 25,000-message benchmark.
- Portable p256 has 10,363 files (407,506,904 bytes); sanitized source p256 has
  129 files (608,416 bytes). Hub/UI/source/MCP startup checks, privacy scanning,
  and manifest byte verification passed. The manifest records `local-worktree`
  provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`;
  the output contains uncommitted worktree changes.
- No real WeCom delivery, QQ scan/sync, hosted Actions/upload, or real-account
  media was exercised.

## 2026-10-13 - P13 handshake validation and P14 p257

- The bot WebSocket handshake now requires `Connection` to include the
  case-insensitive `Upgrade` token and fails closed on extensions/subprotocols
  not offered by the client. Fake-socket tests cover missing Connection,
  mixed-case comma-separated tokens, and unsolicited negotiation.
- The full local gate passed 554 Python tests, 4 source-mutation tests, and 24
  WebUI tests, plus package privacy/manifest/smoke fixtures, syntax checks, and
  the synthetic 25,000-message benchmark.
- Portable p257 contains 10,363 files (407,510,350 bytes); source p257 contains
  129 files (608,935 bytes). Isolated Hub/UI/source/MCP startup, privacy scan,
  and manifest byte verification passed. Manifest provenance is
  `local-worktree` against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`;
  packages include uncommitted changes.
- Real WeCom delivery, QQ scan/sync, hosted Actions/upload, and real-account
  media remain unverified.

## 2026-10-13 - P13 WebSocket length validation and cancellation completion

- The WeCom WebSocket parser rejects non-minimal extended payload lengths and
  invalid 64-bit lengths, while canonical boundary frames remain accepted.
  Synthetic protocol tests pass alongside the existing sender/media tests.
- Resource ZIP cancellation now reports a terminal state only after temporary
  files are cleaned up. This removes a status-vs-cleanup race caught by the
  full gate. The first gate run exposed three failures; after the fix, the
  focused resource/protocol suite passed 26 tests and the full local gate
  passed 556 Python, 4 source-mutation, and 24 WebUI tests plus the 25,000-
  message benchmark with no WAL failures.
- A local rebuild of the vendored QQ QCE source completed offline with Rust
  1.98.1 and MSYS2 GCC 16.2.0; the executable remains outside the repository
  and has not been integrated into the portable builder or runtime-tested.
  Current official-runtime packaging therefore still does not advertise the
  added cache date-filter capability.
- The full-gate run verified local fixtures only. Package p257 predates this
  source revision; a refreshed portable/source package and manifest are still
  pending. No account scan, sync, source-client mutation, or message delivery
  was performed.

## 2026-10-13 - P14 p258 package refresh

- Portable build p258 contains 10,363 files (407,513,918 bytes). Bundled
  Hub/UI/three-source, isolated-storage, read-only MCP, and standalone MCP
  stdio startup checks passed.
- Sanitized source p258 contains 129 files (609,759 bytes), passed the privacy
  scan and extracted empty-storage startup check. Manifest verification passed
  for both packages with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the packages include uncommitted
  worktree changes.
- QQ login start refused to launch because `QQ.exe` is already running. No QR
  session or account sync was verified. The portable package still includes
  the official QCE executable rather than the local source rebuild with cache
  date filtering. Hosted Actions/artifact transfer and real-account media or
  delivery remain unverified.

## 2026-10-13 - P11 vendored QCE build and P14 p260

- Windows packaging now builds QCE from the vendored v6.3.2 server/core source
  and lockfile, verifies the loopback bind and date-filter capability, and
  embeds the output SHA-256 into the staged provider allowlist. The official
  NapCat shell remains independently archive-hash pinned.
- The sanitized source bundle now includes the QCE source tree, Cargo.lock,
  upstream GPL license, and QCE build script. Privacy scan and extracted
  isolated startup checks passed.
- Full local gate passed 557 Python tests, 4 source-mutation tests, 24 WebUI
  tests, package privacy/manifest/smoke fixtures, syntax checks, and the
  synthetic 25,000-message benchmark.
- Portable p260 has 10,364 files (426,909,831 bytes); source p260 has 669 files
  (24,941,758 bytes). Manifest `local-20261013-p260` verified both outputs with
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; the manifest is not signed.
- The packaged provider accepts the packaged QCE binary, and the binary starts
  with isolated profile paths and listens on loopback. No QQ login, account
  sync, or message read was performed. Hosted Actions/artifact transfer,
  real-account QQ history/media, remaining P11 parity, and live WeCom delivery
  remain unverified.

## 2026-10-13 - P9 QQ login handoff

- Read-only checks found both running Hub instances report QQ login unavailable;
  the NapCat/QQ processes expose no listening login bridge. The existing
  process guard correctly refuses to start a second QQ client.
- Localized this specific refusal in the login UI and instructed the user to
  exit QQ from the system tray and retry. The app never terminates QQ.
- No QR, account data, sync, or delivery was obtained or performed; live P9
  acceptance remains open.

## 2026-10-13 - P11 QQ media alias parity

- Unified media SQL now follows the QQ reader's `extra.media` fallback when
  `extra.roaming_resources` is absent or non-array, so those attachments appear
  in chat and all-source media-library queries.
- The new synthetic regression covers image/voice resource identity, canonical
  type filters, filename search, and totals. `python -m unittest hub.test_store
  -v` passed all 74 tests. Full resource/decryption parity and real-cache
  acceptance remain open.

## 2026-10-13 - P12 unsupported-browser notification state

- Settings now detects browsers without the Web Notifications API, explains
  why desktop notifications cannot be enabled, and disables only the enable
  action; an already-enabled capability can still be turned off.
- New synthetic settings-state coverage and the existing notification
  permission/click-target tests pass, along with `node --check webui/app.js`.
  Real browser permission and click acceptance remain open.

## 2026-10-13 - P11 QQ media alias across consumers

- QQ `extra.media` now serves as the fallback consistently in media analytics,
  indexed resource resolution/ZIP export, MCP selected-attachment reads, and
  referenced-media backup inclusion, as well as chat and unified media
  listings. An explicit `roaming_resources` array (even an empty array) keeps
  precedence.
- The focused store, analytics, backup, server, and MCP tests pass. The full
  four-module targeted run passed 272 tests; the latest five alias/precedence
  regressions also passed. Full resource/decryption parity and real-cache
  acceptance remain open; P11 is not complete.

## 2026-10-13 - P14 QCE linker drift guard

- The QCE build now requires and reports MinGW-w64 GCC 16.2.0. CI no longer
  requests an unpinned full MSYS2 update, and the package smoke contract guards
  both expectations. This is a version gate, not a full compiler package lock
  or fixed QCE binary digest; those still require a trusted clean hosted build.
- The package smoke fixture passes locally. GCC 16.2.0 was present at
  `C:\msys64\mingw64\bin\gcc.exe` outside PATH, and the vendored QCE source
  rebuilt successfully with Rust 1.98.1/GNU and Cargo.lock. The output hash is
  `a0f8b6c4f34b88c90f37f1ffcd9da6c2c1dac7a5667e7b665c642add28c64711`.
- The rebuilt server passed an isolated temporary-profile runtime smoke:
  healthy standalone mode, offline, IPv4-loopback-only listener, and an
  authenticated empty media-gallery response with `dateFilterSupported=true`.
  No NapCat, QQ login, or user data was touched. This hash is diagnostic local
  build evidence, not a reviewed package allowlist. Hosted workflow and
  artifact round-trip remain unverified.
- The QCE shipped in portable p260 was independently started in the same
  isolated profile and passed the same checks. Its SHA-256 is
  `adf2db90cd93d508a6d8c9e739fa22d68a37e547601757ee311c8c0f7f1b2107`; both
  packaged copies match. This is local package evidence only, not hosted
  artifact provenance or real-account acceptance.

## 2026-10-13 - P9 QQ history-import budget validation

- Request budgets now accept JSON integers and ASCII decimal strings only.
  Boolean, floating-point, and fractional-string inputs fail validation rather
  than being truncated or coerced.
- The focused server regression and QQ provider suite passed (63 tests). No QQ
  client/account was started or accessed; login, history, and media acceptance
  remain open.

## 2026-10-13 - P14 p262 final portable privacy scan

- The Windows portable builder now reruns the privacy scanner against the
  assembled package after its packaged smoke test; `test-package-smoke.ps1`
  asserts that final scan ordering. The p262 build passed bundled Hub/UI/source
  checks, isolated standalone MCP initialize/tools/list/exit, and the
  post-smoke privacy scan. An independent scan of the delivered directory also
  passed.
- Sanitized source p262 passed privacy scanning and extracted empty-storage
  startup. The byte-verifying local-worktree manifest `local-20261013-p262`
  records 10,365 portable files (426,828,372 bytes) and one source ZIP
  (24,947,064 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; this is not a hosted build and
  includes uncommitted worktree content. Packaged QCE SHA-256:
  `481999bc4de1c59e01e6b04539e2ba770e4f5cfbf999f95161968f73878bcc45`.
- QQ remains offline in this validation; no real account, history, or media was
  accessed. Hosted Actions/artifact round-trip and real-account P11/P13 checks
  remain outstanding.

## 2026-10-14 - Analytics, Export Cancellation, and Send Revocation

- Group analytics now preserves the full observed member count when the
  visible-member limit is zero; the ranked sentinel remains internal and is
  not returned as a visible member.
- Auto-export cancellation and atomic publication now serialize under the
  manager lock. A deterministic threaded regression verifies cancellation
  cannot report success while publishing a final file.
- QQ, WeChat, and WeCom approval challenge/approval handlers recheck their
  capability after the shared route gate, preventing a capability revoked
  between middleware and handler execution from minting or consuming approval.
- The refreshed full local gate passed 735 Python tests, four source-mutation
  fail-closed tests, all 27 WebUI tests, privacy/package/extracted-startup
  checks, deterministic ZIP verification, and the 25,000-message
  search/pagination/backup benchmark with zero WAL failures. The approved QQ
  read-only scan returned `not_found`; no key was saved and no history import
  started. Real-account acceptance and hosted artifact transfer remain open.

## 2026-10-13 - P14 p263 bytecode fixture and package refresh

- Added the missing `.pyc` fixture to the privacy scanner regression, the
  failure class previously found in a post-build portable audit. Focused
  privacy and package-smoke fixtures pass; final package privacy scanning is
  also enforced after the portable smoke.
- Portable p263 and sanitized source p263 passed build, isolated startup, and
  privacy checks. Manifest `local-20261013-p263` matches all package bytes:
  10,365 portable files (426,830,834 bytes) and source ZIP 24,947,361 bytes,
  labeled `local-worktree` against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- QCE SHA-256 is
  `fe8831b62a2c311e08cb3bdfbb3f39535b0a47f01502f6dbd50304ea89660386`.
  The source capability checks and package inclusion passed; its independent
  runtime/date-filter result is recorded below. No QQ client or account was
  accessed; hosted artifact transfer also remains open.

## 2026-10-13 - P14 p264 current-source archive and manifest

- Refreshed the sanitized source ZIP after the p263 roadmap/log updates and
  `.pyc` regression so the archive includes the current recorded evidence. The
  package privacy scan and extracted empty-storage startup pass.
- Manifest `local-20261013-p264` verifies the p263 portable directory and p264
  source ZIP byte-for-byte: 10,365 portable files (426,830,834 bytes) and
  source ZIP 24,947,811 bytes. Provenance is explicitly `local-worktree`, based
  on HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; it is not a hosted run.
- Hosted Actions/artifact round-trip and real-account P11/P13 acceptance remain
  outstanding.

## 2026-10-13 - P14 p263 packaged QCE runtime follow-up

- The exact packaged QCE hash
  `fe8831b62a2c311e08cb3bdfbb3f39535b0a47f01502f6dbd50304ea89660386`
  started in standalone/offline mode with an isolated temporary profile,
  passed `/health`, and an authenticated empty `/api/resources/files` query
  with `startTime` and `endTime` returned `dateFilterSupported=true`.
- Validation used loopback only. NapCat, QQ, and account data were not
  accessed. This closes runtime verification for this exact local binary, not
  QQ login/history or hosted Actions/artifact transfer; P9/P11/P14 remain open.

## 2026-10-13 - P14 p265 source refresh and manifest

- Sanitized source p265 includes the packaged-QCE runtime follow-up above and
  passed archive privacy scanning plus extracted empty-storage WebUI/MCP
  startup. Manifest `local-20261013-p265` verifies p263 portable (10,365
  files, 426,830,834 bytes) and source p265 (24,948,110 bytes) against base
  HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. This is not hosted CI or an uploaded/downloaded artifact.
- The manifest entry is in the current worktree, not in the archive it records.
  Hosted artifact transfer and real-account acceptance remain outstanding.

## 2026-10-13 - P9 live NapCat QR status update

- A read-only probe of the running NapCat WebUI at `127.0.0.1:6099`
  authenticated, returned `waiting_qrcode`, and provided a valid QR challenge;
  it was rendered to a temporary local PNG for the user. This supersedes the
  earlier observation that no login bridge was listening.
- Existing Hub preview instances still cannot consistently provide the QR:
  one reports unavailable, one returns a generic provider error, and two refuse
  to launch a second QQ client. QCE history ports `40653`/`40654` remain closed.
  No scan confirmation, history read/sync, or delivery occurred.
- Started the current source Hub on loopback port 8774 with empty temporary
  storage. The status API reports `waiting_qrcode/available=true`; `/api/qq/start`
  returned a QR image and the UI opened the login modal. QQ remains
  `login_required` with no QQ messages indexed; history access remains
  unavailable while QCE is offline. A later isolated WeChat sync is recorded
  below and does not change this QQ state.

## 2026-10-13 - P14 p266 sanitized source refresh

- Refreshed the source archive to include the current NapCat QR status and the
  p263 packaged-QCE runtime evidence. Package privacy and extracted startup
  checks pass. Manifest `local-20261013-p266` verifies p263 portable (10,365
  files, 426,830,834 bytes) and source p266 (24,948,550 bytes) against base
  HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. The manifest entry is in the current worktree, not inside the
  archive it records; hosted CI and artifact transfer remain unverified.

## 2026-10-13 - P11 local WeChat read-only acceptance

- With the user's prior authorization, started an explicit WeChat-only sync on
  the isolated loopback Hub. It completed successfully and populated the local
  conversation/message index, media catalog, contacts, and locally cached
  avatar references. Aggregate account-volume metrics, identities, content,
  and paths are intentionally omitted from this repository log.
- Data was written only to the isolated Hub's temporary local storage; the
  WeChat source databases were not modified. The real-source acceptance is
  verified for this local WeChat installation, but this temporary index is not
  the user's persistent app store. QQ still awaits QR confirmation and QCE
  history availability; WeCom local snapshot acceptance remains open.

## 2026-10-13 - P14 p267 sanitized source and manifest

- Source p267 includes the WeChat read-only acceptance record and passed
  archive privacy scanning plus extracted empty-storage WebUI/MCP startup.
  Manifest `local-20261013-p267` verifies p263 portable (10,365 files,
  426,830,834 bytes) and source p267 (24,948,901 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, with `local-worktree`
  provenance. The result record itself is in the worktree, not inside p267.
- This is local package evidence only. Hosted Actions/artifact transfer,
  QQ device history acceptance, and WeCom local-source acceptance remain open.

## 2026-10-13 - P14 p268 source refresh

- Refreshed the sanitized source archive to include the P11 WeChat acceptance
  and corrected P9 QQ-only status. Archive privacy and extracted empty-storage
  WebUI/MCP startup passed. Manifest `local-20261013-p268` verifies p263
  portable (10,365 files, 426,830,834 bytes) and source p268 (24,949,007 bytes)
  against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with
  `local-worktree` provenance. The p268 evidence note itself remains in the
  worktree, not inside the archive.
- Hosted Actions/artifact transfer, QQ device history acceptance, and WeCom
  local-source acceptance remain unverified.

## 2026-10-13 - P11 local WeCom read-only acceptance

- The existing local WeCom read configuration and source directories were
  present. Initial sync failed because the source-run Python environment lacked
  `Crypto`; verified the same Python 3.12-compatible dependency already bundled
  in the portable package, then retried in a second isolated loopback Hub.
- WeCom sync completed and its conversations, messages, contacts, and media
  metadata are queryable through the shared Hub index. Only a temporary local
  config copy was used; no keys, identities, paths, message bodies, or volume
  metrics are recorded. The source database files were not modified. The Hub
  index/config remain temporary; avatar and real media preview/export
  acceptance is still open.

## 2026-10-13 - P14 p269 source refresh and manifest

- Sanitized source p269 includes the WeCom acceptance note and passed package
  privacy and extracted empty-storage WebUI/MCP startup checks. Manifest
  `local-20261013-p269` verifies p263 portable (10,365 files, 426,830,834
  bytes) and source p269 (24,949,283 bytes) against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree`
  provenance. The manifest result is documented in the worktree, not inside
  p269. Hosted CI/artifact transfer and real QQ acceptance remain open.

## 2026-10-13 - P10 WeCom MCP client allowlist

- `/api/mcp` exposes the WeCom bot prepare/status tools only while MCP and the
  WeCom bot-send capability are enabled and the bot identity is configured.
  Identity read errors fail closed. Focused synthetic HTTP coverage checks
  enabled, disabled, unconfigured, and unavailable states; MCP still cannot
  approve or deliver messages.

## 2026-10-13 - P13 send composer cancellation

- Closing or replacing a send composer aborts its in-flight request and makes
  its form stale. Late upload/prepare responses no longer continue into or
  reopen the review dialog. A synthetic UI regression closes during a delayed
  attachment upload and verifies no prepare call follows. A prepare request
  already accepted by the local server can remain pending in send history;
  explicit approval is still required for delivery.

## 2026-10-13 - P14 p275/p276 local release verification

- The full local gate passed: 569 Python tests, four source-mutation safety
  gates, 25 WebUI tests, syntax checks, package privacy/isolated startup, and
  the synthetic 25,000-message benchmark. Existing Python SSL ResourceWarnings
  appeared, but all tests passed.
- Portable p275 passed standalone Hub and MCP runtime smokes. Sanitized source
  p276 passed privacy scanning and extracted empty-storage startup. Manifest
  `local-20261013-p276` pairs p275 portable (10,365 files, 426,847,838 bytes)
  and p276 source against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`
  with `local-worktree` provenance. Hosted CI/artifact transfer and real QQ
  acceptance remain outstanding.

## 2026-10-13 - P9/P14 p280 package refresh

- Portable p280 rebuilt the vendored QCE server and stages the reviewed QCE
  crash-recovery runtime. Isolated Hub and standalone MCP startup, package
  privacy scanning, and the synthetic QCE child crash/restart/health/shutdown
  regression pass. The test asserts service recovery, not asynchronous
  login-link announcement. No NapCat restart, QQ QR confirmation, or history
  sync occurred; hosted CI/artifact transfer also remain open.

## 2026-10-13 - P9 live status and P14 p280/p283 verification

- Read-only status probes show port 8772 is unavailable for QQ, while the
  isolated Hub at 8776 reports `waiting_qrcode` and `available=true`. QCE
  history ports 40653/40654 are still closed; no scan, source-client restart,
  history read, or delivery was performed.
- Manifest `local-20261013-p283` byte-verifies portable p280 (10,366 files,
  426,886,859 bytes) and sanitized source p283 (24,959,782 bytes) against base
  HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with `local-worktree`
  provenance. The staged plugin runtime SHA-256 is
  `9e1dc9ba45ce0f536a575ec2b1f0895d35240422976ecb5809d3e0e1bb55c2c1`.
  Hosted Actions and remote artifact transfer remain unverified.

## 2026-10-13 - P9 QQ rich history and P13 WebSocket continuation

- QQ normalization now retains bounded display-only summaries for structured
  long-message and tofu-record elements, omitting provider resource IDs, XML,
  and raw payload. The 62-test QQ provider suite and rich-content UI regression
  pass.
- The WeCom standard-library WebSocket client now reassembles fragmented text
  receipts, answers interleaved ping frames, ignores pong frames, and caps the
  assembled response at 1 MiB. Eighteen sender/media tests and Python syntax
  checks pass. These are synthetic checks; no real QQ scan/history read or
  WeCom bot delivery occurred.

## 2026-10-13 - P14 p287/p290 release refresh

- Portable p287 and sanitized source p290 contain the QQ rich-history and
  WeCom WebSocket changes. Manifest `local-20261013-p290` verifies both package
  inventories with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Local package privacy/startup and
  byte checks pass; hosted Actions/artifact transfer remain unverified.

## 2026-10-13 - P11 paged media preview and P13 upload failure coverage

- Server-paged media rows now resolve against their selected `media_entry`
  resource, forwarding its stable resource index and rendering the resolved
  same-origin preview with the correct type/name. Three focused media UI tests
  pass. The WeCom sender/media suite passes 17 tests, including a rejected
  upload chunk that must not finish or send. These are synthetic checks only;
  real-account media and real bot delivery remain unverified.

## 2026-10-13 - P11 QQ forwarded WebM preview

- The forwarded-resource reader now preloads WebM alongside MP4. Its regression
  confirms a WebM result produces a video element and a download affordance.
  Focused forwarded-attachment, media-gallery, WeCom-resource, and media-context
  tests pass. Real QQ cache availability and browser codec playback remain
  unverified.

## 2026-10-13 - P14 package manifest and hosted toolchain audit

- The manifest self-test passes, covering deterministic package inventories,
  hidden files, provenance, artifact paths, modified bytes, and reparse points.
  The hosted Windows workflow currently installs GCC from a rolling MSYS2
  repository, while the vendored QCE builder requires GCC 16.2.0. Version drift
  fails closed rather than producing a differently-versioned package, but the
  hosted toolchain is not pinned. Pinning the full GCC/runtime dependency set
  was not attempted. Hosted CI and artifact upload/download remain unverified.

## 2026-10-13 - P14 p284/p286 local package refresh

- Portable p284 (10,366 files, 426,893,707 bytes) and sanitized source p286
  include the WebM UI fix and current docs. The `local-20261013-p286` manifest
  passes byte verification with `local-worktree`
  provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
  The packaged QCE server hash is
  `ecccb81b66d6722d7016ca22a1529d9604f11003e2caa659026e1d063ad67b9d`; the
  staged plugin runtime hash is
  `9e1dc9ba45ce0f536a575ec2b1f0895d35240422976ecb5809d3e0e1bb55c2c1`.
  Hosted Actions and remote artifact transfer remain unverified.

## 2026-10-13 - P14 full current-worktree gate and source refresh

- `scripts/check-local-hub.ps1` passes with 572 Python tests, four
  source-mutation fail-closed tests, the WebUI suite, syntax checks, privacy and
  isolated package startup smokes, manifest self-tests, and the synthetic
  25,000-message benchmark.
- Sanitized source p291 includes this record. Manifest
  `local-20261013-p291` byte-verifies source p291 with portable p287 using
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and remote
  artifact transfer remain unverified.

## 2026-10-13 - P14 pinned GCC package inputs

- The Windows CI workflow now installs GCC 16.2.0-5 plus matching libgcc and
  libstdc++ archives from the official MSYS2 mirror, checking fixed SHA-256
  values before `pacman -U` and verifying the resulting compiler version. It
  uses the setup action's reported MSYS2 directory. The three archives were
  downloaded and hash-verified against the [GCC package](https://packages.msys2.org/packages/mingw-w64-x86_64-gcc), [libgcc package](https://packages.msys2.org/packages/mingw-w64-x86_64-libgcc), and [libstdc++ package](https://packages.msys2.org/packages/mingw-w64-x86_64-libstdc%2B%2B) pages.
  The setup action updates installed packages first. A read-only pacman
  transaction against the local MSYS2 installation rejected its
  `16.2.0-4` runtime dependency, and changed no packages; this does not verify
  the hosted runner sequence. Local YAML/PowerShell syntax checks pass. Hosted
  installation and end-to-end artifact upload/download remain unverified.

## 2026-10-13 - P4/P11 local-source sync acceptance

- Read-only sync completed for the already-connected local WeChat and WeCom
  sources, and both reported a completed last-sync state. No account
  identifiers, message contents, or per-source counts were copied into the
  repository. This does not verify cached-avatar/media acquisition or the
  latest package's live UI; those P11 checks remain open. QQ QR/history
  acceptance remains open.

## 2026-10-13 - P11 QQ media compatibility

- QQ chat bubbles and the media gallery now use `extra.media` when the
  provider's `extra.roaming_resources` field is absent, while an explicit
  `roaming_resources` array remains authoritative. Focused synthetic WebUI
  regression tests pass. Real QQ cache, avatar rendering, and refreshed-app
  browser acceptance remain unverified.

## 2026-10-13 - P8 report boundary and P14 gate alignment

- Pair-report reply timing now computes adjacent messages after applying the
  selected UTC+08 year range; a New Year regression prevents pairing a Jan 1
  reply to a Dec 31 message outside the report year.
- The package-smoke assertion now verifies the checksum-pinned GCC/MSYS2 CI
  setup instead of requiring the superseded `update: false` setting.
- `scripts/check-local-hub.ps1` passes: 572 Python tests, four mutation-gate
  tests, WebUI tests, package privacy/startup and manifest checks, and the
  synthetic 25,000-message benchmark. Hosted Actions and artifact transfer,
  plus real-account analytics/media acceptance, remain unverified.

## 2026-10-13 - P14 current-worktree package refresh

- Portable p295 rebuilt the bundled QCE server with GCC 16.2.0 and passed
  isolated UI, three-source, read-only MCP HTTP, and standalone MCP stdio
  startup checks without installed Python or Node. Sanitized source p295
  contains 671 files and passed privacy and extracted startup checks.
- Manifest `local-20261013-p295` verifies the package bytes and
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This is local evidence only;
  GitHub Actions and hosted artifact transfer remain unverified.

## 2026-10-13 - P14 deterministic source archive

- Source ZIP entries now use ordinal path ordering and a fixed 1980 timestamp,
  removing checkout file timestamps and enumeration order from archive bytes.
  The package smoke builds the same source bundle twice and verifies matching
  SHA-256 hashes; the focused smoke passes. Hosted Actions and remote artifact
  upload/download remain unverified.

## 2026-10-13 - P11 local avatar-cache probe

- A bounded sample of eight exact local avatar handles on the already-connected
  read-only instance returned HTTP 200 image responses. No contact identities
  or image bytes were recorded. This verifies retrieval, not browser rendering
  in the latest package; other-platform avatars and QQ cache/media remain open.
- In a separate bounded media sample, 2 of 3 WeChat image records returned
  local image responses; 3 sampled records each from WeCom and QQ did not.
  No message identities or bytes were retained. This is not cache-parity or
  refreshed-package UI acceptance.

## 2026-10-13 - P11 avatar URL boundary

- Avatar rendering now accepts same-origin paths only at `/api/avatar`, the
  local-cache proxy; provider-supplied paths cannot trigger unrelated GETs.
  Allowlisted external avatar URLs remain cache-only and are never fetched.
- Focused avatar render tests pass. Real QQ/WeCom local-avatar coverage and
  refreshed-package visual acceptance remain open.

## 2026-10-13 - P9 bound QQ QCE JSON responses

- The shared QQ provider reader now reads no more than 32 MiB plus one byte
  before rejecting an oversized JSON response, preventing unbounded Hub-side
  buffering and parsing. A focused synthetic HTTP regression verifies the
  bound; it does not constrain allocations inside QCE itself.
- No QQ process, login state, or account data was accessed. Live history and
  media acceptance remain open.

## 2026-10-13 - P9 validate NapCat login flag

- NapCat `isLogin` is now considered true only for the JSON boolean `true`,
  preventing malformed truthy values from hiding the QR or reporting login.
- Focused synthetic coverage exercises string, integer, and null values. No QQ
  process or account state was accessed; real login and history acceptance
  remain open.

## 2026-10-13 - P14 p299 local package verification

- Portable p299 rebuilt the bundled QCE server with GCC 16.2.0 and passed
  isolated WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP
  stdio, and privacy checks. Sanitized source p299 contains 671 files and
  passed privacy and extracted startup checks. Manifest `local-20261013-p299`
  pairs the portable and source packages with `local-worktree` provenance
  against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; byte
  verification passed. Hosted Actions and artifact transfer remain open.

## 2026-10-13 - P14 hosted workflow documentation correction

- Corrected `docs/windows-package.md`: hosted CI updates its MSYS2 environment
  before installing SHA-256-pinned GCC 16.2.0 and matching runtime packages.
  This matches the current workflow's `update: true` setting.
- Local package-manifest and source-package smoke regressions pass. No hosted
  workflow or artifact transfer was invoked; P14 remains in progress.

## 2026-10-13 - P14 p301 source refresh

- Rebuilt the sanitized source bundle after the workflow documentation fix.
  The 671-file archive passed privacy scanning and extracted startup checks.
- Manifest `local-20261013-p301` verifies p301 source bytes and the existing
  p299 portable package against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; provenance is `local-worktree`,
  not hosted CI. Hosted Actions/artifact transfer and live QQ QR/history
  acceptance remain open.

## 2026-10-13 - P13 bounded WebSocket receipt wait

- WeCom bot WebSocket `recv` now carries one monotonic deadline across frame
  fragments and ping/pong control frames. A synthetic endless-ping regression
  verifies it times out instead of extending the wait. All 19 focused bot
  sender/media tests pass. No credentials or network delivery were used;
  real bot receipt and device acceptance remain open.

## 2026-10-13 - P11 search-result avatar fallback

- Global search results now use the conversation's exact account-scoped
  contact avatar when the conversation row has no avatar (NULL or empty), while
  preserving an explicit conversation avatar. Synthetic tests cover fallback,
  precedence, and same-ID cross-account isolation. This changes no source data
  and makes no network request; real-account rendering remains unverified.

## 2026-10-13 - P14 current-worktree package refresh p303

- Rebuilt portable p303 with the bundled GCC 16.2.0 QCE server. Isolated
  WebUI/Lucide, three-source, read-only MCP HTTP, standalone MCP stdio, and
  privacy checks passed.
- Sanitized source p303 contains 671 files and passed privacy scanning and
  extracted startup checks. Manifest `local-20261013-p303` verifies both
  package byte sets with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and artifact
  transfer remain unverified; P14 is still in progress.

## 2026-10-13 - P8-P14 complete local regression gate

- `scripts/check-local-hub.ps1` completed with 578 Python tests and all four
  source-mutation fail-closed tests passing. The WebUI suite, package privacy
  and isolated-startup checks, compilation, and synthetic benchmark also ran
  without a failure. Local tests do not replace hosted Actions or real-account
  QQ/media acceptance.

## 2026-10-13 - P9 live runtime recheck

- Hub ports 8772/8773/8776 report QQ `login_required` and the NapCat
  login-status API is unavailable. NapCat WebUI at 127.0.0.1:6099 currently
  shows its local token-login page; QCE/bridge ports 40653/40654 are closed.
- The existing launcher refuses to start a second QQ client while `QQ.exe`
  is running. No credential was entered and no QQ process was started/stopped;
  QR, history, and media acceptance still await user exit of QQ and an in-UI
  login attempt.

## 2026-10-13 - P11 media sender avatars

- Media-library chat attachment cards now show indexed sender avatar/name via
  the existing local avatar proxy. QQ global-cache entries without a sender do
  not get a fabricated identity. Synthetic render assertions, app.js syntax,
  and `git diff --check` pass; real cache hit-rate and visual acceptance remain
  open.

## 2026-10-13 - P14 p305 artifact re-verification

- Read-only manifest verification passed for portable p303 and source p305,
  including package bytes and local-worktree provenance. Source inventory,
  archive privacy, and manifest/privacy fixtures passed. No package rebuild or
  remote workflow/artifact transfer was run.

## 2026-10-13 - P14 current-worktree portable refresh p308

- Rebuilt portable p308 with the latest P11 media sender-avatar UI and P13
  attachment approval previews. Isolated WebUI/Lucide, three-source, read-only
  MCP HTTP, standalone MCP stdio, and package privacy checks passed. Bundled QCE
  server compiled successfully.
- Sanitized source p311 contains 671 files and passed archive privacy and
  extracted startup checks. Manifest `local-20261013-p311` verifies p308 and
  p311 package bytes with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact transfer
  and real QQ login/history acceptance remain open; P9, P11, and P14 stay in
  progress.

## 2026-10-13 - P13 local attachment approval review

- Pending WeCom bot approval now embeds validated cached images/videos and
  offers local downloads for AMR voice and documents. The route requires an
  enabled capability and a pending request, validates cached bytes through the
  media store, and sends `no-store`/`nosniff`; preview never approves or sends.
- Focused server-route and UI branch tests, JS syntax, and diff checks pass.
  Real bot delivery and official receipt/device acceptance remain unverified.

## 2026-10-13 - P14 stale artifact revalidation

- Current-state manifest verification failed for p308 and p310 at the portable
  `LocalMessageHub.exe` hash. The old manifests therefore do not prove the
  current portable directory bytes; a fresh package pair and manifest are
  required. No old output was modified or removed.

## 2026-10-13 - P14 p312 local rebuild and p313 source manifest

- Fresh portable p312 passed isolated WebUI/Lucide, three-source, read-only
  MCP HTTP, standalone MCP stdio, privacy, and bundled-QCE build checks.
- Sanitized source p313 contains 671 files and passed archive privacy and
  extracted startup checks. Manifest `local-20261013-p313` verifies portable
  p312 and source p313 with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- Hosted Actions/artifact transfer and QQ/WeCom real-account acceptance remain
  open; P9, P11, P13, and P14 are not complete.

## 2026-10-13 - P9 QQ read retry and P11 MCP cache date filters

- QQ history sync retries the exact read-only message page once after a
  transient transport error or HTTP 5xx. Authentication/client errors and
  non-idempotent export actions remain non-retried.
- MCP `list_qq_cache_resources` accepts inclusive UTC+08 start/end dates and
  forwards validated millisecond bounds through Hub to the existing QCE
  gallery. Date-boundary, malformed/reversed-range, opaque-image, and QQ retry
  tests pass.
- Real QQ login/history and media acceptance plus hosted artifact transfer
  remain unverified; P9, P11, and P14 stay in progress.

## 2026-10-13 - P14 p314/p315 local package refresh

- Portable p314 passed isolated WebUI/Lucide, three-source, read-only MCP HTTP,
  standalone MCP stdio, privacy, and bundled-QCE build checks. Sanitized source
  p315 contains 671 files and passed archive privacy and extracted startup.
- Manifest `local-20261013-p315` matched both package byte sets with
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact transfer
  and real-account acceptance remain unverified.

## 2026-10-13 - P11 exact MCP media scope and P13 uncertain send regression

- MCP `list_media` now accepts an exact `conversation_id` only when the caller
  also selects a platform and account. Synthetic same-title conversations
  verify that the SQLite query returns only the requested conversation.
- The WeCom sender suite now simulates a connection reset after a possibly
  partial `aibot_send_msg` write and verifies the adapter/SendService preserve
  `unknown` across repeat approval without another send attempt. The attachment
  review rendering test now asserts the intended local download link rather
  than rejecting it.
- Live WeCom receipt and real-account media acceptance remain open.

## 2026-10-13 - P14 p317/p318 local delivery refresh

- The complete local gate passed: 583 Python tests, 25 WebUI tests, source
  mutation checks, privacy/manifest gates, isolated portable and source startup,
  read-only MCP checks, and the 25,000-message memory benchmark.
- Portable p317 rebuilt the bundled QCE server and passed isolated WebUI/Lucide,
  three-source, read-only MCP HTTP, standalone MCP stdio, and privacy checks.
  Sanitized source p318 contains 671 files and passed archive privacy and
  extracted startup checks. Manifest `local-20261013-p318` matched both package
  byte sets with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- Hosted Actions/artifact transfer, real QQ login/history, live WeCom delivery,
  and real-account media/browser-notification acceptance remain open.

## 2026-10-13 - P9 sanitized QQ login diagnostics

- The QQ login-status API now returns only an allowlisted error code when the
  local NapCat status request fails; exception text, credentials, and QR payloads
  are never returned. The WebUI translates known codes into actionable local
  status text. Focused server and UI tests pass.
- Read-only local checks found NapCat WebUI reachable but not authenticated by
  the Hub; no QQ/NapCat process was started or stopped, no token or QR was read,
  and no history sync was triggered. Real QQ login/history acceptance remains
  open.

## 2026-10-13 - P12 native-trigger fail-closed coverage

- The isolated WCDB optional-export harness now verifies that absent message
  anti-revoke and Moments block-delete exports remain unavailable without any
  native call. It also verifies blank message session IDs are rejected before
  the message-trigger library is invoked.
- This is wrapper/safety coverage only: no trigger implementation is vendored
  or enabled, no source-client database was accessed, and P12-03/04 remain
  `NOT_STARTED` pending inspectable native semantics and isolated
  install/check/uninstall/rollback evidence.
- Source p322 (671 files) passed extracted startup and current-profile privacy
  scanning; manifest `local-20261013-p322` verified its bytes with portable
  p320 and `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. This remains local evidence,
  not hosted Actions or uploaded-artifact acceptance.

## 2026-10-13 - P11 daily self-send analytics parity

- Added the WeFlow daily self-send intensity chart for private chats across
  selected platforms, based only on the Hub's normalized SQLite index. UTC+08
  days are zero-filled; the exact full-range daily baseline is compared with
  daily counts and a 7-day moving average. The API returns only 37 recent days
  for the 31-day chart and its moving-average lead-in, not message content.
- Synthetic checks pass for private-chat scope, UTC+08 dates, zero-send days,
  account/platform isolation, empty data, capability denial, and WebUI chart
  output. Broader P11 parity, real-account display, and P11-02 acceptance remain
  open; this does not close P11.

## 2026-10-13 - P14 refreshed local delivery artifacts

- Portable p324 passed its bundled QCE build, WebUI/Lucide check, three-source
  check, isolated read-only MCP HTTP/stdio checks, and current-profile privacy
  scan. Source p326 contains 672 files and passed archive privacy and extracted
  startup/API checks.
- Manifest `local-20261013-p326` verifies portable p324 and source p326 with
  `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions/artifact transfer
  and real-account acceptance remain unverified; P14 stays `IN_PROGRESS`.

## 2026-10-13 - P11 private-session classification correction

- The self-send daily distribution now requires a matching conversation row
  with an explicitly recognized private kind. Orphan messages with unknown
  conversation scope are excluded, matching WeFlow's private-session filter;
  analytics and HTTP fixtures cover known private and orphan records.
- A complete analytics/server test run exposed an HTTP fixture that depended
  on the old implicit-private fallback. The fixture now supplies an explicit
  direct conversation. All 22 analytics tests and the focused endpoint/capability
  regression pass after the correction.
- Added a WebUI export-contract regression for all nine message formats,
  exact chat identity and filter forwarding, global-search scope, download
  naming, and button recovery after an API error; it is part of the local gate.
- The complete `scripts/check-local-hub.ps1` gate passes after the changes:
  587 Python tests, four source-mutation regressions, WebUI tests including the
  export contract, package/privacy checks, isolated API smoke, syntax, and
  benchmark checks.
- Portable p327 passed isolated WebUI/Lucide, three-source, read-only MCP HTTP
  and stdio, and current-profile privacy checks. Source p331 passed archive
  privacy plus extracted startup/API checks. Manifest `local-20261013-p331`
  verifies portable p327 and source p331 against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; hosted Actions/artifact transfer
  and real-account acceptance remain open.

## 2026-10-13 - P11 bundled WXGF decoding and p332/p336 artifacts

- Hub source launch now passes the repository's pinned `ffmpeg-static` binary
  to the inherited WeFlow WXGF/WXAM decoder. Portable Hub and standalone MCP
  runtimes resolve the same bundled binary; failed or oversized conversions
  retain the original `.wxgf` download and the 64 MiB decoded-output bound.
- Portable packaging includes the FFmpeg binary's upstream license and
  build/source metadata, verifies the binary runs inside the isolated package,
  and fixes staged QQ-provider UTF-8 decoding so Chinese source text remains
  valid Python after QCE hash injection.
- Focused WeChat media tests pass (52); the complete local gate passes 591
  Python tests, four source-mutation checks, WebUI tests, and package/privacy
  checks. The source package script now loads its required compression
  assembly itself, and its standard direct invocation passes.
- Portable p332 passed isolated Hub HTTP, standalone MCP stdio, privacy, and
  bundled FFmpeg execution checks. Sanitized source p336 contains 673 files
  and passed archive privacy/extracted-startup checks. Manifest
  `local-20261013-p336` verifies both artifact byte sets with `local-worktree`
  provenance against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- Real QQ scan/history and real-account decoded image acceptance remain open;
  this local synthetic/package evidence does not complete P9 or P11.

## 2026-10-13 - P14 local performance benchmark refresh

- Re-ran the deterministic 25,000-message local benchmark: ingest 1.316 s,
  first indexed search 0.031 s, 50 pages of 500 rows in 1.458 s, and
  backup/restore in 0.083/0.244 s. The 200-message WAL writer had no reader
  failures.
- This is synthetic local evidence; it does not measure browser RSS or
  real-account workload, and does not replace hosted CI or artifact transfer.

## 2026-10-13 - P14 p338 source refresh

- Rebuilt sanitized source p338 after recording the fresh benchmark. Its 673
  entries passed archive privacy scanning and extracted WebUI/API/MCP startup.
- Manifest `local-20261013-p338` verified source p338 and portable p332 byte
  inventories with `local-worktree` provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted Actions and artifact
  upload/download remain unverified.

## 2026-10-13 - P14 Git-visible privacy gate

- Extended the existing privacy scanner to enumerate Git-visible repository
  files, including staged, modified, and non-ignored untracked paths. It rejects
  known account/runtime configs, local database/key artifacts, current-user
  paths, and high-confidence GitHub/AWS/Slack/private-key credential formats;
  the existing package-directory scan remains unchanged in scope.
- Synthetic Git-repository regressions cover credential patterns, tracked-file
  edits, forbidden configs, current-user paths, and ignored local files. The
  exact upstream NapCat/QCE Docker config-template path is allowlisted after
  confirming it contains defaults and no credentials.
- Binary payload contents are not covered by the text scanner; pinned upstream
  archive provenance and manual asset review remain the evidence for those
  files. Hosted workflow execution remains unverified.
- The complete local acceptance gate passed with 591 Python tests, four
  source-mutation regressions, 27 WebUI tests, package/privacy checks, isolated
  API/MCP startup, and the synthetic 25,000-message benchmark. Sanitized source
  p339 contains 673 files and passed archive privacy and extracted startup.
  Manifest `local-20261013-p339` verifies it with portable p332 against base
  HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; hosted CI remains unverified.

## 2026-10-13 - P11/P12/P13 local boundary regressions

- WeChat cached BMP is inline only when its file header matches the BMP
  signature; a `.bmp` filename with mismatched bytes remains a forced download.
  HTTP regressions also retain the existing SVG-download and Range behavior.
- Automatic-export output paths are resolved before directory creation, and
  UNC/mapped-network targets reached through a parent junction/symlink are
  rejected without creating the destination. WebSocket Close frames with a
  one-byte payload now fail as malformed protocol input.
- Focused auto-export/account-safety and WeCom sender tests pass (29 total); the
  three HTTP media regressions pass. The complete local gate passes with 594
  Python tests, four source-mutation regressions, 27 WebUI tests, Git/package
  privacy, isolated API/MCP smoke checks, and the 25,000-message benchmark.
  No real source client or sender was contacted.

## 2026-10-13 - Query, memory and usability optimization

- Indexed context ranges and conversation ordering reduce measured synthetic
  query time. QQ binary verification streams its hash; WeChat contacts avoid an
  intermediate list; ChatLab export projects one message at a time. Exact
  measurements and remaining memory limits are recorded in performance.md.
- HTTP and automatic exports use stable cursors. Contact exports retain account
  and kind filters. Unchanged source polls and attachment-progress updates avoid
  page redraws; obsolete async responses cannot refill inactive views.
- Startup recognizes a locked running Hub and portable startup respects explicit
  storage and no-browser. The build includes the shared startup helper. Supported
  page hashes survive refresh; the live source-page refresh was verified.
- Focused store/export/HTTP checks passed (95 tests); provider/startup checks
  passed, including six startup tests. All registered WebUI Node checks passed.
  No real login, history synchronization, or message delivery was performed.
- Four idle preview services were retired, releasing approximately 190.3 MiB
  of their observed working sets. QQ, NapCat and active Hub services were left
  running. Automatic approval rejected the active 8772 backend restart before
  execution; backend changes are not yet loaded there. Static UI changes are
  visible after refresh. The optional browser-memory benchmark was not rerun.
- Portable staging exposed an upstream empty PEM input-placeholder false positive
  in the privacy scanner. Only the exact audited asset path and fixed placeholder
  are excluded from credential matching; the remainder of the file is scanned.
  Regressions reject additional keys, altered placeholders and unknown paths.
  Package and Git privacy regressions and the Git-visible repository scan pass.
- Final portable scanning also rejected Crypto's bundled development test keys.
  Packaging collects its native binaries rather than all package test sources;
  application imports and the standard PyInstaller Crypto hook remain in place.
  The privacy rule for actual private-key material is unchanged.
- Portable p341 passed WebUI/API and standalone MCP isolated startup, final
  package privacy scanning and absence checks for Crypto's SelfTest directories.
  Sanitized source p341 contains 676 files and passed archive privacy and
  extracted startup. Manifest local-20261013-p341 verifies both byte inventories
  with local-worktree provenance against base HEAD
  8f45f2525c432ba81d6125b8d728f0ae5517ef5d. This is not a hosted CI result or a
  committed-release claim. All 29 WebUI Node checks passed; real encrypted-source
  and message-delivery acceptance were not performed.

## 2026-10-13 - QQNT Exporter References and Key Retry

- Compared QQNT_Export schema/cache definitions and ARK-1 SQLCipher settings.
  Fixed group ID fallback, repeated quote parsing and image MD5 variants.
  Owned snapshots receive peer/time indexes; original QQ databases stay untouched.
  Existing UI/MCP avatar serving now accepts bounded, signature-checked QQ
  avatar copies from the selected account's local cache; no network fallback.
- ARK-1 needs a supplied key. Its linked Windows helper requires debugger
  startup, breakpoint writes and process termination; it was not run under the
  existing read-only authorization. Android formulas are not claimed for Windows.
- Included ASCII spaces in memory key candidates and retried: four readable
  processes, 1,304 candidates, 349 MiB read, none authenticated. No key saved.
- Focused reader/WAL/key and Hub avatar/local-sync checks passed (21 tests).
  The running 8772 backend and previous p341 packages have not been rebuilt or
  restarted with these changes; real encrypted QQ history remains unverified.

## 2026-10-13 - Authorized QQ Debugger Key Extraction

- The user explicitly authorized debugger startup, a transient breakpoint and
  ending the launched QQ process, then asked for QQ closure and extraction.
  A pinned, inspected QQBackup helper was downloaded outside the repository;
  an ignored local runner suppresses key/candidate output, validates the active
  wrapper version and bounds the debuggee lifetime to 180 seconds.
- The first wait returned no key. On retry, module load and breakpoint install
  succeeded after the user completed login. Page authentication, an owned
  DB/WAL snapshot and SQLite quick_check passed before the key was saved only
  in private Hub configuration.
- The local reader returned ready without writing to the source database. No
  source content, account identifier or key was added to the repository. No QQ
  process remained.
- Printable-space validation is consistent between the key reader, local
  provider and Hub configuration; five focused checks passed. No repository
  commit/push, 8772 restart, package rebuild or full history UI import occurred.

## 2026-10-13 - Local QQ Integration and Cache Path Follow-up

- Corrected the current QQ status above: the private local key is configured,
  the running 8772 Hub reports the local source connected, and a local sync has
  completed. Historical notes about a missing key or unrefreshed service are
  superseded by this entry. No private key, account identifier or message data
  is recorded here.
- The WebUI now recognizes bounded `hub-avatar://qq/` references; a live QQ
  contact view loaded locally cached avatars. The QQ reader adds the published
  QQNT_Export CRC64 cache-name derivation for `chatraw`, `chatimg`, and
  `chatthumb`, retaining existing file-size, MD5, path-boundary and Hub-cache
  checks. A synthetic sharded-cache test and the existing filename-cache test
  pass.
- Three sampled real image attachments still did not resolve from local cache.
  Indexed attachment metadata is not proof that the corresponding file is
  cached; real QQ image preview remains unverified. The full test/package gate
  was not run during this follow-up.

## 2026-10-13 - Local Gate and Sender Lifecycle Regression

- Clearing the Hub-owned local index now preserves the configured WeCom bot
  sender adapter. A synthetic regression verifies it remains available to
  prepare a locally reviewed request after cleanup; no message was delivered.
- The full local gate passed: 625 Python tests, four fail-closed source-mutation
  tests, all registered WebUI tests, package privacy/manifest/startup checks,
  compilation and the synthetic performance benchmark.
- Hosted Actions and artifact round-trip remain unverified. The QQ debugging
  workflow has not been integrated into the Hub; the source reader remains
  read-only and does not launch or debug QQ.

## 2026-10-13 - Explicit Read-Only QQ Key Discovery Integration

- The QQ data-source page now offers an explicit confirmation action only when
  the local database reports a missing or invalid key. A bounded single worker
  scans same-user QQ processes with read/query-only access, caps aggregate
  reads at 512 MiB and 180 seconds, and saves only a candidate that passes
  database authentication and a fresh provider status check.
- Added capability enforcement for both discovery routes. Failed scans do not
  alter configuration, and UI/API status is sanitized. Synthetic tests cover
  confirmation/capability, verified persistence, failure non-persistence,
  aggregate scan budget and confirmation dismissal.
- This integration does not invoke debugging, injection, process termination
  or MCP key return. No scan was run against the real client because its local
  database is already connected. The complete local gate passed 629 Python
  tests, four source-mutation tests, 30 WebUI tests, package privacy/manifest
  and startup checks, Python compilation and the synthetic performance test.
  Hosted Actions and artifact round-trip remain unverified.

**P14 QQ local-reader package refresh (2026-10-13):** source and portable
package builders now include QQ local database/WAL/key-discovery modules, and
PyInstaller declares their dynamic imports. The package regression check
requires these modules in both builders. Source p343 passed privacy and isolated
startup checks; portable p342 compiled the pinned QCE server and passed bundled
Hub, WebUI, three-source, MCP, and standalone MCP smoke checks. A local-worktree
manifest was generated and byte-verified for both packages. No live QQ scan was
run. Hosted Actions/artifact round-trip and real-account/media acceptance remain
open; the manifest identifies base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`
with `local-worktree` provenance, not a committed release.

**P14/WeChat image-key refresh (2026-10-13):** the focused QQ/WeChat suite
passed 86 tests; QQ key-discovery and rich-message UI checks passed. The Windows
portable p344 package compiled the pinned QCE server, passed privacy scanning,
and passed isolated Hub, WebUI, three-source, MCP and standalone MCP checks.
Source p345 passed privacy and extracted startup checks with 683 files. The
portable/source manifest was generated from base HEAD
`8f45f2525c432ba81d6125b8d728f0ae5517ef5d` with local-worktree provenance and
its current byte set verified. This is not hosted CI or a committed release.
Real-account image/decryption, real-device notification/send, full WeFlow
parity, native source mutation and hosted artifact round-trip remain open.

## 2026-10-13 - Read-Only Discovery and Integration Refresh

- Fixed QQ process-owner filtering to compare the SID pointer contained in
  `TOKEN_USER`, rather than the address of the token buffer. A regression test
  now exercises the Windows token layout without scanning a live process.
- WeChat SILK decoding now bounds both compressed input and decoded PCM, keeping
  oversized content available in its original cached format. WeCom bot media
  writes refresh their socket timeout against the operation deadline, and a
  synthetic upload/send lifecycle test passes. No real send was performed.
- Isolated headed-browser acceptance loaded three synthetic local avatar images
  at 1x1 pixels; account lock and body-free notification event filtering passed.
  System notification delivery was not exercised in the headless test profile.
- Final local gate passed 642 Python tests, four fail-closed mutation tests, 27
  WebUI tests, privacy and isolated-package checks, compilation and the synthetic
  performance benchmark. Two closed-SSL-socket `ResourceWarning` notices were
  emitted during Python tests; all tests passed. No live QQ memory scan or
  source-client mutation was run. Hosted Actions/artifact round-trip, real
  account/media, real notification/send, native mutation, and full WeFlow parity
  remain open.

## 2026-10-13 - WeCom Media Export and Transport Boundary Regressions

- Added a synthetic end-to-end regression from the real WeCom adapter's cached
  attachment resolver through Hub identity validation and ZIP export. It verifies
  resource-index selection, exported bytes and SHA-256, path-free manifest, and
  unchanged source-cache hashes.
- Added a direct production WebSocket-wrapper assertion that the remaining
  operation timeout reaches its underlying socket; the existing protocol test
  continues to verify deadline refresh at each send.
- Focused tests pass. No real account data, source mutation, or bot delivery was
  used. The full local gate then passed with 644 Python tests, four
  source-mutation fail-closed tests, 27 WebUI tests, package/privacy/startup
  checks and the synthetic 25,000-message benchmark. P11/P13 remain open for
  real media/device acceptance; P14 still requires a refreshed package and
  hosted Actions/artifact round-trip.

## 2026-10-13 - Notification Permission Revocation

- The WebUI now rechecks browser notification permission for every SSE event.
  If permission was revoked while the page was open, it suppresses the alert
  and closes the existing event stream instead of continuing to process events.
- `node webui/test-notification-click-target.cjs`,
  `node webui/test-notification-support.cjs`, `node --check webui/app.js`, and
  `git diff --check -- webui/app.js webui/test-notification-click-target.cjs`
  pass. Synthetic data only; OS-level notification delivery and complete
  browser lock/relock/unlock acceptance remain open.

## 2026-10-13 - WeCom Staged Attachment Read Bound

- The bot outbox loader previously used `read_bytes()` before enforcing the
  50 MiB attachment limit, so an oversized/corrupted cached blob could cause an
  unbounded allocation during request preparation or delivery. It now rejects
  oversized files by stat and caps the actual read at 50 MiB plus one byte to
  cover growth after the size check.
- Added a sparse oversized-cache regression. WeCom media and sender protocol
  tests pass (24 total); no real bot credentials or delivery were used. Real
  target reachability, acknowledgement semantics, and device delivery remain
  unverified.

## 2026-10-13 - Clean-Checkout Portable Build Dependency

- The Windows CI workflow built the portable package without installing the
  root npm dependencies, while the package builder requires
  `node_modules/ffmpeg-static/ffmpeg.exe`. A clean checkout would therefore fail
  before producing the portable artifact.
- CI now runs `npm ci --ignore-scripts` from the tracked lockfile, invokes the
  lockfile-selected `ffmpeg-static` install script explicitly, and checks for
  the Windows binary before packaging. The package smoke test asserts that this
  preparation precedes the portable build.
- `scripts/test-package-smoke.ps1` passes, including extracted source startup,
  privacy-related environment isolation, rejection fixtures, and deterministic
  source ZIP comparison. This verifies the local workflow contract only; no
  hosted Actions run or uploaded-artifact round-trip has been performed.

## 2026-10-13 - Local Acceptance Refresh

- WeChat avatar resolution now selects the newest signature-valid cached image
  when duplicate rows exist for one exact username. A synthetic SQLite
  regression covers reverse insertion order; the provider suite passes 18 tests.
- Notification event handling now closes its existing SSE stream if browser
  permission is revoked after connection. WeCom bot attachment staging rejects
  oversized files before unbounded reads and caps reads at 50 MiB plus one byte.
- The full local gate passes with 646 Python tests, four fail-closed source
  mutation checks, 27 WebUI tests, package privacy/manifest/startup checks,
  compilation, and the 25,000-message synthetic benchmark. No real messages,
  key scan, desktop notification, or source-client mutation was performed.
- The running local instance at `127.0.0.1:8774` reports all three platforms
  disconnected. Real account/device acceptance, source mutation, full WeFlow
  parity, hosted Actions, and artifact upload/download verification remain open.

## 2026-10-13 - QQ Key Discovery Confirmation and Portable Refresh

- The QQ key-discovery confirmation now states that a verified local key is
  saved to private Hub configuration and immediately starts local QQ history
  sync. It also states that the scan will not debug, inject into, or close QQ,
  and that the key and chat content are not sent to a network service.
- The full local gate passes with 646 Python tests, four fail-closed source
  mutation tests, 27 WebUI tests, privacy and isolated-package checks,
  compilation, and the 25,000-message synthetic benchmark. The QQ key-discovery
  and local database suites pass focused tests.
- Portable package p357 builds with the bundled QCE runtime and passes isolated
  Hub/WebUI/source-list/MCP startup checks. No live QQ scan or sync was run;
  the running Hub reports all three platforms disconnected. Real account/media,
  native mutation, hosted Actions/artifact transfer, and complete WeFlow parity
  remain open.

## 2026-10-13 - Bounded-Memory Scheduled Export

- Scheduled exports now read ascending keyset batches from the existing
  transactionally consistent SQLite snapshot and stream JSON, CSV, Markdown,
  TXT, HTML, XLSX, SQL and ChatLab outputs directly into the atomic temporary
  file. ChatLab member metadata is streamed from a windowed SQLite query rather
  than accumulated with all messages.
- Focused tests cover all formats, snapshot consistency during concurrent
  ingest, cancellation cleanup, and fixed-memory writing across a 3,000-message
  synthetic export. The current auto-export, store, and memory-focused suite
  passes 93 tests. No real account data or QQ key scan was used.
- P12-01 remains in progress because device/browser acceptance is outstanding;
  P14 hosted Actions and artifact transfer remain unverified.

## 2026-10-14 - Explicit QQ Sender Readiness

- Settings now checks QQ sender readiness only after an explicit user action.
  The local NapCat probe calls OneBot `get_login_info` on the configured
  `127.0.0.1` endpoint and reports endpoint reachability plus the returned
  account ID/nickname. It never sends chat content; when QQ sending is disabled
  the endpoint is not contacted. Startup remains network-idle for this check.
- Synthetic tests cover logged-in identity, unreachable endpoint, capability-off
  no-call behavior, the HTTP route, and the visible Settings status. The focused
  sender and Hub server suites pass 203 tests; JavaScript syntax, focused WebUI
  assertions, and `git diff --check` pass. No live NapCat or QQ account was
  contacted; P9-02 real-device acceptance remains open.

## 2026-10-14 - Bounded WeCom Attachment Staging

- Browser uploads for WeCom bot attachments now stream from the loopback HTTP
  request into a same-directory temporary file in 1 MiB chunks. The staging
  path hashes while writing, checks text encoding incrementally, validates the
  final signature, and atomically publishes only complete files; short reads,
  over-reads and invalid text remove the temporary file.
- Synthetic media, sender and Hub HTTP tests pass (214 tests). Package smoke,
  package privacy, deterministic ZIP and `git diff --check` checks pass. This
  bounds the incoming upload path; the existing sender still loads the staged
  media before protocol upload. No real bot delivery was performed.

## 2026-10-14 - QQ Discovery Deadline Bound

- The explicit QQ key-discovery worker now starts its monotonic deadline before
  database discovery and enforces it through process enumeration/opening as
  well as memory scanning. Candidate QQ process enumeration is capped at 256;
  hitting that cap reports `limit_reached` instead of implying a complete scan.
- Eight synthetic reader tests pass, including deadline and handle-cleanup
  cases. No live QQ process was scanned or read.

## 2026-10-14 - Stable Contact Pagination

- Contact results with equal names and IDs now use platform and account as
  deterministic final sort keys, preserving a stable cross-platform/account
  order at pagination boundaries.
- A synthetic store regression pages three same-name/same-ID contacts across
  two platforms and accounts and verifies exact order with no missing or
  duplicated rows. Real-account schema and avatar acceptance remain open.

## 2026-10-14 - Moments XLSX Export

- Filtered Moments export now supports XLSX through the existing local API and
  WebUI format picker. Spreadsheet values are escaped inline strings (not cell
  formulas), media URLs/keys/tokens are omitted, cell values obey Excel's
  length bound, and ZIP output is capped at 32 MiB while rows are consumed.
- Synthetic renderer/archive, HTTP, and WebUI regressions pass. Real Moments
  schema/cache acceptance and full media resource/decryption parity remain
  open; no real account data was used.

## 2026-10-14 - Moments AVIF and HEIC MCP Media

- Explicit cached Moments media reads now accept AVIF/HEIC when the ISO-BMFF
  `ftyp` box is bounded and contains a recognized image brand. MP4 validation
  also rejects malformed declared box sizes.
- Synthetic local-cache and MCP response tests pass, including a lookalike
  brand rejection. No network or real account cache was used; live cache and
  MCP-client rendering remain unverified.

## 2026-10-14 - WeChat Group Roster Avatars

- WeChat group-member API responses now attach avatar references from the
  exact indexed platform/account/contact identities in a single bounded query.
  The roster UI already routes those references through the local avatar proxy;
  no remote lookup or source-database change was introduced.
- A synthetic HTTP test confirms the current account's avatar is returned and
  an identical member ID from another account is ignored. Real-account visual
  and schema acceptance remain open.

## 2026-10-14 - Confirmed QQ Read-Only Key Scan

- After explicit user confirmation, the local Hub performed one read-only
  automatic key discovery attempt against eligible same-user QQ processes.
  It reached the configured safety limit without verifying a key; no key was
  saved and no QQ sync was started. The QQ source remains locked/disconnected.
- No QQ process was injected into, debugged, or closed; the source database was
  not modified. Historical-account acceptance remains open.

## 2026-10-14 - Moments Scan-Budget Correction

- Corrected the local Moments reader to count the XML payload against its
  aggregate scan-byte budget instead of counting the timestamp column.
- A synthetic two-row test verifies truncation at the configured byte ceiling.
  `integrations.wechat.test_moments` passes 10 tests; no source account data was
  read.

## 2026-10-14 - Account-Lock Interaction

- The unlock page now blocks duplicate submissions while a request is pending,
  announces its busy state, and clears/refocuses the password field after a
  failed attempt. Its isolated UI regression is included in the full local gate.
- The focused UI test and account-safety/API tests pass. Real-device browser
  interaction remains unverified.

## 2026-10-14 - Bounded QQ Candidate Scan

- QQ key discovery now tests context-derived candidates, documented key forms,
  and generic 16-byte candidates within each already-read memory chunk instead
  of rereading process memory for a second candidate pass. The existing byte,
  deadline, and process limits and read-only access are unchanged.
- A generic synthetic key authenticates within the bounded single pass. The
  prior explicitly authorized live attempt reached its safety limit. A second
  user-approved scan through the running local Hub also returned
  `limit_reached`; no key was saved and no history sync started. The server
  status does not distinguish which specific bound ended that run.
- Context-derived SQLCipher key/MAC reads now charge the same shared 512 MiB
  budget as chunk scanning. The focused QQ reader/local-WAL/provider suite
  passes 32 tests; the source process and database were not accessed for this
  bound correction.

## 2026-10-14 - Bounded Moments Pagination and Export

- Moments timeline pagination now retains only the requested page while still
  computing filtered totals and preserving SQL order; 11 reader tests and the
  focused HTTP route test pass.
- Moments JSON export now allowlists post/comment/location/media/source fields
  and excludes the full account catalog and unknown internal fields. The
  analytics/export suite passes 32 tests; no account data was accessed.

## 2026-10-14 - WeCom Staged Attachment Cleanup

- Startup cleanup now reclaims stale orphan `.part` staging files with the
  expected opaque-ID filename, while retaining recent partials, live blobs,
  and unrelated files. Media/sender/credential tests pass (30 tests); no bot
  credentials or real delivery were used.

## 2026-10-14 - Exact Moments Image Signatures

- AVIF/HEIC detection now validates the ISO-BMFF `ftyp` layout and compares
  exact 4-byte brand slots, rejecting malformed boxes and substring lookalikes.
- Focused Moments/media tests pass (18 tests); no real account cache was read.

## 2026-10-14 - Windows Portable Package Smoke

- Rebuilt a CPython 3.12 Windows portable package from the current worktree with
  QCE/NapCat intentionally omitted (7,546 files, 287,850,999 bytes). Isolated
  UI and standalone read-only MCP startup passed without installed Python or
  Node. The package privacy checks passed.
- Generated a sanitized 686-entry source ZIP (about 25 MB); its extracted
  UI/MCP startup and privacy checks passed. The local-worktree manifest binds
  both packages to base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`, and a
  copied delivery pair passed byte/provenance verification.
- The complete local gate passed 673 Python tests, four fail-closed mutation
  tests, the WebUI suite, and the synthetic performance/backup benchmark. The
  default QCE-enabled package, clean committed-checkout verification, hosted
  Actions, and hosted artifact transfer remain unverified.

## 2026-10-14 - QQ Resource Signature and Scheduled Export Race

- QQ forwarded-media classification now validates ISO-BMFF `ftyp` length and
  alignment and checks exact major/compatible brand slots. Added regressions for
  minor-version lookalikes, truncated boxes, and misaligned lengths.
- Scheduled auto-export now holds its manager lock through worker startup, so a
  concurrent disable cannot report success between the due check and launch.
- `python -m unittest integrations.qq.test_provider hub.test_auto_export -v`:
  80 tests passed. All fixtures were synthetic; no account data or exports were
  accessed.

## 2026-10-14 - P14 Pinned QCE Build Probe

- Found the pinned Rust 1.98.1 GNU and GCC 16.2 toolchain in its existing
  private directory. A locked QCE build completed in an isolated temporary
  directory; the output was 30,974,233 bytes and its SHA-256 independently
  verified as
  `823a6577252092b46926a0917024130619bceac0588028c119bbd883466d38dd`.
- The portable builder cannot consume a prebuilt QCE executable and would
  rebuild it, so the QCE-enabled package smoke was not started. No NapCat or QQ
  process was launched and no repository build configuration was changed.

## 2026-10-14 - Final Local Package Refresh

- Rebuilt the QCE/NapCat-omitted Windows portable package after the QQ
  forwarded-image signature and auto-export schedule race fixes. Its 7,546
  files (287,861,409 bytes) passed isolated UI/MCP startup and privacy checks.
- The sanitized 686-file source archive passed extraction, UI/MCP startup, and
  privacy checks. A local-worktree manifest and copied delivery directory passed
  exact byte and provenance verification. Hosted CI/artifact transfer and the
  QCE-enabled package remain unverified.

## 2026-10-14 - Bounded QQ Retry and Cross-Source Hardening

- One explicitly approved read-only QQ scan through the already-running Hub
  returned `limit_reached` with a generic safety-limit message. Its live status
  endpoint did not expose the specific bound. QQ remains `key_required`; no key
  was saved and no history import began. The database was not modified.
- WeChat cached-file staging now rejects files above 50 MiB and detects growth
  or size changes during copy while preserving any previous complete cache.
  Account-lock first-time setup is now atomic under concurrent requests.
  WeCom bot IDs are validated against the send queue's accepted identity format.
- Focused Python suites passed 128 tests; QQ key-discovery UI and package smoke
  tests passed. These are synthetic/local checks; real QQ sync and real WeCom
  delivery remain unverified.
- The package builder now accepts an optional prebuilt QCE binary only with a
  matching SHA-256; source compilation remains the default. The QCE-enabled
  portable package at `%TEMP%\LocalMessageHub-portable-qce-final-d215d8937522436b92d26b7501722405`
  contains 9,775 files (586,359,542 bytes). Isolated UI, three-source, MCP,
  package identity and privacy checks passed. The staged WeChat attachment
  module exactly matches the worktree SHA-256; the 30,974,233-byte QCE
  executable matches the independently verified build hash. No NapCat/QCE
  server was launched.
- A sanitized 686-file source ZIP passed extracted startup
  and privacy checks. The local-worktree manifest verified both artifacts by
  exact bytes and provenance against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`. Hosted CI and hosted artifact
  transfer remain unverified.

## 2026-10-14 - QQ Read-Only Retry and Local Regression Follow-Ups

- Per the user's explicit confirmation, retried one read-only QQ key discovery
  through the already-running local Hub. It returned `limit_reached`; the local
  status remains encrypted and `key_required`, with no key saved and no history
  import started. The history-import API confirms that online roaming is
  disabled. QQ's database was not modified and QQ was not restarted.
- Pair analytics now counts a same-second reply as a valid zero-second response.
  Cross-tab account-lock handling now checks the body-free lock status after an
  SSE disconnect and reloads only when the account is confirmed locked.
- The hosted package-manifest regression now binds creation, upload verification
  and downloaded-artifact verification to the same commit/run provenance and
  rejects a drifted run ID.
- Focused validation passed: analytics (23), account safety (5), notification
  click/lock UI regressions, package-manifest provenance tests, and
  `git diff --check`. Hosted Actions/artifact transfer, real QQ history,
  real-device notification acceptance, and complete WeFlow resource/decryption
  parity remain unverified.

## 2026-10-14 - Full Local Gate and Bounded Resource Follow-Ups

- After the user's explicit confirmation, ran one additional QQ key scan through
  the existing Hub. It again returned `limit_reached` within seconds, without
  exposing the precise bound. QQ remains encrypted and `key_required`; no key
  was saved, no history import began, and the QQ database was not modified.
- The Windows key-reader now enumerates up to 65,536 PIDs and applies its
  256-process cap only to same-user QQ executable candidates. Focused regressions
  cover a QQ PID after 256 unrelated processes and avoid falsely reporting a
  process-limit hit on non-QQ-heavy systems. The already-running Hub was not
  restarted, so this scan does not prove that the live process loaded that fix.
- Resource exports now admit one active task per manager, cap terminal metadata
  at 64 records, prune completed ZIPs to the task budget plus bounded overhead,
  and verify actual archive size before publishing the URL. Only exact
  tool-owned orphan work directories are removed at startup. The resource suite
  passed 15 tests.
- Browser notification permission is rechecked after tab visibility/focus
  returns. The WeCom bot sender emits `ping` before its next command after 25
  seconds idle. QCE package hash allowlisting now rejects malformed or ambiguous
  anchors and normalizes accepted hashes. The WeChat test sender fixture now
  implements the readiness and delivery interface used by the service.
- The complete `scripts/check-local-hub.ps1` gate passed: 504 Python tests, 4
  source-mutation gate tests, all 27 WebUI test files, privacy/package isolation,
  and deterministic source ZIP checks. A 25,000-message benchmark passed with
  0.029 s first search, 1.952 s for 50 cursor pages, and zero WAL failures.
  Hosted Actions/artifact round-trip, real QQ history, real WeCom delivery,
  device notification acceptance, and full WeFlow resource/decryption parity
  remain open.

## 2026-10-14 - Regressions and Final Local Gate

- Fixed a test-only Moments cache fixture that omitted its return value after
  the initial full gate exposed downstream media/export failures. The 19
  focused Moments service tests now pass.
- Moments parsing now traverses nested media XML iteratively; WeCom heartbeat
  state is per delivery; notification events are filtered against current
  platform/account/conversation/mute settings. Their focused regressions pass.
- Re-ran `scripts/check-local-hub.ps1`: 697 Python tests, four mutation-gate
  tests, 27 WebUI tests, privacy/package isolation, deterministic source ZIP,
  and the 25,000-message performance/backup benchmark all passed. The benchmark
  recorded 0.031 s first search, 1.475 s across 50 cursor pages, and zero WAL
  failures. `git diff --check` passed.
- The authorized QQ scan returned `limit_reached`; QQ remains `key_required`,
  no key was saved and import did not begin. The running Hub was not restarted,
  so the updated scanner has not been exercised live. Real-account/device
  acceptance and hosted CI/artifact transfer remain outstanding.

## 2026-10-14 - QQ Scan Retry and Lock Transition Guard

- Per the user's one-time approval, triggered one bounded read-only QQ key scan
  through the already-running local Hub (the process was not restarted to load
  newer worktree code). It returned `limit_reached`; no key was verified or
  saved, and no history import started. No QQ database was written.
- Closed a lock-screen race: once lock navigation begins, new API calls are
  rejected, in-flight responses are discarded, and toast/render helpers cannot
  restore cached content before reload. Added an asynchronous response regression.
- The first full gate exposed stale send-history test expectations for current
  bot identity lookup and local readiness checks; the expectations/mocks were
  aligned with the current UI behavior. The final `scripts/check-local-hub.ps1`
  run passed 701 Python tests, 4 fail-closed mutation tests, all 27 WebUI tests,
  source-package/privacy checks, deterministic ZIP verification, and the
  25,000-message benchmark. `git diff --check` passed.

## 2026-10-14 - QQ Scan Status and Cross-Roadmap Regression Gate

- Rechecked the already-authorized bounded QQ scan through the local status API:
  it ended at `limit_reached`; the local QQ source remains `key_required`, no
  key was verified/saved, and history import did not start. No second scan or
  QQ database write was performed.
- Tightened QQ MCP image classification to accept only aligned ISO-BMFF `ftyp`
  boxes with exact AVIF/HEIC brand slots. Added malformed-box regressions.
- Added fail-closed settings regressions proving `anti_recall` and `qq_history`
  cannot be enabled or persisted, and UI assertions that both remain unavailable
  without interactive controls.
- Expanded package-manifest rejection coverage for modified source archives,
  portable inventory additions/omissions, and traversal paths. Repaired an
  incomplete indentation in the untracked QQ provider test so the full gate can
  import the test module.
- `scripts/check-local-hub.ps1` passed: 725 Python tests, four source-mutation
  gate tests, all 27 WebUI tests, privacy/package isolation and extracted
  startup checks, deterministic source ZIP verification, and the synthetic
  25,000-message benchmark with zero WAL failures. Hosted Windows Actions and
  uploaded-artifact download verification, real QQ key/history acceptance,
  real-account media, and other device-only checks remain outstanding.

## 2026-10-14 - Current-Workspace QQ Read-Only Scan

- After the user's one-time approval, ran one bounded key discovery using the
  current workspace worker against the configured local QQ source. It returned
  `not_found` before the 180-second/512 MiB limits; no key was verified or
  saved, and no history sync started. No QQ database was modified.
- The local browser Hub was not listening on port 8772, so the scan ran through
  the Hub's worker directly. No retry was made; another scan requires fresh
  user approval.

## 2026-10-14 - Stable Reports, Export Multiplicity, and Lock Resume

- Annual and all-time report series now always expose ordered January–December
  buckets with zeroes for missing months. Regression coverage passes for both
  report modes.
- Message resource export now preserves repeated identical attachments in one
  source list while deduplicating mirrored representations across aliases.
  Synthetic JSON, ChatLab, and SQL exports cover the behavior.
- Returning to a visible lock-enabled tab checks server session expiry without
  refreshing its idle timer. Expired sessions return to the lock screen;
  transient status errors do not force logout. Updated the notification test
  harness to dispatch all listeners registered for the same browser event.
- `scripts/check-local-hub.ps1` passed 731 Python tests, four source-mutation
  fail-closed tests, all 27 WebUI tests, package/privacy and extracted-startup
  checks, deterministic source ZIP verification, and the 25,000-message
  search/pagination/backup benchmark with zero WAL failures. Real-device and
  hosted CI/artifact transfer acceptance remains outstanding.

## 2026-10-14 - QQ Scan and Account Isolation, WeCom Handshake

- After a fresh one-time approval, repeated the bounded QQ read-only scan using
  the local worker. It returned `not_found` in about 18 seconds; QQ remains
  `key_required`, no key was verified or saved, and history import did not
  start. The database was not modified. No further scan was made.
- QQ reader shutdown now clears account-specific names, UID mappings, source
  identity, warnings, and media-index state before the reader can be reused.
  A two-account synthetic regression proves stale peer resolution fails closed.
- WeCom bot WebSocket handshake parsing now rejects empty and duplicate
  response-header names; synthetic duplicate-header cases cover `Upgrade` and
  `Sec-WebSocket-Accept`.
- Focused QQ provider/WAL and WeCom bot sender/media suites passed 59 tests.
  The full local gate then passed 732 Python tests, four source-mutation
  fail-closed tests, all 27 WebUI tests, privacy/package/extracted-startup
  checks, deterministic ZIP verification, and the 25,000-message benchmark
  with zero WAL failures. Real QQ history/key and real bot delivery acceptance
  remain outstanding.

## 2026-10-14 - QQ Configuration Race and Gallery Pagination

- QQ key discovery now holds the Hub configuration lock across candidate
  verification, temporary save, final recheck, and rollback; a concurrent
  settings save cannot be overwritten by a failed discovery rollback. A
  synthetic test synchronizes on the configuration thread's actual lock
  attempt and verifies the new directory survives with no candidate key.
- QQ cached-resource gallery paging now honors arbitrary offsets by combining
  adjacent QCE pages when responses are short. Follow-up reads stop after four
  pages; `has_more` is derived from the requested offset and returned rows.
  Regressions cover offset=1, short-page continuation, and the request bound.
- QQ configuration/key-discovery concurrency and cached-resource pagination
  focused tests pass. The P14 privacy, Git-privacy, manifest, and extracted
  package smoke gates also pass. No QQ scan, source-client mutation, or real
  account access occurred in this turn. Clean-checkout/hosted artifact,
  real-device data/media, and real-send acceptance remain outstanding.
- The fresh `scripts/check-local-hub.ps1` run passed 738 Python tests, four
  source-mutation fail-closed tests, all 27 WebUI tests, privacy/package and
  extracted-startup checks, deterministic ZIP verification, and the 25,000-
  message search/pagination/backup benchmark with zero WAL failures. Benchmark
  results: 0.030 s first search, 1.522 s for 50 pages, 0.092 s backup, and
  0.268 s restore.

## 2026-10-14 - Authorized QQ Read-Only Retry

- After the user's fresh one-time approval, started one bounded scan through
  the already-running local Hub at its recorded loopback URL. The API reported
  `limit_reached`; no key was verified or saved, and the failure path did not
  start a new QQ history sync. The scan did not write QQ's database. Another
  scan requires fresh approval.
- QQ remains disconnected for live local-source reads; its existing Hub index
  remains available. This does not close P9 real-history or P11 real-media
  acceptance.

## 2026-10-14 - QQ Gallery Type Filter Scope

- Limited the QQ global-cache media type selector to the QCE gallery's supported
  image, video, voice, and file categories. Chat media still exposes its cached
  sticker filter.
- `node --check webui/app.js`, `node webui/test-media-gallery-preview.cjs`, and
  `git diff --check` pass. No QQ account or cache was accessed.

## 2026-10-14 - WeChat GIF Cache Completeness

- Tightened cached GIF integrity checks: trailing zero padding remains valid,
  but nonzero data after a trailer marker is no longer accepted as a complete
  original image.
- The complete `integrations.wechat.test_media` module passed (57 tests).
  No real WeChat database or cache was accessed.

## 2026-10-14 - Capability, Lock-Transition, and WeCom Upload Hardening

- Capability normalization now forces core modules enabled even if persisted
  settings contain a stale `search: false`. Added dedicated registry tests and
  registered them in `scripts/check-local-hub.ps1`.
- A notification created before account-lock navigation now only closes when
  clicked during the lock transition; it cannot select a conversation or
  navigate the still-mounted page.
- WeCom staged attachments are revalidated incrementally, then uploaded from
  the same open file in 512 KiB chunks. Upload digest/length mismatches stop
  before media finalization and message delivery, avoiding a whole-file memory
  copy in the sending path.
- The complete local gate passed 743 Python tests, four source-mutation
  fail-closed tests, 27 WebUI tests plus the separate daily-chart, lock, and
  navigation/performance checks, package/privacy/extracted-startup validation,
  deterministic ZIP verification, and the 25,000-message benchmark with zero
  WAL failures (0.031 s first search, 1.495 s for 50 pages, 0.094 s backup,
  0.245 s restore). No real account, source-client mutation, or message delivery
  was used. Real-device acceptance and hosted CI/artifact transfer remain open.

## 2026-10-14 - QQ Cache, Moments BMP, and Manifest Atomicity

- QQ local attachment staging now enforces a 50 MiB cap while streaming,
  validates before/after file identity and size, indexed size and optional MD5,
  and does not publish a changed or partial cache file. A growth-after-stat
  regression verifies temporary cleanup and preservation of the prior cache.
- WeFlow-compatible Moments cache resolution and MCP media reading now accept
  BMP by its local `BM` signature, with cache, route, and MCP synthetic tests.
- Package-manifest writes now use a same-directory temporary file and atomic
  replacement; a replacement failure leaves the prior manifest intact and
  removes the temporary file.
- The refreshed full local gate passed 746 Python tests, four source-mutation
  fail-closed tests, all 27 WebUI tests plus daily-chart, lock, and
  navigation/performance checks, package/privacy/extracted-startup validation,
  deterministic ZIP/manifest checks, and the 25,000-message benchmark with zero
  WAL failures (0.038 s first search, 1.462 s for 50 pages, 0.085 s backup,
  0.237 s restore). No new live QQ scan, account access, source mutation, or
  delivery was used; real-device acceptance and hosted CI/artifact transfer
  remain open.

## 2026-10-14 - Media Signature and Restore Bounds

- QQ's QCE `PicType` mapping includes TIFF (`1004`); local QQ attachment
  validation now recognizes classic little/big-endian TIFF and BigTIFF headers.
  MCP image reads return TIFF with the correct MIME type and reject lookalike
  data. The local media route permits inline TIFF only after checking its
  signature.
- WeChat decrypted-image validation now checks the two-byte `BM` signature at
  the correct offset, allowing synthetic legacy-XOR BMP images through the
  same validator as other supported cached formats.
- Media-backup restore now checks the manifest's declared and streamed size
  against a 64 MiB limit before JSON parsing, avoiding an unbounded allocation
  from an otherwise permitted large archive member. The regression confirms
  an oversized manifest is rejected before index/media restore and staged data
  is cleaned up.
- Focused QQ/Hub/WeChat modules passed 327 tests; media-backup tests passed 7.
  The complete local gate passed 750 Python tests, four source-mutation
  fail-closed tests, the WebUI checks, package/privacy and extracted-startup
  checks, deterministic ZIP/manifest checks, and the synthetic performance
  benchmark. The approved QQ scan remains `limit_reached` with no key saved and
  no new sync. Real account/cache acceptance, source-client mutation work,
  real delivery, and hosted CI/artifact transfer remain open.

## 2026-10-14 - Local WeChat Sync and Upload/Cache Parity

- The user-authorized WeChat-only sync completed through the running local Hub.
  Its source state reported `connected`, and the local avatar database was
  available. After restarting the Hub with the bundled Python runtime, the
  user-authorized WeCom local snapshot sync also reported `connected`. Two
  earlier WeCom attempts used system Python without `Crypto`; no source config
  or key was changed. WeCom contact rows did not provide avatar URLs. QQ
  remained disconnected and no QQ scan was repeated. Public project
  documentation intentionally omits account/message totals.
- WeChat Moments media-cache identity now follows WeFlow's WHATWG URL host
  normalization, so explicit default ports and userinfo do not change the
  cache key; non-default ports remain distinct and volatile URL parameters
  remain excluded. Synthetic cache-hit tests cover these variants.
- WeCom media upload now fills each bounded 512 KiB protocol chunk across
  short reads from a legal file-like stream. Early EOF or invalid overread is
  rejected before an incomplete protocol chunk is sent.
- WeCom snapshot failures that identify a missing decryptable database or
  message database now produce fixed safe diagnostics; arbitrary exception
  paths and SQL details stay hidden from the API response.
- Focused Moments/provider and WeCom media/sender/HTTP tests passed. The
  refreshed full local gate passed 754 Python tests, four source-mutation
  fail-closed tests, all WebUI tests, package/privacy and extracted-startup
  checks, deterministic ZIP/manifest checks, and the synthetic performance
  benchmark. Real-account media parity, real sends, and hosted package/CI
  acceptance remain open.

## 2026-10-14 - Local Preview and QQ Key Boundaries

- Conversation-list previews now replace structured/XML/HTML payloads with a
  generic label, redact HTTP(S) URLs, hide explicit credential fields, and cap
  display text at 240 characters. The same sanitizer runs when serializing old
  indexed preview values; original message text remains intact and searchable.
- QQ memory-discovery limits are validated inside the worker: at most 180
  seconds, 20,000 candidates, and 512 MiB. QQ attachment lookup rejects a
  symlinked `nt_data` root or media subtree escaping the selected account.
- QQ keys now use current-user Windows DPAPI in `qq-local.json`. Existing
  plaintext settings migrate only after the encrypted database page verifies;
  protected data fails closed for another user or unsupported platform. The
  native Windows DPAPI round-trip test passed.
- The latest explicitly approved read-only scan through the running Hub ended
  as `not_found` in about 17 seconds. No key was saved and no QQ sync started;
  the source remains `key_required`.
- After the code reload, a fresh previously authorized read-only sync returned
  WeChat and WeCom to `connected`; QQ remained disconnected. Automatic sync is
  still disabled.
- `scripts/check-local-hub.ps1` passed 760 Python tests, four source-mutation
  fail-closed tests, all 27 WebUI tests plus separate daily-chart, lock, and
  navigation/performance checks, package/privacy and extracted-startup checks,
  deterministic ZIP/manifest verification, and the 25,000-message synthetic
  benchmark (0.029 s first search, 1.438 s for 50 pages, 0.084 s backup,
  0.245 s restore; zero WAL failures). Real QQ history/media, source-client
  mutation, real delivery, desktop-browser acceptance, hosted CI and artifact
  transfer remain open.

**P9/P11 follow-up (2026-10-14):** the freshly approved QQ read-only key scan
ended `not_found` within its 180-second limit. No key was saved and QQ history
sync did not start. Annual reports now include the WeFlow-compatible 7×24
weekday/hour heatmap and longest consecutive active-day range, calculated from
UTC+08 indexed timestamps within the selected platform/account/conversation
scope. WebUI renders the heatmap and streak; annual CSV exports both. Focused
synthetic analytics and export tests pass. The refreshed local gate passed all
764 Python tests, the four fail-closed mutation checks, WebUI suites, and
package/privacy/startup checks. Real QQ history, broader P11 resource/decryption
parity and device acceptance remain open.

**P11/P13 media follow-up (2026-10-14):** media gallery tiles and the preview
modal now recognize HEIC/TIFF resources and offer a local original-download
path when inline decoding is unsupported, instead of rendering a broken image.
The P13 attachment send path was audited against current code: it revalidates
the staged file with bounded reads, rewinds that same stream, and reads at most
512 KiB per protocol chunk; the older roadmap statement that it buffers the
whole attachment is superseded. The focused WebUI preview test and 34 WeCom
media/sender tests pass. Real cache and real delivery were not used.

**P11 QQ HEIC gallery follow-up (2026-10-14):** fixed the QQ cache gallery path
so HEIC MIME metadata is honored even when the item has not yet resolved a local
URL. Resolution carries the original filename and MIME through the UI; the
loopback media route responds as `image/heic` with attachment disposition and
streams the original bytes. Synthetic WebUI and HTTP regressions cover the
unsupported-preview fallback, named download, and exact payload. No QQ cache or
real account was accessed.

**P14 package verification follow-up (2026-10-14):** source package smoke now
checks ZIP entry timestamps, ordinal ordering, canonical relative paths, and
Windows case-insensitive path uniqueness alongside byte-for-byte rebuild
comparison and clean extracted startup. `scripts/test-package-smoke.ps1` passes.
This only strengthens local package verification; hosted CI and uploaded
artifact round-trip remain unverified.

**P9 latest scan result (2026-10-14):** one user-approved read-only scan of the
current user's running QQ processes completed in about 19 seconds with
`not_found`. QQ remains `key_required`; no key was saved and no history import
started.

**P9 retry (2026-10-14):** after the user's approval, the local Hub performed
one more scan of current-user QQ processes with its 180-second/512 MiB bounds.
It returned `not_found`; QQ remains `key_required`, no key was saved, and no
history sync/import started. A bounded negative scan does not establish that a
key is absent.

## 2026-10-14 - P12 Moments HTTP mutation-boundary regression

- Added static route-binding assertions for the Moments block-delete install
  and uninstall endpoints and post-delete endpoint. Each route must dispatch
  with its expected HTTP method, and each handler must keep the source-service
  call inside `denySourceMutationRoute`.
- `node scripts/test-source-mutation-gate.cjs` passes all four tests. These
  assertions prove local fail-closed wiring only; P12-03/04 remain
  `NOT_STARTED` pending inspectable native semantics, isolated backup/rollback
  lifecycle evidence, and explicit operation-level approval. No client, native
  component, account, or source database was accessed.

## 2026-10-14 - P11 WeCom Analytics and P13 Input Validation

- Relationship achievement media categories now count WeCom attachment type
  aliases emitted in Chinese by the provider, matching the media library.
- WeChat send preparation rejects non-string conversation IDs before checking
  the `@chatroom` suffix, returning a validation error for JSON null instead of
  an internal error.
- The focused analytics, send-service, and HTTP route run passed 43 tests.
  No real cache was read and no message was sent; P11/P13 remain `IN_PROGRESS`
  pending real-account acceptance.

## 2026-10-14 - P8-P14 Complete Local Gate

- `scripts/check-local-hub.ps1` passed 766 Python tests, four fail-closed
  source-mutation tests, all 27 WebUI test files plus separate daily-chart,
  lock, and navigation/performance checks, package privacy and extracted
  startup checks, and deterministic ZIP/manifest verification.
- The 25,000-message synthetic benchmark completed with zero WAL failures:
  first search 0.032 s, 50 cursor pages 1.545 s, backup 0.085 s, restore
  0.248 s.
- Real QQ history/media, real browser/native acceptance, live message delivery,
  clean committed-checkout, hosted CI, and artifact transfer remain open; this
  gate is local synthetic/package evidence only.

## 2026-10-14 - P14 Windows Package Rebuild

- Fixed extracted source smoke startup on Windows Python virtual environments:
  `Start-Process` can report the Python launcher PID while the Hub writes its
  service PID. Smoke still requires a positive service PID, a loopback URL,
  isolated storage, and successful WebUI, source, conversation, and read-only
  MCP HTTP checks. Portable EXE smoke retains its exact process-ID check.
- Rebuilt portable p358 (9,783 files, 586,659,320 bytes) and source p363
  (690 ZIP entries). Both passed privacy and extracted
  startup checks; the portable build also passed standalone MCP stdio smoke.
  The source archive includes QQ `key_store.py` and `send_target.py`.
- Generated and verified local-worktree manifest p363 against base commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; per-file hashes and inventory
  matched. `scripts/test-package-smoke.ps1` passed, including deterministic ZIP
  checks and occupied-port behavior.
- P14 remains `IN_PROGRESS`: no clean committed checkout or hosted CI run was
  produced, and GitHub artifact upload/download verification remains open.

## 2026-10-14 - QQ Scan Result and MCP Sticker Retrieval

- The approved read-only QQ key scan ended with `not_found` within the configured
  180-second/512-MiB bounds. No key was saved and no history import was started.
- MCP `get_attachment` now permits only indexed WeChat `raw_type=47` stickers
  without child attachments through the existing cached-image reader. The
  reader still enforces exact-account cache containment, supported image
  signatures, and the 8-MiB response limit.
- Personal WeChat sending now enters approved text with Unicode keyboard events,
  leaving all existing clipboard formats untouched. The sender test double
  checks clipboard preservation on success and pre-send failure, and validates
  the native INPUT structure size.
- The focused MCP sticker contract and WeChat sender suites passed 22 tests.
  No real cache, QQ database, or real WeChat window was used; P9/P11/P13 live
  account acceptance remains open.

## 2026-10-14 - P13 Clipboard-Safe Sender and P14 p359/p364 Packages

- Personal WeChat approved text now enters through `SendInput` Unicode events;
  sender tests cover UTF-16 surrogate pairs, key down/up balance, native INPUT
  structure size, and clipboard preservation on success and pre-send failure.
- The focused MCP sticker contract and WeChat sender checks passed 22 tests.
- `scripts/test-package-smoke.ps1` passed source privacy, extracted Hub/UI/source
  and read-only MCP startup, occupied-port, storage isolation, and deterministic
  ZIP checks. Portable p359 passed isolated Hub/UI and standalone MCP stdio
  smokes. Source p364 passed extracted startup and privacy checks (690 entries).
- Manifest p364 verifies both package inventories against base HEAD
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; SHA-256:
  portable EXE `0aad50fa85d296695e9650dfe442d1e431a715311d4259cc9e5a129235f9d342`,
  source ZIP `53b12316bedb20d82ef3999c05a9689410e569e4f95d4185b094c6989c0aae1c`,
  manifest `ee87fba0b40957ba48e8bfa442dd5f032ca094359bf27ecd9f4714588814e166`.
- No real WeChat window, delivery, QQ database, or message cache was used.
  Hosted CI, artifact upload/download, clean committed-checkout acceptance, and
  P9/P11 real data acceptance remain open.

## 2026-10-14 - P9 Sanitized Key-Scan Diagnostics

- The QQ key-discovery status now carries only validated counters within scanner
  bounds: QQ process count, tested candidate count, and MiB read. PIDs, paths,
  account IDs, keys, and arbitrary scanner fields remain excluded.
- The data-source UI renders the three counters only after a scan ends. Focused
  Hub and WebUI checks passed, including boolean, negative, and over-limit
  counter rejection. The already completed `not_found` scan has no retroactive
  counters; a future scan will expose them.

## 2026-10-14 - P14 p360/p365 Package Refresh

- Portable p360 (9,783 files, 586,677,973 bytes) and source p365 (690 entries,
  25,042,454 bytes) were built after the QQ diagnostics change. Reused QCE
  binary SHA-256 `9f5b59203134d9ea7772f9aa8021b77269b4c676c8be3cadbf2dc6d60fff2ba9`
  only after the builder verified that exact input digest.
- Portable Hub/UI and standalone MCP startup passed. Source archive passed
  privacy, extracted Hub/UI/source/read-only MCP startup, and deterministic ZIP
  order/timestamp/path checks. Manifest p365 passed inventory and hash
  verification against base HEAD `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`.
- SHA-256: portable EXE
  `0fae5ad313b9d441cb8b9f138a59aa81b7202b6e523e89148298fea7839048d6`, source
  ZIP `16f10307b5bf046ce82ac6e945ef1be66659c6b19b6402390b0ddeeeda5cd9b58`,
  manifest `adc1fe51cf40ba5bed290f3f9f29bd9eea34b5ad08dd02fadda251c4f8ba9f94`.
- P14 remains `IN_PROGRESS`: no clean committed checkout, hosted CI run, or
  artifact upload/download round trip was performed.

## 2026-10-14 - P9 Local QQ History Acceptance

- After fresh user approval, the running local Hub completed one bounded,
  read-only QQ key discovery. The key was verified and saved locally; QQ history
  indexing completed and the source reports `connected` without an error.
- No QQ database write, account identifier, key material, or message content
  was exposed or recorded. This closes local QQ read-source acceptance; live
  NapCat sending and other platform account acceptance remain open.

## 2026-10-14 - P13 WebUI Route and P14 Local Gate Repair

- The personal WeChat send composer now marks requests as `wechat`, routing
  preparation and subsequent approval actions through the WeChat endpoints.
  The inspector button now names WeChat correctly. Focused composer and avatar/
  inspector regressions pass; no message was sent.
- Fixed the PowerShell continuation in `scripts/check-local-hub.ps1` so
  `integrations.qq.test_send_target` runs in the unittest invocation. The
  complete local gate passed: 777 Python tests, four fail-closed mutation tests,
  the listed WebUI suites, package privacy/startup/determinism checks, the
  synthetic benchmark, and `git diff --check`.
- After explicit authorization, read-only local sync completed for WeChat and
  WeCom. The running local Hub reports all three platforms as `connected` with
  no source errors. This does not verify real message delivery, media-cache
  completeness, hosted Actions, or artifact upload/download.
- The data-source UI now shows those live connection states. A bounded local
  avatar check returned signature-validated cached images for WeChat and QQ;
  WeCom contact rows currently have no avatar URL. The media index contains
  image entries, but no chat attachment bytes were opened in this check.

## 2026-10-14 - P9 Scan Short-Circuit and P14 Artifact/CI Audit

- The local QQ source currently reports `connected` with a completed sync
  timestamp. The user's approved discovery retry was rejected by the existing
  guard because the database is already readable; no QQ process memory was
  scanned and no local configuration changed.
- Reverified manifest
  `local-20261014-roadmap-p367-local-wechat-route` against portable p361 and
  source p367. The verifier confirmed all package bytes and local-worktree
  provenance. Portable inventory: 9,783 files / 586,682,852 bytes; source ZIP:
  25,084,494 bytes, SHA-256
  `c9616583f11c94740273e6671672cb7387459e3e7ef28b703ca2e3e046b42bea`; manifest
  SHA-256 `c6771a57a59dd72a8bd7a11aca2663f1253213885a441c3fff0537554fe7beb7b`.
- GitHub read-only inspection found one Local Hub CI run, on initial commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; it failed in the local gate due
  to missing `cryptography` and a runner-path-specific assertion. The current
  worktree workflow/dependency/test updates are not present on the remote main
  branch, so this old run does not verify the current worktree. P14 remains
  `IN_PROGRESS`; no publish or source-client mutation occurred.

## 2026-10-14 - P11 WeChat 4.x Sticker Classification

- WeChat message type `11000` now follows the existing sticker placeholder
  across message normalization, media filtering/counts, cached-preview refusal,
  quoted-message preview, and MCP attachment metadata. Both cursor and offset
  sync preserve only a 32-character hexadecimal MD5 found in the XML emoji
  element. The existing account-bound cache resolver still decides whether any
  local file is readable, and no cache path is inferred.
- WebUI treats type 11000 as a sticker and excludes it from ordinary image
  preview requests. The anti-recall settings description now accurately states
  that only read-only status checks are available.
- Added focused synthetic regressions for the placeholder and sticker counts.
  Real type-11000 cache rendering and broader P11 attachment-byte acceptance
  remain open; the user's bounded QQ scan was short-circuited because the local
  QQ database was already readable.

## 2026-10-14 - P9 WeChat Group Preparation and P14 Tabular Privacy Scan

- WeChat `@chatroom` identifiers now pass the shared send validator, MCP
  parameter schema and WebUI form check. Conversation type must still match the
  suffix. Focused tests only prepare a pending request; no delivery occurs.
- Restored three WeChat send tests that were placed after `unittest.main()` and
  therefore were never collected. Added MCP and WebUI group-target regressions.
- The source-package privacy scan now reads CSV and TSV contents, and the source
  packager excludes both data extensions entirely. Regressions put synthetic
  profile paths in both formats and verify the scanner rejects them; a packaging
  contract assertion verifies both extensions are excluded before ZIP creation.
- The 25,000-message synthetic benchmark now fails on broad ingest/search/
  pagination/backup/restore ceilings. Latest timings were 1.685 s ingest,
  0.043 s first search, 2.004 s pagination, 0.109 s backup, and 0.312 s restore;
  these are local measurements, not host-independent targets.

## 2026-10-14 - P8-P14 Focused Regression Verification

- Re-ran the send service suite (19 tests), WeChat MCP group preparation,
  three WebUI media/send regressions, the CSV/TSV privacy fixture and Git-visible
  privacy scan; all passed. `git diff --check` passed.
- Re-ran the synthetic 25,000-message benchmark: 1.685 s ingest, 0.043 s first
  search, 2.004 s cursor pagination, 0.109 s backup and 0.312 s restore; all
  remained below the new broad ceilings, with 50 pages and zero WAL failures.
- P9 live delivery, P11 real attachment-byte preview, P12-03/04 audited native
  implementations, and P14 hosted Actions/artifact roundtrip remain unverified
  or unavailable. No real message was sent and no source-client database was
  modified.

## 2026-10-14 - P12 Browser Lock Acceptance

- Isolated Microsoft Edge against a temporary Hub database verified passphrase
  setup, immediate lock, unauthenticated API denial (423), three-second idle
  expiry returning to the lock page, and successful unlock after expiry.
- The browser run exposed that disabling the passphrase input before creating
  `FormData` sent `null` to the unlock API. `webui/lock.js` now captures the
  passphrase first; `webui/test-lock-interaction.cjs` models disabled-field
  omission and asserts the submitted value. The focused regression passes.
- The temporary Hub contained no real source accounts or messages. Real desktop
  notification delivery remains unverified; P12-01 stays `IN_PROGRESS`.

## 2026-10-14 - QQ Readiness Retry and Local Sync

- After the user's one-time approval, retried the bounded QQ key-discovery
  action. The Hub refused to scan process memory because the local QQ database
  was already readable with a locally verified key.
- Started one read-only local QQ sync through the Hub. It completed across all
  local conversations and refreshed the Hub index; QQ source files were not
  modified. No account identifiers, key material, message totals or chat
  content are recorded here.

## 2026-10-14 - P12 Synthetic Notification and Export Recheck

- Re-ran 31 focused backend tests for account safety, scheduled export, Moments
  export, and the body-free notification SSE endpoint. Coverage includes
  capability/filter behavior and stream closure when notifications are
  disabled or the account locks; all passed.
- Three WebUI synthetic checks passed for notification target/privacy,
  unsupported-browser handling, and unlock-form submission. The isolated Edge
  lock/relock/unlock lifecycle was already verified in the preceding P12 entry
  and was not repeated.
- No OS-level notification was delivered and no real account/source-client
  database was accessed. P12-01 remains `IN_PROGRESS` for real desktop
  notification delivery; P12-03/04 remain blocked on inspectable native
  implementations and isolated lifecycle/rollback evidence.

## 2026-10-14 - P14 Full Local Gate Recheck

- `scripts/check-local-hub.ps1` passed: 779 Python tests, four source-mutation
  gate tests, 32 WebUI tests, package/privacy/startup and deterministic ZIP
  checks, syntax/compile checks, and `git diff --check`.
- The synthetic 25,000-message benchmark passed: 1.873 s ingest, 0.043 s first
  search, 2.893 s for 50 cursor pages, 0.126 s backup, 0.395 s restore, and zero
  WAL failures. Python emitted socket `ResourceWarning`s, but the suite exited
  successfully with all tests passing.
- GitHub's latest Local Hub CI run still targets the old initial commit and
  failed before packaging. This worktree has not been committed, so hosted CI
  and the real uploaded/downloaded artifact round trip remain unverified. No
  commit, push, publication, message delivery, or source-client mutation was
  performed.

## 2026-10-14 - P11 Local Media Availability Check

- Corrected the original probe to follow WebUI field mapping, including nested
  attachment indices, and sampled 12 chat images per platform. Two WeChat media
  URLs resolved; remaining WeChat items and all sampled WeCom/QQ items returned
  the ordinary not-cached result. No HTTP errors occurred.
- The earlier `message_missing` result was caused by the probe's incorrect
  parameter mapping. This live sample confirms some WeChat media can resolve,
  but does not establish broad cache coverage or complete media acceptance.
- Only platform-level candidate and availability counts were recorded. No
  message text, account identifiers, paths or media bytes were logged, and no
  source-client data was modified.

## 2026-10-14 - P11 QQ Media Scan Priority

- The local QQ resolver now checks a known media-category cache before the
  broad `chatraw` tree, preserving the existing scan and file-size limits.
- Three focused QQ media tests passed, including a one-entry budget regression
  proving a cached category file is found even when `chatraw` exhausts the
  remaining budget. `git diff --check` passed for the touched files.
- QQ remains connected with a readable local database. The latest approved key
  discovery retry was short-circuited because the configured key already
  authenticated the database; no process-memory scan ran during that retry.
  Real-device cache coverage remains unverified.
- Rebuilt portable package p372 (9,783 files, 586,695,037 bytes) and sanitized
  source ZIP p372 (690 files, 25,087,753 bytes). Portable and extracted-source
  startup/MCP/privacy checks passed; the local-worktree manifest verified every
  input hash. Hosted CI and artifact upload/download remain unverified.

## 2026-10-14 - P11 WeCom Shared Attachment Identity

- WeCom attachment lookup now keeps every distinct `file_table4` message ID
  for an MD5 and links resolved CacheMapping files and media-index fallbacks to
  each message. The focused synthetic regression passed for two messages
  sharing one cached image, and `git diff --check` passed.
- An explicitly approved QQ discovery retry was sent to the local Hub. It
  refused the scan because the saved key already authenticates the local QQ
  database; no process memory was read and no configuration changed.
- Real WeCom cache coverage remains unverified.

## 2026-10-14 - P8 MCP Chat Capability Gate

- At this checkpoint `search_messages` was temporarily mapped to `chat`, so the
  tool was omitted when chat was disabled. A later P8 contract review found
  this contradicted P8-01's always-available core search requirement; the
  follow-up below restores search under its own core capability while keeping
  conversation and message reads gated. Every MCP tool still has an explicit
  capability mapping.

## 2026-10-14 - P13 WeChat Receipt Baseline

- The WeChat text sender now fails before GUI input when the pre-send database
  read fails. A valid empty message set stays distinct from a failed read, and
  the regression confirms an older identical self-sent row cannot trigger a
  false delivery receipt after a failed baseline.
- The focused new regression and the existing stale-receipt regression passed;
  `git diff --check` passed. No live WeChat window or account was used.
- Real-device delivery remains unverified.

## 2026-10-14 - P14 Local Review Package p373

- Rebuilt the Windows portable package and sanitized source ZIP after the
  P11/P13 fixes. The portable directory contains 9,783 files
  (586,698,253 bytes); the source ZIP contains 690 files (25,088,378 bytes).
- Isolated portable and extracted-source startup checks passed, including the
  WebUI, three source entries, privacy scans, and read-only MCP. Manifest p373
  verified both package byte inventories against the local worktree.
- Hosted CI and GitHub artifact upload/download remain unverified; no commit or
  publication was made.

## 2026-10-14 - P14 Local Review Package p375

- Rebuilt the Windows portable package and sanitized source ZIP after the P8
  capability fix. Both packages passed isolated startup and read-only MCP
  checks; package text and Git-visible changes passed privacy scans.
- The local-worktree manifest verifies both package byte inventories. Hosted
  CI and artifact upload/download remain unverified; no commit or publication
  was made.

## 2026-10-14 - P8 Full Gate and P14 Local Review Package p376

- The complete local gate passed 785 Python tests, four source-client mutation
  gate tests, WebUI suites, package checks and the performance benchmark after
  updating the prior chat-disabled MCP expectation.
- The p376 portable and sanitized source packages passed isolated startup and
  read-only MCP checks. Their local-worktree manifest verifies both byte
  inventories; hosted CI and artifact roundtrip remain open.

## 2026-10-14 - P11/P12/P13 Local Regression Follow-up

- MCP attachment inspection prompts now instruct clients to use `list_media`
  and pass the selected WeCom/QQ `resource_index`, including explicit selection
  for multi-attachment messages. A synthetic prompt/reader regression passed.
- The shared lazy WeChat sender now serializes readiness and full delivery.
  WeCom marks send-attempt state immediately before its send-frame write; a
  controlled-clock regression verifies deadline expiry before that write is a
  retryable authentication timeout, while existing transport interruption
  remains `unknown`. Focused sender tests passed.
- App-lock-enabled tabs now poll the body-free local lock status every two
  seconds, independent of desktop notifications; page exit and disabling the
  lock stop polling. The account-activity and navigation regressions passed.
- The full local gate passed 787 Python tests, four fail-closed source-client
  mutation tests, 32 WebUI checks, privacy and isolated-package checks, and the
  deterministic ZIP smoke. The 25,000-message benchmark completed with zero
  WAL failures (1.369 s ingest, 0.030 s first search, 1.872 s for 50 pages,
  0.083 s backup, 0.238 s restore). No real message was delivered and no
  source-client database was modified.

## 2026-10-14 - P14 Local Review Package p377

- Rebuilt the portable package (9,783 files, 586,714,465 bytes) and sanitized
  source ZIP (25,090,581 bytes). Portable Hub/UI/three-source, read-only MCP,
  standalone MCP stdio and extracted-source startup smokes passed.
- Privacy scans passed. Manifest
  `LocalMessageHub-artifact-manifest-review-20261014-p377.json` verifies the
  local-worktree package byte inventories against base commit
  `8f45f2525c432ba81d6125b8d728f0ae5517ef5d`; its SHA-256 is
  `ada67b5b0f20f82d551edd074d215b156d4bf0296fb2be2b964d28d19156e996`.
- Hosted Actions and the downloaded-artifact round trip remain unverified. No
  commit, push, or publication was made.

## 2026-10-14 - P14 Hosted CI toolchain correction

- Public branch `codex/roadmap-p8-p14-20261014` contains p377 at
  `638555653242501c1b08bbfd7f039e0037e5af1e`; the earlier clean-archive
  omission of workflow/package helpers is closed by that commit.
- Hosted run 38540434801 failed during MinGW package download, before the
  local gate or package steps, because the geo-redirected mirror returned a
  Cloudflare verification page. The workflow now uses the official package
  host directly; all three existing SHA-256 pins were confirmed against full
  downloads, and YAML plus embedded PowerShell parsing passed locally.
- Hosted run 38541563975 passed all three direct-download SHA-256 checks but
  failed at `pacman -U`, which could not resolve the exact pinned `libgcc` and
  `libstdc++` requirements of GCC. Inspection of each downloaded `.PKGINFO`
  confirmed that package names, versions, and dependency constraints match.
  The workflow now runs installation inside the setup-msys2 shell and converts
  `RUNNER_TEMP` to an MSYS path before invoking pacman. YAML, embedded
  PowerShell, Bash syntax, and `git diff --check` pass locally; the pacman
  context fix has not yet had a hosted run. setup-msys2 logged a non-fatal
  `clangarm64.db` timeout while continuing its update.
- Hosted run 38542671122 reported the same two missing GCC sibling dependencies
  even with all three explicit package targets in that MSYS2 shell. This
  disproved path conversion as the cause. The workflow now asks pacman to
  install the pinned `libgcc`, `libstdc++`, and GCC archives in dependency
  order, with an immediate failure check after each transaction. YAML and the
  embedded PowerShell/Bash syntax checks pass locally; sequential installation
  still needs a hosted run.
- Hosted run 38543367064 installed the two hash-pinned runtime packages but
  pacman then upgraded them to `16.2.0-6` from the current sync repositories
  during the GCC transaction; the new exact-version guard caught the drift.
  The workflow now ignores upgrades of only those two runtime packages while
  installing GCC with pacman's targeted `--assume-installed` option, then
  requires `pacman -Q` to confirm that all three actual installed packages are
  version `16.2.0-5`. The existing SHA-256 checks and normal checks for all
  other GCC dependencies remain active. This correction still needs hosted
  verification.
- Hosted run 38544398023 showed why package-release pinning conflicts with the
  updated MSYS2 repository: current `cc-libs` requires newer `libgcc` and
  `libstdc++` releases, and pacman correctly refused the ignored older runtime
  packages. CI now installs GCC and its complete dependency graph from the
  updated official signed repositories, while the existing build step still
  requires GCC 16.2.0. The package smoke checks the install command and version
  gate. This change still needs a hosted run.
- Hosted run 38544901625 passed GCC installation and reached the locked root
  npm install, where strict peer resolution rejected the existing React 19 /
  ansi-to-react peer range. The WCDB compatibility workflow already uses
  `--legacy-peer-deps` for this lockfile. The Windows packaging workflow,
  package smoke, and build instructions now use the same lockfile-compatible
  setting; no dependency versions changed. A full hosted run remains pending.
- Hosted run 38545314515 passed toolchain and dependency installation but
  exposed 25 Windows-only test failures. Most compare long runner profile paths
  with their `RUNNER~1` 8.3 aliases, cascading into temp-backed cache, media,
  and Moments tests. CI now sets `TEMP` and `TMP` to the runner's dedicated
  temp directory to keep those paths canonical. One short synchronization
  timeout may remain; the focused rerun will confirm.
- Hosted run 38546410777 failed at workflow validation before creating a job
  because the runner temp expression was placed at job scope. The temp
  environment is now set through `GITHUB_ENV` in a PowerShell step after runner
  startup, before the Windows checks and package smokes.
- A new hosted run and uploaded-artifact download/provenance round trip are
  still required; no real message was sent and no source-client database was
  modified.

## 2026-10-14 - P13 Exact WeChat Window Identity

- Replaced prefix matching for the personal WeChat history-window marker with
  exact-title matching. A different conversation whose title only begins with
  the selected contact plus `chat history` can no longer be treated as the
  approved target.
- Added synthetic checks for exact marker matches and arbitrary trailing text.
  The sender test suite passes without opening WeChat or delivering a message;
  real window-title variants and delivery remain unverified.

## 2026-10-14 - P14 Hosted Windows CI and Artifact Round-Trip

- Commit `c38d5fb90f9195d348831932ef5fb93afe7b273f` fixed Windows checkout
  line endings for JavaScript test sources. The 32 WebUI checks passed locally.
- Hosted run 38547565569 passed the complete Windows gate, including 788 Python
  tests, four source-client mutation-gate tests, WebUI checks, benchmark,
  portable and source-package builds, and isolated package smokes.
- The hosted run created and pre-verified the provenance manifest, uploaded
  the release artifact, downloaded it again, and passed byte/provenance
  verification. `LocalMessageHub-windows-release` is 251,740,883 bytes and is
  retained through 2026-10-21. P14-01 is DONE.
- The approved QQ key-discovery request was sent to the local API; the API
  refused a repeat scan because the QQ database is already readable. Its
  existing key/index were left untouched. The separate QQ online-history
  import capability remains disabled; no import or message delivery occurred.

## 2026-10-14 - P14 Hosted Verification on Current Roadmap Commit

- Hosted run 38550004873 passed on commit
  `75fa73185b5b1acf960da35ef331901c3284deef`, including the full Windows gate,
  portable and sanitized source package builds, isolated startup/MCP smokes,
  artifact manifest verification, upload/download, and downloaded-byte and
  provenance checks.
- `LocalMessageHub-windows-release` is 251,741,288 bytes and expires
  2026-10-21. This reran P14 against the current documentation commit; the
  product code is unchanged from the previously verified code commit.

## 2026-10-14 - Local Three-Platform Sync and Avatar Display

- Started the Hub on loopback with automatic sync disabled, then explicitly
  requested one read-only sync for all configured sources. WeChat, WeCom, and
  QQ completed with connected status and no reported errors.
- The live WebUI loaded the indexed conversations and visually rendered cached
  WeChat avatars. No message contents or account totals are recorded here.
- A separate QQ key-discovery request returned the existing-key short circuit;
  no fresh process-memory scan ran and no source-client database was modified.

## 2026-10-14 - Approved QQ Read-Only Scan Retry

- Ran the bounded scanner directly after the Hub's normal endpoint correctly
  skipped key discovery for an already-readable database. It found no QQ
  process accessible to the current user and read 0 MiB; the existing local key
  still reports the database as ready.
- No key/configuration was changed, and no history import was needed or started.
  The scanner output exposed no candidate material; QQ database files were not
  modified.

## 2026-10-14 - P8 Search Capability Enforcement

- Follow-up contract review corrected the new HTTP and MCP gates: search is an
  always-enabled core capability under P8-01, so both search interfaces remain
  available when chat browsing is disabled. Conversation, message-page, and
  message-context reads continue to require the `chat` capability.
- Focused regressions confirm both sides of that boundary.

## 2026-10-14 - P14 Full Gate on Current Worktree

- After aligning HTTP and MCP search to the core `search` capability, the
  complete local gate passed: 789 Python tests, four source-client mutation
  gate checks, 32 primary WebUI checks, package/privacy checks, deterministic
  source ZIP verification and the 25,000-message performance benchmark.
- The run recorded 2.041 seconds for synthetic ingest, 0.038 seconds for the
  first search, 1.948 seconds for 25,000 paged rows, 0.109 seconds for backup,
  0.339 seconds for restore, and no WAL failures. Hosted artifact verification
  still refers to committed HEAD; these worktree changes remain uncommitted.

## 2026-10-14 - P11 Bounded Local Media Resolution

- A read-only probe used the WebUI's indexed identities and media resolver,
  checking at most 12 items per platform and media type. Bounded byte reads
  succeeded for cached WeChat image and WeChat/QQ voice items.
- The sampled WeChat video, WeCom media, and QQ image/video entries had no
  readable local cache. No chat text, names, account identifiers, or totals
  were recorded. This does not verify full media decode or playback.

## 2026-10-14 - P11 QQ Relationship Media Aliases

- Objective media achievements now fall back from an empty QQ attachment
  `type` to the provider's `kind` or legacy `k` category field. A synthetic
  regression covers image and voice counts without reading local media.
- The analytics suite passed 26 tests. Full resource/decryption parity and
  real-cache acceptance remain open.

## 2026-10-14 - P12 Native Trigger Source Audit

- Checked the repository and WeFlow pinned commit
  `2f23e72cebf7f309a0f4d129307d16d7b0cd41c3`. Both contain the WCDB wrapper
  and trigger ABI declarations, but no auditable native trigger source or
  WCDB library; upstream says users supply the native component.
- The source-mutation gate passed 4 tests and the optional-export harness passed
  44 synthetic tests. No native binary or database was accessed. P12-03/04
  remain `NOT_STARTED` pending inspectable implementation and isolated
  install/check/uninstall/rollback evidence.

## 2026-10-14 - P12 Browser Notification API Check

- An isolated Edge context with a temporary SQLite store granted browser
  notification permission and consumed one synthetic incoming message. The
  browser notification `show` event fired with only the generic platform
  message; a sentinel message body was absent from the notification fields.
- The hidden-page state was simulated in the isolated test page. This verifies
  the browser Notification API path, not delivery by the Windows notification
  center. Real desktop-notification acceptance remains open. The isolated
  browser and Hub process were closed; recursive cleanup of the synthetic
  temporary data directory was blocked by the automatic review gate.

## 2026-10-14 - P11 QQ Compact Attachment Export

- QQ export normalization now maps compact attachment keys `k` and `sz` to
  the standard `type` and `size` fields. A synthetic regression confirms the
  fields survive JSON export and the source cache path is omitted.
- Focused export regressions passed. Real-device cache/export coverage and
  broader media parity remain open, so P11 stays `IN_PROGRESS`.

## 2026-10-14 - P11 Group Roster Count Semantics

- Group analytics now preserves an explicit roster count of zero and falls
  back to observed senders only when the roster count is absent (`NULL`). A
  synthetic group with one observed sender verifies the roster and observed
  counts stay distinct.
- All 27 analytics tests passed. Real-device roster completeness remains part
  of the broader P11 acceptance.

## 2026-10-14 - P11 Boolean Attachment Index Validation

- Media resolution now rejects Boolean `resource_index` values before WeCom or
  QQ roaming provider lookup; Python otherwise treats `true` as integer index
  1. A synthetic regression verifies both paths leave their providers untouched.
- The new regression and adjacent WeCom bound and QQ local-index tests passed.

## 2026-10-14 - P14 Full Local Gate After P11 Follow-Ups

- `scripts/check-local-hub.ps1` passed from the current worktree: 792 Python
  tests, four source-client mutation-gate checks, 32 WebUI checks, package
  privacy and deterministic ZIP checks, plus the 25,000-message benchmark.
- The benchmark recorded 1.577 seconds for ingest, 0.035 seconds for first
  search, 2.223 seconds for 50 cursor pages, 0.092 seconds for backup and
  0.295 seconds for restore, with zero WAL failures. The worktree changes are
  uncommitted; hosted CI and artifact round-trip evidence still applies to
  committed HEAD `75fa73185b5b1acf960da35ef331901c3284deef`.

## 2026-10-14 - P14 Current-Worktree Package Verification

- Rebuilt the Windows portable and sanitized source ZIP from the current code
  worktree. The portable Hub served the WebUI and three source entries from
  isolated storage; the standalone MCP executable completed initialize,
  `tools/list`, and exit over stdio.
- Privacy scans passed for the portable and extracted source package. A
  `local-worktree` manifest verified every package file hash and size. This is
  local package evidence only; it does not replace hosted artifact transfer
  evidence for a committed revision.

## 2026-10-14 - P11 QQ Reply Preview Attachments

- Local QQ parsing now retains bounded media metadata embedded in kind-7
  reply previews. The WebUI offers cache resolution inside the quoted block
  and filters quoted entries from the ordinary attachment strip.
- Synthetic parser and local-cache resolution regressions pass; all 28 local
  QQ provider tests and the QQ rich-content WebUI check pass. Real-account
  cache coverage remains unverified, so P11 remains `IN_PROGRESS`.

## 2026-10-14 - P11 Orphan Message Analytics Scope

- Private-profile aggregation now excludes messages without a matching,
  explicitly private conversation row, following the established daily
  analytics classification rule.
- The existing synthetic private-profile fixture now includes an orphan
  message and verifies the recognized private profile still returns its
  original counts.

## 2026-10-14 - P13 WeChat Delivery Readiness Probe

- The user selected one indexed conversation and supplied a test message for
  a live acceptance attempt. The Hub resolved one matching conversation but
  the sender readiness probe found no WeChat window.
- No send request was prepared or delivered. The temporary `wechat_send`
  capability enablement was reverted to off; real delivery awaits a logged-in
  desktop client and the existing per-message approval.

## 2026-10-14 - P14 Final Current-Worktree Gate

- The full local gate passed 793 Python tests, four source-mutation gate checks,
  32 WebUI checks, package privacy/determinism checks, and the synthetic
  25,000-message benchmark. Ingest/search/50-page pagination/backup/restore
  took 1.427/0.031/1.959/0.095/0.245 seconds; WAL failures: zero.
- The refreshed Windows portable and sanitized source packages passed isolated
  Hub/MCP/source startup, privacy scanning, and manifest byte/provenance checks.
  Hosted CI/artifact evidence remains for the last committed revision only.
- Port 8765 was unavailable at final UI refresh. The prior process was no longer
  running, but automatic review rejected starting the replacement as `blocked
  by policy`; the running UI was not updated by this gate.
- Public-source review replaced a machine-specific anecdote in a vendored
  comment with the general rowid-mapping rule; Git-visible and package scans
  pass without carrying that local acceptance detail.

## 2026-10-14 - P11 QQ Attachment Index and P13 Readiness

- Filtering quoted QQ reply attachments no longer renumbers the remaining
  message attachments. The renderer keeps each original resource index, so a
  later attachment action resolves the intended cached resource.
- The regression and full local gate passed: 793 Python tests, four source
  mutation checks, 32 WebUI checks, package privacy/startup checks, and the
  25,000-message benchmark. Search was 0.044 seconds, 50-page pagination was
  3.001 seconds, and WAL failures were zero.
- The Hub is currently not running on the local UI ports and `wechat_send` is
  off. No live WeChat request was prepared or delivered; P13 remains open.

## 2026-10-14 - P12 Current-Worktree Audit

- Rechecked the P12 capability gates and current implementation: Moments,
  notifications, auto-export, account safety and relationship footprint remain
  independently gated; the three relationship-footprint MCP tools are
  capability-gated and return content-free metrics. Anti-recall and Moments
  source mutation remain unavailable behind the Electron/HTTP fail-closed
  gate.
- Focused local verification passed 74 Python tests across auto-export,
  account safety, analytics, Moments parsing/export, Hub HTTP and MCP; all four
  source-mutation gate checks and four notification/lock/Moments WebUI test
  files passed. No live source database or client was accessed.
- P12-01 remains `IN_PROGRESS` for real desktop notification delivery and
  remaining real-account/cache acceptance. P12-03/04 remain `NOT_STARTED`
  until an auditable native implementation and isolated backup/rollback
  lifecycle are available.

## 2026-10-14 - P8-P14 Latest Local Gate and Package

- The current worktree passed 797 Python tests, four source-client mutation
  gates, 32 WebUI checks, privacy and deterministic ZIP checks, and the
  25,000-message benchmark. Ingest/search/50-page pagination/backup/restore
  took 1.552/0.030/1.519/0.099/0.260 seconds; WAL failures: zero.
- The refreshed local-worktree portable package contains 7,554 files
  (288,305,491 bytes); the sanitized source ZIP is 25,056,673 bytes. Isolated
  Hub/UI/MCP startup, package privacy scans, and manifest byte/provenance
  verification passed. These packages include uncommitted worktree changes;
  hosted CI and artifact round-trip remain open for this exact state.
- The Hub was not running on the checked local ports, `wechat_send` was
  disabled, and the exact chat window was not open during that probe. It found
  no queued request then; current persisted status is unverified. No message
  was sent.

## 2026-10-14 - P9 Boolean Port Validation

- NapCat OneBot configuration now rejects JSON booleans wherever an integer
  port is required, both when repairing the saved config and when reading the
  endpoint for use. Focused coverage verifies invalid `true` is replaced with
  an allocated integer port and rejected at the transport boundary.
- `python -m unittest integrations.qq.test_sender` passed 19 tests. Live
  NapCat and real-account delivery remain unverified.

## 2026-10-14 - P11 Moments Video Gallery Tiles

- Media-library tiles no longer place playable Moments video URLs in `<img>`;
  videos without a separate cached poster show the existing video icon. Cached
  Moments image thumbnails remain unchanged.
- `node --test webui/test-wecom-resource-render.cjs` passed with synthetic
  image and video tile assertions. Real media-cache acceptance remains open.

## 2026-10-14 - P14 Package Refresh After Focused Fixes

- Rebuilt the Windows portable package and sanitized source ZIP after the P9
  and P11 follow-ups. Isolated Hub/UI/MCP startup, source-package smoke, and
  privacy scans passed. The portable package contains 7,554 files
  (288,307,456 bytes); the source ZIP is 25,098,410 bytes.
- The local-worktree package manifest verified both packages against base
  commit `75fa73185b5b1acf960da35ef331901c3284deef` and run label
  `local-worktree-final4-20261014`; manifest SHA-256:
  `BD3C5BBD0F75154BD68E0EF3B104D59A82E59E88FC21F378C53DB25E2C6C6287`.
  These uncommitted changes still lack a matching hosted artifact round trip.

## 2026-10-14 - P14 Complete QQ Runtime Package

- Built the vendored QCE server with the pinned Rust 1.98.1 GNU toolchain,
  GCC 16.2.0, loopback-source guards, and the locked Cargo dependency graph.
  QCE SHA-256: `e471dd4bc3b3fee22c518de122e8144bab734b1693b120f65fda1d61caf3dc89`.
- Rebuilt a complete Windows portable package containing the verified NapCat
  6.3.2 shell and QCE server, plus the sanitized 690-file source bundle.
  Isolated Hub/UI/MCP and source-package smokes passed; package privacy checks
  passed. The portable tree has 9,783 files (586,737,019 bytes).
- These package bytes are verified by the local manifest, but the working
  tree remains uncommitted and has no matching hosted CI/artifact round trip.

## 2026-10-14 - WeChat Cache, Export, Sending, and Notification Follow-up

- WeChat media lookup accepts WeFlow's original, mid, and thumbnail suffix
  variants and uses `<id>_raw.mp4` when `<id>.mp4` is absent. Synthetic media
  tests passed without reading a real account cache.
- ChatLab preserves group type in JSON/JSONL and rejects mixed group/private
  render or auto-export scopes before a destination file is published. The
  auto-export path obtains distinct conversation kinds with a bounded SQL
  snapshot and keeps message output paginated.
- A send adapter receipt only marks delivery `sent` when it returns a bounded,
  nonblank string message ID; malformed receipts resolve to `unknown` and are
  not automatically retried. Focused send service tests passed.
- Message-event SSE now sends a body-free checkpoint on connect and after
  filtered batches, preserving the initial cursor across disconnects. Focused
  SSE regressions passed; event payloads remain conversation metadata only.
- MCP initialization documents personal WeChat request preparation while
  retaining local human approval for all delivery. Focused MCP checks passed.
- No local Hub port was listening during the user's WeChat request. The exact
  test target and text were not queued or sent, and are intentionally omitted
  from this public log. Live request preparation still requires the local Hub
  and enabled, ready WeChat sender; delivery still requires explicit in-UI approval.

## 2026-10-14 - P14 Current-Worktree Package 009

- Rebuilt the Windows portable package and sanitized source bundle from the
  current worktree. Isolated Hub/UI/MCP startup, standalone MCP stdio, source
  bundle smoke, and user-path privacy checks passed.
- The portable package contains 9,783 files (586,786,811 bytes); the 690-file
  source ZIP is 25,104,236 bytes. Local manifest verification matched both
  package byte inventories and base commit `75fa73185b5b1acf960da35ef331901c3284deef`.
  Manifest SHA-256: `423DDC3D91EE319A6E3618DE01BB2859A4753B4C260431EF7D35DC63098034A5`.
- This is local-worktree provenance; hosted CI/artifact round-trip remains
  outstanding for these uncommitted changes.

## 2026-10-15 - P9 Indexed QQ Send Target Guard

- QQ send preparation now resolves every target, including numeric UINs and
  group codes, through the exact account-scoped local conversation index and
  rejects private/group type mismatches before creating the approval record.
- `py -3 -m unittest hub.test_server -k send` passed 19 focused HTTP/send
  tests with synthetic records. No sender adapter delivered a message.
- MCP QQ preparation now uses the same account-scoped resolver as HTTP. It
  rejects unindexed, wrong-account and wrong-kind numeric targets before queue
  creation, keeps indexed UID-to-UIN conversion, and hides the prepare tool if
  the resolver is unavailable. Three focused MCP send tests passed; no delivery
  adapter ran.
- Full `scripts/check-local-hub.ps1` then passed with 811 Python tests, four
  source-mutation gate checks, 32 WebUI checks, package privacy/determinism and
  isolated source smoke. The 25,000-message benchmark measured 1.542 s ingest,
  0.034 s first search, 1.565 s for 50 pages, 0.120 s backup and 0.299 s
  restore, with zero WAL failures.
- P11 audit confirmed that full resource-library/decryption parity and live
  cache coverage remain open. WeFlow image suffix variants with separate
  priority semantics are not mapped until their meaning is verified.

## 2026-10-15 - P8-P14 Local Gate Follow-up

- The 810-test local gate found one assertion issue in the SSE privacy test:
  it could select a checkpoint payload instead of the message event payload.
  The test now reads data from the event block it matched. Both focused SSE
  regressions pass; the full gate has not been rerun after this test-only fix.
- `git diff --check` passed. Public docs contain no exact local send target or
  test text. P14 hosted CI/artifact round-trip and real-client acceptance stay
  open.

## 2026-10-15 - P14 Current-Worktree Package 010

- Rebuilt the Windows portable package and sanitized source bundle after the
  indexed QQ target guard and public-document privacy correction. Isolated
  Hub/UI/MCP startup, standalone MCP stdio, source extraction smoke, user-path
  privacy scan, and exact-target archive scan passed.
- Portable package: 9,783 files / 586,784,821 bytes. Sanitized source ZIP:
  690 files / 25,104,868 bytes. The local-worktree manifest verified both
  inventories against base commit `75fa73185b5b1acf960da35ef331901c3284deef`;
  SHA-256: `B08553F2FD41F99E185387D2CD7CA00FCE554134672F8A15770E6B60611E2A74`.
- These changes remain uncommitted, so hosted CI and uploaded/downloaded
  artifact verification for the current source state remain outstanding.

## 2026-10-15 - P14 Privacy-Refreshed Package 012

- Removed machine-specific cache sample counts, dates, window measurements and
  operator anecdotes from the vendored WeChat reader comments while preserving
  its decryption logic and calibrated click coordinates. Roadmap notes now use
  generic local acceptance wording and omit the test request details.
- Rebuilt the portable package with the hash-verified QCE server and rebuilt the
  source bundle. Isolated executable/UI startup, all three source entries,
  read-only MCP, standalone MCP initialize/tools/list/exit, and package privacy
  checks passed.
- Portable package: 9,783 files. Source ZIP: 690 entries. Local-worktree manifest
  verification passed for both package
  inventories and hashes against base commit
  `75fa73185b5b1acf960da35ef331901c3284deef`.
- At this checkpoint the Hub was stopped, so the test request was not queued.
  The later startup and disabled-capability state are recorded below; no message
  was sent.

## 2026-10-15 - Local Hub Startup and Send-Gate Check

- Started the Hub with the repository launcher on `127.0.0.1:8765`; the UI and
  source-status API returned successfully, and all three source entries
  reported connected without errors.
- `wechat_send` remains disabled in local settings. No approval request was
  created and no message was delivered. Public documentation omits the exact
  target and test text.
- P9/P13 real-delivery acceptance, P11 complete media parity, P12 native and
  desktop-notification acceptance, and P14 hosted verification for the current
  uncommitted worktree remain open.

## 2026-10-15 - P11 Unknown WeChat Image Variant Tier

- A review against the pinned WeFlow active image selector found that `_b/.b`,
  `_w/.w`, and `_c/.c` are not classified there as a thumbnail quality tier.
  Removed those suffixes from the Hub thumbnail resolver so `best` and explicit
  `thumb` do not silently treat an unknown cache variant as a thumbnail.
- Updated synthetic coverage for the known `_t/.t/_thumb` tier and all six
  unknown B/W/C suffix forms. Focused verification is recorded below.
- The WeChat send path now refuses duplicate indexed display names and the
  final approval warning names the unsent-draft clearing behavior. Focused
  sender/provider/WebUI checks passed; the full local gate passed before these
  narrow corrections.
- Post-change verification passed one focused media-resolver test, 45 WeChat
  sender/provider tests, the WebUI send-review contract, and `git diff --check`.
- The current-worktree portable package (9,783 files) and sanitized source ZIP
  (690 entries) were rebuilt. Isolated Hub/UI/MCP startup, standalone MCP
  protocol, package privacy scans, and `local-worktree` manifest verification
  passed for both outputs; hosted verification of this uncommitted worktree
  remains open.
- Broader WeFlow resource/decryption parity and real-cache acceptance remain
  open.

## 2026-10-15 - ChatLab Kind Validation and Package Refresh

- ChatLab exports now fail closed when a message has an unknown, null, or empty
  conversation kind. The store retains null kinds from orphaned message rows,
  so automated export cannot mistake a non-empty dataset for an empty one.
  Truly empty ChatLab exports keep their established private default.
- The WeChat sender now checks indexed display-name uniqueness even when a
  resolved display name equals the target conversation ID; synthetic coverage
  passes for this case and existing ambiguous-name cases.
- Corrected the roadmap's P11 B/W/C suffix description and clarified that
  disabling a send capability revokes unused approval challenges but does not
  erase queued requests or cancel delivery already authorized by the user.
- Focused ChatLab and WeChat sender tests passed. The earlier local gate
  reported 813 passing Python tests and four passing source-mutation checks;
  its final combined-script exit was not captured, so this log does not claim a
  complete gate pass for that run.
- A fresh Windows portable package and sanitized source archive passed isolated
  startup, MCP, privacy, and local-worktree manifest checks. Real-platform
  delivery, complete P11 media parity, P12 desktop/native acceptance, and
  hosted verification of the current uncommitted tree remain open.

## 2026-10-15 - Live Queue Snapshot and ChatLab Regression

- Confirmed the existing Hub listener returned HTTP 200. A read-only aggregate
  query was performed; no approval was consumed and no message was sent. The
  process predates the current source edits, so this is not live verification
  of the latest code. Queue state is omitted from public project documentation.
- Re-ran five focused ChatLab export regressions for orphan/null kinds,
  group preservation, mixed/unsupported kinds, empty export defaults and
  compact QQ attachment aliases; all five passed.
- Removed accidental patch-marker characters from the roadmap. The exact
  target, account, message text, queue counts and request ID are omitted from
  public docs.
- Rebuilt both Windows packages from the current worktree: the portable bundle
  contains 9,783 files and the sanitized source archive contains 690 entries.
  Isolated Hub/UI/MCP startup, standalone MCP initialize/tools/list/exit,
  privacy scans and local manifest byte/provenance verification passed.
- Hosted CI and artifact upload/download verification for these uncommitted
  changes remain outstanding.
- The newest hosted run failed in its local-check step: ChatLab memory and
  automatic-export fixtures omitted conversation kinds after the exporter
  began rejecting null/unknown kinds. Updated the synthetic fixtures to use an
  explicit private kind; all four affected focused tests pass locally, while
  orphan/null-kind rejection remains covered. Hosted rerun and artifact
  upload/download verification remain outstanding.

## 2026-10-15 - P14 Hosted Rerun

- Hosted run `38621260116` passed on commit
  `c2c755024f8114ca56a84edf93fd1194d376b5f4`: full Windows checks, portable
  and sanitized source package builds, package smoke tests, provenance
  manifest verification, artifact upload/download, and downloaded-byte checks.
- The release artifact is 251,776,531 bytes and retained through 2026-10-21.
- P14-01 is complete. Live P9/P13 delivery, P11 real-cache parity, and P12
  desktop/native acceptance remain open.

## 2026-10-15 - Isolated Notification and Local Media Probes

- A temporary Hub with synthetic-only storage and a headed Edge session
  exercised the real browser Notification API after a body-free SSE event. The
  notification used the fixed title/body, omitted a sentinel message string,
  and had a click handler. The automation overrode `document.hidden`, so this
  is not evidence of a visibly presented or clicked Windows toast; P12 remains
  open for that native acceptance.
- A bounded read-only local WeChat image resolver request returned HTTP 500.
  The source status was connected, but its image key was not configured and one
  database was unavailable. The failing request's exact cause remains unknown;
  no account identifier or message content was recorded, and P11 remains open.
- Added a focused unavailable-media result for the decoder's explicit missing
  image AES key error, preserving its user-readable status through the Hub.
  The provider and Hub regression tests pass. The earlier live 500 has not been
  retried against the updated process, so its cause remains unconfirmed.
- Commit `55a6e083f59f0b572bf9cb60fc795501dc9babcc` passed hosted run
  `38628212261` in 16m05s: the full Windows gate, portable and sanitized source
  package builds/smokes, manifest verification, artifact upload/download, and
  downloaded-byte/provenance checks. Artifact `LocalMessageHub-windows-release`
  is 251,779,840 bytes with digest
  `sha256:800093022296894f91cfcff6dacba7866b59fb468e78ac1395ef1d9c0d0e5ee0`,
  retained through 2026-10-21. P14-01 is complete for this revision.

## 2026-10-15 - WeChat Media Locator Failure

- A bounded, read-only check found an indexed WeChat image without the source
  `local_id` and database shard required by the cache reader. The media list
  worked, but resolving that record returned HTTP 500.
- The shared resolver now returns `local_media_identity_unavailable` before
  invoking the provider. One focused HTTP regression passed, and
  `git diff --check` passed. No message body or local account identifier was
  recorded, and no source database was modified.
- The running 8765 server predates this code change, so the live request has
  not yet been repeated against the fix. A read-only WeChat resync restored the
  source locator fields in the Hub index; a bounded image request after resync still returned
  HTTP 500 from the old process. Current-source tests cover the explicit missing
  key status, but the running process has not loaded that code and the runtime
  exception remains unconfirmed. No source database was modified. P11 real-cache
  acceptance remains open.
- Hosted run `38630724422` on documentation-only commit
  `c996b1332c501a59e14e44ea06404b7a68fad56c` succeeded and produced a
  251,779,476-byte artifact. It contains the prior code revision, not this
  local media-locator fix.
- `scripts/check-local-hub.ps1` passed with 819 Python tests, four source-client
  mutation-gate checks, the configured WebUI/JS suites and the 25,000-message
  performance benchmark. The benchmark reported 0.029-second first search,
  1.442-second pagination recheck, 0.088-second backup and 0.253-second restore.
- Rebuilt the current worktree portable package (9,783 files, 586,807,254 bytes)
  and sanitized source bundle (690 files, 25,108,576 bytes). Isolated Hub/UI/
  three-source/MCP startup, standalone MCP stdio smoke, package privacy scan,
  and local-worktree manifest creation and verification passed. The manifest
  binds the packages to base commit `c996b1332c501a59e14e44ea06404b7a68fad56c`
  with build context `local-worktree`; hosted verification and uploaded/downloaded
  artifact checks for the current source change remain open.

## 2026-10-15 - Release Gate and WeCom Timeout Bound

- Hosted run `38636598608` exposed one Windows-only failure in the WeCom bot
  transport timeout test. The captured remaining timeout exceeded its configured
  300-second media limit at a monotonic-clock boundary. The shared send path now
  clamps the socket timeout to that configured ceiling, with a deterministic
  regression check. No delivery behavior or source-client state was changed.
- `scripts/check-local-hub.ps1` passed after the fix: 819 Python tests, four
  source-client mutation-gate checks, configured WebUI/JS suites, privacy and
  package checks, and the 25,000-message performance benchmark. First search was
  0.033 seconds, pagination recheck 1.467 seconds, backup 0.086 seconds, and
  restore 0.251 seconds.
- Rebuilt the Windows portable package (9,783 files, 586,811,450 bytes) and
  sanitized source bundle (690 entries, 25,108,689 bytes). Isolated Hub/UI/
  source/MCP startup and privacy checks passed; local-worktree manifest creation
  and byte verification passed for commit `a2a1aae365cb31aaee9a7de406d9af1723efd4ea`.
- Hosted run `38638279271` on that commit passed the full Windows gate, portable
  and source package builds/smokes, artifact upload/download, and downloaded-byte
  and provenance verification. Artifact `LocalMessageHub-windows-release` is
  251,780,323 bytes with digest
  `sha256:85e3bd31d62b8745b6ea27dd822f2520db95306093d572aef4c35bccb947ca6c`,
  retained through 2026-10-21. P14-01 is complete for this source tree.

## 2026-10-15 - WeChat DAT Candidate Fallback and Latest Hosted Evidence

- The WeChat local image resolver now tries `_b/.b/_w/.w/_c/.c` DAT files as
  ordered decode candidates only for `best`, after all known original, mid and
  thumbnail tiers are absent. These variants are not labeled as a quality tier
  and cannot satisfy explicit original/full/thumbnail selection. Account-root
  containment, symlink rejection and content signature validation remain in
  the existing resolver/decrypt path.
- One focused synthetic regression verifies candidate order, invalid-candidate
  fallback, missing-AES-key continuation and selection boundaries. Unexpected
  runtime errors still surface. No real WeChat cache or database was read;
  real-cache acceptance and broader WeFlow parity remain open.
- GitHub run `38641017039` completed successfully for public commit
  `5c184cf2d9b29e217670d9f5ad6b5bacf96c503d`; artifact
  `LocalMessageHub-windows-release` (`11930632619`) is 251,779,370 bytes with
  SHA-256 `da0608ad3d8711fe73c9b9297e30896635386c39694d12f3943c5987b8092876`,
  retained through 2026-10-21. This run predates the local DAT-candidate change,
  so P14 is `IN_PROGRESS` for the current worktree until that change passes a
  hosted Windows gate. Mirrored `main` run `38643215399` also completed
  successfully on the same commit.
- The live `/api/sources` status check reports WeChat, WeCom and QQ connected
  without source errors. No account identifiers or message counts were recorded.

## 2026-10-15 - Stream Manual Message Exports

- Manual `/api/export` now pages rows into a temporary SQLite spool, retains
  timestamp and stable tie ordering, and feeds the existing streaming writer.
  This removes the full in-memory row list and full response byte buffer from
  the manual export path. ChatLab mixed conversation-type rejection is retained.
- Three focused endpoint checks and `git diff --check` passed. No real message
  data was accessed for the regression checks. The temporary spool consumes
  disk space proportional to the export size.
- Windows run `38645889678` targets the earlier commit `e1155df2d91523f9e061c6609125402c444e813b`,
  before this patch; it cannot serve as hosted acceptance for this change.
- Windows run `38646851948` on commit `4add500a5255e2e66c462ac10d25742bcf0a9bd3`
  passed all 24 workflow steps, including package build/smokes, upload/download,
  and artifact byte/provenance verification. Artifact `LocalMessageHub-windows-release`
  (`11932494397`) is 251,793,400 bytes, SHA-256
  `d7235471bf806931f4fe5dca5f3cf04f239cf36915bf35ae5801e55353602cdb`, retained
  through 2026-10-21. The documentation status update itself follows that run.
- Documentation commit `a50e67c67f14685e277f8e01bcbd924adb5f49f8` then passed
  full Windows CI and artifact round trips on `main` (run `38648975832`, artifact
  `11934851538`, 251,793,008 bytes, SHA-256
  `85392dfb5d03d7a28b6c5662d554465242320cb033f9032b38d80c120b30d8ea`) and the
  roadmap branch (run `38648954782`, artifact `11933702889`, 251,795,340 bytes,
  SHA-256 `53a5e8f07325c4d3d055931570c3cb2f25570642f85d89c24a14d9b3d409fe57`).
  Both artifacts expire 2026-10-21. A final correction to this record needs one
  fresh source package run to keep the published bundle's docs in sync.

## 2026-10-15 - P8-P14 Current Hosted Gate and Public History Cleanup

- Rewrote the public `main` and roadmap branch histories to remove an old
  machine-specific disk-throughput measurement from a source comment. Both
  branches now point to the same sanitized source revision; the commit uses the
  repository's generic noreply identity. No tags point to the old history.
- Fixed QQ media search so each attachment's `name`, `n`, and `file_name` fields
  are searched independently, including when display name and file name differ.
  Clarified that WeChat readiness confirms the client/account only; the target
  conversation must also be open and uniquely identifiable before delivery.
- The focused QQ media search test, WebUI send-history render check, and
  whitespace check passed. The full hosted Windows workflow also passed on
  both branches, including Hub checks, portable/source package builds and
  smokes, provenance manifests, artifact upload/download, and downloaded-byte
  verification. Artifacts `11936560661` and `11936965667` expire 2026-10-21.
- The running local Hub reports WeChat, WeCom, and QQ connected, but its process
  predates the current source revision. No real message was delivered. P9/P13
  real-account delivery, P11 complete real-cache/media parity, P12 visible
  desktop notification acceptance, and P12-03/04 inspectable native
  implementation and isolated mutation/rollback acceptance remain open.

## 2026-10-15 - Opaque Desktop Notification Tags

- Browser notifications now receive an opaque random tag keyed to the exact
  platform/account/conversation tuple in page memory. Repeated messages from a
  conversation retain OS notification grouping, while account and conversation
  IDs remain available only to the in-page click handler.
- `node webui/test-notification-click-target.cjs` and `git diff --check` pass.
  The check verifies tag opacity, reuse for the same conversation, click
  routing, and body-free notification content. Real desktop delivery remains
  unverified.
- The current source commit `f5ffedcca788c2bc7e0256b75b19d5b7264475b1` has
  Windows runs `38658744084` (`main`) and `38658743704` (roadmap branch) in
  progress. They predate this local change and do not validate it.

## 2026-10-15 - Revoke WeCom Bot Credentials While Sending Is Off

- Added a loopback-only, confirmation-bound credential removal endpoint that
  remains available when `wecom_bot_send` is disabled. Reads, saves, and every
  sending route remain capability-gated; the existing account-lock and origin
  checks still run first. Removal invalidates only WeCom approval challenges.
- Settings exposes the removal action without revealing whether credentials
  are configured. The focused server send/approval route test, WebUI settings
  test, and `git diff --check` pass. No credentials or deliveries were used.
- The remote Windows runs for commit `247970d4433939c7a12daccd03421afe12175dde`
  predate this local change and do not validate it.

## 2026-10-15 - P14 Verification for Credential Revocation Commit

- Commit `30250bf3edd9273f7b21c1e734ba2313c3cce18b` passed the full hosted
  Windows workflow on both branches: run `38661876945` (`main`) and run
  `38661876852` (`codex/roadmap-p8-p14-20261014`). Both completed Hub checks,
  portable/source package builds and smokes, manifest verification, artifact
  upload/download, and downloaded-byte/provenance verification.
- Artifacts `11939054856` (251,813,951 bytes, SHA-256
  `bba03131297e5931a3f30debe8009d736904b7a56cbfe25a6b979d886532f98f`) and
  `11939950904` (251,813,602 bytes, SHA-256
  `a8abfce4f7014c3467223a977b1e9917a4fde8677e9e1fd93873bed6d70533a0`)
  are retained through 2026-10-22.
- P14 is verified for this application source commit. Real P9/P13 delivery,
  P11 full cache/resource parity, P12 live desktop notifications, and P12-03/04
  inspectable native source and isolated mutation acceptance remain open.

## 2026-10-15 - P12 Desktop Notification Probe

- An isolated Hub and Edge profile used only synthetic records; the browser
  permission was granted and the notification capability was enabled.
- Minimizing the automated Edge window still reported the app page as visible,
  so no event was injected and no Windows toast or operating-system click is
  claimed. P12-01 remains open for genuine background-window acceptance.

## 2026-10-15 - WeChat Cached Thumbnail Variant

- The WeChat image resolver now recognizes `.thumb.dat` as a thumbnail, in
  line with the repository's pinned WeFlow `ImageDecryptService` tier mapping.
  It remains a read-only candidate under the existing account-root and symlink
  checks. Unknown `_b/.b/_w/.w/_c/.c` variants remain unclassified.
- The existing synthetic resolver test now checks this suffix, explicit
  thumbnail selection, and `best` fallback. Real-account cache acceptance
  remains open; hosted package checks on the subsequent source revision are
  required for P14.

## 2026-10-15 - P14 Documentation Commit Verification

- Documentation commit `49a2fc6791fd52ea40ebac493d20c6206b43ba6b` passed the
  full hosted Windows gate on `main` (run `38663589850`) and the roadmap branch
  (run `38663612455`). Both workflows report success and show the portable
  release artifact digest after upload and downloaded-byte verification.
- Artifact SHA-256 values are `9570968be452e1844d59e8a023319426d4f7e09493e652b884d8c872e7507fd1`
  (`main`) and `3e2410bbce8b4becba84deb1f0b759e3715e40896e15afdfca5c1931441f80de`
  (roadmap branch). These runs predate the `.thumb.dat` source change above;
  P14 remains in progress for the current worktree.

## 2026-10-15 - P14 Verification for Small Cached Originals

- Application commit `13597f1b0061380d2d383d99d0e18e689e5fd27e` passed the full
  hosted Windows workflow on `main` (run `38666599984`) and the roadmap branch
  (run `38666600073`). Both completed Hub checks, portable/source package builds
  and smokes, provenance checks, artifact upload/download, and downloaded-byte
  verification.
- The uploaded artifact digests matched the downloaded bytes. Artifacts
  `11942167003` (`main`, 251,816,391 bytes, SHA-256
  `95c4cf0de487a851aadf3b9210fb6b704bb9a378f90d59465d6c1f285f1ee51a`) and
  `11942112349` (roadmap branch, 251,814,248 bytes, SHA-256
  `52434498ee70dd6f9b68d7d5cc203cc0094de9d51a6a0dd8aff5b249a943b769`) are
  retained through 2026-10-22.

## 2026-10-15 - P11 Unindexed WeChat Media Gap

- The unified media library initially indexed Hub messages and could not list
  source-database media that has not been synced. The source bulk lookup only
  returns local IDs; completing list, preview, and ZIP export requires a stable
  account/conversation/shard/local-ID identity and consistent deduplication and
  pagination across those paths. No partial API or UI was added.
- The source-media identity gap was implemented later the same day below. Full
  WeFlow media/decryption parity and real-account cache acceptance remain open.

**2026-10-15 P11 small original cache selection:** WeFlow classifies `_h`,
`.h`, `_hd`, and `.hd` files as HD image tiers without a minimum file-size
rule. Hub cached selection now structurally validates decrypted originals
below 1 KiB in status, `original`, `best`, `full`, and pre-download paths;
truncated files remain rejected. The existing download-wait size/stability
gate is unchanged. Focused regression
`integrations.wechat.test_media.MediaTests.test_small_cached_original_requires_complete_structure_for_selection`
passed (1 test); the full WeChat media module passed 61 tests and
`git diff --check` passed. No real account cache was read.

## 2026-10-15 - P11 WeChat Source Media, P12 UI Boundary, and P14 XLSX Gate

- The WeChat media library now pages unindexed source rows for one exact local
  account and conversation. Composite account/conversation/shard/local-ID
  identity flows through previews and ZIP export; indexed rows are deduplicated,
  source message bodies are not read, and file paths remain server-side. The
  listing is bounded to 10,000 raw rows per view. Full WeFlow cache/decryption
  parity and real-account media acceptance remain open.
- P12-03 Settings now retains read-only anti-revoke status refresh/search/list
  and removes inactive install/uninstall controls until the native implementation
  can be inspected. Source-mutation checks passed 5/5. P12-03 remains NOT_STARTED
  for native lifecycle and isolated rollback acceptance.
- Hosted workflow `38668177485` failed one existing XLSX byte-equality test
  because ZIP entry timestamps varied between renders; `main` run
  `38668177268` passed. The Moments XLSX exporter now fixes entry timestamps.
  The focused export module passed 10/10 and `git diff --check` passed. Current
  worktree changes still require both hosted package runs and artifact round-trip
  verification before P14 can be marked complete.
- Focused P11 checks passed: WeChat cursor/provider 6/6, composite resource
  identity 1/1, source media list-preview-ZIP HTTP flow 1/1, and media WebUI
  preview/export coverage. No real media was opened or sent.

## 2026-10-15 - P11 Bounded SILK Reads

- The shared voice decoder now reads at most 16 MiB plus one sentinel byte,
  even if a cached SILK file grows after its initial size check. Oversized input
  is rejected before decoding for WeChat, WeCom, and Hub/QQ callers.
- Focused regressions passed (2 tests), including simulated post-stat growth;
  `git diff --check` passed. No real media cache was accessed. Full resource and
  decryption parity remains open.

## 2026-10-15 - P14 Hosted Acceptance for d61c086

- Commit `d61c08696fcc41bb333343068ae3ed30f0f98a3a` passed both full hosted
  Windows workflows: `38671025839` on `main` and `38671026098` on the roadmap
  branch. Hub checks, portable/source package builds, provenance manifests,
  artifact download, and downloaded-byte/provenance verification all passed.
- The `main` release artifact is 251,849,201 bytes with SHA-256
  `183bac524c1247cd3328bf903d279d7f949d2f66a4901cf0e8845018f2871f4d`; the
  roadmap artifact is 251,847,714 bytes with SHA-256
  `618ead63b359bd92d0938329bd6b3571e6ad1b4ad8446687ded18bfe6daa8d36`.
  Both expire 2026-10-22. This verifies application commit `d61c086`; the
  documentation update is followed by a package run so the source artifact
  contains this acceptance record.

## 2026-10-15 - P11 Source Media Continuation and Sticker Cache

- WeChat source-media pagination now orders NULL, zero, and negative sort
  sequences consistently across shards, rejects malformed cursor types, and
  skips repeating the full row count on continuation pages. This removes the
  former 10,000-row scan ceiling while retaining one 500-row source page at a
  time; deep offsets still scan preceding rows.
- Unindexed type-47/11000 stickers now appear in the WeChat source-media view.
  Preview and ZIP export re-read only the exact account/conversation/shard/local
  row on explicit access, validate its type, parse a bounded emoji MD5, and use
  the existing account-scoped cache resolver. Bodies, hashes, and cache paths
  remain out of API output and export manifests.
- Focused checks passed: WeChat cursor/provider and resource validation (29),
  source-media HTTP list/preview/ZIP flow (1), and WebUI media preview/export
  checks plus JavaScript syntax. Fixtures only; no real cache/database read.
- The user reported seeing no OS notification. The previous Edge probe never
  reached a hidden-page state, so it did not create a Windows toast. P12-01
  remains open for genuine foreground/background and notification-center
  acceptance.
- The complete local gate passed: 835 Python tests, five source-mutation gate
  checks, 32 WebUI checks, package/privacy smoke, and the synthetic 25,000-row
  benchmark. Timings: 1.417 s ingest, 0.030 s first search, 1.437 s for 50
  pages, 0.091 s backup, and 0.235 s restore; zero WAL failures.
- A 9,787-file Windows portable package (587,000,016 bytes) and the 692-file
  sanitized source archive (about 25 MiB) passed isolated startup/MCP/source
  smoke. A `local-worktree` manifest was generated and byte-verified against
  this source tree.
- Hosted verification still covers `d61c086` only; current-source artifact
  upload/download round-trip and hosted Windows run remain open, so P14-01 is
  `IN_PROGRESS`.

## 2026-10-15 - P12 Explicit Test Notification

- Settings now has a user-triggered test notification, enabled only when the
  notifications capability is on. It uses fixed text and no account,
  conversation, or message data, and can be exercised while the page is visible.
- Focused checks passed: notification settings render/availability, permission
  gating and body-free explicit test flow, and `node --check webui/app.js`.
  The full gate then passed 835 Python tests, 33 WebUI checks, five
  source-mutation gates and the 25,000-message benchmark. It does not verify
  Windows notification-center delivery or real foreground/background behavior.
- The earlier Hub launch was blocked by automatic review. No alternate launch
  path was attempted. P12-01 remains `IN_PROGRESS` pending a live OS notification
  check; P14 current-source hosted package verification also remains open.
- The refreshed 9,787-file portable package (587,017,262 bytes) and 692-file
  sanitized source archive passed isolated startup/MCP/source smoke. Their
  `local-worktree` manifest matches package bytes and commit provenance; hosted
  upload/download verification is still required.

## 2026-10-15 - P11 HEIC and TIFF Local Preview

- Local media URLs can now request a JPEG preview for validated HEIC/TIFF cache
  files. Conversion reuses the bundled FFmpeg, allows one conversion at a time,
  caps source/output size and runtime, and never edits the cached original.
- Gallery thumbnails and the full-size viewer use the converted preview while
  retaining an original-download link. Conversion failures leave the original
  available for download. This is a local usability improvement; upstream
  WeFlow parity and real-cache acceptance are not claimed.
- The user confirmed the test notification did not appear on the Windows
  desktop. P12-01 remains unaccepted; no OS delivery is inferred from browser
  API use. Syntax and diff checks pass; the bundled FFmpeg advertises HEVC and
  TIFF decoders, but no real-file conversion or Windows toast was verified.

## 2026-10-15 - P11 WeChat Source Media Keyset Pages

- The WeChat source-media API now wraps its existing SQLite shard cursor in a
  scope-bound page token. Continuations resume after the final returned raw
  media row and reuse the first page's total, so the WebUI no longer re-scans
  every preceding media row or repeats full counts when browsing forward.
- The WebUI uses cursor paging only for unindexed WeChat source media. Legacy
  offset callers remain supported and retain their existing deep-offset cost.
  Internal shard/local-ID cursor metadata is stripped before API output.
- Focused checks passed: source-media HTTP pagination/resource flow, provider
  cursor mapping, and WebUI media preview/paging-mode checks. Synthetic rows
  only; no real WeChat database or cache was read, and the full local gate and
  hosted package round trip were not rerun for this follow-up.
- P11 remains `IN_PROGRESS` for complete WeFlow media/decryption parity and
  real-cache acceptance. The user confirmed that the Windows test notification
  did not appear, so P12-01 also remains unaccepted.

## 2026-10-15 - P14 Current Worktree Package Smoke

- Rebuilt the Windows portable application and sanitized source archive from
  the current worktree. Both package privacy scans passed; the portable Hub,
  read-only MCP endpoint, standalone MCP stdio flow, and extracted source Hub
  passed isolated smoke checks with empty temporary storage.
- The local manifest records `local-worktree` context and verifies package
  inventories and bytes. Its commit field is the base `2860ce8` HEAD; it does
  not claim a hosted run or identify uncommitted edits by commit SHA.
- P14 remains `IN_PROGRESS`: GitHub CLI is unavailable here, so no hosted
  Windows run or uploaded/downloaded artifact round trip was verified. The
  current local gate was not rerun as part of this package build.

## 2026-10-15 - P9/P11 Local Follow-up

- Send history now orders equal-second records by SQLite insertion order before
  applying its 100-item limit. A frozen-clock regression creates 105 synthetic
  requests and verifies the newest 100 deterministically; no adapter is called.
- Cached WeChat, WeCom, and QQ SILK decoding now writes content-addressed WAV
  files under Hub-owned storage. The WeCom regression decodes synthetic SILK
  through the shared decoder and verifies the source cache directory remains
  unchanged; a shared-decoder regression also verifies output placement and
  temporary-file cleanup.
- Three focused tests, Python compilation, and `git diff --check` pass. No real
  account cache was read, no source-client data was modified, and the complete
  local gate was not rerun. P9-02 and P11-01 remain `IN_PROGRESS` for their
  outstanding live-account/media acceptance.
- Package r4 predates these changes. Rebuild the portable/source packages and
  manifest before treating P14 local package smoke as current; hosted
  verification remains separate.
- Rebuilt as package r5 after the changes: the portable output contains 9,787
  files (587,050,524 bytes), and the sanitized source archive is 25,133,738
  bytes. Isolated Hub/MCP/source smokes, privacy scans, and the `local-worktree`
  manifest byte/provenance verification pass for base HEAD `2860ce8`. Hosted
  upload/download round trip is still open.
- Refreshed again as r6 after correcting the QQ caller assertion: 9,787
  portable files (587,050,616 bytes) and a 25,133,866-byte sanitized source
  archive pass isolated package smoke, privacy scans, and manifest
  byte/provenance verification. Hosted CI and artifact round trip remain open.

## 2026-10-15 - P13 WeChat Preparation Identity Binding

- HTTP and MCP preparation now require the requested WeChat account ID to match
  the current logged-in account reported by sender readiness. MCP reuses the
  readiness result within the tool call; mismatches fail before a pending
  request is persisted.
- MCP status lookup remains available while the WeChat client is offline, so a
  pending local request can still be inspected. Preparation remains gated on
  sender readiness.
- Five focused MCP/HTTP preparation, status, and validation tests pass. Python compile
  and `git diff --check` pass. No send adapter was called and no real message
  was sent. P13 remains `IN_PROGRESS` for live delivery acceptance.
- The user confirmed the explicit test notification was not visible on the
  Windows desktop. Browser `Notification` API execution is not OS-level
  delivery proof; P12-01 remains `IN_PROGRESS` pending a working, visible
  Windows notification-center path and acceptance.

## 2026-10-15 - P14 Current Worktree Package Smoke r7

- Built the Windows portable package and sanitized source archive after the
  P11 account-wide WeChat source-media and P13 preparation identity changes.
  Portable contains 9,787 files (587,107,842 bytes); source ZIP is 25,138,225
  bytes. Isolated Hub/MCP/source smoke passed for both package forms.
- Portable privacy scanning and manifest inventory/provenance verification
  passed. Manifest context is `local-worktree`, run label `local-p8p14-r7`, and
  base HEAD `2860ce8ba87d69be1b473e4dfbfe8a39d648ad73`; uncommitted changes are
  not represented by that commit field.
- Hosted Windows CI and uploaded/downloaded artifact round-trip remain open.
  The documentation-only package record was added after r7 was built.

## 2026-10-15 - P11 Account-Wide WeChat Source Media

- The WebUI source-media view now requires a selected WeChat account but allows
  the conversation field to stay empty to browse all visible sessions. An
  exact conversation ID still narrows the existing per-conversation view.
- Account-wide listing merges source media from every session and message
  shard, skips rows already indexed by the Hub, and uses a deterministic
  account/filter-bound cursor. The saved cursor resumes the same conversation
  at its shard-local row and applies a sort-sequence threshold to the other
  sessions. Session enumeration explicitly rejects accounts above 100,000
  visible sessions instead of silently truncating them.
- Three focused Python regressions cover equal sort-sequence shard ordering,
  105 synthetic sessions over multiple cursor pages, indexed-row dedup,
  cursor scope rejection, exact preview identity, and source database hashes.
  One focused WebUI regression verifies account-wide mode is available without
  a conversation ID. The existing exact-identity preview/ZIP flow also passes.
- No real account data was read, no source database was modified, and no Hub was
  launched. P11 remains `IN_PROGRESS` for real-cache completeness and full
  WeFlow media/decryption parity. No broad test suite or package build was run.

## 2026-10-15 - P12 Windows System Notification Delivery

- Added a Windows-only, inbox PowerShell/WinForms notification-area balloon
  path for the fixed test notice and body-free incoming-message notices. The
  browser no longer needs notification permission; incoming notices still
  honor platform, allowlist, and mute filters.
- The message endpoint accepts only a recent event ID issued by the local SSE
  stream, rechecks the current message and filters, expires queued IDs, and
  suppresses duplicate delivery across tabs. Notifications contain only a
  platform label and generic message indicator, never a title or message body.
- Focused validation passed: one server regression, two WebUI notification
  checks, Python/JavaScript syntax checks, and `git diff --check`. A live local
  PowerShell notification command exited successfully; user-visible delivery
  is still awaiting confirmation.
- The native balloon does not support clicking through to the selected chat,
  and real hidden-window delivery remains unverified. The running Hub on 8765
  was left untouched; P12-01 remains `IN_PROGRESS`.
- The integrated fixed-content test endpoint returned `shown: true` through
  the isolated Hub at `http://127.0.0.1:8773/`, which uses empty temporary
  storage. Its Settings page is open for the user to confirm shell visibility.

## 2026-10-15 - P14 Windows Package r8

- Rebuilt the Windows portable package with the native notification script and
  the latest Hub/WebUI sources: 9,791 files, 587,097,430 bytes.
- Built the sanitized source archive (694 files, 25,136,806 bytes). Source and
  portable isolated startup checks, privacy scanning, and manifest byte and
  provenance verification passed.
- Manifest: `output/windows/LocalMessageHub-artifact-manifest-p8p14-20261015-r8.json`,
  marked `local-worktree` for HEAD `2860ce8ba87d69be1b473e4dfbfe8a39d648ad73`.
  Hosted CI and hosted artifact download round-trip remain open. The package
  record and final server-test assertion were added after archive creation, so
  the archives omit those non-runtime updates.

## 2026-10-15 - P14 Windows Package r9

- Rebuilt the portable package after making the notification script ASCII-only
  and passing its Chinese title as a Unicode process argument, for consistent
  Windows PowerShell 5.1 decoding. Portable package: 9,791 files and
  587,087,366 bytes. Sanitized source archive: 694 files and 25,137,093 bytes.
- Portable/source startup smoke, privacy scan, and manifest byte/provenance
  verification passed. Manifest:
  `output/windows/LocalMessageHub-artifact-manifest-p8p14-20261015-r9.json`,
  marked `local-worktree` at `2860ce8ba87d69be1b473e4dfbfe8a39d648ad73`.
- Hosted artifact verification remains outstanding.

## 2026-10-15 - P12 Windows Toast Follow-Up and P14 Package r10

- The user confirmed that the earlier test was not visible. Inspection found
  the old WinForms notification-area balloon path and that the live 8765
  notification capability was disabled; the earlier successful POST used an
  isolated Hub instance. Replaced the balloon with a Windows Runtime Toast
  submitted through the registered Windows PowerShell app identity.
- API responses now say `submitted` after Windows accepts a Toast request; they
  do not claim the shell showed it. The fixed-content test passed both directly
  and through 8765 while the capability was temporarily enabled. The setting
  was restored to off. The user has not yet confirmed the new Toast appeared;
  Windows attributes it to Windows PowerShell, and click-through is unsupported.
- A read-only all-platform sync after the 8765 restart completed with WeChat,
  WeCom, and QQ reporting connected without errors. Original client databases
  were not modified.
- The focused notification HTTP regression passed. The r10 sanitized source
  archive passed privacy scanning and isolated startup; the portable package
  passed executable UI, three-source, MCP, and privacy smoke checks. The r10
  manifest passed byte and provenance verification.
- Portable: 9,791 files, 587,100,485 bytes. Source ZIP: 694 files,
  25,096,458 bytes. Manifest:
  `output/windows/LocalMessageHub-artifact-manifest-p8p14-20261015-r10.json`.
  Hosted artifact upload/download verification remains open; P14 stays
  `IN_PROGRESS` and P12-01 remains `IN_PROGRESS` pending user-visible Toast
  confirmation.

## 2026-10-15 - P14 Source Documentation Refresh r11

- Rebuilt the sanitized source archive after recording the P12 Toast result.
  It contains 694 files and 25,096,860 bytes and passed privacy and isolated
  startup checks.
- Manifest `output/windows/LocalMessageHub-artifact-manifest-p8p14-20261015-r11.json`
  pairs source archive r11 with portable package r10. Manifest byte and
  provenance verification passed; hosted artifact upload/download remains
  open.

## 2026-10-15 - P11 QQ Keyset Sync Pagination

- QQ local snapshot sync now pages by `(timestamp, rowid)` instead of issuing
  increasingly deep `OFFSET` queries. The order remains the existing newest
  first order, tied timestamps are deterministic, and NULL timestamps remain
  reachable. Interrupted scans persist the keyset cursor across Hub restart;
  completed scans still replay the full snapshot to reconcile older edits.
- Focused regressions passed for tied/NULL timestamps and restart recovery.
  A live read-only QQ sync on 8765 completed without errors and preserved the
  indexed message total. Source QQ databases remain read-only.
- This reduces page-skip work but still decrypts a fresh local snapshot and
  scans it on a completed sync. Complete QQ cache/media parity remains open.

## 2026-10-15 - P12 Windows Toast Identity

- After the user reported that no Toast appeared, checked the desktop
  notification contract and found the Hub was sending under the Windows
  PowerShell identity. It now creates a per-user Start menu shortcut carrying
  its own AppUserModelID and submits Toasts under that identity.
- Registration-only verification succeeded; Windows enumerates
  `LocalMessageHub.DesktopNotifications`. No Toast was sent as part of this
  fix. The UI now says a test was submitted and asks the user to check whether
  it appeared; P12-01 remains open until visible delivery is confirmed.
- Compared Windows notification settings for the new Hub AUMID and the
  existing PowerShell identity: the former is unknown before first use; the
  latter is explicitly enabled. The sender now blocks explicit disabled
  states only, without changing Windows notification permissions.
- Rebuilt the portable and sanitized source packages as r13. Portable UI,
  source-list, MCP, and privacy smoke; source ZIP isolated startup/privacy
  scans; and manifest package-byte/provenance verification passed. Manifest
  `output/windows/LocalMessageHub-artifact-manifest-p8p14-20261015-r13.json`
  records local-worktree provenance at base HEAD `2860ce8`; hosted artifact
  round-trip verification remains open.
- Rebuilt package r14 with the notification-state handling. Portable/source
  isolated startup, UI/source/MCP smoke, privacy scans, and manifest
  byte/provenance verification passed. Hosted artifact transfer and actual
  desktop display remain unverified.

## 2026-10-15 - P11 WeChat Source Media Runtime Follow-Up

- The user's desktop did not show the synthetic notification. The live Hub
  notification capability is currently off, and Windows has no per-app
  settings record yet for the Hub identity; no setting was changed and no
  additional notification was sent.
- A read-only media request exposed a `WeChatProvider`/`LocalReader` method
  ownership error in source-media sender-name lookup. The call now uses the
  reader that owns the method, and its existing focused provider tests cover
  that boundary.
- The account-wide source-media total also differed from the exact rows left
  after identity filtering when indexed rows were stale. Completed session
  scans now reconcile the indexed count estimate with the exact unindexed
  rows observed; early-stopped sessions still use the inexpensive count
  estimate. The account-wide pagination regression exercises a stale count.
- Three focused regressions passed. A read-only SQLite snapshot of the real
  local index and WeChat source returned one first-page sticker, total four,
  and a continuation cursor without reading message bodies. The current
  running 8765 process loaded code before this fix, so the WebUI route still
  needs a Hub restart to use it. The approved stop of PID 41824 was rejected
  by tool policy (`blocked by policy`); no process was stopped.
- Full real-account media/cache parity remains open. P11-01 stays
  `IN_PROGRESS`.

## 2026-10-15 - P12 Toast Acceptance Clarification

- The user clarified that they do not recall whether the latest synthetic
  Toast appeared, but have seen notifications before. This leaves that test
  unconfirmed; it does not establish that notifications never appeared.
- The live notification capability remains off. No setting changed and no
  additional notification was sent. P12-01 remains `IN_PROGRESS` pending a
  confirmed current Toast and the remaining desktop/background acceptance.

## 2026-10-15 - P11 WeChat Source Media MCP Alignment

- Added cursor-paged `list_wechat_source_media` and exact-identity
  `get_wechat_source_media` MCP tools. Both require an explicit WeChat account
  and conversation; reads additionally require shard, decimal local row ID, and
  media type. MCP listings omit body/path/URL/bytes and preserve the opaque
  scope-bound cursor from the existing Hub source-media service.
- Exact reads recheck the logged-in local account and source row's actual
  attachment type, then reuse `Hub.read_cached_image` and the existing
  `resolve_media` path. The same local-cache root checks, 8 MiB read bound, and
  content-signature checks apply; source databases are not modified.
- Focused synthetic checks passed: MCP page/scope/metadata and image response,
  plus Hub source identity and local PNG cache/signature resolution (2 tests).
  No real chat content/cache was read and Hub was not started. Real-account
  attachment-byte acceptance and full P11 WeFlow media parity remain open.

## 2026-10-15 - P14 Hosted Windows Acceptance

- Commit `030ca901c2847f56ff020dfb7c687d04c9515d38` passed full hosted Windows
  workflows on `main` (`38748409211`) and
  `codex/roadmap-p8-p14-20261014` (`38748409701`). Both runs passed 846 local
  checks, portable/source package builds and smoke, privacy and manifest checks,
  artifact upload/download, and downloaded byte/provenance verification.
- `main` artifact `11981321199`: 251,955,218 bytes,
  SHA-256 `ae5e72062c5ffafd79197649912dfb8333221ae39b14594c40672277bd9e14a5`.
  Roadmap artifact `11981770568`: 251,950,610 bytes,
  SHA-256 `eaa6ff2e4d0e1aa5a6156bac4b529bca77532da4810a5751e7a2282c134ee880`.
  Both are retained through 2026-10-22. P14-01 is complete for this revision;
  real-device delivery, current Toast display, and real-cache parity remain open.

## 2026-10-15 - P9/P13 Isolated Send Acceptance Recheck

- 110 focused QQ sender/target, personal WeChat sender, WeCom bot
  sender/media, and shared durable-send tests passed. Nine HTTP route tests
  passed for default-off capabilities, request-bound approval, expiry,
  revocation, rejection, local QQ target resolution, and WeCom bot identity.
- A read-only process/listener inventory found WeChat processes but no
  NapCat/QQ send runtime. No client endpoint was contacted; no message was
  prepared or sent.
- No implementation gap was established by this review. Real NapCat delivery,
  WeChat UI composition and read-back receipt, and WeCom bot/device delivery
  remain open; P9-02 and P13-01 stay `IN_PROGRESS`.

## 2026-10-15 - P11 Real Indexed Media Probe

- The running local Hub reported all three sources connected, with indexed
  records and no source errors. The account-wide unindexed WeChat source-media
  page returned no candidates in this snapshot.
- The existing media API resolved one real indexed WeChat image to JPEG and
  one voice to WAV. Both local responses returned HTTP 200, non-empty content,
  and matching file signatures. The first bounded candidate for WeChat
  video/file and WeCom/QQ images did not resolve.
- No message body, account identifier, path, or media bytes were recorded.
  The source clients were not modified; this was a resolver read, which may
  populate Hub-owned media cache. Only image/voice cache reads are verified;
  complete cache coverage and WeFlow parity remain open. P11-01 stays
  `IN_PROGRESS`.

## 2026-10-15 - P11-02 Session Export Media Bundle

- Added an opt-in session export bundle: the existing selected message export
  format is placed in a cancellable ZIP beside media identities collected from
  the same cursor-paged export rows. The existing bounded resource resolver,
  media capability, 100-item limit, per-file/aggregate byte limits, and atomic
  archive publication are reused. QQ resolution uses only Hub-cached media and
  does not call QCE or download from a source client.
- Unavailable individual media is listed as failed in the identity manifest;
  exceeding the item or byte budget, renderer failure, or cancellation fails
  the whole job and does not expose a partial archive. The manifest contains
  identity mapping and archive-relative names, never local cache paths or
  temporary URLs. The intermediate message export is removed after ZIP
  creation. The export UI keeps its prior format flow unless the user
  selects “附带已解析媒体” and reports that unresolved resources appear in the
  manifest.
- Focused HTTP/resource tests cover selected-format bytes, identity mapping,
  capability rejection, path/URL redaction, missing media, the 100-item hard
  limit, byte-budget failure, and cancellation without a published archive.
  The UI contract covers both opt-in bundle requests and the unchanged ordinary
  export request. Source-only WeChat media, Moments catalogs, the QQ global
  gallery, full resolver parity, and real-cache acceptance remain outside this
  bundle path; P11-02 stays `IN_PROGRESS`.

## 2026-10-15 - P12-01 Local Hub Toast Probe

- After the user's approval, the running 8765 Hub accepted its fixed test
  notification request. Notifications were restored to disabled immediately;
  an empty platform allowlist suppressed message-event notifications during
  the probe.
- API acceptance does not prove Windows displayed the toast. The user still
  needs to confirm notification-center visibility, and the background-window
  path remains unverified; P12-01 stays `IN_PROGRESS`.

## 2026-10-15 - P14 Current Worktree Gate

- `scripts/check-local-hub.ps1` passed on the current modified worktree: 852
  Python tests, five source-mutation gate checks, 32 primary WebUI checks,
  package privacy/smoke/determinism checks, Python/JavaScript syntax checks,
  and `git diff --check`.
- The 25,000-message synthetic benchmark completed with 1.364 s ingest,
  0.029 s first search, 1.851 s for 50 cursor pages, 0.088 s backup, and
  0.241 s restore; WAL reported zero failures.
- Hosted Windows CI and artifact round-trip have not been run for this
  uncommitted worktree. P14-01 hosted evidence remains tied to the commit
  recorded in its status row.
- A post-gate review found the synthetic WeChat voice test writing its decoded
  WAV into the repository runtime directory. The test now redirects that
  output to its temporary runtime; its focused regression passes, the generated
  artifact and task cache were removed, and the full gate was not rerun for
  this test-only isolation fix.

## 2026-10-15 - Latest Hosted P14 Acceptance

- Commit `c424fdd4a1601fa77fb43f698efe7d24aee42341` passed the hosted Windows
  workflow on `main` (`38750963688`) and
  `codex/roadmap-p8-p14-20261014` (`38750980409`), including local checks,
  portable/source builds, privacy and deterministic-manifest checks, artifact
  upload/download, and downloaded-byte/provenance verification.
- The `main` artifact (`11981748188`) is 251,955,476 bytes with SHA-256
  `b3df9e74a670e852e9c0e71c1d8db05fac0a4f07512c9b3d20876a3014ad6ac0`;
  the roadmap artifact (`11982382739`) is 251,955,030 bytes with SHA-256
  `7d3ccb3b5a9e02bf27d5eeb54119bf20e00c48a4d0c5c0f1ac2b35b22348bac2`.
  Both are retained through 2026-10-22.
- These hosted runs do not include the current uncommitted P11-02 bundle work.

## 2026-10-15 - P11-02 Bundle Hosted Acceptance

- Commit `899ba14f42ad70cc5b097c4b42d10a90faa9d35d` passed hosted Windows
  workflows on `main` (`38759795335`) and
  `codex/roadmap-p8-p14-20261014` (`38759795133`). Both completed the full
  local check gate, portable/source builds, privacy and deterministic-manifest
  checks, artifact upload/download, and downloaded-byte/provenance verification.
- The `main` artifact (`11986561145`) is 251,958,893 bytes with SHA-256
  `5f47a8da29f040d1a66e9c5c3198d9750df86eb9ade2b2cb876666e9d3f9c09d`;
  the roadmap artifact (`11986111205`) is 251,957,719 bytes with SHA-256
  `cf72ec9e5ffa0e6b947133b98d4bf8ff3f43c70f9f233d9ea549bbcd448a97b1`.
  Both are retained through 2026-10-22.
- This verifies P14 for the P11-02 bundle code revision. P9/P13 real sends,
  P11 real-cache breadth, P12-01 visible toast delivery, and P12-03/04 native
  mutation remain open.

## 2026-10-16 - P12-01 Fixed Toast Probe

- After the user authorized one body-free test, the live 8765 Hub accepted
  `/api/notifications/test` with `submitted: true`. Notifications were enabled
  only for this request; a follow-up `/api/settings` read confirmed the
  capability was disabled again.
- The user has not confirmed that Windows displayed the toast. This records
  API submission only; P12-01 remains `IN_PROGRESS` pending visible desktop
  acceptance.

## 2026-10-16 - P11 Media Classification and Achievement Counts

- QQ attachment projections now resolve categories from `type`, then `kind`,
  then `k` in both chat-only and unified media queries. Existing aliases and
  attachment order remain intact; explicit `type` wins conflicting fields.
- WeChat sticker raw types 47 and 11000 no longer count toward the private-chat
  image achievement. Legacy NULL raw types still count as images, and WeCom/QQ
  attachment-derived counts are unchanged.
- Five focused synthetic store/analytics regressions passed. Real-cache breadth
  and full WeFlow media parity remain unverified; P11-01 stays `IN_PROGRESS`.
