# Local Message Hub MCP

`hub.mcp_server` is an independent MCP stdio process. It opens the
same `<storage>\hub.sqlite3` as the WebUI and does not start a full sync,
desktop shell or network listener. Platform readers are initialized only when
an attachment tool is explicitly called. Group rosters use an existing local
platform reader and do not trigger a sync; they fail closed until that reader
is connected. The Python standard library is the only runtime dependency.

## Run

From the repository root:

```powershell
integrations/wechat/runtime/venv/Scripts/python.exe -m hub.mcp_server --storage .local
```

`START_MCP.cmd` is provided for local manual launch. For Codex, add this entry
to `%USERPROFILE%\.codex\config.toml`, replacing both paths with absolute paths
on this machine:

```toml
[mcp_servers.local-message-hub]
command = 'C:\path\to\python.exe'
args = ['-m', 'hub.mcp_server', '--storage', 'C:\path\to\repository\.local']
cwd = 'C:\path\to\repository'
startup_timeout_sec = 20
```

The stdio process uses newline-delimited JSON-RPC and writes protocol messages
only to stdout. It never logs message contents. MCP clients should start one
process per connection; SQLite WAL and the store's busy timeout allow it to
coexist with the local WebUI.

Hermes reads MCP servers from `%USERPROFILE%\.hermes\config.yaml`. Add this
entry, replacing the paths with absolute paths on this machine:

```yaml
mcp_servers:
  local-message-hub:
    command: 'C:\path\to\python.exe'
    args:
      - -m
      - hub.mcp_server
      - --storage
      - 'C:\path\to\repository\.local'
    cwd: 'C:\path\to\repository'
    # Keep the client surface intentionally narrow. Write tools appear only
    # after the local Settings opt-in is enabled.
    tools:
      include:
        - search_messages
        - list_conversations
        - list_messages
        - get_message_context
        - get_quoted_message_context
        - list_contacts
        - list_group_members
        - list_media
        - get_message
        - get_attachment
        - list_qq_forward_attachments
        - get_qq_forward_attachment
        - list_sources
        - list_imports
        - list_private_chat_profiles
        - list_group_chat_profiles
        - get_annual_report
        - get_pair_report
```

See the [Hermes MCP feature guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)
and [configuration reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md)
for client-specific options. MCP search and message-list tools accept an
optional opaque `cursor`. A cursor is scoped to the original filters and uses
timestamp plus SQLite row id as a stable continuation key; reuse with different
filters is rejected. `offset` remains available for clients that need it.

## Tools and Permissions

- `search_messages`: cross-platform or platform-scoped keyword/date/type search.
- `list_conversations`: bounded conversation listing.
- `list_messages`: bounded messages from one platform/account/conversation.
- `get_message_context`: one message and up to 50 neighbors on each side.
- `get_quoted_message_context`: resolve a WeChat quote using only the exact
  indexed reference message, account, and conversation, then return the uniquely
  matched target and its bounded context. The caller cannot supply a raw WeChat
  source ID; ambiguous, missing, or cross-conversation targets fail closed.
- `list_contacts`: bounded contacts and groups, optionally restricted to one
  exact `account_id` within the selected platform.
- `list_group_members`: locally available names and role metadata for one exact
  indexed group. It requires a concrete platform, account, and conversation ID;
  the Hub verifies that the indexed conversation is a group owned by that
  account, then reuses its platform reader for filtering and pagination. Pages
  are capped at 100. It returns no message content and never initiates sync or
  queries enterprise-side archives; an unavailable reader is an error.
- `get_contact_avatar`: return an explicitly requested contact avatar using
  the exact `platform`, `account_id`, and `contact_id`. It reads only an image
  already present in the Hub's local avatar cache and returns it as MCP `image`
  content, with identity, MIME type, and byte count as text metadata. A missing
  cache entry fails; this tool never fetches a remote URL.
- `list_media`: bounded image/sticker/video/voice/file attachment metadata, optionally
  restricted to one exact account ID within a required concrete platform; including sender,
  conversation title, filename, indexed preview, platform media reference and
  `lmh://message/...` parent-message resource URI. WeCom attachments and QQ
  roaming resources attached to text messages are listed individually with a
  `media_entry.resource_index`; pass that index with the returned platform,
  account ID, and message ID to `get_attachment`. The listed attachment metadata
  omits source paths and URLs. Optional `start` and `end` accept inclusive
  UTC+08 calendar dates, matching the local media library filter. Moments and
  the QQ cache gallery remain available through their dedicated tools. This
  tool never returns attachment bytes.
- `get_message`: retrieve one message by platform/account/message ID. Contact
  and conversation list results retain indexed `avatar_url` references, and
  message `extra` can contain source-provided dimensions or parsed metadata.
- `get_attachment`: for one explicitly selected indexed image, video, voice, or
  file attachment, return only bytes already found in a local cache plus safe
  message metadata. Images and audio use MCP media content; video and files use
  a bounded embedded resource.
  The reader resolves media only within the configured platform cache
  (WeChat runtime media, WeCom's local decrypted output, or the Hub-managed QQ
  media/roaming cache), rejects traversal and symlinks, checks supported media
  signatures, and caps one attachment at 8 MiB. QQ MCP reads only files already in that cache;
  a cache miss never triggers QCE export or download. It does not fetch avatars
  or chat attachments from remote hosts. `resource_index` selects one attachment
  where a message has multiple attachments. Supported image/video/audio
  signatures are checked before MIME assignment; document bytes are returned
  unchanged and never opened, executed, or extracted. Cache misses never trigger
  source downloads.
- `list_qq_cache_resources`: page the authenticated QCE local QQ gallery by
  type/name; returns metadata and opaque IDs only, never QCE URLs or local paths.
- `get_qq_cache_resource`: return one explicitly requested gallery item by its
  opaque ID and exact account ID. The ID is valid only for the current QCE
  session/account; the Hub reuses its cache-root, signature, and 8 MiB checks.
  Images/audio use MCP media content; video/files use bounded embedded resources.
- `list_qq_forward_attachments`: for one indexed account, conversation and
  merged-forward message, returns safe metadata for up to 50 attachments plus
  short-lived opaque handles. No local paths, URLs or raw QCE bridge identifiers
  are returned.
- `get_qq_forward_attachment`: accepts one handle returned by the list tool and
  the same account/conversation/message identity. The local NapCat/QCE bridge
  revalidates that scope, downloads only the selected item into the Hub-managed
  forward cache, checks its signature, and returns at most 8 MiB as MCP
  image/audio/resource content. Handles expire after 10 minutes. It does not
  accept QCE IDs, URLs, paths, or bridge parameters from the MCP client. Both
  tools are hidden when the `media` capability or local reader is unavailable.
- `list_sources`: indexed counts per platform; it does not probe client login or
  claim that an adapter is currently connected.
- `list_imports`: local import-job status, file fingerprints and validation
  errors; it cannot start, cancel or modify a job.
- When the default-off `relationship_footprint` capability is enabled, MCP
  exposes `list_relationship_footprint`, `get_relationship_timeline`, and
  `get_relationship_achievements`. These return only per-conversation counts,
  UTC+08 daily totals, and objective private-chat milestones; they never return
  message bodies or infer sentiment/relationship meaning. They disappear from
  `tools/list` and direct calls are denied when the capability is disabled.
- `list_private_chat_profiles` and `list_group_chat_profiles` expose bounded,
  content-free conversation/member aggregates when `advanced_analytics` is
  enabled. The group tool requires an exact platform and account; its page and
  member limits are capped at 50. `get_annual_report` and `get_pair_report`
  require `annual_reports`; conversation-level annual reports require an exact
  platform/account, and pair reports require an exact private conversation.
  All four tools disappear from `tools/list` and direct calls are denied when
  their capability is disabled. None returns message bodies.
- The example client allowlist above includes these read-only tools; enable the
  matching local analytics capability in Settings before expecting them in
  `tools/list`.
- By default, the server exposes only the read tools above. The Settings view
  has a separate, off-by-default `允许整理元数据` switch. When enabled, newly
  started MCP processes additionally expose `update_conversation_metadata`,
  `set_contact_identity` and `remove_contact_identity`. These can only change
  Hub-owned notes/tags/archive state and contact identity links; source messages
  and platform databases remain read-only. Turn the switch off and restart the
  MCP client process to revoke the extra tools.
- When the independent `QQ 文本发送` capability is enabled, MCP additionally
  exposes `prepare_qq_send` and `get_qq_send_status`. Preparing only creates a
  durable `pending_approval` request in the shared local SQLite database;
  status reads that request and its audit trail. MCP does not expose approve,
  reject, or delivery tools. The user must review the full target account,
  conversation number, type, and text in the local Settings approval queue.
  The local UI requires an explicit confirmation and uses a 60-second,
  request-bound, one-time server challenge for approval; fixed confirmation
  strings cannot deliver a message. This is a user-interaction guard, not a
  security boundary against other processes running as the same Windows user,
  which can access the loopback service. The NapCat adapter rechecks that the
  online QQ account exactly matches the approved account;
  timeouts/interrupted sends become `unknown` and cannot be retried using the
  same request. The capability defaults off and the server rechecks it on each
  call.
- When the default-off `个人微信文本发送` capability is enabled and a logged-in local WeChat client is detected, MCP exposes `prepare_wechat_send` and `get_wechat_send_status`. Preparation writes only a durable `pending_approval` request; it requires a 1-64 character local WeChat account ID and a private (or `@chatroom`-suffixed) conversation ID, and refuses empty or oversized text. The status tool reads that request and its audit trail. Both tools disappear from `tools/list` and direct calls are denied when the capability is off or the client is not ready. MCP never approves, rejects, or delivers a WeChat message. When the request is approved locally, delivery is performed by the Hub on the Windows host via public Win32 input into the already-open conversation window of the logged-in client; no process injection, DLL hook or client database write is involved, and the sender is labelled as the logged-in personal WeChat account, not the Chat user. Ambiguous or absent receipts become `unknown` and are never retried.
- When `企业微信机器人文本发送` is enabled and a bot identity is configured,
  MCP exposes `prepare_wecom_bot_send` and `get_wecom_bot_send_status`. The
  preparation tool requires the configured bot ID and only writes a durable
  pending request; status is restricted to bot-platform requests. These tools
  never approve or deliver. The UI labels the sender as an independent bot,
  not the logged-in employee, and requires the same request-bound local review
  before delivery. Removing credentials hides the tools and denies direct calls.
  MCP supports text requests only; it cannot select local files or prepare
  media requests. The local WebUI separately supports explicitly selected and
  validated image/file/AMR voice/video attachments under the same per-message
  approval flow. Personal WeChat sending uses the separate route described above, and no live
  bot delivery is claimed.

The Settings view exposes whether the bundled server module is available,
generates a machine-local Hermes configuration for copying, and shows the 20
most recent tool calls. The local index retains at most 200 operation records;
each contains only tool name, success/error and timestamp, never arguments,
search terms or message content.

The server implements the MCP `2025-11-25` tools, resources and prompts surfaces.
`tools/call` returns text content for older clients and `structuredContent` for
clients that support structured tool output. `resources/list` offers a source
summary; `resources/read` reads that summary or one `lmh://message/...` URI
returned by `list_media`/`get_message`. `resources/templates/list` documents the
message URI shape. `prompts/list` and `prompts/get` provide local search and
attachment-inspection prompt templates. Resource reads return indexed JSON
metadata/message fields only; they do not open paths, fetch URLs, or stream
attachment bytes. Attachment bytes are available only through an explicit
`get_attachment` call, and the MCP client may forward requested media/files to
its model according to the client's own privacy policy.

`search_moments` is a default-off, capability-gated read-only tool for querying
recognized rows in the local WeChat `sns.db` by author, text and date. It uses
the provider's private decrypted runtime copy, returns parsed post metadata
only, and never follows database media URLs or downloads CDN content. Unknown
schema, missing database, or unavailable key states fail closed and are
reported by the local service.

When Moments and MCP are enabled, one locally cached image or video can be
returned only after an explicit `get_moment_media` call using the `media_url` from
`search_moments`. The Hub resolves its 15-minute process-local token under
the configured WeFlow-compatible cache directory, rechecks the file boundary
and image/video signature, and caps the response at 8 MiB. Images use an MCP
`image` block; MP4 and WebM videos use an embedded MCP resource with the matching
video MIME type. Remote URLs, arbitrary paths, expired tokens, unsupported
media types and oversized files are rejected. The client may forward explicitly
requested media according to its own privacy policy.

Contact and conversation listing results carry indexed `avatar_url` references
as metadata only. For an explicit contact-image request, `get_contact_avatar`
resolves the avatar by exact platform/account/contact identity and returns an
already-cached image as MCP `image` content. The Hub accepts only its supported
local avatar cache keys, validates cached image signatures and size, and never
fetches remote URLs; a cache miss fails. The WebUI likewise renders only
same-origin avatar paths and serves a known avatar cache key only when its image
already exists locally.

The loopback WebUI also exposes `GET /api/conversation-members` for an indexed
WeChat, WeCom, or QQ group when its connected local reader can provide a roster.
The route requires the exact indexed account and group identity and returns
locally filtered, bounded member metadata. QQ's bundled local QCE endpoint
currently returns its full roster in one response, which the Hub filters and
caps locally. MCP `list_group_members` delegates to the same Hub method and
applies the `chat` capability gate; if the stdio process has no connected local
reader, the call returns an unavailable-source error. This does not query
enterprise server-side archives.
Implemented standard surfaces are tools/list+call,
resources/list+read+templates/list and prompts/list+get. Resource subscriptions,
sampling, elicitation, logging, completion, roots and task-based tool execution
are not implemented; clients should not assume those capabilities.

Pages are bounded to 100 records and offsets to 1,000,000. Cursor pages return
`has_more` and `next_cursor`; exports remain complete and do not use page
cursors. Tool schemas reject
unknown fields and the service validates sizes again. Search returns local
message text, which is untrusted user content. Do not treat message text as
instructions, execute links/scripts from exports, or forward results to a
remote model unless the user explicitly chooses that privacy tradeoff. Tool
schemas reject unknown fields and the service validates sizes and permission
again. Operation records never include arguments, note text or chat content.

## Verification

```text
integrations/wechat/runtime/venv/Scripts/python.exe -m unittest hub.test_mcp -q
```
