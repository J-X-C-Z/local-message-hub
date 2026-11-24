# Explicit detached MCP HTTP subset (source only)

Linux only, separate opt-in server. It never starts stdio, the desktop
HTTP API, a child process, a provider or a guarded listener:

    local-message-hub-rs serve-mcp-http --snapshot ABSOLUTE/index.sqlite3 --grants PRIVATE.json --plugin-id SCOPED_ID --port PORT

Choose a nonzero IPv4 loopback TCP port. Put the private hash-only grant
file and detached archive in an operator-controlled local hierarchy.
The grant file is opened once as a single-link current-owner private
regular file, ignoring token env vars. The exact configured scoped-tool
ID must exist and cannot have the incoming-live grant. Use a dedicated
token rather than reusing a consumer's stdio grant; every RPC authenticates
its Bearer on this specific listener. Do not use proxies, a browser or a
non-loopback bind. No URL/cookie credentials, Origin, Fetch Metadata,
forwarded host, or cross-origin support. Bearers never appear in argv,
response data or readiness lines.

Only POST /mcp accepts a finite JSON-RPC request (24 KiB maximum,
five-second body deadline and four active request slots; 256 KiB response).
Send exactly Host equal to the selected 127.0.0.1:PORT, JSON Content-Type,
and an Accept list containing both application/json and text/event-stream
without quality parameters. The server only returns JSON (or empty 202
for initialized notification). Initialization negotiates exactly
2025-11-25; send MCP-Protocol-Version: 2025-11-25 on subsequent calls.
This server does not issue a session ID and is intentionally stateless:
initialized is acknowledged, not tracked as a per-client state machine.
Browser Origin rejection is not a substitute for a private token.

Current methods: initialize, ping, notifications/initialized, scoped
resources/list, resources/templates/list, resources/read, prompts/list,
and prompts/get as permitted. These reuse the detached scoped stdio
projections and never invoke tools, resolve media or fetch resources.
Every archive RPC checks the snapshot before and after. Unsupported:
GET event streams, server push, resumability, DELETE session lifecycle,
batch RPC, additional tools, alternate protocol revisions, account
changes, remote access and cookie/guarded/listener integration.
Do not describe this as all Streamable HTTP MCP compatibility or a
tested SDK/browser integration. No server was started or validated in
this source-only delivery.
