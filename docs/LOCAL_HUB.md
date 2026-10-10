# Local Message Hub

## Start

Double-click `START.cmd` in the repository root. The launcher opens the local
WebUI and starts a hidden loopback-only service if it is not already running.
Alternatively run `integrations/wechat/runtime/venv/Scripts/python.exe -m hub.server`.
Default URL: `http://127.0.0.1:8765`; if occupied, the service selects a free port.
The actual URL/PID are recorded in ignored `.local/server.json`.

The current machine's Python environment, icon library and NapCat release are
already provided. A fresh source checkout bootstraps Python packages and Lucide
only when absent; Python 3.12 and Node/npm are prerequisites for that bootstrap.
The QQ release runtime must travel with the local tool. It is deliberately not
a source-controlled private runtime directory.

## Reproducible Checks

On a clean Windows checkout, install the small public CI test requirements and
run `./scripts/check-local-hub.ps1` from PowerShell. The same check runs in
`.github/workflows/local-hub-ci.yml`; it uses synthetic fixtures, never local
chat databases or ignored account settings.
For a larger local-only benchmark, run
`integrations/wechat/runtime/venv/Scripts/python.exe scripts/benchmark-local-hub.py`.
It creates and deletes a temporary synthetic database and reports measured
search, pagination, concurrent WAL write, and backup/restore results.

## Data Sources

| 平台 | 可读历史范围 | 媒体能力 | 恢复与对账 | 导出边界 |
|---|---|---|---|---|
| 微信 | 当前 Windows 用户本机 WeChat 4.x 数据库快照；不访问云端归档 | 仅本机数据库/缓存中可解析的内容 | 按 `(sort_seq, shard, local_id)` 持久续读；source keyset 不会发现游标之前的回填 | 可导出本工具索引中的标准消息记录，不导出/改写微信原始数据库 |
| 企业微信 | 当前用户本机本地缓存快照；不包含企业后台会话存档或未缓存文件 | 本地快照能解析或定位的附件；缺失缓存不可恢复 | 每次完整重读本地快照；按消息 ID 幂等更新，不因快照缺行删除既有索引 | 可导出本工具索引中的标准消息记录，不导出/改写企业微信原始数据库 |
| QQ | 当前 NapCat 可见记录，加上 QCE 能获取的私聊漫游范围；覆盖率受平台预算限制 | 漫游端能下载的图片、语音、视频、文件；未取回附件明确显示未缓存 | 中断后续读已提交的位置；下一次完整同步从第一页重读并按消息 ID 对账；分页期间源变化可能要等完整重扫才收敛 | 可导出本工具索引中的标准消息记录，不导出 QQ 客户端数据库 |
| Telegram | Telegram Desktop 用户主动导入的单个会话 JSON；不是原生客户端或自动同步 | 保留安全的相对附件路径元数据；不读取本机导出目录中的附件文件 | 不适用；由用户导入新导出 | 仅统一索引导出，不回写 Telegram |

所有平台的 JSON/CSV/Markdown/TXT/HTML 导出都来自统一索引，不代表原始
客户端数据库的完整镜像。WeChat 的 keyset 续读、QQ 的位置分页与 WeCom
的完整快照是不同保证；只有 WeChat 是 source-cursor 增量采集。QQ/WeCom
通过完整重扫/快照重放和消息 ID 幂等对账，不声称零重扫或删除镜像。

WeChat: direct bundled read-only reader for local WeChat 4.x. Keep the client
logged in for key discovery. Per-database keys are saved in `.local/wechat.json`.
The UI can override the data directory; it never exposes saved keys.

Enterprise WeChat: user's own local account, directly integrated source under
`integrations/wecom`. Ignored `.local` configuration contains the selected slot,
database root and SM4 key. Sync parses local snapshots, not enterprise backend
conversation archives. Files absent from the local cache cannot be displayed.

QQ: bundled NapCat/QCE release and the original exporter history APIs. Use
Data Sources > QQ > Login (or open `/#qq-login`), scan the locally rendered
QR code with your own mobile QQ account, confirm login, then synchronize.
NapCat authentication credentials are retained only in process memory; they
are not returned to the WebUI or placed in browser URLs. The login QR is
encoded to PNG locally, without any external QR generation service.
Service readiness and account online status are distinct. An unavailable service
or an unlogged account must never be represented as connected or filled with
demonstration messages.

For older QQ history, open a direct/private conversation and choose Import
roaming history in its details. The bounded QCE scanner imports the selected
date range (up to 1461 days) with configurable message and sequence-query
budgets. Downloaded images, voice, video and files are retained below the local
storage directory and resolved through local-only media capabilities. Message
text, structured content, mentions, sender metadata and recall status are also
kept in the SQLite index. Attachments QCE cannot download remain marked as
uncached. QQ roaming is private-chat only; QCE reports whether its bounded scan
was partial, and Tencent server-side completeness cannot be proven even when
the scan finishes without a partial result.

## First-Phase Surface

Platform selection/all-platform search, conversations, sender labels, contacts
and groups, date/type/text filters, older-message pagination, local media
resolution, local voice decoding, JSON/HTML/CSV/Markdown/TXT exports, refresh,
auto synchronization and light/dark settings. A basic SQL statistics view is
included. Annual/pair reports, Moments and advanced WeFlow-specific features are
not yet ported into this WebUI; the full original WeFlow source is retained.

## Index Backups

Settings can create/download an online SQLite snapshot and restore a selected
snapshot. Restore checks database integrity, schema version and required tables
and columns, and keeps a pre-restore recovery snapshot. If replacement fails,
the service attempts to restore that recovery snapshot. This covers only
`.local/hub.sqlite3`; it does not include the original platform databases,
credentials, or media cache. Treat downloaded backups as sensitive chat data.

Settings also has a two-step local cleanup action. It pauses automatic sync and
removes the Hub index, its backup/import files and generated QQ roaming cache.
Active tasks must finish their cancellation before deletion can proceed. Source
application databases, account configuration and unrelated files are retained;
the UI reports that boundary after cleanup.

Conversation notes, tags, archive state and cross-platform contact identity
links are stored separately from imported messages. MCP remains read-only by
default. Settings has an explicit opt-in for MCP to edit only this local
metadata; changing it requires restarting the MCP client process. The operation
history records action/entity/time but not note text or message contents.

## User Imports

The 导入记录 view accepts UTF-8 JSON arrays (or objects containing a
`messages`/`items` array) and UTF-8 CSV using the Local Message Hub export
columns or common English field names. Preview validates rows and reports up to
100 errors before writing. Records with platform/account metadata keep it;
otherwise choose a default platform and account. Files are limited to 256 MiB
and one million rows. Confirmed imports run as persisted local jobs, retain a
SHA-256 fingerprint and bounded validation report, checkpoint every 500 rows,
and can be cancelled at a batch boundary. A service restart resumes from the
last committed batch; replay is idempotent if a crash happens between the
message commit and checkpoint. Normalized staging files remain under the local
Hub storage directory and are deleted on success; failed/cancelled jobs retain
their stage for diagnosis and completed reports remain in the index. Only
queued/running jobs are resumed automatically after a service restart.
The same view also accepts a Telegram Desktop single-chat JSON export through a
dedicated format path. It stores normalized message/media metadata in the same
index and warns when an unsafe relative attachment path is discarded; it never
opens attachment files or presents Telegram as a native sync source.

Long text searches use SQLite FTS5 trigram candidates when the bundled SQLite
supports them, with escaped literal matching applied afterward. Queries shorter
than three characters use the literal fallback; Settings reports whether FTS5
is available. Clicking a search result opens its surrounding message context.
Search and conversation-message APIs support stable cursor continuation ordered
by timestamp and SQLite row id. Cursors are bound to their platform, query,
conversation, and date/type filters; using one with changed filters is rejected.
Offset pagination remains available for older clients.

Local management data uses separate schema-v5 tables for conversation notes,
tags, archive state, contact identity links and operation records. These tables
do not modify or annotate imported message rows.

## Privacy And Verification

HTTP binds only `127.0.0.1`, rejects foreign Hosts/Origins/cross-site requests,
and accepts writes only as same-origin JSON or the explicitly scoped local file
upload endpoints. Media links are random local
capabilities, not arbitrary file path inputs. Source databases stay untouched.
Decrypted snapshots and the local index contain sensitive plaintext; protect
this Windows account and repository directory. Do not publish `.local`,
`.runtime`, provider runtime/cache directories or browser verification output.

Run focused verification:

```text
integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_store hub.test_server integrations.wechat.test_media integrations.qq.test_provider -q
node --check webui/app.js
```

Screenshots and browser logs live only in ignored `output/playwright` and
`.playwright-cli`. Upstream provenance and license boundaries are in `UPSTREAM.md`.
