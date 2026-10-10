# QQ integration source

Vendored source: [shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter), release `v6.3.2`, commit `7d154b847a0cb3f9cffbdb126598c4d1434995ba` (annotated tag object `a1c0fcc5bb48af7844bbc1538963b4d0a05c927f`, unsigned). The tag's upstream `LICENSE` is GPL-3.0; keep it with any redistributed upstream source or derived work.

The source archive is `upstream/source-v6.3.2.zip`; extracted upstream tree is `upstream/expanded/qq-chat-exporter-6.3.2/`. Source archive SHA-256: `1CA3141920769B8ADA0AA43629A95465AA2718C694234685C9A3DA85971B647B`.

## Windows shell runtime

The official x64 shell asset is stored and unpacked under `.runtime/qq/v6.3.2/` (runtime is local-only). Asset: `NapCat-QCE-Windows-x64-v6.3.2.zip`, 42,346,596 bytes; downloaded 2026-10-10 06:04:46 UTC. SHA-256: `3FC093BA2332B97AC17E16160D5BAA6FFE0A8DEF6C394D515A94777FE0CA492C`. The upstream release lists Windows x64 and requires a QQ client login; shell mode is not a login-free data source.

The hub launches the bundled `NapCatWinBootMain.exe` directly with injection metadata staged only inside this runtime directory; it does not write into the installed QQ directory. The QCE server binary is locally rebuilt from the vendored source with its bind address restricted to `127.0.0.1`. NapCat WebUI is likewise configured for loopback and its console logging is disabled. `start-standalone.bat` only views prior exports and is not the live data path.

### Rebuilding the loopback QCE server

Build from the pinned v6.3.2 source and lockfile. The source requires Rust 1.82 or newer; the local verified build used rustc 1.98.1 and winlibs GCC 16.2.0 (x86_64-w64-mingw32, MSVCRT). Stage the source and Cargo target directory under an ASCII-only path such as `%LOCALAPPDATA%\LocalChatHub\qq-build` because the Windows GNU toolchain's `dlltool` failed with the workspace's non-ASCII path. In `qq-chat-export-server/src/main.rs`, bind the listener to `[127, 0, 0, 1]` (not unspecified `0.0.0.0`), then build with the GNU target and `Cargo.lock`:

```powershell
$env:PATH = '<ASCII staging>\winlibs\mingw64\bin;' + $env:PATH
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = '<ASCII staging>\winlibs\mingw64\bin\gcc.exe'
$env:CARGO_TARGET_DIR = '<ASCII staging>\target'
cargo build --release --locked --target x86_64-pc-windows-gnu --manifest-path '<ASCII staging>\source\qq-chat-export-server\Cargo.toml'
```

The locally rebuilt `qce-server.exe` is 30,992,534 bytes, SHA-256 `B928A9E556DB6D1F8BA93B4213B70238190CD580743C4CE5DD7FCC0309ED1FCC`. The official release shell's server binary is also accepted only at SHA-256 `066D9FF15C3E6CB7EFE23ACC564B5B7B34D6DA2849FEC77A7DCC890290BB7745`; its containing archive is independently pinned above. The pinned v6.3.2 server source binds to `127.0.0.1`. The provider refuses to launch NapCat when the bundled binary is absent or has a different hash.

The provider authenticates to the local NapCat WebUI API without placing its credential in a URL or returning it to the hub. `POST /api/QQLogin/CheckLoginStatus` supplies the current phase, and `POST /api/QQLogin/GetQQLoginQrcode` supplies the QR payload. The hub should render the `launch()` result's `qr_url` as a QR image and show `login_phase`; the user must scan it with QQ to connect. This machine currently reports `waiting_qrcode`; QCE is not yet listening, and live history remains unavailable until login and bridge initialization complete.

## Hub adapter contract

Talk to the local QCE HTTP service (release defaults to `http://127.0.0.1:40653`) rather than coupling the hub to private NapCat bridge calls:

- `GET /api/system/status`: use `data.online` as the live account/bridge status. `GET /api/system/info` also returns `napcat.selfInfo` when connected.
- `GET /api/groups?page=1&limit=...`: paged groups; `GET /api/friends?page=1&limit=...`: paged friends.
- `POST /api/messages/fetch`: body requires `peer: { chatType, peerUid }`; group `chatType` is `2`. Optional `filter.startTime/endTime` are Unix timestamps. Use `page`/`limit` for pagination and `forceRefresh` to bypass cache. Response contains `data.messages` and paging metadata.
- For downloaded media and durable exports, use `POST /api/messages/export`, then poll `GET /api/tasks/:taskId`; retrieve exported resources through `/api/exports/files/:fileName/resources/*path` or `/api/download-file` as applicable. The underlying bridge uses `FileApi.downloadMedia`; it is not a stable public HTTP endpoint for the hub.

Authenticated QCE requests use `Authorization: Bearer <token>`. The token is generated/stored by QCE in `%USERPROFILE%/.qq-chat-exporter/security.json` under `accessToken`. Never commit or log the token, place it in a URL, or expose NapCat's WebUI credential. Both local services must remain bound to loopback.

Live requests require a logged-in QQ account and running NapCat/QCE bridge. The standalone viewer can only inspect prior exports; it cannot list live contacts or query live history.
