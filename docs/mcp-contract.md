# Local Message Hub MCP

`hub.mcp_server` is an independent MCP stdio process. It opens the
same `<storage>\hub.sqlite3` as the WebUI and does not start platform readers,
the desktop shell or a network listener. The Python standard library is the
only runtime dependency.

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
        - list_contacts
        - list_sources
        - list_imports
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
- `list_contacts`: bounded contacts and groups.
- `list_sources`: indexed counts per platform; it does not probe client login or
  claim that an adapter is currently connected.
- `list_imports`: local import-job status, file fingerprints and validation
  errors; it cannot start, cancel or modify a job.
- By default, the server exposes only the read tools above. The Settings view
  has a separate, off-by-default `允许整理元数据` switch. When enabled, newly
  started MCP processes additionally expose `update_conversation_metadata`,
  `set_contact_identity` and `remove_contact_identity`. These can only change
  Hub-owned notes/tags/archive state and contact identity links; source messages
  and platform databases remain read-only. Turn the switch off and restart the
  MCP client process to revoke the extra tools.

The Settings view exposes whether the bundled server module is available,
generates a machine-local Hermes configuration for copying, and shows the 20
most recent tool calls. The local index retains at most 200 operation records;
each contains only tool name, success/error and timestamp, never arguments,
search terms or message content.

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
