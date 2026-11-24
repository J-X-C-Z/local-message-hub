# Local notification preferences and preview

This is an explicit opt-in native-live desktop plugin capability. It does not
send, schedule, display, or activate OS notifications. It never accesses
provider accounts, and does not implement the older Hub notification actions
or browser SSE route. A separate native opt-in SSE route is below.

Give one exact account desktop grant `notifications.read` to read preferences
and pull metadata-only previews. Add `notifications.write` to replace the
preferences for that same plugin, account, and platform. No message-body read
or archive-write grant is implied. No guarded, browser, or detached snapshot
route is exposed.

- `GET /api/v1/notifications/preferences` returns `revision` and
  `preferences`. Initial state is disabled with revision 0.
- `POST /api/v1/notifications/preferences` accepts those same fields.
  `preferences` has required `enabled`, `mode` (`all` or `allowlist`),
  `style` (`normal` or `silent`), `muted_conversations`, and
  `allowed_conversations`. The lists have at most 100 bounded IDs apiece;
  muting wins over the allowlist. A stale differing write conflicts;
  a repeated identical desired state is a no-op and returns the current
  revision. Scope is set by the grant, never by values in the lists.
- `GET /api/v1/notifications/preview` without a cursor returns the current
  checkpoint and no history. On the next request, supply the returned
  `cursor` and an optional page `limit` (1–50, default 30). A page scans at
  most that many incoming journal entries, excluding revisions, outgoing
  messages and offline imports. It can be empty with `has_more: true` when
  preferences suppress all entries scanned. Cursors advance past suppressed
  entries; use the returned cursor even for an empty page.
- Matched entries carry only an account-local conversation ID and the
  configured style. No message ID, text, sender, title, timestamp, activation
  link, or device token is included. The style is metadata, not a promise to
  display anything. Changing preferences applies to future pages, not to
  entries a consumer already advanced past.

Cursor identities bind the archive generation, plugin, platform and account.
Cursors from other modes or scopes fail closed; the cursor is not an
authorization credential. Preview requires the created-incoming journal
from rebuild-011. Older native archives use the existing offline
`upgrade-mcp-incoming` command. Preference storage is initialized within
the first changing write transaction, without changing the native archive
version. This code is pending the consolidated verification phase.

## Native acknowledged pull

Desktop grants with both `notifications.read` and `notifications.write` can
opt into their own durable progress on an ordinary native-live archive.
This is a routing/acknowledgment contract for an authorized local client,
not a push/delivery API. It is independent of the read-only preview cursor.

- `POST /api/v1/notifications/pull` takes an object with optional `limit`
  (1–50, default 30). When disabled it returns an empty batch and does not
  initialize progress. The first enabled pull starts at the current account
  head, not at historical entries. Subsequent pulls return
  `enabled`, `preference_revision`, `has_more`, `items`, and a nullable
  `receipt`. Each item has only the exact account's local conversation ID
  and presentation style. No text, sender, title, message ID, device, URL
  or activation target is returned.
- A non-null receipt covers the entire scanned batch, even if every entry
  was filtered out. After handling that batch, POST its exact
  `{"receipt":"…"}` to `/api/v1/notifications/ack`. Acknowledgment
  changes only that grant's durable position, and returns
  `{"acknowledged":true,"duplicate":false}`. Only the exact current
  starting position can advance; a replay at its immediate end is a no-op
  marked duplicate. Do not interpret this as display, delivery or human read.
- The receipt is signed, short-lived (two minutes), process-local, and
  bound to the archive instance, exact grant/account and preferences
  revision. Changing preferences, including disabling, invalidates the
  outstanding page; in-flight pages are reevaluated under current filters
  from the last acknowledged point. If the receipt expires, the service
  restarts or another client advances the state, pull again. The position
  survives restart; receipts do not. Pull is at-least-once for unacknowledged
  pages; a caller should serialize processing and make local UI handling
  idempotent.

## Resolve a local notification target

An ordinary native-live Desktop grant with `notifications.read`,
`notifications.write` and `conversations.read` can opt into
`POST /api/v1/notifications/activation-target`, as advertised by its plugin
manifest. Send only `{"receipt":"…","conversation_id":"…"}` from a pulled
page. There are no query selectors. The signed receipt is not authentication
by itself: the same archive instance, process, exact grant and account,
current enabled policy/revision, and expiry are all checked. The read
snapshot must attest that the selected conversation was eligible inside
the signed scanned range. An empty filtered page cannot resolve anything.

The response holds only platform, account and conversation ID plus optional
safe title and known local kind. Missing targets and unverifiable receipts
return the same not-found response. Repeated explicit resolution, even after
an ACK, is read-only while the receipt remains valid under the same policy;
it neither moves progress nor marks messages read. The native Rust Desktop
notifications client can explicitly resolve an item from its current
session's page and checks the returned identifiers. It performs no action
with the target; render the optional title as plain text.

Pinned Python `hub/server.py:608–615,4333–4337` consumes a one-time token
minted in its OS notification path; `webui/app.js:2325–2335` then opens
the conversation. This is instead an idempotent local read for a
receipt-backed native client, not an alias. No browser, desktop UI
integration, delivery, OS launch, arbitrary link, or provider fetch is
implemented. The older `test`, `message` and `activate` routes remain
unmounted. Source only; compilation and runtime qualification are deferred.
The change has no data/schema migration, so rolling back the source commit
removes only this endpoint and its client/discovery contract.

## Opt-in filtered SSE

`GET /api/v1/notifications/stream` is separately advertised as
`local_notifications.stream`. It needs an ordinary native-live, exact-account
Desktop bearer grant with **all three existing permissions**:
`events.read`, `events.legacy.read`, and `notifications.read`.
There are no permission or credential changes. The bare
`/api/events/messages` stream and versioned unfiltered event lane are unchanged.

With no cursor it immediately sends a `checkpoint` event with the current
account head, not history. Resume with exactly one of `Last-Event-ID` or
the `cursor` query value from this endpoint. Other endpoint, grant, account
and archive-generation cursors are not accepted. Each loop scans up to 50
account-local created incoming entries under the preference snapshot read
for that page. Revisions, historical/offline imports and outbound messages
are not eligible. The body-free `message` frame carries only
`{"conversation_id":"…","style":"normal|silent"}`, without its own ID.
There is no title, sender, event/message ID, timestamp, text, device or link
in a message frame. A `checkpoint` frame has data `{}` and an ID for every
advanced page, **even if all entries were muted, allowlist-filtered or
disabled**. No message frame is required to save that checkpoint.

If the consumer resumes from the saved checkpoint after unmuting, rows
already scanned and checkpointed do not become new notifications. If the
connection drops before that checkpoint has been saved, the unread page can
replay under the then-current filters. Preferences are sampled per page, not
again after its frames were written. There is no server-side durable position;
the separate pull/ACK protocol retains its existing re-filter behavior for
unacknowledged pages. The stream has existing shared resource limits and
a five-minute lifetime; reconnect from the last saved checkpoint.

This selectively restores the checkpoint behavior of
[pinned legacy SSE](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/server.py#L3619-L3676).
It is not a drop-in browser/OS notification path: no pending delivery token,
no call to the OS, and no activation, read receipt or acknowledgement.
Revert the source commit to remove only this additional route and its
discovery; no storage migration. No tests or builds have been run.
