# Local provider profile metadata

The provider profile commands record **operator intent**, not provider
configuration sufficient to connect. They are local operator CLI commands
over an existing, ordinary Rust-native archive. There is no HTTP/plugin route,
guarded or legacy index support, provider discovery or access, remote status
check, sync, scheduling, or side effect on existing import and source jobs.

- `provider-profile-create --directory DIR --adapter ADAPTER --account-alias ALIAS --id PROFILE`
  creates a disabled record at revision 1. Retrying the same ID, adapter and
  alias returns the current record without resetting it. The ID is unique in
  the archive; it cannot be reassigned to another scope.
- `provider-profiles --directory DIR --adapter ADAPTER --account-alias ALIAS`
  lists exactly that scope (JSON, sorted by profile ID). A fresh archive returns
  an empty array without any schema writes.
- `provider-profile-set --directory DIR --adapter ADAPTER --account-alias ALIAS --id PROFILE --revision REVISION --intent enabled`
  requires the current revision. Use `--intent disabled` to turn intent off.
  An actual change increases the revision; a current-revision no-op keeps it.
  A stale revision conflicts even when the requested value happens to match.

- `provider-profile-retire --directory DIR --adapter ADAPTER --account-alias ALIAS --id PROFILE --revision REVISION`
  permanently retires an already-disabled profile in the exact scope.
  It removes the active entry and stores a tombstone at the next revision in
  the same transaction. An identical retry returns that receipt; changed
  scope, stale revision and still-enabled profiles fail. Retired IDs disappear
  from the list and cannot be recreated, reassigned or re-enabled even by an
  older core that attempts to create a row. A retired ID frees an active
  profile slot but permanently occupies one of 4,096 tombstones per archive.
  This is local metadata retirement, not a provider logout, account deletion,
  or a change to existing imports. There is no unretire command.

The account alias and profile ID are local labels (1–64 characters, start with
a lowercase ASCII letter or digit, then lowercase letters, digits, underscores
or hyphens). Choose opaque aliases instead of real mailbox addresses, hostnames,
access tokens or account secrets; the CLI does not take provider connection
settings or resolve aliases into credentials or identities. `--directory` points
only to the existing Hub archive. The first successful creation adds a
versioned table within that archive. There is a cap of 256 profiles per archive.

| Adapter | Standalone capability, not activated by the profile |
| --- | --- |
| `wecom-rendered` | Detached preview and confirmed import |
| `wechat-provider-page` | Detached preview only |
| `qq-hub-csv` | Detached preview and confirmed import |
| `telegram-desktop-json` | Detached preview and confirmed import |
| `mail-eml` | Detached preview and confirmed import |
| `mail-imap-fixture` | Explicit loopback fixture only; not IMAP login/sync |

`enabled_intent` does not grant access, validate an external account or
start an adapter. It also does not block a standalone import when disabled:
existing per-operation input selection, account parameters and confirmations
still apply. `provider_connected`, `live_provider_supported` and
`profile_executes_adapter` are always false in this implementation. They are
static capability state, not observations about a live remote system.
