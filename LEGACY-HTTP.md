# Guarded legacy HTTP adapter, rebuild-014/015 (Linux)

Start the dedicated `serve-guarded-legacy --directory <guarded-native>
--grants <private-grant-file>` from a trusted native desktop parent with
anonymous stdin/stdout pipes. It takes the guarded archive's exclusive
lease, so stop the ordinary guarded listener (and any older runtime)
before using it. The profile and password protocol are the same as
`serve-guarded`; see README. The ready mode is `guarded-legacy-read-only`.
All guarded profiles start locked; the local parent unlocks via private
stdin only. After unlocking, a separate scoped desktop bearer must have
`legacy.http.read`, `conversations.read` and `messages.read`.

Only the listed GET paths, the scoped export below and the report paths described later are implemented. Search requires the additional
`legacy.search.read` opt-in; contacts requires `contacts.read`. Settings
reflect those permissions independently.

| Route | Response | Intentional deviations from the old server |
|---|---|---|
| `/api/sources` | `{"sources":[...]}` | A single scoped source with account-local indexed counts. No provider status, connected accounts or settings are inspected. |
| `/api/settings` | Safe capability metadata and lock state policy | `chat` plus search, contacts and scoped text export when explicitly granted; unsupported capabilities disabled. No profile paths, stored settings, verifier, autostart state, provider options, or Python idle-session/cookie policy. The Rust lease is fixed. |
| `/api/conversations` | `{"items":[...],"total":N}` | Default/explicit `all` resolves only to the granted scope. Avatar/raw metadata are blank. Accepts `platform`, `account_id`, `q`, `limit`, `offset`. |
| `/api/messages` | `{"items":[...],"total":N,"has_more":Bool,"next_cursor":null-or-string}` | Specify exact `platform`, `account_id`, `conversation_id`. Missing identity returns no items. The guarded native bearer listener preserves stock timestamp ties for a v9 projection using a scoped cursor; normalized imports and the cookie reader retain Rust ID ties. No stock global row-ID cursor. Raw media/file/extra metadata are blank. Also accepts `q`, `type`, `start`, `end`, `limit`, `offset`, `cursor`. |
| `/api/search` | Bounded message preview page | Exact granted account, literal search, 1,200 SQLite character preview; optional conversation and standard message filters. Guarded v9 projections preserve the source tie order with a private keyset; normalized archives retain the Rust scoped keyset. Not all-account search. |
| `/api/contacts` | `{"items":[...],"total":N,"accounts":[...]}` | The accounts list is scoped before lookup. Only name/ID search, not hidden aliases. Raw avatar, external/link identities, and metadata are omitted. |
| `/api/context` | Window with `target_index` and before/after flags | Require explicit exact platform, account and normalized message. The native bearer listener can resolve a uniquely indexed conversation when omitted; the cookie reader requires the conversation. Windows cap at 50 on either side. Equal timestamps follow local row order, which preserves the relative source order for guarded v9 imports. No source row IDs are published. |
| `/api/quoted-message` | Context window and `target_message_id` | WeChat only. Resolve from the canonical scoped reference row, never a caller-supplied provider source ID. No missing, ambiguous, self-referential or foreign targets. |

The native bearer listener additionally offers `GET /api/export` with
`messages.export`, `messages.read` and the existing exact desktop legacy
grant. Specify a conversation ID and optionally exact platform/account,
format and inclusive start/end (Unix seconds or recognized date/ISO
bounds). It uses the same scoped archive and bounded renderer as native
`/api/v1/messages/export`. Supported formats: JSON, CSV, TXT, Markdown,
HTML and text/system ChatLab JSON or JSONL. The old multi-account, search,
contact, member, bundle and provider export surfaces remain unsupported.
The cookie browser reader does not expose the export path or advertise
that capability.

The regular `/healthz` remains public process liveness. These paths
use the old top-level locked `423` with `code:"account_locked"`, a
strict exact IPv4 loopback Host, `Cache-Control: no-store`, and no browser
Origin or Cookie. Duplicate, unknown, malformed or over-budget query
parameters are refused. Every request is authorized in the scoped account
before data lookup. Literal conversation/chat text remains untrusted user
data and may itself contain URLs. No credential or data should appear in
argv/URLs/logs; the Bearer header is for the native caller only.

Not a browser/web app, old cookie/session connector, or unchanged WebUI
replacement. No v1 routes, static HTML/JS, account-management endpoints,
provider state, media, sync, unrestricted export, writes, events or notifications are
registered on this separate listener. Normal guarded mode does not expose
these legacy paths. These legacy operations are partial compatibility surfaces,
not drop-in compatibility. Validation of the export addition is deferred.


Contacts and quote-resolution inputs are present only when corresponding
normalized fields are already in the archive. The guarded text JSON import
does **not** populate contacts or provider quote metadata. Both read paths
were compared using synthetic fixtures, not an import of real contacts or
WeChat records. Old `source_message_id` context/quote extensions are refused.

Rebuild-016 adds an operator-only one-account path to populate a fresh
guarded profile from a supported detached v9 snapshot (see README). This
supports migrated contacts and canonical WeChat quote queries without
fixture seeding. The text JSON importer does not carry those fields.

Rebuild-018 adds a ninth partial old route, `GET /api/reports/annual`.
Requires `analytics.annual.read`. Optional exact conversation produces the
same bounded aggregate shape. No contact rankings, arbitrary type labels, or
server-side report export.


Rebuild-019 adds a tenth partial old route, `GET /api/reports/pair`, under
independent `analytics.pair.read`. Requires explicit platform, account,
private conversation. Response timing counts adjacent sender roles,
not the original counterpart selection, and emits no identifying titles,
sender identities or raw text. See README for reply window, ties and scope.

The legacy report routes also accept an omitted `year`. As in the pinned
Python reports this returns `year: null`, with no date filter. Full history
means the selected account's available indexed rows (or only the selected
private conversation for the pair report), not every account or provider-side
history. The annual report's 12 bins group UTC+08 activity by calendar month
across years, while active days, busiest day and streak use full UTC+08 dates.
The pair calculation keeps the current bounded role-adjacency metric, including
across year boundaries. Both reports error rather than returning a truncated
aggregate if the matching rows exceed 200,000 or exceed the archive query budget.
An explicit year keeps the same behavior; the native `/api/v1/analytics/annual`
and `/api/v1/analytics/pair` still require a year. The guard, grants, date-bound
mode and intentionally redacted identifying response fields are unchanged.

The legacy report responses also provide `message_types` rows (`type`, `count`)
for the original [report type chart and CSV client](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js).
Only exact indexed `text`, `image`, `sticker`, `video`, `voice`, `file`, `link`,
`location`, and `system` are separate buckets. Null and all other labels count
as `unknown`; arbitrary labels, raw types, attachment records and message text
are not returned or inspected. Counts share the report's exact period and
scope, and sort by count descending, then type ascending. Empty reports return
an empty vector. Native v1 responses remain unchanged. No browser support or
full report parity is implied by this field.

The guarded bearer mode also exposes the partial `GET /api/export/contacts`
redacted tabular download and `GET /api/conversation-members` /
`GET /api/export/group-members` observed-sender projections with
mandatory `source=observed`. These are bounded, never provider-backed,
and not official rosters or full legacy drop-in implementations. See
[GROUPED-EXPORTS.md](GROUPED-EXPORTS.md).

## Indexed account and guarded status reads (038 source-only)

Only the bearer-only guarded legacy listener adds:

- `GET /api/account-safety/status`: exact desktop bearer with the legacy
  opt-in, even while locked. Returns guarded configuration/lock status and
  fixed parent-pipe lease duration. The old idle timeout does not apply
  (`idle_timeout_seconds: null`). No key, verifier, paths, token, lock/unlock
  action or browser route. `/healthz` still reports process liveness only.
- `GET /api/accounts`: `items` can contain only the bearer grant's indexed
  platform/account, when present in allowed conversations/messages/contacts.
  Optional explicit scope must match. No provider status, other accounts
  or Moments account enumeration.
- `GET /api/stats`: additionally requires `contacts.read`; aggregate only
  the one granted indexed account with a bounded per-type and UTC+08 daily
  query. The media number is normalized message-kind only; do not interpret
  it as actual media/attachments. No global storage size, import list,
  provider metadata or unscoped statistics.

Comparable versioned routes and a safe effective feature map are documented
in the OpenAPI; there are no new settings writes or browser capabilities.
See [LEGACY-ROUTE-BACKLOG.md](LEGACY-ROUTE-BACKLOG.md) for the pinned method
inventory. New behavior is not yet validated.

## Partial analytics aliases (039 source-only)

The separate bearer listener now mounts the pinned GET literals
`/api/analytics/private`, `/api/analytics/groups`,
`/api/analytics/self-sent-daily`, `/api/analytics/relationships`,
`/api/analytics/relationships/timeline` and
`/api/analytics/relationships/achievements`. They require the corresponding
`analytics.profiles` or `analytics.relationships` grant in addition to
the existing guarded legacy admission. The prior UTC+08 and response
budgets apply. Explicit foreign scope fails before lookup. The group
alias supports only `member_limit=0` or omission (no member ranking).
Relationship reads require one exact private conversation; the summary
wraps that one item rather than listing account-wide relationships.
These return native local aggregates, not full stock wire compatibility.
There are no new cookie/browser, settings-write or delete routes.

## Narrow old literals on ordinary native-live (040 source only)

These aliases do **not** mount on guarded or browser listeners. The normal
native Host/bearer boundary, exact grant scoping, request bounds, and
endpoint-specific permissions remain mandatory.

- POST `/api/imports/preview`: requires an exact-account Desktop grant with
  `imports.local.manage`. Existing JSON/base64 with no query still stages a
  native job, not a message import. A separate literal
  `application/octet-stream` + `format=json` query branch provides the
  [read-only stock JSON preflight](IMPORT-STOCK-PREFLIGHT.md); it writes no
  files or job and returns no confirmation. No multipart or alphanumeric
  legacy job ID is accepted.
- GET `/api/imports/{id}` and POST `/api/imports/{id}/cancel`:
  use this grant's 64-hex Rust job ID and no query. Cancel is bodyless.
  Unknown/old IDs cannot be mapped. No unscoped list, import commit
  or other guarded writes; native commit remains explicitly confirmed.
- GET `/api/campus/profile`, `/api/campus/sources`,
  `/api/campus/updates`, `/api/campus/items` and
  `/api/campus/items/{id}`: require the existing separately scoped Campus
  grant. Use the imported-only native query/response shapes, not live
  feeds, old configuration state, refresh or any provider access.
- GET `/api/operations`: requires `operations.read` and the existing
  per-view grant for the explicitly supplied `kind`. Returns named,
  redacted native views, not an unscoped activity log.

These are code-only aliases, not legacy client qualification or removed
guarded-family restrictions. Broad settings writes, deletion, cookie unlock,
and all provider/auth dependent branches remain absent.

## Incoming event and imported-asset aliases (041 source only)

- GET `/api/events/messages` is only ordinary native-live with an exact
  desktop bearer holding `events.read` and `events.legacy.read`. Starts
  at the own-account high-water mark unless supplied a scoped opaque
  `Last-Event-ID` or `cursor`, not both. Stock integer IDs and native
  created/revised event tokens are rejected. Emits a checkpoint and
  metadata-only incoming-created messages, with conversation title and
  kind redacted to null. No self/revision/offline event, account data
  outside the grant, notification filters/delivery, cookie or guarded
  listener. Clients reconnect after the five-minute bound.
- GET `/api/resource-jobs/{id}`, GET `.../{id}/download` and POST
  `.../{id}/cancel` use already-created Rust 64-hex scoped artifacts
  in the Linux imported-media listener, requiring exact destination
  grant and existing verified blob ownership. Queries and cancel
  body are not accepted. The old create path, raw paths, 20-character
  job IDs, provider resolution or additional download hooks are not
  mapped. The archive uses only generated ASCII names in bounded
  ZIP32/STORED output.

These are partial safe aliases, not tested wire parity or a stock client
replacement.

## Bounded state and imported media (042 source only)

- In ordinary native-live only, POST `/api/conversation-metadata`,
  `/api/contact-identities` and `/api/contact-identities/unlink` adapt old
  flat names to the existing native scoped writer. These require explicit
  matching platform, account, conversation/contact and expected revision,
  plus the corresponding write/read grants. An old blind request fails
  closed. The unlink action cannot set a new identity. No guarded edits.
- A separately scoped Campus editor may POST `/api/campus/items/{id}/state`
  on ordinary native-live to update the existing local bookmark with
  revision checks. This is not the old events/outlet state or provider.
- Guarded bearer GET `/api/conversation-metadata` (and native guarded GET
  `/api/v1/metadata/conversation`) reads one authorized local conversation
  metadata revision after unlock. This opt-in requires the conversation
  metadata grant, a matching account and conversation. Same profile and
  unlock-generation checks; no browser route.
- In Linux imported-media mode only, GET `/api/media` lists opaque local
  attachment IDs and sizes for one exact MessageKey. No listing across
  the account or external URL. The new native `.../{id}/verify` reads and
  verifies the whole bounded object with the existing grant before
  returning size and SHA-256; never raw paths, provider resolution or
  body bytes. This does not replace legacy provider-backed media browsing.

## Local discovery and exact job list (043 source only)

- GET `/api/mcp`: exact scoped desktop with `messages.read` and
  `mcp.discovery.read`. Ordinary native snapshot/live can report local
  scoped stdio support; unlocked guarded mode reports it unavailable.
  Discovery does not mean a separate Tool grant is configured or
  launched. No stock config text, file paths, argv, keys, tool logs,
  write access, provider operations or browser connector.
- GET `/api/imports` in ordinary native-live: existing
  `imports.local.manage` sees only its own grant/account/archive jobs.
  The default is 20, maximum 50, ordered by binary opaque job ID with
  an own-ID continuation anchor. It reports phase and bounded counts,
  no confirmation or input payload. No stock-global list or guarded writes.
- The native backup capabilities endpoint describes whether the local
  private operator capsule CLI is implemented. No old HTTP backup
  list/download/create/restore alias; bearer auth does not prove
  encrypted catalog ownership. No global settings writes, provider
  configuration, real sends or automatic workflow via these endpoints.

## Exact local reference and availability (045 source only)

In the Linux imported-media listener, GET `/api/media/resolve` requires
`source=imported_bundle`, an exact platform/account/conversation/message
and the opaque imported attachment ID under the existing grant. Old
provider, cache, URL and file selectors are rejected. It verifies the
entire bounded blob, then returns a typed download selector, relative
route, size and digest, not a public/persistent URL. The stable selector is
bound to the unchanged imported bundle, never transferable authorization.
GET `/api/resource-jobs` lists only the same grant's local short-lived
artifacts; no stock POST or provider action. The native
`/api/v1/sources/local-availability` checks indexed presence only and
has no legacy readiness/authentication alias.


### Search order for guarded v9 projections (094)

Only the existing guarded legacy `GET /api/search`, through its authorized
bearer or guarded browser listener, uses this mode. The reserved v9 import
receipt qualifies the already verified protected archive in the same read
transaction as search. The importer reads one exact account in ascending
source rowid order and inserts into an empty protected archive in that
order. Thus descending local rowid restores the **relative order** of tied
timestamps for those projected rows. Source numeric rowids are not
preserved, copied, accepted or returned; projected message IDs are unchanged.
Ordinary native search, guarded normalized imports and conversation paging
retain their previous sort and tokens.

The versioned opaque cursor contains the already visible exact-account
message ID and timestamp, not a numeric rowid. Its query fingerprint binds
the archive, current grant and exact filters, but is not authorization.
On continuation the exact scoped primary-key lookup validates the anchor
and rechecks optional conversation, literal term, type and date bounds on
that one row. Provenance, lookup, exact whole-query total and the page share
one transaction. The full count does not use the cursor as a filter.
Missing anchors and different query/old cursor formats fail closed. For
an in-progress page chain made before this change, start from the first
page with no cursor; stock Python cursor formats also remain unsupported.
No old ordering fallback is used for a qualifying guarded v9 archive.

The protected import is still capped at 1,000 messages with no copied FTS
index. The anchor is at most one indexed point lookup; counting and
sorting still do real work under the existing limits. No measured speedup,
stock browser/session compatibility, or broader search scope is claimed.
Rollback: revert the source patch and start from the first page again.
No schema, row or provenance data are modified. Build and tests deferred.

### Guarded v9 conversation history paging

The pinned [conversation loader](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L1684-L1693)
carries `next_cursor` into subsequent reads of the selected conversation.
The pinned [message page](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/store.py#L877-L907)
and [context window](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/store.py#L1116-L1133)
use the same timestamp and source-row tie order. The guarded v9 importer
preserves the relative order (not the actual source row IDs). For example,
equal-time source rows `m9`, `m1`, `m4` in ascending insertion order page
as `m4`, `m1`, then `m9`; a context window at `m1` places `m9` before
and `m4` after. This relationship is by source inspection, not runtime
qualification. The bearer listener can now resolve the missing context conversation as
described below. The original browser app is not directly supported.

The new conversation cursor is distinct from the existing guarded search
cursor. It contains an account-local message ID, timestamp and a digest of
archive, grant, exact platform/account/conversation, and filters; the ordinal
is resolved privately under those same predicates. The import evidence,
anchor, total and page read use one verified transaction, with the existing
fresh preflight/postflight and gate fencing. Pre-upgrade Rust cursors and
unmodified Python row-ID cursors cannot continue the guarded-v9 conversation
page; clear them and reload the first page. Normalized imports, native v1 and the existing search cursor are unchanged.
The cookie reader uses the pre-096 Rust ID tie order and token, not the
native bearer v9 page. A browser cursor produced by the intermediate 096–097
sources must be cleared and restarted; the native bearer v9 cursor is unchanged. This is still
literal filtered coverage, not stock FTS, media, or complete UI parity.

### Native bearer legacy context jump

The pinned [context jump](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L728-L743)
stores an already indexed message ID, then requests
[its context](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L1568-L1574)
with an explicit platform/account but no conversation. This also happens
after the existing scoped WeChat quote endpoint returns a normalized
`target_message_id`. The guarded **native bearer** listener accepts that one
shape: under the same exact-account Desktop grant and `messages.read`,
a bounded lookup by normalized primary key must resolve to one conversation.
Lookup and window share the verified archive transaction. The response has
the same conversation-scoped projection, window limits, context order and
guard preflight/postflight as other guarded reads. A missing or ambiguous
target returns the same 404. An explicitly empty conversation is not the
omitted shape; a wrong explicit conversation never falls back. Neither a
provider `source_message_id` nor scope inference is accepted.

The cookie reader still requires an explicit conversation, and native v1 and
the quote request/response are unchanged. No provider metadata or original
preview props were enabled. This is source-only compatibility, not a claim
that the original browser client, cookie protocol or all quote workflows work.

## Exact legacy organization sidebar on ordinary native-live

The pinned [chat sidebar](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L1591-L1597)
loads `GET /api/conversations` with platform, title query and paging.
Its [organization form](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L649)
takes note, tags and archived state from the chosen returned row.
Previously that stock list name was not available when reading an ordinary
native-live archive, despite its native v1 list and revisioned metadata API.

An ordinary live client may now opt in using an exact scoped Desktop
bearer with the existing `legacy.http.read` grant (which requires both
`messages.read` and `conversations.read`). The route uses the same strict
stock list query and redacted projection as the already implemented
guarded alias. Scope is resolved from the grant before pagination and
counting; a different explicit platform/account fails closed. The exact
account list keeps literal title matching, bounded paging and the existing
metadata join. It returns the saved note, tags and archive flag without
needing extra reads; no avatars, provider extras, credentials or live
source lookup are added. No note/tag search or new filtering is implied.

This adds only the ordinary native-live read alias. Existing native v1,
guarded bearer and browser-cookie routes are unchanged. No new stock
write or auto-resolving metadata revision; edits still require the
existing permission and optimistic revision. No schema or migration.
Source-only pending unified validation.

### Exact message list and search on the same ordinary listener

The opt-in ordinary live desktop bearer above can also use the pinned
names `GET /api/messages` and `GET /api/search`. Both require the
existing `legacy.http.read` and `messages.read`; search additionally
requires `legacy.search.read`. They never use the guarded store/pager
or the protected legacy/browser route family. A different explicit
platform/account fails closed before any archive query.

- Messages needs all three exact selectors (`platform`, `account_id`,
  and `conversation_id`). Missing selectors return the existing legacy
  empty page and reject a supplied cursor; there is no wildcard
  conversation history lookup. Search is confined to the grant's one
  platform/account, even when the input platform is blank or `all` as in
  the pinned client. Optional conversation and existing text/type/date
  filters apply before both count and pagination.
- Legacy pagination follows the local archive's descending timestamp and
  relative row order for ties, like stock and consistent with context
  ordering. The same archive on an ordinary restart retains its ordering.
  Keep `next_cursor` opaque and pass it to the same route with the same
  grant/account and filters. Changes that remove or change the anchor's
  matching timestamp/filter membership require a fresh first page. This is
  not a snapshot held across requests; concurrent ingestion can update the
  archive. Discard old cursors on upgrade, migration, restore, maintenance
  that changes SQLite row ordering or when changing archive. Native v1
  pagination is unchanged, and stock, previous ID-based, guarded and v1
  cursor tokens are not accepted here.
- Results carry `items/total/has_more/next_cursor` and the existing legacy
  allowlist. Search text is a bounded preview. No raw row identifiers,
  upstream extras, avatars, attachments, local quote enrichment, or
  navigation/reply link compatibility is added.
- Both reads follow the ordinary host's verified archive work path. Native
  v1 and all guarded/browser behavior are unchanged. No schema, writes,
  provider operations or multi-account browsing. Revert this source patch
  to remove the two aliases. Source-only pending unified verification.

### Exact message context and redacted contacts

The ordinary native-live listener also supports stock-named
`GET /api/context` and `GET /api/contacts` for the same exact Desktop
bearer opt-in; contacts separately requires `contacts.read`. Discovery
lists these as `legacy_message_context` and `legacy_contacts` without
changing the native v1 operations.

Context requires the explicit platform and account included in the Desktop
grant and a normalized message ID. Search hits can omit the conversation;
the unique message lookup and context window use one read transaction. An
explicit but wrong conversation never falls back to inference. Context
returns a bounded chronological window and target index, not cross-account
lookup or source-ID quote target resolution. Contacts is limited to the
grant's exact account even for
omitted scope fields or `platform=all`. Filtering matches display name and
ID literally, not hidden aliases, and the response omits redacted metadata.
Neither route accesses providers or modifies the archive. These are
ordinary native-live read aliases, not browser or full stock compatibility.

### Revisioned organizer using ordinary native-live legacy names

For a client that uses the old endpoint name but supports explicit revisions,
`GET /api/conversation-metadata?platform=...&account_id=...&conversation_id=...`
reads the same local metadata state as the native read. It requires a Desktop
grant for the exact account with both `legacy.http.read` and
`conversations.metadata.write`, plus the read permissions already required to
enable those roles. The read returns only `revision` and `metadata` (note,
tags and archived state). An absent metadata row for an existing conversation
still has revision 0; an absent or foreign conversation is not returned.

Use that revision as `expected_revision` in the existing flat POST to
`/api/conversation-metadata`. Keep the exact platform, account and
conversation, and supply only the fields deliberately changed. A conflicting
stale change receives 409; read the current state and resolve it before
retrying. The native v1 writer and old-named POST authorization are unchanged.
No guarded, browser-cookie, or snapshot access was added. Stock clients that
write without a revision are still unsupported.
