# Local client preferences

An ordinary native-live Hub may opt an exact account-scoped Desktop grant into
`client.preferences.manage`. Preferences belong to that grant ID, archive,
platform and account. Token rotation under the same grant ID retains preferences;
using a different grant ID or changing the platform/account does not. No browser,
guarded, snapshot, imported-media, MCP or source principal can use this API.

GET `/api/v1/client/preferences` returns `version` and a full `preferences` object:

```json
{"version":0,"preferences":{"list_page_size":30,"list_density":"comfortable","preview_lines":1,"show_avatars":true}}
```

Version 0 is the unstored default. POST the full `preferences` object and
`expected_version` from the last GET. `list_page_size` accepts 10–100,
`list_density` accepts `compact` or `comfortable`, `preview_lines` accepts
0–3, and `show_avatars` is a boolean. These are presentation hints only;
they do not change server-side retrieval limits, permissions, or behavior.

Writes are atomic and increment the version only for actual changes. The immediate
same-value replay of a successful write returns the current version; other stale
writes return 409 and the client should re-read and resolve. Unknown fields or
invalid values are rejected. At most 1,024 owners may store records per archive.
Defaults remain available without allocating a record. There is no eviction, and
the server does not apply these preferences to other surfaces.

## Deliberate differences from pinned Python

The pinned Python `hub/server.py` GET `/api/settings` assembled global settings
plus the storage directory, FTS, lock, autostart and auto-export information.
POST could alter global capabilities, auto-sync and interval, autostart, MCP write,
attachment downloads and notification filters. This API is **not** compatible
with that setter. It exposes none of those fields and cannot change any of them,
credentials, provider config, or network paths. Notification preferences remain
in their own independently permissioned API. The existing safe read-only legacy
settings view is unchanged.
