# QQ integration source

## Current History Mode

The Hub reads local QQNT databases through `local_provider.py`; it does not
launch NapCat, scan a QR code or request online roaming history. Encrypted data
requires a locally verified database key. See [local-reader details](../../docs/qq-local.md).
The QCE/NapCat material below describes the retained optional online adapter,
not the default history reader.

## Sending

The local reader never writes to QQ; sending still uses the retained NapCat
OneBot 11 adapter (`sender.py`), which requires an online NapCat channel and
per-request local approval. Because the reader keys private conversations by
QQNT uid while OneBot accepts only numeric uin/group code, `send_target.py`
resolves the target at prepare time from the reader's own snapshot:

- a 5-20 digit id is used as-is;
- a private uid is mapped through `nt_uid_mapping_table` / `profile_info_v6`;
- a group that is not already a group code, and any unknown uid, is refused.

The resolved number is what is stored, reviewed and delivered, and
`hub.QQAccountBoundSender` re-checks that the online NapCat account matches
the request account. See [local-reader details](../../docs/qq-local.md).

Vendored source: [shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter), release `v6.3.2`, commit `7d154b847a0cb3f9cffbdb126598c4d1434995ba` (annotated tag object `a1c0fcc5bb48af7844bbc1538963b4d0a05c927f`, unsigned). The tag's upstream `LICENSE` is GPL-3.0; keep it with any redistributed upstream source or derived work.

The source archive is `upstream/source-v6.3.2.zip`; extracted upstream tree is `upstream/expanded/qq-chat-exporter-6.3.2/`. Source archive SHA-256: `1CA3141920769B8ADA0AA43629A95465AA2718C694234685C9A3DA85971B647B`.

## Windows shell runtime

The official x64 shell asset is stored and unpacked under `.runtime/qq/v6.3.2/` (runtime is local-only). Asset: `NapCat-QCE-Windows-x64-v6.3.2.zip`, 42,346,596 bytes; downloaded 2026-10-10 06:04:46 UTC. SHA-256: `3FC093BA2332B97AC17E16160D5BAA6FFE0A8DEF6C394D515A94777FE0CA492C`. The upstream release lists Windows x64 and requires a QQ client login; shell mode is not a login-free data source.

The hub launches the bundled `NapCatWinBootMain.exe` directly with injection metadata staged only inside this runtime directory; it does not write into the installed QQ directory. The QCE server binary is locally rebuilt from the vendored source with its bind address restricted to `127.0.0.1`. NapCat WebUI is likewise configured for loopback and its console logging is disabled. `start-standalone.bat` only views prior exports and is not the live data path.

### Rebuilding the loopback QCE server

The Windows portable builder compiles the server from the vendored v6.3.2 source using `Cargo.lock`; it never packages the release's prebuilt server. Build prerequisites are Rust 1.98.1 GNU, MinGW-w64 GCC 16.2.0, and access to the locked Cargo crates on first build. Run `scripts/build-qce-windows.ps1 -OutputPath <path>` directly to build just the server. The script verifies the source still binds to `127.0.0.1`, includes the date-filter capability, checks and prints the GCC version, stages under an ASCII path, and writes a SHA-256 sidecar. The package builder embeds that digest into its staged provider allowlist, so a replaced binary is rejected at runtime. The full package inventory is recorded in its unsigned manifest; that manifest detects accidental changes but is not a signature.

The provider authenticates to the local NapCat WebUI API without placing its credential in a URL or returning it to the hub. `POST /api/QQLogin/CheckLoginStatus` supplies the current phase, and `POST /api/QQLogin/GetQQLoginQrcode` supplies a QR payload only while login is needed. The hub renders `launch()`'s QR locally and never exposes NapCat credentials. Live history requires both an authenticated QQ session and a healthy local QCE bridge; these are checked at runtime and are not inferred from a cached index.

## Hub adapter contract

Talk to the local QCE HTTP service (release defaults to `http://127.0.0.1:40653`) rather than coupling the hub to private NapCat bridge calls:

- `GET /api/system/status`: use `data.online` as the live account/bridge status. `GET /api/system/info` also returns `napcat.selfInfo` when connected.
- `GET /api/groups?page=1&limit=...`: paged groups; `GET /api/friends?page=1&limit=...`: paged friends.
- `POST /api/messages/fetch`: body requires `peer: { chatType, peerUid }`; group `chatType` is `2`. Optional `filter.startTime/endTime` are Unix timestamps. Use `page`/`limit` for pagination and `forceRefresh` to bypass cache. Response contains `data.messages` and paging metadata.
- For downloaded media and durable exports, use `POST /api/messages/export`, then poll `GET /api/tasks/:taskId`; retrieve exported resources through `/api/exports/files/:fileName/resources/*path` or `/api/download-file` as applicable. The underlying bridge uses `FileApi.downloadMedia`; it is not a stable public HTTP endpoint for the hub.

Authenticated QCE requests use `Authorization: Bearer <token>`. The token is generated/stored by QCE in `%USERPROFILE%/.qq-chat-exporter/security.json` under `accessToken`. Never commit or log the token, place it in a URL, or expose NapCat's WebUI credential. Both local services must remain bound to loopback.

Live requests require a logged-in QQ account and running NapCat/QCE bridge. The standalone viewer can only inspect prior exports; it cannot list live contacts or query live history.
