# 本地消息中枢 Rust 后端：只读快照与插件接口

这是 J-X-C-Z/local-message-hub 自有后端的迁移切片，基线
c7052745fab3fa1880c6e3a496f40609185668b9。不使用外部 PR #1 的后端或桌面壳。
它是可运行的 Rust 进程，但还不是现有 Python 中枢的完整替换。不接管原启动脚本、
数据库迁移、账号授权或桌面 UI。

## 用合成数据运行

在仓库根目录执行（以下用 POSIX shell；Rust 服务构建后不依赖 Python）：

    cargo build --manifest-path rust-hub/Cargo.toml --locked
    BIN="$PWD/rust-hub/target/debug/local-message-hub-rs"
    test_dir="$(mktemp -d)"
    python3 - "$test_dir/index.sqlite3" <<'PY'
    from pathlib import Path
    import sqlite3
    import sys
    with sqlite3.connect(sys.argv[1]) as db:
        db.executescript(Path("rust-hub/tests/fixtures/index-v9.sql").read_text())
    PY
    "$BIN" new-plugin-token

保管输出的 token，不要写入 Git、命令行参数或日志。把同一次输出的
token_sha256 放入测试目录的 plugins.json，例如：

    {
      "version": 1,
      "grants": [
        {
          "id": "example.desktop",
          "kind": "desktop",
          "token_sha256": "<替换为生成的 64 位小写十六进制 verifier>",
          "permissions": ["conversations.read", "messages.read", "contacts.read"]
        }
      ]
    }

    "$BIN" serve --snapshot "$test_dir/index.sqlite3" --grants "$test_dir/plugins.json"

不指定端口时系统分配本机端口。父进程从 stdout 读取
{"event":"ready","origin":"http://127.0.0.1:…","api_version":1}。
另外启动桌面插件示例，并在交互提示中输入上面的 token：

    python3 rust-hub/examples/desktop_plugin.py http://127.0.0.1:实际端口

上述示例只读取合成记录。真实使用应选择经过本人授权、独立目录中的**可信 v9
归档副本**，而不是活动 Hub 配置目录、数据库原件或 SQLite WAL。不要删除安全设置
来避开这些检查。快照和配置目录应仅由当前操作者控制；进程运行期间保持快照
不变。把含有私聊的归档与授予该归档读取权限视为敏感操作。

## 插件接口

插件是独立进程的**本机 API 客户端**，不是加载进后端进程的二进制扩展。
桌面壳、CLI 或开发工具都可接入，不重复实现索引和查询。后端不自动下载、
安装、扫描或执行插件，也不向插件公开系统命令、文件权限或发送聊天能力。
桌面插件应由原生进程持有 bearer 并进行本机 RPC，不能把凭据暴露给外站、
网页脚本、URL 或跳转请求。宿主配置更改（包括撤销）在重启后生效。

除 /healthz 外，所有请求通过 Authorization: Bearer <token> 认证，拒绝带 Origin
的请求或非精确的本机 Host。不以 query token 或 cookie 认证，也不接受请求体。
仅绑定 IPv4 的 127.0.0.1。这不是操作系统沙箱：同一账号下运行的程序仍由操作系统
及凭据存放方式约束。每个插件使用独立、随机的 token，宿主只保存验证摘要。
授权按操作区分，**当前不是分账号授权**：对某类读取授予权限后可读整个选定快照
中该类数据。路径和 URL 如果随消息、头像等返回，仅是档案元数据；
Rust 服务不会读取这些路径或访问这些 URL。

| 请求 | 访问权限 / 作用 |
| --- | --- |
| GET /healthz | 仅进程存活；不证明数据库可读 |
| GET /api/v1/manifest | 识别当前插件、协议版本、该插件可用资源 |
| GET /api/v1/openapi.json | [OpenAPI 3.1 契约](openapi.json) |
| GET /api/v1/status | 任一归档读取权限；快照状态、架构版本、FTS |
| GET /api/v1/conversations | conversations.read |
| GET /api/v1/messages | messages.read；按平台、账号、会话查询 |
| GET /api/v1/messages/context | messages.read；读取目标消息及相邻消息 |
| GET /api/v1/messages/quoted-context | messages.read；定位同账号、同会话内唯一的微信引用原文 |
| GET /api/v1/search | messages.read；跨平台搜索、可加账号和会话过滤 |
| GET /api/v1/contacts | contacts.read |

[插件授权 JSON Schema](plugin-grants.schema.json) 定义文件结构，宿主另外拒绝重复
标识、摘要、权限和未知字段。列表上限 1,000；响应页数据限约 4 MiB，
过大返回 413（可缩小页长；单一过大记录无法通过该接口获取）。
四个并行数据库请求已占满时返回 503，单次 SQL 有截止时间。游标为不透明字符串，
续页必须保留原过滤条件；游标来自当前快照，快照切换后勿复用。
消息上下文必须指定平台、账号和会话。引用接口只接收归档的引用消息 ID，
不会返回原始平台服务端 ID；查不到唯一匹配时返回 404。
搜索只返回正文片段，媒体读取和事件订阅不在此版。普通错误格式为
{"error":{"code":"..."}}；插件发现能够成功，不代表快照目前可读。

## 原生实时归档

当前另有隔离的 [native-live 模式](NATIVE-LIVE.md)，可由单独生产者写入标准消息并
由桌面插件通过持久事件游标跟随。旧只读快照模式仍没有写入或事件订阅。

## MCP 工具插件

同一 Rust 二进制可以由可信本机 MCP 客户端通过子进程运行，
不启动网络监听。为该客户端创建独立的 kind: tool 授权；客户端的安全启动器应将
该授权的 token 通过 LMH_PLUGIN_TOKEN 环境变量传给子进程，不要硬编码
到仓库或命令行：

    "$BIN" mcp --snapshot "$test_dir/index.sqlite3" --grants "$test_dir/plugins.json" --plugin-id example.tool

这是 MCP 2025-11-25 的初步只读支持。客户端先发 initialize，
再发 notifications/initialized；每条 JSON-RPC 消息占一行。
tools/list 和 tools/call 只对当前授权提供 list_conversations、list_messages、
search_messages、list_contacts 中的对应工具。不是现有 START_MCP.cmd 的替换，
不支持资源、提示词、附件、实时消息、写操作或自动发送。归档内容应视为不可信数据，
不能当成给 MCP 客户端的指令。请勿向不可信的插件分发访问私人归档的授权。

## 验证

    cargo test --manifest-path rust-hub/Cargo.toml --locked
    cargo test --manifest-path rust-hub/Cargo.toml --locked --test python_store_equivalence -- --ignored

第二条需要 Python 3，使用真实 Python ChatStore 写入合成数据，再和 Rust 比较，
不接触真实账户数据。原生端到端测试启动真实 Rust 进程，验证两个独立插件的
鉴权和授权、Web Origin 拒绝、防重复游标、快照运行中出现锁配置时停止读取，以及
读取前后的数据库字节相同。详见[迁移盘点](MIGRATION.md)。

### Exact-account desktop grants (rebuild-001)

The local owner can optionally put an **exact** account scope on a `desktop`
grant. This field does not add capabilities; keep only the permissions the
plugin needs.

```json
{
  "id": "example.desktop",
  "kind": "desktop",
  "token_sha256": "<SHA-256 from new-plugin-token>",
  "permissions": ["conversations.read", "messages.read", "contacts.read", "events.read"],
  "account_scope": {"platform": "wechat", "account_id": "synthetic-account"}
}
```

- Omitting `account_scope` preserves the trusted 003 desktop behavior, including
  access across available accounts. Explicit `null`, platform `all`, and this
  field on `tool` or `producer` are rejected. Account IDs are literal, never
  wildcard patterns. Tools and producers remain
  trusted roles; do not give their unscoped grants to restricted clients.
- For a scoped desktop, list/search/context queries fill omitted account/platform
  from the grant. For list/search, an explicit foreign scope gives 403; exact
  context/quote requests that specify a foreign scope give the same 404 as a
  missing message. Contact account choices are also filtered. Scoped
  message/search pagination keeps timestamp ordering and uses the account's own
  message IDs for ties; its cursors are not interchangeable with 003 cursors.
- Scoped replay/SSE return account-local event sequence numbers, including
  checkpoint and page metadata. Save and forward the opaque cursor strings;
  don't build them from integers. There are no foreign-account gaps. A cursor is
  tied to the same archive, grant ID and exact scope. Restarting against the same
  archive preserves the cursor. This is not a cross-restore or cross-archive
  cursor contract. Grant files are loaded at service startup; restart the service
  to apply edits and retire old streams.
- `/api/v1/status` still exposes only archive readiness and format metadata,
  not account counts. Snapshot mode still has no event endpoints.

### Project a detached v9 SQLite backup (rebuild-002)

This owner-run command creates a new **read-only serving snapshot** from a
detached legacy Hub v9 SQLite backup. It does not restore in place or turn the
legacy backup into a writable native profile. Run the existing Python
backup operation first if the source Hub is open; do not copy its live SQLite
file yourself. Put the generated raw backup in a separate directory under the
name `index.sqlite3`. On Unix:

```sh
mkdir -m 700 fresh-snapshot
local-message-hub-rs project-snapshot \
  --source /private/detached/index.sqlite3 \
  --directory fresh-snapshot
local-message-hub-rs serve --snapshot fresh-snapshot/index.sqlite3 --grants /private/plugins.json
```

- Keep the source detached and unchanged for the entire projection. Nearby
  legacy profile files or SQLite journals are refused. Source is opened
  read-only, integrity-checked and hashed before and after; the output is
  integrity-checked and has its own SHA-256. The JSON receipt only contains
  checksums and counts for its five projected tables.
- The target must exist, be private and empty. On Unix no group/other mode
  access is allowed. Windows ACL setup and runtime verification remain separate;
  this milestone does not claim Windows qualification. Output is staged in the
  target directory and published with a no-replace hard link; use a filesystem
  that supports hard links. It cannot overwrite an existing target. Concurrent
  requests may both fail; if so, retry with a fresh empty target.
- Copies conversation, message, contact, conversation metadata and contact
  identity rows into a fixed v9 schema, preserving IDs and timestamp/rowid tie
  ordering. Text and attachment *metadata* remain private archive content and
  are not scanned for embedded secrets; don't share the projected database.
  Provider configuration, grants, operational/outbound tables, sync
  checkpoints, import-job state and attachment files are not copied. No legacy
  FTS index is copied; search uses the fallback and is not a benchmark for old
  FTS search performance. The original archive is not modified.
- Only a recognized raw v9 SQLite schema with the projected tables is accepted,
  not a native archive, old/future schema, mixed-media ZIP or other restore
  format. Treat validation as a format check, not proof of archive provenance.

### Export a single conversation (rebuild-003)

Grant `messages.export` and `messages.read` together only to a desktop
plugin with an exact `account_scope`. The plugin manifest advertises the
download path under `resources.message_export`. For example:

```
GET /api/v1/messages/export?conversation_id=room&format=csv&start=1720000000&end=1720000100
Authorization: Bearer <dedicated desktop token>
```

- Both snapshot and native-live archives serve the same local endpoint.
  `conversation_id` is required; omit platform/account to use the exact grant,
  or specify both matching values. A foreign target or absent conversation
  gets 404. `start` and `end` are optional inclusive Unix seconds. Output is
  ascending timestamp, then binary message-ID order for a tie.
- JSON (default) is a UTF-8 array of explicitly whitelisted message fields;
  CSV is UTF-8 without a BOM, with CRLF and the old header order. CSV quotes
  separators and doubles embedded quotes. Formula-like cells receive a
  leading apostrophe, including when a formula marker is preceded by
  whitespace, controls or BOM. UTC+08:00 display timestamps are independent
  of machine time zone; a timestamp of zero has a blank display value.
- No message or provider metadata beyond the selected allowlist is read for
  the export. No attachment media, filenames, paths, URLs or bytes are
  fetched from metadata. CSV retains empty media/filename columns and a
  literal `[]` attachment column for table compatibility. Message *text*
  remains private and may itself contain links, names or secrets; this is
  not text redaction. The server writes no files.
- The selection must fit at most 1,000 rows, 64 KiB per text cell, and 4 MiB
  for the completed export including quoting. Exceeding any cap fails with
  413 and no partial download; use a smaller timestamp window. If over 1,000
  selected records share the same timestamp, this slice cannot split that tie.
  Unrecognized formats are rejected; there is no HTML, ZIP or automatic export.
- It is intentionally not the original `/api/export` endpoint. Legacy
  attachments, arbitrary extra data, cross-account exports, old tie ordering,
  and legacy file writes are not reproduced.

### Edit user metadata (rebuild-004)

These routes are only registered for a Rust-owned `serve --live` archive.
An account-scoped desktop plugin must be separately granted:
- `conversations.read` + `conversations.metadata.write` for
  `GET/POST /api/v1/metadata/conversation`;
- `contacts.read` + `contacts.identity.write` for
  `GET/POST /api/v1/metadata/contact`;
- `metadata.audit.read` for `GET /api/v1/metadata/audit`.

A GET to an entity route takes `id` and optionally exact matching
`platform`/`account_id`; use the returned `revision` in a write. Example
conversation request: `{"target":{"id":"room"},"expected_revision":0,
"note":"My note","tags":[" project ","project"],"archived":true}`.
Omit fields to retain them; use `""` or `[]` to clear them. Tags are trimmed,
deduplicated in first-seen order, maximum 50 supplied tags and 64 Unicode
scalars per trimmed tag. Notes allow up to 4,096 scalars and ordinary line
breaks/tabs, with other control characters rejected.

Contact requests explicitly supply `identity_id` as a trimmed nonempty
string of at most 256 Unicode scalars, or `null` to clear. The plugin chooses
the value; the backend does not infer relationships, merge across accounts,
or change other contacts. Clearing an absent mapping is an unchanged success.
A foreign target has the same 404 as a missing in-scope target.

A real change is transactionally committed with one account-local audit row.
If the requested outcome already matches current state, even with a stale
expected revision, the result is unchanged at the current revision with no
new audit row. A differing update with a stale revision fails with 409; GET
and review the latest revision before retrying. Concurrent writers with
different changes and the same revision cannot both apply them.

Audit returns only the exact account’s entity identifier/type, action,
revision, timestamp and field **names**. It does not store prior note bodies,
tag values, identity values or tokens in audit. The current metadata is still
private archive content, and this is not guaranteed secure erase of old
SQLite pages/WAL. Audit is append-only within native archive storage limits;
pages are capped to 100, with `after` and `next_after` for navigation.
Edits do not emit message replay events or send anything to providers.

### Inspect a legacy mixed-media ZIP (rebuild-005)

For a local, operator-selected backup from the old Python Hub, run
`local-message-hub-rs inspect-media-backup --source /path/to/backup.zip`.
This is a **read-only preflight**, not an import or restore. It returns
archive/index SHA-256 and verified file counts/bytes, split across
referenced QQ and WeChat cache items. `reported_missing_media_count`
is just the old manifest's hint, not an independently verified count.

The verifier checks an exact v1 manifest, ordinary Stored/Deflate members,
member declaration/count/size/digest and CRC, local/central headers, and
actual references in the detached SQLite index. QQ must be a referenced
Hub roaming cache path; WeChat must match a valid indexed message's cache
identity. The index alone is temporarily staged under a fixed name in two
private temporary directories and validated with the detached v9 projection.
No media is materialized, no names/bytes are printed, and no original
provider database, runtime cache or account is opened. Hashes establish
the bytes inspected, not archive authorship or trust.

Preview supports a deliberately narrower subset than old restore: ordinary
comment-free single-disk ZIP32 files; no preamble, gaps, descriptors, member
extras/comments, encrypted entries,
ZIP64, directories, symlinks, unusual codecs or duplicate/undeclared names.
Limits: 256 MiB archive, 512 MiB declared uncompressed total, 128 MiB index,
64 MiB per media entry, 2 MiB manifest/central directory, 4,096 entries and
250,000 examined reference rows. Source caps can reject otherwise valid
old backups. No HTTP adapter, media restoration, active profile rollback
or Windows/macOS runtime qualification is provided.

### Import a legacy media ZIP into a new private archive (rebuild-006, Linux)

This is an explicit operator command, separate from the read-only preview.
Use the same exact v1 ZIP32 subset and limits. Choose a new leaf under an
existing private directory (no group/other permission bits or parent
symlink), on a Linux filesystem that supports atomic no-replace rename:

```sh
mkdir -m 700 /private/new-archive-parent
local-message-hub-rs import-media-backup --source /private/backup.zip \
  --directory /private/new-archive-parent/new-archive
```

- The same parser/verification pass uses a single open ZIP file. Each verified
  cached payload is stored privately as `objects/<sha256>` and identical
  bytes share one object. ZIP names are never used as destination paths.
  `catalog.json` maps `SHA256(b"lmh-legacy-cache-v1\\0" + entry_name_utf8)`
  to payload digest, size, and source type, under version 1. These hashes are
  identifiers, not encryption, signatures, or a provider access grant.
- The safe read projection is `snapshot/index.sqlite3`; it can be opened
  explicitly with `serve --snapshot` and customary plugin grants. It contains
  normal private legacy text/metadata. No HTTP media-serving API is registered
  in normal snapshot mode. The separate bundle mode below is opt-in. This is
  not restoration to the old Hub or a live source.
- All files are verified before a completed directory is published atomically
  through `renameat2(RENAME_NOREPLACE)`. Any existing target, including a
  symlink/empty directory, is not replaced. Before commit ordinary failures
  remove temporary staging; if the process crashes, the uncommitted private
  temporary directory may need cleanup, but no partial final name is published.
  The receipt's `parent_synced:false` would mean the complete directory was
  published but the final directory sync did not confirm crash durability.
  Use a trusted operator-controlled path hierarchy; pathname-based staging
  does not defend against same-UID or ancestor mutation while import runs.
- The source ZIP, running applications, originals, backups and provider cache
  directories are untouched. There is no restoration in place, provider
  login, credential/config import, or non-Linux import fallback. The archived
  bytes remain private and are not decoded as trusted images or documents.

### Read attachments from a private imported bundle (rebuild-007; Linux)

Start a separate loopback host; it does not activate providers, modify the
bundle, serve names/legacy metadata, or serve the general snapshot routes:

```sh
local-message-hub-rs serve --media-bundle /private/new-archive-parent/new-archive \
  --grants /private/plugin-grants.json
```

- The desktop grant must include **both** `messages.read` and `media.read`
  and one exact `account_scope` for the platform and account. The normal
  localhost/Bearer checks still apply. No query credentials, browser Origin,
  tool grants, automatic download, or external URL access.
- GET `/api/v1/messages/media` with `platform`, `account_id`,
  `conversation_id`, and `message_id`. The response is
  `{"items":[{"id":"opaque-id","size":123}]}`. Item IDs are bundle- and
  exact-message-bound, not credentials. GET `/api/v1/messages/media/{id}`
  with the same four parameters and grant. Raw bytes use a fixed
  `application/octet-stream` attachment filename, `nosniff`, and
  `no-store`. A single range is supported only after whole-object verification. Items larger than 8 MiB
  are not listed/served. The full object digest and size are verified
  before an HTTP body and at most four archive work/response slots may
  be occupied concurrently.
- QQ entries use exact snapshot references and ambiguous multi-message
  references are not served. Old rebuild-006 catalogs can serve uniquely
  owned QQ items. Older WeChat items have no recoverable binding: re-import
  the original ZIP into a fresh leaf with rebuild-007. The new catalog keeps
  the validated WeChat basename and cache key **privately on disk** to
  check the original reference hash against account/session/shard/local ID.
  Neither these values nor object digests or filesystem paths are returned
  by these endpoints.
- Use only an import you created from a trusted legacy backup in a private
  operator-controlled path. The bundle host verifies the snapshot hash at
  startup and file identities around requests; it refuses detected changes.
  Startup reads up to 128 MiB of index and scans at most 250,000 candidate
  rows with a 256 KiB per-reference metadata limit. An over-limit or invalid
  candidate field fails startup rather than hiding a potential competing
  owner. Each request uses cached bounded bindings and rechecks file identity.
  It does not authenticate an adversarially replaced catalog/backup or
  defend against same-user filesystem/ancestor tampering. Do not edit the
  bundle while serving it; re-import and restart instead. There is no
  Linux-to-macOS/Windows fallback for this mode.

### Import old Hub text JSON into an initialized native archive (rebuild-008)

This is a two-step **local operator command**, not an HTTP endpoint or a
provider operation. Keep the export outside the private native directory,
pause writers while migrating, and name the exact platform and account
explicitly. Files containing other accounts are rejected, not split or
remapped. No token is accepted; filesystem access to the native archive
and explicit manual confirmation authorize the local write.

```sh
local-message-hub-rs preview-legacy-json --source /private/messages.json \
  --directory /private/native --platform qq --account <account-id>
# After verifying the source, target and displayed row count:
local-message-hub-rs import-legacy-json --source /private/messages.json \
  --directory /private/native --platform qq --account <account-id> \
  --confirm <confirmation-from-preview>
```

- Supported input is a stock Python Hub message-export JSON array of 1–100
  text messages, at most 512 KiB of input and 512 KiB after normalization.
  IDs, UTF-8 text, sender/context fields, self flag and integer Unix timestamp
  are preserved. Missing identity fields, duplicate message IDs, invalid
  shapes, mixed accounts and nontext rows fail the complete batch. Not a
  CSV/JSONL/media/metadata restore; attachments, URLs in resource fields,
  self-described metadata, formatted timestamp and extras are not imported
  or opened. Literal text is preserved and may contain URLs.
- Preview reads and validates the file and checks native target identity, but
  does not apply any row or promise there will be no write conflicts
  (SQLite may use its normal transient reader sidecars). The
  confirmation is bound to exact file bytes, native archive ID and explicit
  platform/account. Any change requires another preview. It is a receipt
  identifier, not a password or replacement for filesystem permissions.
- Commit atomically appends new rows through the native transactional ingest
  kernel. It never overwrites existing rows: an exact unchanged row from the
  same offline importer is accepted, but any differing row or any identity
  already owned by another producer aborts the entire batch. Retrying the
  identical confirmed file against the same archive returns its original
  receipt. The receipt does not reveal paths, tokens or event checkpoints.
  A later live producer cannot claim IDs already owned by this importer;
  avoid importing overlapping IDs if that producer needs to manage them.
- No live events are emitted, and no source/cache/application files are
  modified. After restarting writers, refresh attached clients explicitly;
  SSE reconnect/replay will not deliver offline-imported rows.

Use local operator-controlled paths, not directory trees writable by another
process. Linux x86_64 was tested; no macOS or Windows runtime qualification.

### Supervise the native runtime from a desktop app (rebuild-009; Linux)

The downloadable Linux x86_64 add-on is a portable release, not an
installer. Extract it to a trusted directory, validate the bundled
checksums, then launch its executable directly (not through a shell):

```sh
./local-message-hub-rs desktop-supervise --live /private/native \
  --grants /private/plugins.json
```

The app must launch this **with stdin/stdout pipes**, keep stdin open while
using the backend, and continuously consume stdout. Snapshot and imported
media-bundle modes take the same archive flags as `serve`. No fixed port,
caller-provided backend executable, token argument, or token environment
variable is required or accepted.

- The launcher executes another process of the same binary, clears the
  child environment, and accepts only a private, singly linked regular
  grant file. On Linux use mode `0600` or `0400`; keep grant hashes and
  archive data in trusted operator-controlled directories. This does not
  protect data from another program running as the same user.
- Read one JSON line from stdout. `event:"ready"` reports `origin` (an
  ephemeral exact `http://127.0.0.1:<port>`), `api_version:1`, `mode`
  and process diagnostic `backend_pid`. It follows backend configuration
  validation plus a bounded localhost `/healthz` probe; this check does
  not validate your app's bearer. Use the versioned OpenAPI/grant schema
  and perform an authenticated manifest request before showing connected.
- Store the desktop bearer separately in the app's secure storage; send
  it **only** via the HTTP `Authorization: Bearer …` header to the verified
  loopback origin. Do not put it in argv, URLs, logs, or grant files; grant
  records store token digests. For scoped streaming, read the checkpoint
  and pass `Last-Event-ID` to resume when appropriate.
- To stop, send exactly `stop\n` or close stdin. Read a second line with
  `event:"stopped"` and boolean `forced`. The launcher asks for graceful
  shutdown and force-kills/reaps if active streams or slow operations do
  not close in time. It does not restart the child automatically. Linux
  parent-death handling terminates the child if its supervisor is killed.
  Stderr is bounded/generic operator diagnostics; a startup failure exits
  nonzero without ready. For local diagnostics use direct `serve`, which
  does not have this pipe lifecycle.
- The supplied ELF is optimized for Linux x86_64 GNU, dynamically linked
  against the normal system glibc/libgcc runtime. Consult its artifact
  manifest for symbol floor, hash and build provenance. macOS, Windows
  and browser are not qualified. This is not the historical desktop
  launcher's auth/IPC contract or a replacement for the old Python API.


### Exact-account MCP on stdio (rebuild-010)

Use the existing `mcp --snapshot … --grants … --plugin-id …` command.
Provision a dedicated `kind: "scoped-tool"` grant with an exact
`account_scope`. Launch it on pipes and supply its token privately in the
child process environment as `LMH_PLUGIN_TOKEN` (not in argv, URLs, logs,
or grant JSON). The HTTP host rejects this grant kind. Existing trusted
unscoped `tool` and scoped `desktop` grants keep their prior behavior.

- Opt in to `messages.read` for `list_messages`, `search_messages`, and
  `lmh://message/{platform}/{account_id}/{message_id}`. Missing platform
  and account filters are filled with the exact configured scope; a caller
  cannot widen it. The resource URI uses uppercase percent-encoding of
  UTF-8 bytes (e.g. literal `/` → `%2F`, `%` → `%25`); no alternate
  spelling, query, fragment, or cross-account read. Use the returned
  `resource_uri` from tool results rather than building paths manually.
  Output contains normalized fields only; raw extras, media/file fields,
  avatar/cache URLs and bytes are not exposed. Literal chat text remains
  untrusted data and may contain URLs.
- Opt in to `mcp.sources.read` for `get_source_status` and `lmh://sources`.
  Results are content-free counts for just the granted account, not a
  source probe, runtime status, broad platform report or configured accounts.
- `mcp.prompts.read` requires `messages.read`. The legacy-named
  `cross_platform_search` prompt is confined to the single granted account.
  `inspect_attachment` only points to an existing normalized message
  resource; it does not read cached media, expose attachment metadata,
  invoke a tool, send anything, or authorize an external action.
- Supported protocol revisions: 2025-11-25, 2025-06-18 (also
  `structuredContent`), and 2025-03-26, 2024-11-05 (JSON text tool result).
  Only verified detached v9 snapshots are supported. The scoped permission
  set is intentionally not a drop-in replacement for the old broader
  Python MCP server. No real app/account/provider was used in verification.

### Bounded live MCP incoming notifications (rebuild-011)

Launch `mcp --live <private-native-directory> --grants <grants.json>
--plugin-id <id>` on stdin/stdout. Use a separate `scoped-tool` grant with
one exact `account_scope` and **only** `mcp.incoming.read`. Supply its
`LMH_PLUGIN_TOKEN` in the child environment as for snapshot MCP. The grant
cannot read snapshots, authenticate HTTP, search, fetch attachments, or
invoke provider actions. This server never starts a network listener.

- Call `wait_for_new_messages` without `after_cursor` to get a checkpoint
  and no history. Pass the returned string cursor back in `after_cursor`.
  `timeout_seconds` 0–30 (default 15) controls polling/waiting, and
  `limit` 1–50 (default 20) caps each page; if `has_more` is true, resume
  from that page's cursor. A pending wait occupies the stdio session until
  it returns; use short requests for interactive work. A storage error can
  stop polling before the timeout.
- Only incoming native **creations** emit metadata: account, platform,
  conversation ID, message ID, timestamp and false self flag. There is no
  body, and self, revisions, other accounts and offline JSON import do
  not advance this feed's account-local sequence. Refresh after offline
  imports. Existing desktop SSE revisions and cursors are not changed.
- Cursors are bound to the native instance ID, grant ID, platform and
  account. A restart keeps the cursor; a different scope/instance, a
  future cursor, or stock Python's bare integer `after_event_id` is
  rejected rather than silently reset. An intentional byte-copy of a
  database retains the same instance ID; independent clones and arbitrary
  rollbacks are not distinct generations. Reinitialize when changing to a
  different database history, even if copied from the same directory.
- New `init-live` archives already contain the created-only index. For an
  existing native archive, stop writers, retain a backup, and run the
  explicit `upgrade-mcp-incoming --directory <private-native-directory>`
  once. The atomic backfill reads preexisting **native** created journal
  entries, not Python rows or the old offline-import state. The upgrade is
  idempotent; if the archive is unavailable or storage safety bounds are
  hit, no partial sequence is retained.

### Optional guarded native bootstrap (rebuild-012; Linux)

This is a separate **fresh, read-only profile/bootstrap mode**, not the
default native server. Rebuild-012 did not populate guarded profiles;
rebuild-013 adds the controlled text import described below. Existing live
or provider archives cannot be converted into guarded profiles. Guarding controls access through this local listener, **not
at-rest encryption** or protection from processes with the owner's file access.

- Create an empty private directory. Launch `init-guarded-live --directory
  <directory>` with a private stdin pipe. Send one newline-terminated JSON
  frame with a `passphrase` field (12–256 Unicode characters, no normalization).
  Store no plaintext alongside the database. The private database stores the
  profile marker and verifier (PBKDF2-HMAC-SHA256, 310,000 iterations,
  16-byte random salt, 32-byte digest), not the original passphrase.
- Launch `serve-guarded --directory <directory> --grants <private-grant-file>`
  with stdin/stdout from a trusted parent using anonymous private pipes.
  The FIFO descriptor check alone does not establish pipe exclusivity; do
  not use a shared named pipe or an untrusted launcher. The ready frame names an ephemeral exact
  loopback origin, mode `guarded-read-only`, and `locked:true`. Public
  liveness is separate. Protected GETs receive HTTP 423 `account_locked`
  until the desktop sends a private stdin frame with `command:"unlock"`
  and a passphrase; no HTTP password route, URL field, environment variable,
  shell argument or cookie is accepted for that command. Read responses
  still require a separate exact-account desktop bearer in Authorization.
- `command:"lock"` invalidates the current generation. The host does not
  return buffered handler results from a previous generation. Bytes already
  sent cannot be recalled; there are no streaming routes. Unlock lasts at
  most 15 minutes (a fixed lease, not Python's sliding idle/cookie session).
  Within the process, five failures in one minute block further attempts
  for a minute. Restart always locks and resets the in-memory retry limiter.
  `command:"stop"` or stdin EOF shuts down.
- Only discovery/status and scoped conversations/messages are enabled.
  Ordinary live startup, snapshot aliases, MCP, import, writes, export,
  producer ingestion and SSE are not a way to access the guarded profile.
  Malformed/missing verifier state refuses the guarded mode or invalidates
  reads. This is not compatibility with the original WebUI/auth session,
  historical desktop launcher, or provider runtime. Use private owner-
  controlled paths; mode checks do not defend against hostile same-UID path
  replacement. SQLite files are not encrypted and direct filesystem access
  is outside this access-control boundary.


### Populate a guarded profile with text (rebuild-013; Linux)

Stop the guarded listener and wait for its process to exit. Run
`preview-guarded-legacy-json --source <messages.json> --directory
<guarded-native> --platform <platform> --account <exact-account>`, then run
`import-guarded-legacy-json` with the same arguments followed by
`--confirm <preview-digest>`. Each command requires exactly one
newline-terminated JSON record with a `passphrase` field in stdin, then EOF.
The trusted operator app should use an anonymous private pipe and close it
after writing the record. Never write the passphrase as a shell literal,
command argument, environment variable, URL, bearer, or log. The preview
digest confirms the exact file bytes, target archive and account, not the
password. A fresh native directory created with `init-guarded-live` is required.

- The shared 008 parser accepts 1–100 stock Python Hub JSON text rows for
  one explicit platform/account and at most 512 KiB. No media/CSV, native
  archives or arbitrary metadata are copied. Both preview and commit require
  password authorization; one verification attempt per command. This is
  not a persistent rate limiter across separately invoked operator commands.
- Commit is a single SQLite transaction under the checked archive's exclusive
  local file lease. Same-input retries return the existing receipt; changed
  or conflicting rows fail without overwriting or partially importing.
  No journal/SSE events, provider, credential import, background operation,
  or HTTP writer. Restart the guarded listener and refresh the desktop;
  after restart the listener is locked until explicitly unlocked.
- The guarded listener and import commands both acquire the exclusive lease.
  A running current listener/second importer is refused rather than waited
  on. Use only trusted local filesystems supporting advisory flock. Stop any
  previous release manually; older executables or arbitrary direct SQLite
  tools do not participate. File paths and authenticated database instance
  are rechecked. Access control is not at-rest encryption or a defense
  against hostile same-user file manipulation.

### Opt into a guarded legacy HTTP wire adapter (rebuild-014)

`serve-guarded-legacy` is a separate guarded listener offering four old
read-only paths for a **native client**. It requires explicit
`legacy.http.read` alongside both scoped message/conversation read grants.
See [LEGACY-HTTP.md](LEGACY-HTTP.md) for fields, query bounds, and the
intentional incompatibilities. No browser cookies or static frontend are
served, and the ordinary guarded listener is unchanged.


Rebuild-015 adds the original `/api/search`, `/api/contacts`,
`/api/context`, and `/api/quoted-message` paths to that opt-in adapter.
Those are the pinned Python literals, not the native `/api/v1/messages/*`
names. The same scope and transport rules apply. The current guarded text
import does not populate contacts or provider quote metadata.

### Restore one account from a detached legacy v9 index (rebuild-016; Linux)

Stop any guarded runtime and create a **fresh empty** protected archive
with `init-guarded-live`. Use the legacy application's consistent backup
to produce a detached `index.sqlite3`, outside either live profile. Then
run `preview-guarded-v9` with `--source <index.sqlite3> --directory
<guarded-native> --platform <platform> --account <exact-account>`. Run
`import-guarded-v9` with the same arguments and
`--confirm <preview-digest>`. Both processes use the same trusted
anonymous private password pipe and EOF as the guarded text import.
Never put the password in arguments or URLs.

- Linux bounded migration: detached v9 schema and integrity checked,
  source bytes and account fixed by confirmation, no sidecars/active
  profile, up to 128 MiB of input, 1,000 chosen message rows, 256 rows per
  other selected table, and a conservative 4 MiB projection budget.
  Trusted operator-owned paths and non-concurrent source editing apply.
- Preserves normalized conversations, messages, contacts, scoped identity
  links, conversation notes, ordered tags and archived flags. For WeChat
  only the two recognized canonical decimal message/quote keys survive;
  no media URLs, avatar paths, arbitrary extras, provider settings,
  credentials, sync/import history, attachments or write queues are copied.
  Linked IDs remain private; the legacy contact HTTP projection omits them.
- This is a one-account first load into an empty guarded archive, not a
  multi-account restore. The native target is exclusive throughout
  password verification, source validation and commit. The entire import
  is atomic and emits no event/SSE. An exact retry returns the same
  receipt but does not append or overwrite. After successful v9 migration
  no later text or other v9 import can add history to that archive. If a
  guarded JSON import happened first, the v9 command refuses to merge.
- The guarded legacy contacts and quote endpoints can now read these
  migrated normalized records after unlock with appropriate account
  grants. Encrypted at-rest storage, real provider integration and the
  original desktop/browser launcher are still not part of this mode.

### Encrypted backup and guarded restore (rebuild-017; Linux)

Stop the guarded listener first. Create a separate private output parent
(`0700`) outside live profiles, and use a trusted native parent process
with anonymous stdin/stdout pipes. To create a new encrypted file:

```text
create-guarded-backup --directory <guarded-profile> --output <new-file> --platform <platform> --account <exact-account>
```

Each command reads exactly one newline-terminated JSON frame and EOF;
it contains `profile_passphrase` and `backup_passphrase`. Keep these
independent, private and out of shell literals, argv, URLs or logs. The
profile passphrase authorizes the offline source; the backup passphrase
encrypts the account projection. Neither passphrase, the existing profile
verifier, grants, events, credentials, queues, media nor raw metadata are
copied into the backup. Literal message/note contents are not scrubbed.
Snapshot payload caps are the same as rebuild-016
and the encoded plaintext cannot exceed 4 MiB. This is not ZIP/SQLite
or a full-machine/profile backup.

```text
restore-guarded-backup --source <encrypted-file> --directory <nonexistent-new-guarded-directory>
```

On restore, `profile_passphrase` is a **new** profile secret, different
from the backup passphrase. The restored runtime uses a fresh generation,
and starts closed; supply a separately provisioned desktop grant file.
Both ciphertext and complete restored directory publish atomically without
replacement in the checked private parent. The archive payload is validated
after authentication, before staging.

Version 1 uses **AES-256-GCM** with a random 96-bit nonce and a 128-bit
salt for **PBKDF2-HMAC-SHA256** (600,000 rounds). The exact 48-byte header
is authenticated associated data: magic `LMHENC01`, four version/KDF/AEAD/
reserved bytes `01 01 01 00`, big-endian unsigned 32-bit rounds and
ciphertext+tag length, then 16 salt and 12 nonce bytes. A 16-byte tag
follows encrypted account data. The public ciphertext length is visible.

Only the **backup file** is encrypted. Running/restored SQLite remains
plaintext inside its private guarded directory. Restoration constructs the
database inside a hidden mode-0700 private staging directory before final
publication. Normal failure removes staging; abrupt failure may leave
private plaintext staging to clean up. No secure-erasure claim. Put the
private restore parent (including its scratch) on protected storage when
required. Backup creation never writes a plaintext export to disk.
`parent_synced:false` means publication happened but final parent sync
did not verify crash durability. Use trusted local path hierarchies, not
a hostile same-user filesystem.

#### Preview and verify the exact capsule before restoring it

```text
preview-guarded-backup --source <encrypted-file> --platform <platform> --account <exact-account>
restore-guarded-backup --source <encrypted-file> --directory <new-guarded-directory> --confirm <ciphertext_sha256>
```

Preview uses one bounded newline-terminated JSON frame with only
`backup_passphrase` on the private stdin pipe, followed by EOF. It checks the
authenticated capsule, exact requested account/platform and same payload
validation as restore. On success, it returns the SHA-256 and byte count
of the entire untouched ciphertext file, not the account, plaintext, rows,
or path. No directory, database, or plaintext staging file is created.

Passing the reported lowercase SHA-256 as `--confirm` makes restore require
the exact reviewed ciphertext before decrypting or staging. Restore still
requires both independent passphrases and a nonexistent private destination.
The digest is neither a credential nor a signature. It does not bind a
destination path, and a preview does not reserve a target or guarantee
publication. When present, a successful restore returns that same digest
and the existing `parent_synced` flag. The original command without
`--confirm` remains available for compatibility. A lost report or a
post-publication sync warning is not an instruction to retry into a different
directory; reconcile the already selected target first. This is not a
legacy HTTP upload, live restore, or media merge. Source only until unified
verification.

If the restore report was lost, use the exact destination and reviewed digest:

```text
status-guarded-backup-restore --directory <guarded-directory> --ciphertext-sha256 <ciphertext_sha256>
```

Stop its guarded listener. Send one bounded JSON frame with only
`passphrase` (the destination profile passphrase) through private stdin,
followed by EOF. This acquires the existing exclusive guard and reads only
the exact transaction receipt. A positive result shows that this archive
contains the recorded import of that ciphertext. It is not a row-by-row
verification or proof of the original publication's final parent sync.
A false result on an otherwise valid profile means the exact receipt is
absent. Missing paths, invalid identity and authorization or storage errors
are errors, not negative receipts. No credentials, message data, or path
are returned. The query does not modify archive rows or unlock a server.
SQLite may create private WAL and shared-memory coordination files.

### Account-only annual activity report (rebuild-018)

`GET /api/v1/analytics/annual?year=2026` requires a desktop grant with one
exact account scope and `analytics.annual.read`. Omitted platform/account
filters resolve to that scope; an explicit different scope is refused.
Use `conversation_id` to select a single conversation in the same account.
An explicit year in 1970–9999 is required. This route works on native live,
detached snapshots and the normal guarded listener after unlock. The
legacy opt-in listener uses `GET /api/reports/annual` and additionally
requires its usual legacy permissions. The later pair-report endpoint
requires its own grant; annual permission never authorizes it.

A fixed UTC+08 calendar produces counts, sent/received totals, monthly
and hourly activity, busiest day/hour, Sunday-first heatmap, and longest
active-day streak. Ties favor the earliest slot or streak. A revised
message is counted once at the current stored timestamp, not twice
from an event journal. No message bodies, names/contacts/rankings,
arbitrary message-type labels, file references, or provider details are
queried or returned. Calculation is capped at 200,000 matching rows;
an overflow returns no partial report. This is a privacy-limited
projection of the old annual report, not all analytics or full parity.


### Private-conversation activity report (rebuild-019)

`GET /api/v1/analytics/pair` (or guarded opt-in `GET /api/reports/pair`)
requires its own `analytics.pair.read` permission and an exact-account
desktop grant. Supply all four query fields: `platform`, `account_id`,
`conversation_id`, and `year` (1970–9999). The exact platform/account gate
runs before any archive lookup. A missing conversation returns 404; group
conversations are not accepted. The legacy adapter still requires its
usual three additional read grants. Its shared reports flag means at least
one report is available; check the explicit `annual_activity_enabled` and
`pair_activity_enabled` settings for each.

Activity uses a fixed UTC+08 calendar, sender **role** counts, hourly
buckets, active days, and current stored rows. Reply metrics count adjacent
incoming-to-self transitions in the same private conversation and selected
year only, for inclusive delays of 0 through 3,600 seconds. Equal timestamps
use the archive's local row order; one-account v9 import preserves relative
row order, while re-created data and text imports may have a different
order. Average seconds round to nearest integer with ties to even. More
than 200,000 matching rows returns an error, not a partial result.

This deliberately differs from the stock report if a conversation has
several incoming identities: Python uses the selected/dominant
counterpart, while Rust uses role transitions regardless of identity
(`response_basis: "adjacent_incoming_to_self"`). No caller-selected
counterpart, conversation title, message text/types, contact IDs/names,
counterpart ranking or hidden peer attribution is read or returned.

### Write-only source plugin (rebuild-020)

Use a distinct operator-configured, write-only grant for each normalized source.
Load it on the existing private native-live runtime; it does not authorize
starting a process or connecting to any provider, client account or cache.

```json
{
  "id": "example.source",
  "kind": "scoped-producer",
  "permissions": ["messages.ingest"],
  "token_sha256": "<SHA-256 from new-plugin-token>",
  "account_scope": {"platform": "qq", "account_id": "synthetic-account"}
}
```

- With that source token, GET `/api/v1/manifest` advertises its own scope and only
  the ingest resource. The public contract (OpenAPI 1.3.0) is also available
  with this token. It cannot read stored messages, status, analytics, events,
  attachments, metadata or MCP data. API/grant protocol majors stay at 1.
- POST `/api/v1/ingest` with a nonempty idempotency key and 1–100 normalized
  records, each with explicit, exact case-sensitive platform and account.
  Missing/foreign scope is never filled in. Any mismatch denies the whole
  batch before storage. Normal transport/decoded limits remain 530/512 KiB.
  Do not pass provider URLs/files or credentials in a normalized record.
- A successful atomic write returns counts and the account-local incoming
  event high water (no gaps from other accounts; not a desktop cursor).
  It can include another source's events within the same authorized account.
  Incoming creations/revisions advance that journal; own messages do not.
  Existing older archives without the account event index require the
  explicit upgrade documented above. A native exact desktop grant with
  `events.read` is separate and uses its own checkpoint/opaque cursor.
- Retry the same key with the same decoded normalized records in the same
  order after an uncertain write, including after restart. It returns the
  original receipt, not a new checkpoint. Changing content under that key
  returns 409. Grant ID, exact scope and archive define the namespace, even
  after token rotation. Do not reuse the same ID within a scope for an
  unrelated source. The source can revise only its own normalized records.
  The same ID under trusted `producer` does not confer scoped ownership or
  receipt continuity: existing trusted records are not adopted by a new
  scoped grant. `producer` remains an explicit **unscoped trusted** role for
  operators who need it, not for restricted plugins.
- This is a local feed boundary, not a QQ/WeChat/other platform connector,
  credential manager, synchronization service or message-sending feature.

### Source adapter resume (rebuild-021)

An exact `scoped-producer` on native-live can use a second ingest lane that
atomically advances its own checkpoint with the records. Existing
`POST /api/v1/ingest` is unchanged and never advances this checkpoint. The
manifest advertises this lane in its optional root-level `source_resume`
only to scoped producers, preserving the old `resources` object. Other clients
do not gain access. Both lanes share message ownership and idempotency
keys: do not use the same key for different operations.

1. Use the source bearer in the Authorization header to GET
   `/api/v1/source/checkpoint` without query parameters. Its own sequence
   starts at 0. Persist and forward the returned revision verbatim; it is
   a compare-and-swap marker, not a credential, journal cursor or storage
   for upstream tokens. No other owner/account/instance's revision is valid.
2. After obtaining the next complete source batch, POST
   `/api/v1/source/ingest` with
   `{"batch":{"idempotency_key":"my-batch","messages":[...]},
   "advance":{"expected_revision":"<revision from GET>","sequence":123}}`.
   The source defines the sequence units (e.g. a monotonic adapter counter);
   integers must be strictly increasing and at most 9,007,199,254,740,991.
   Only the source knows how that sequence maps to its local records.
3. Keep the attempted key, batch and checkpoint metadata unchanged if
   retrying an uncertain outcome. The original result is returned even
   after later successful advances. New keys with stale revisions or
   non-increasing sequences return `409 source_checkpoint_conflict`;
   refresh, reconcile the local sequence, and don't blindly skip records.
   Changing a previous key's records/advance metadata instead returns
   `409 idempotency_conflict`. Either failure leaves resume metadata and
   messages unchanged. No zero-message advancement.

Older ordinary Rust archives with the account-event upgrade can adopt this
path: reading initial state installs nothing and the first successful
write adds the progress tables transactionally. Old ordinary ingest
ignores this metadata, so keep coordinated writes on the new lane.
This is not rollback/clone detection: an exact filesystem copy retaining
the same archive instance and grants retains the same checkpoint namespace.
New archives and guarded backup restores do not inherit it. No upstream
connector, file/cache reader or external synchronization is included.

### Optional source SDK (rebuild-022)

The portable release includes a [reference one-batch source broker](source-sdk/README.md)
and synthetic adapter. They exercise the native Rust resume API from a
trusted local executable without giving the executable its Hub token or
broker configuration. The broker is optional Python stdlib tooling, not
a dependency of the Rust backend and not an OS sandbox or provider connector.

### Detached Telegram Desktop text import (rebuild-023)

The Rust CLI handles a bounded subset of the official
[Telegram Data Export Schema](https://core.telegram.org/import-export).
Use the Telegram Desktop **JSON** export and give Hub only the detached
`result.json`; keep its folder private. This is not a Telegram login, live
source, backup restore, or HTML/media import.

```sh
local-message-hub-rs preview-telegram-json --source /private/result.json --directory /private/native --account user123
local-message-hub-rs import-telegram-json --source /private/result.json --directory /private/native --account user123 --confirm <exact-preview-digest>
```

- `user123` is an example; supply the canonical `user<N>` identifier of the
  original Telegram account. Full export requires matching
  `personal_information.user_id`; those fields themselves are not imported.
  For a single-chat export, the owner is an explicit operator assertion (exports
  are not signed and cannot authenticate provenance). Personal-chat rows must
  come from the selected owner or that chat's peer. Group direction uses exact
  `from_id == user<N>`, not a caller's `is_outgoing` flag.
- Supports `personal_chat`, `private_group` and `private_supergroup` in a
  single Chat or full `chats.list`. Namespaces are respectively
  `tg:user:<id>`, `tg:chat:<id>`, and `tg:channel:<id>`; appending the
  message number keeps equal IDs across peer types distinct. Stores seconds
  from `date_unixtime` without guessing time zones from the local date.
  Concatenates plain and formatted visible text in order and discards
  formatting/link targets. If both text representations are present they
  must agree.
- Skips media, captions, service rows, empty text, unsupported fields/types,
  and unsupported chat types. Does not open path references or links, read
  adjacent files, copy profile/contact/session/attachment metadata, or fetch
  anything over the network. Supported rows with malformed IDs, author or
  timestamp fail the whole preview instead of silently misattributing them.
  Limits: 512 KiB source and normalized batch, 32 source chats, 1,000 source
  rows, at most 100 imported rows; not a whole-account large-export tool.
- Preview returns counts and a digest bound to exact bytes, archive and
  account; it writes no messages. Commit is atomic and append-only:
  unchanged duplicates are accepted under the same source owner, changes
  or foreign ownership conflict, and exact retry returns the same receipt.
  Archive must be ordinary Rust-native, not a guarded profile or legacy
  index. No desktop SSE or MCP incoming event is generated; refresh views
  after import. No source file is changed.
- The older Python Telegram adapter is not a drop-in match. Its
  single-chat text/time/sender normalization is the comparison oracle;
  it additionally accepts some dates/attachments, uses raw conversation
  IDs and explicit outgoing flags, and returns row-level error lists.

### QQ text CSV exported by the old Hub (rebuild-024)

Reimports a detached **QQ message CSV exported by Local Message Hub**. This
is **not** Tencent QQ's native backup, QQChatExporter/QCE JSON, an app cache,
a provider or a live sync mode. It only reads the supplied CSV file.

```sh
local-message-hub-rs preview-qq-hub-csv --source /private/messages.csv --directory /private/native --account exact-profile --self-sender exact-sender-id
local-message-hub-rs import-qq-hub-csv --source /private/messages.csv --directory /private/native --account exact-profile --self-sender exact-sender-id --confirm <exact-preview-digest>
```

- Use the 13-column legacy **message** CSV, UTF-8 with optional BOM, quoted
  comma/quote/newline cells, and complete LF or CRLF record endings. Missing,
  extra, truncated or malformed records fail closed. Limits: 512 KiB file
  and normalized batch, 1,000 source rows, 100 imported rows. Target must
  be ordinary Rust-native; the original file is unchanged.
- Every row, even one that will be skipped, must name `qq` and the explicit
  exact account. The export has **no outgoing flag**. Verify your sender
  ID from a known message you sent; supply the literal `发送者ID` value, not a
  nickname or guessed account mapping. It must appear in at least one imported
  text row. Missing/ambiguous sender IDs on imported rows fail; other sender
  IDs are classified as incoming. If you cannot verify this mapping (or your
  file contains no known outgoing text), use the explicitly normalized JSON
  import with the correct per-row `is_self` instead. CSV itself does not
  authenticate ownership or attest which person a sender ID represents.
- Keeps exact message/conversation IDs and serialized text. UTC+08 calendar
  values become Unix seconds; empty timestamp is the old renderer's 0/missing
  sentinel. Empty optional title/name become null. CSV spreadsheet-protection
  apostrophes are preserved: a literal leading apostrophe and an inserted
  one cannot always be distinguished. No formula or link is evaluated.
- Non-text, empty text, and rows declaring media/file/attachments are skipped.
  No media fields, paths or adjacent files get opened or stored. Preview
  exposes only counts and a digest of the bytes, archive, account and sender
  mapping. The commit is an atomic append; exact retries retain the original
  receipt. Existing identical normalized legacy JSON records dedupe when
  all retained fields match; old/foreign ownership or changed content
  conflicts and rolls the whole batch back. The CSV's lossy fields
  (including apostrophes and sender direction) can cause such conflicts.
  No live SSE/MCP events; refresh views after import.

### Detached plain-text email (rebuild-025)

Import one detached `.eml` file with the Rust CLI. This is a restricted
[RFC 5322](https://www.rfc-editor.org/rfc/rfc5322.html) /
[MIME](https://www.rfc-editor.org/rfc/rfc2045.html) subset, **not** mailbox
access, IMAP/SMTP, MBOX, or a replacement for the old full email adapter.

```sh
local-message-hub-rs preview-eml --source /private/message.eml --directory /private/native --account mailbox@example.invalid
local-message-hub-rs import-eml --source /private/message.eml --directory /private/native --account mailbox@example.invalid --confirm <exact-preview-digest>
```

- Supply one canonical mailbox. Scope is accepted only if that address is
  the single simple `From` or single simple `To` in the file. An exact From
  match marks the message as outgoing. Local part is case-sensitive; domain
  is lower-cased. No aliases, Cc/Bcc-based attribution, lists, complex
  address headers or ownership inference. Verify the detached file and
  mailbox yourself: these self-declared headers are **not** account login
  or sender-authenticity proof.
- One single-part `text/plain` message, ASCII or UTF-8 body, 7bit/8bit,
  Base64 or supported quoted-printable. Requires a single literal Message-ID
  and valid Date with known numeric offset; uses the first References ID,
  then In-Reply-To, then Message-ID as the thread root. Subject is a simple
  ASCII header. The parser refuses duplicate routing headers, ambiguous
  formats, HTML, multipart, attachment dispositions, unsupported encodings
  and missing identifiers instead of guessing. Limits: 256 KiB file,
  16 KiB headers, 64 KiB decoded text.
- Stores only the normalized message and its thread: explicit account,
  hashed account-specific message/thread IDs, sender, UTC timestamp,
  subject and stripped plain body. Recipient lists, unpublished headers,
  remote links and paths are never interpreted or used for I/O. Nothing is
  sent, deleted or marked read. The old Python parser additionally handles
  multipart/HTML, fallback IDs/dates and attachment metadata, and prefixes
  headers to the message body; this privacy-minimized path does not.
- Preview returns only the count and a digest bound to the exact bytes,
  archive and mailbox. It writes no rows. Commit is append-only and atomic,
  copies no files, and exact retries return the original receipt. Another
  file with the same Message-ID is unchanged only if retained fields agree;
  a conflicting row is never rewritten. Ordinary Rust archive only; there
  is no live SSE/MCP event (refresh after importing).

### Synthetic read-only IMAP loopback fixture (rebuild-026)

This is a native Rust **protocol fixture**, not a real mailbox connector.
The CLI can talk only to IPv4 loopback and a test server using the exact
synthetic PREAUTH greeting with a hash of the explicit mailbox, not general
IMAP, DNS discovery, STARTTLS, login or OAuth. No production mailbox or TLS
profiles/keys are shipped. Use the synthetic integration harness for examples.

- Commands: `preview-imap-fixture` and `import-imap-fixture`. Both take
  `--directory`, `--port`, `--ca-der`, `--leaf-sha256` (lowercase hex),
  `--account`, `--uidvalidity`, `--after` (previous UID; initial 0) and
  `--uid` (explicit larger UID). Import additionally takes
  `--confirm <exact-preview-digest>`.
- Socket is hard-coded to `127.0.0.1`, TLS 1.3 only. Trust exactly the
  operator's detached fixture CA, verify SAN `hub-imap-fixture.localhost`
  and the independently confirmed leaf SHA-256, then verify the exact
  mailbox marker before sending any IMAP command. Test certificates are
  ephemeral and generated in a private temporary directory. The fixture
  mailbox marker is an assertion from the pinned peer, not mail auth.
- Only `EXAMINE INBOX` with a confirmed read-only response and one exact
  numeric `UID FETCH ... (UID BODY.PEEK[])` are sent. See
  [RFC 9051](https://www.rfc-editor.org/rfc/rfc9051.html) for these read-only
  semantics. This checks the singleton response, exact UID, complete
  literal, and UIDVALIDITY before reading any body. Maximum literal 256 KiB;
  existing 025 strict plain-text, scope and MIME limits still apply. No
  SEARCH, enumeration, credentials, SMTP, STORE, SELECT, mark-read, deletion
  or automatic sync. Operator-selected UID gaps are not checked for
  completeness, and this is not a general-purpose wire implementation.
- Preview writes no rows or progress. Confirmation binds fetched bytes,
  archive, pinned mailbox identity, epoch, previous UID and selected UID.
  Import fetches the same current bytes again and atomically advances the
  cursor (leaf/mailbox/archive scoped) with the normalized EML row and
  receipt. Identical EML content already in the same offline owner can
  advance the cursor unchanged; conflicts leave both unchanged. Exact
  retry re-fetches the fixture and returns its original receipt if the
  bytes still match. UIDVALIDITY change fails closed; no automatic reset,
  and cursor state is not portable through guarded backup or to a new archive.
  No live SSE/MCP event is generated.

### Local conversation drafts (rebuild-027)

An inert plain-text scratchpad per existing conversation in an ordinary
Rust native-live archive. It cannot queue, approve or send anything, and
has no provider credential or delivery integration. It is not the old
server's outgoing request/approval workflow.

- For the exact-account desktop plugin grant, add `conversations.read`
  and `drafts.read`; additionally add `drafts.write` only for editors.
  Authorized manifests add `local_drafts: {url, can_write}`. Same-account
  authorized clients share the conversation's draft, but not retry receipts.
  Guarded and snapshot modes do not offer drafts.
- GET `/api/v1/drafts?conversation_id=<exact-id>` returns
  `{"revision":0,"body":null}` for a conversation with no edits. Scope
  comes from the grant; explicit mismatches and unknown conversations
  are identically not found. There is no all-account or all-conversation
  draft listing. Treat returned body as literal text, not HTML or code.
- POST `/api/v1/drafts` with
  `{"target":{"conversation_id":"<exact-id>"},"expected_revision":0,
  "request_id":"<new-local-edit-id>","body":"<text>"}`.
  Body is up to 16 KiB UTF-8. Send an explicit `null` to clear it; this
  keeps the revision tombstone so stale clients cannot recreate it.
  Revision mismatches conflict, equal desired state at the correct
  revision leaves it unchanged, and the same grant ID/scope/key and
  exact original edit return their original redacted receipt
  `{"revision":1,"present":true}`, even after later edits or token rotation.
  A repeated key with different data conflicts.
- The slot, receipt and local audit are one transaction. Audit records
  only scope, conversation, revision, fixed operation and time; the body
  is never in audit, receipts or errors. The private archive still stores
  the current text and opaque comparison hashes for retries, and clearing
  is not secure erasure of SQLite history. Drafts do not appear in message
  export, legacy routes, events or guarded backup. No provider is invoked.

### Offline Campus index (rebuild-028)

A local index for **operator-curated public SYSU article text**. It never
fetches URLs, reads private feeds or browser caches, uses student/login
credentials, starts background jobs, or calls a scoring model. Bookmarks,
read-state, profiles for relevance, and legacy campus routes are not
implemented here.

- Use a detached JSON file following `campus-detached.schema.json`:
  `{"version":1,"profile":"local-campus","articles":[...]}`. Each article
  requires `url`, `title`, `source_id`, `source_name` and `category`, and
  optionally accepts `published_at` (YYYY-MM-DD), `summary` and `content`.
  Omitted dates stay empty. Summary and content are supplied text, not
  generated. Only import public material you have verified and can retain.
  A well-formed URL is not proof that a file genuinely came from the source.
- Only HTTPS `sysu.edu.cn` or its subdomains with an article path, no
  userinfo, query, or nonstandard port. Normalize host/path and remove
  fragments; article ID is SHA-256 of the canonical URL. The URL is an
  inert identifier/provenance string. Render fields as plain text, not
  HTML, and never have a client automatically visit any item URL.
- Seed an ordinary Rust archive using `preview-campus-json --source <file>
  --directory <native-directory> --profile <local-campus>`, then
  `import-campus-json` with the same arguments and
  `--confirm <exact-preview-digest>`. The document and explicit profile
  must match. Inputs: ≤512 KiB, up to 100 articles per batch, up to 4,096
  per profile, summary ≤2 KiB and content ≤16 KiB UTF-8. Preview does
  not write rows. Commit is append-only: identical records dedupe, a
  conflicting canonical ID rolls back the whole batch, exact retries
  return the original receipt. No message/SSE/MCP entries are produced.
- Give a native caller its own `campus-reader` grant with explicit
  `campus_profile` and only `campus.read`. It cannot read ordinary archive
  or other profiles. GET `/api/v1/campus/items` lists and searches that
  profile (title, summary, content and source name; lowercased literal
  matching), bounded to 50 items with optional category, source, limit
  and offset. Order is descending date and ascending ID for ties. GET
  `/api/v1/campus/items/{id}` includes the detached text. Summary pages omit
  article bodies. Foreign profiles and absent items are both not found.
- The old Campus `/api/campus/items` store/Handler were used to compare
  shared offline projection, common search inputs and deterministic order.
  The Rust index does not emulate old relevance/score, global state,
  bookmark, WeChat, updates or fetch routes. Manifest capability flags
  explicitly report unsupported features. Not a legacy browser adapter.

### Offline Campus bookmarks and dated digest (rebuild-029)

The `campus-reader` grant stays read-only. To let an operator set or clear
bookmarks, issue a **separate** `campus-editor` grant for the exact profile with
only `campus.read` and `campus.bookmarks.write`. It cannot read or write chats,
approve or send anything, or trigger retrieval/scoring. Both campus roles
discover these resources under `campus_local_state`; the older
`campus_offline` discovery object is unchanged for legacy native clients.

- GET `/api/v1/campus/bookmarks` returns up to 50 bookmarked article
  summaries from that profile, sorted by existing publication date then ID
  (descending date, ascending ID; optional `limit` and `offset`).
  GET `/api/v1/campus/bookmarks/{id}` returns the article's boolean and
  revision. An existing article initially has false and revision 0; a
  missing or explicitly foreign article/profile is not found.
- POST the exact item route with `{"bookmarked":true,"expected_revision":0}`
  or with false to clear, supplying the observed revision. The boolean
  must be explicit; this is not a toggle. The edit and private audit
  commit together; audit keeps only profile, article ID, revision,
  boolean and timestamp. Same-state updates at the current revision
  do not append audit. A stale revision returns 409; fetch current
  state before retrying, including if a response was lost. Unsetting
  keeps a revision tombstone across restarts to prevent stale restores.
- GET `/api/v1/campus/digest?date=YYYY-MM-DD` returns only articles with
  that exact imported publication date, sorted by ID. A missing,
  impossible, or zero-year date is rejected; undated records are never
  promoted. Date, total and sorted category counts are deterministic;
  the items page is bounded to 50. Each item contains its imported
  summary verbatim, or the empty string; no body fallback, grouping,
  ranking, model text or inferred deadlines.
- The old Campus item-state/briefing handlers were used to compare
  bookmark booleans and same-day material identity, not HTTP/auth or
  briefing parity. Those legacy paths have separate grouping/ranking and
  read-state behavior; this native implementation does not register them.
  There is still no fetch, private feed, model, autosync or legacy alias.

### Guarded browser HTTP foundation (rebuild-030)

A separate scoped, read-only loopback listener can issue cookie sessions
through a trusted native parent and private pipeline. It is independent
from the guarded bearer APIs and not an installed or tested browser UI.
See [the HTTP and isolated-cookie-jar contract](BROWSER-FOUNDATION.md).

Rebuild-031 adds an optional `--reader` to that guarded listener. It serves
three embedded first-party assets for a limited read-only archive harness,
not the old frontend or a desktop UI. See
[the opt-in mode and limitations](BROWSER-FOUNDATION.md#optional-embedded-read-harness-rebuild-031).

### Scoped export and imported attachment reads

`GET /api/v1/messages/export` is available with `messages.export` and
`messages.read` on an exact desktop grant in ordinary snapshot/native-live
and unlocked guarded-native modes. The separate bearer-only guarded legacy
listener also provides a partial `GET /api/export` alias; not the legacy
browser/cookie, multi-account exports or download/jobs protocol.
Choose one conversation and optionally narrow the date range. The service
builds the entire attachment before responding (1,000 rows, 64 KiB per
cell, 4 MiB response), writes nothing to disk, and returns 413 rather
than a partial file. Formats: JSON, CSV, TXT, Markdown, HTML and ChatLab
JSON/JSONL. CSV formula neutralization remains on; Markdown uses indented
code for data; HTML escapes untrusted values, emits no link or remote
resource and is a sandboxed attachment. ChatLab 0.0.2 supports one
group/private conversation and text/system rows only; unsupported kinds
are rejected. It emits no media/resource references, and its exportedAt
field records actual export time. Treat downloaded text as untrusted.

On Linux `serve --media-bundle`, `GET /api/v1/messages/media/{id}`
additionally supports a single byte range for the same exact account,
conversation and message authorization. The server checks the entire
8 MiB-or-smaller imported blob and its digest before returning any bytes,
even for small ranges. Accepted forms: `bytes=first-last`,
`bytes=first-`, `bytes=-suffix`; bad/unsatisfied/multiple ranges yield
416 only after authorization and integrity, including the full length in
Content-Range. With If-Range, the server returns the full verified object,
since it does not issue a representation validator. No provider URL,
arbitrary path, multipart response or old media resolve alias is supported.


### Other code-first native additions

- [Detached platform imports](DETACHED-PLATFORM-IMPORTS.md): one explicit
  rendered WeCom conversation text import, plus count/fingerprint-only WeChat
  provider page preview. No provider connection or raw client extraction.
- [Manual local import jobs](LOCAL-IMPORT-JOBS.md): exact native-live grant,
  bounded detached bytes; durable prepare/status/confirm/cancel without a
  background worker or external effects.
- [Local notification preferences](NOTIFICATIONS.md): scoped settings and
  pull preview, no OS notifications, notification delivery or background loop.

The rebuild-036 additions are a source checkpoint pending unified validation.

Rebuild-037 adds code-first [grouped local exports](GROUPED-EXPORTS.md),
[account-scoped chat analytics](CHAT-ANALYTICS.md),
[manual source job state](SOURCE-JOBS.md), and
[macOS/Windows portable wrappers](packaging/README.md) for a separately
supplied core binary. No packaged platform was built, signed or qualified.

Rebuild-038 is a source-only update: see [guarded status and scoped overview](LEGACY-HTTP.md),
[relationship analytics](RELATIONSHIPS.md), [offline guarded backup catalog](GUARDED-BACKUP-CATALOG.md),
[private guarded backup retention planning](GUARDED-BACKUP-RETENTION.md),
[local provider configuration planning](PROVIDER-PROFILES.md), and the
[legacy HTTP route backlog](LEGACY-ROUTE-BACKLOG.md). Provider plans do not
connect to any provider, and the encrypted catalog has no HTTP alias.

Rebuild-039 is a source-only checkpoint. The separate guarded bearer listener
adds [partial analytics aliases](LEGACY-HTTP.md). Native modes also add
[explicit imported-media asset bundles](RESOURCE-JOBS.md),
[imported Campus catalog reads](CAMPUS-CATALOG.md) and
[redacted local operation status](OPERATIONS.md).

Rebuild-040 is a source-only checkpoint: [local human draft review](DRAFT-REVIEW.md)
records an explicit local operator annotation but never sends; [exact-grant UI
preferences](CLIENT-PREFERENCES.md) cannot modify provider or security settings.
The [macOS snapshot supervisor](MACOS-SNAPSHOT-SUPERVISION.md) is source only
and not a platform qualification. No Windows supervision is implemented by
this update. See the [ordinary-live legacy literals](LEGACY-HTTP.md).

Rebuild-041 is source-only. It adds [scoped legacy incoming and artifact
aliases](LEGACY-HTTP.md), [detached public Campus WeChat article import](CAMPUS-WECHAT-DETACHED.md),
[Windows snapshot supervision](WINDOWS-SNAPSHOT-SUPERVISION.md) and an opt-in
[read-only IMAP probe](IMAP-READONLY.md). These are separate code paths, not
provider enablement, tested protocol compatibility, Windows qualification or
runnable platform distributions. No live account/network was exercised.

Rebuild-042 source includes [bounded metadata and imported media compatibility](LEGACY-HTTP.md),
[manual voice annotations](MANUAL-TRANSCRIPTION.md),
and a [desktop connection SDK](DESKTOP-CONNECTION-SDK.md). The SDK only
constructs and validates typed local settings/descriptors; it does not
start a process, make HTTP calls or approve a provider. The release is
not built or tested.

The [explicit IMAP import](IMAP-READONLY-IMPORT.md) is a separate opt-in
Linux/macOS operator workflow for one verified UID, preview and confirmed
append only. Neither that source code nor the local integrations connect,
authenticate, schedule sync, or operate on providers unless a trusted
operator separately runs the corresponding workflow. No server was used
for this source checkpoint.

Rebuild-043 stays source-only: [scope-safe MCP and backup discovery and the
redacted import job list](LEGACY-HTTP.md), [manual offline Campus event groups](CAMPUS-EVENTS.md),
[operator-only draft handoff](DRAFT-HANDOFF.md) (not delivery), and local
[portable package provenance staging/verification](PORTABLE-PROVENANCE.md).
No provider, backup HTTP controller, executable verification, real data,
or binary packaging was exercised for this source checkpoint.

Rebuild-044 is a source-only checkpoint: [separate scoped Linux MCP HTTP
subset](MCP-HTTP-SUBSET.md), [manual exact-account export presets](EXPORT-PRESETS.md),
[private guarded session status/revocation](BROWSER-FOUNDATION.md),
and [explicit normalized detached WeChat text import](WECHAT-DETACHED-IMPORT.md).
It does not claim browser/SDK interoperability, stock automatic exports,
raw WeChat cache compatibility or executable platform support.

Rebuild-045 is source only. See [local scope and pending unified verification](LOCAL-READINESS.md),
[exact imported attachment references](LEGACY-HTTP.md),
[typed native desktop loopback client](DESKTOP-LOOPBACK-CLIENT.md),
[manual outbound packet acknowledgements](DRAFT-HANDOFF-RECEIPT.md)
and [inert Campus source visibility](CAMPUS-SOURCE-PREFERENCES.md).
Nothing here enables provider credentials or proves external delivery.

Rebuild-047 continues the original scoped local [WeChat / WeCom source
contracts](WECHAT-WECOM-INTEGRATION.md), using previously authorized data,
not new provider access. Personal WeChat accepts the pinned old Hub JSON
text export subset through the scoped source SDK; no extraction or client
invocation is part of this source milestone. The [WeCom rendered sync
bridge](WECOM-DETACHED-SYNC.md) preserves eligible pinned local rows and
tracks atomic local apply progress, not a provider cursor.

Rebuild-049 adds [scoped legacy notification settings](SCOPED-LEGACY-SETTINGS.md),
[manual voice lookup and local annotation handoff](MANUAL-TRANSCRIPTION.md),
and [deterministic local Campus category scoring](CAMPUS-LOCAL-SCORING.md).
No global settings, OS notifications, audio/model processing, or external
Campus scoring is enabled. This version is source only.

Rebuild-050 adds a scoped [imported media catalog](IMPORTED-MEDIA-CATALOG.md)
and the [local export schedule ledger core](EXPORT-SCHEDULE-CORE.md).
The ledger does not start background jobs, render files, or expose
automatic export commands. This release is source only.

Rebuild-051 adds [reversible local guarded backup visibility quarantine](GUARDED-BACKUP-QUARANTINE.md)
with confirmed catalog scope. It does not delete, move or restore backups,
or add HTTP, scheduled retention, or remote handling. Source only.

Rebuild-052 adds bounded [local export artifact rendering and atomic
archive publication](EXPORT-SCHEDULE-CORE.md) to the existing private
scheduler ledger. No timer, endpoint, command, external transfer, or
renderer admission surface was enabled. Source only.

Rebuild-053 adds an exact-profile [Campus display profile and visible
feed](CAMPUS-DISPLAY-PROFILE.md), with an explicit write permission, no
student profile fields, fetching, or changes to old feed routes. Source only.

Rebuild-054 adds an [explicit private Linux export operator](EXPORT-SCHEDULE-CORE.md)
for current scoped desktop grants. No timers, HTTP, external effects, or
actual run were enabled. Source only.

Rebuild-055 adds [selected messages plus verified imported attachments ZIP](SELECTED-MESSAGE-BUNDLES.md)
in Linux imported-media mode under exact Desktop export permissions.
It has no old route, automatic media discovery, provider reads, or
compatibility promise. Source only.

Rebuild-056 adds separately authorized and revocable [recurring local
export](EXPORT-SCHEDULE-CORE.md). Only an explicitly started, bounded
Linux foreground process may render a permitted due slot inside the
archive; no service, output path, automatic download, or delivery is
enabled. Source only.

Rebuild-057 adds native [one-shot source handoff](SOURCE-SYNC-HANDOFF.md)
for existing exact scoped jobs. It does not launch a process, perform sync
or implement the old global sync route. Source only.

Rebuild-058 adds [scoped notification metadata pull and acknowledgment](NOTIFICATIONS.md).
It is not a push, OS display/activation, or provider feature. Source only.

Rebuild-059 adds [detached text Moments](MOMENTS-DETACHED.md) for
authorized offline input with exact-account preview/confirmation and
separate scoped native read/export. It does not read app data or
compatibly replace the stock routes. Source only.

Rebuild-060 adds an explicit [claimed WeCom job to local import receipt
handoff](WECOM-CLAIMED-LOCAL-SYNC.md). It is not live provider work.
The independent authorized detached input is still required. Source only.

Rebuild-061 adds [private generated-artifact inventory and confirmed
retention](EXPORT-SCHEDULE-CORE.md). It cannot touch original messages,
backups or filesystem exports. See the [code-first convergence
checkpoint](LOCAL-READINESS.md#061-code-first-convergence-checkpoint).
Source only.

Rebuild-062 adds [explicit local Campus scoring materialization](CAMPUS-LOCAL-SCORING.md#explicit-local-materialization).
Only a matching current bounded rank result can be read; no remote model,
automatic work or legacy scorer route is enabled. Source only.

Rebuild-063 adds an explicit exact-account claimed personal-WeChat
detached-text import with an immutable atomic local receipt. It does not
launch or read a client, or advance a provider cursor. Source only.

Rebuild-064 adds optional [exact live references to imported media](LIVE-IMPORTED-MEDIA.md)
in ordinary Linux native-live mode. It also hardens detached regular-file
opening on macOS. No download, provider access, runtime checks or OS
qualification are implied. Source only.

Rebuild-065 adds [explicit bounded selected-UID IMAP imports](IMAP-READONLY-IMPORT.md#explicit-selected-uid-batch-065-source-only)
to the existing narrow pinned TLS operator flow. No account discovery,
automatic polling, mutation of mail, or actual connection was performed.
Source only.

Rebuild-066 adds a [disconnected WeCom Bot text protocol core](WECOM-BOT-TEXT-PROTOCOL.md).
It has no network transport, process/service/route, credentials or
delivery integration. No actual sending was attempted. Source only.

Rebuild-067 adds the private [WeCom Bot release-attempt kernel](WECOM-BOT-RELEASE.md)
for an exact reviewed draft and destination. No CLI, HTTP integration,
real transport or sending is enabled. Source only.

Rebuild-068 adds an exact-profile [Campus detached import source
registry](CAMPUS-SOURCE-REGISTRATION.md) with explicit revision-aware
controls. No remote fetch or changes to archived articles. Source only.

Rebuild-069 adds typed [scoped search and context to the backend
client](DESKTOP-LOOPBACK-CLIENT.md), including exact WeChat quote
context. No desktop UI or actual platform execution. Source only.

Rebuild-070 exposes typed [native notification pull and acknowledgment
in the backend client](DESKTOP-LOOPBACK-CLIENT.md) under its existing exact
account and capabilities. No GUI, OS notification, service run or live
delivery. Source only.

Rebuild-071 adds the [explicit private detached PCM WAV handoff](DETACHED-VOICE-WAV.md)
from a verified imported voice attachment. It does not launch a
transcriber, write annotations, or expose audio over HTTP. Source only.

Rebuild-072 adds a [typed backend client for local import jobs](LOCAL-IMPORT-JOBS.md)
under existing exact scoped permissions. It never opens a selected file
or retries a write on its own. No desktop UI. Source only.

Rebuild-073 adds a separately opted-in [small verified imported media
bytes path](LIVE-IMPORTED-MEDIA.md) behind an additional exact Desktop
grant and expected digest. No provider acquisition or Range. Source only.

Rebuild-074 adds source-only [ordinary macOS managed live support and
bounded owner-loss drain](MACOS-SNAPSHOT-SUPERVISION.md). Snapshot,
guarded and other operating-system modes retain their prior behavior;
no macOS execution or OS qualification was performed.

Rebuild-075 adds [exact-source handoff acknowledgement
recovery](SOURCE-SYNC-HANDOFF.md), never extending an existing queue
deadline or granting another execution. It completes the 066–075
source-only checkpoint group. No application, provider or tests run.
