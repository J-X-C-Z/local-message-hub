# Native scoped settings adapter (049)

Pinned Python reference: `c7052745fab3fa1880c6e3a496f40609185668b9`,
`hub/server.py` GET around line 3725 and POST around line 4457.
Python exposes one global settings object. Rust is deliberately not a
global setter: the native-live bearer adapter stores only settings for
the exact existing Desktop grant, platform and account. The guarded and
browser `GET /api/settings` compatibility views are unchanged; they
still reject POST. Snapshot, imported-media and source principals have
no access to the native write route.

- **GET `/api/settings`** reports the fields actually available to the
  caller. A scoped Desktop with `notifications.read` gets
  `notification_filters`, `notification_revision` and the stored
  `notification_preview_enabled` setting (not runtime/delivery state).
  An existing `client.preferences.manage` grant also receives its native
  presentation state and the existing v1 CAS route for writes. No account
  list, paths, credentials or permission changes are exposed.
- **POST `/api/settings`** requires both existing notification read/write
  permissions plus exact Desktop scope. The sole writable legacy key is
  `notification_filters`, accompanied by `expected_revision` from the
  read. No query parameters. Full object replacement, not a merge.
- For compatibility with the original filter shape, each muted/allowed
  entry is a JSON string encoding exactly
  `[platform, account_id, conversation_id]`. `platforms` must contain
  exactly the authorized platform. Another account/platform, an empty or
  multi-platform list, invalid ID, extra field, or non-JSON body is rejected;
  no partial writes. Each list accepts at most 100 IDs of at most 256
  bytes. Duplicate IDs normalize to the first occurrence.
- A successful POST returns the new notification filter view and revision.
  The write uses the same persisted record and notification CAS as the
  native v1 preference route. A stale differing revision returns 409;
  equivalent repeats are no-ops. A filter write preserves the local
  preview-enabled value read inside the write transaction; it cannot enable
  or disable either local preview or any global capability.
- The Python global fields `capabilities`, `auto_sync`, `sync_interval`,
  `auto_index_at_login`, `mcp_write_enabled`, and
  `auto_download_attachments` are unsupported. Supplying any of them,
  including alongside valid filters, fails the request without changing
  anything. No credentials, autostart, network, provider, MCP, OS notification
  or runtime transition is performed by this adapter.

Example POST body for the already authenticated grant scoped to
`wechat` /`local-account`:

```json
{
  "expected_revision": 0,
  "notification_filters": {
    "platforms": ["wechat"],
    "mode": "allowlist",
    "style": "silent",
    "muted_conversations": [],
    "allowed_conversations": ["[\"wechat\",\"local-account\",\"team\"]"]
  }
}
```

Verification status: coded only; compilation and runtime tests have not
been run for this lane. This is a scoped compatibility shape, not parity
with the global Python settings surface.
