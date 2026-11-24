# Legacy HTTP backlog, rebuild-075 (code-first)

Pinned source: local-message-hub at `c7052745fab3fa1880c6e3a496f40609185668b9`,
`hub/server.py` Git blob `9dff68b768542740706429cd1f920d7639577c8d`.
Inventory uses actual dispatch in Handler.do_GET, Handler.get_api and
Handler.do_POST; distinct method/template pairs only. Keeps literal regex
templates as written, groups dynamic source configuration as one template,
and excludes static files and non-API media routes. Line numbers identify
the pinned dispatch, not a documentation approximation. Native v1 routes
are a separate denominator.

- **prior-partial**: previous release demonstrated shared behavior for a
  narrow fixture; no full replacement and no revalidation of this checkout.
- **coded-shape**: local bearer alias with limited matching shape. Some can
  become equivalent for a bounded subset after unified tests, but currently
  untested. Often narrower input/permissions, redacted fields or no providers.
- **native-subset**: conceptually related Rust implementation (including
  explicit CLI/stdio where appropriate), not an equivalent legacy endpoint.
- **deliberately-absent**: unsafe or out-of-scope legacy behavior not mounted.
- **provider/auth open**: unresolved separately authorized provider,
  credentials/consent or live integration; no implementation claim.
- **unresolved**: remaining shape or contract work without a qualified mapping.

Full drop-in qualification remains **0/124**; none of 075 was compiled or
functionally tested. Account status uses a fixed lease instead of a Python
cookie/idle timer; indexed stats omit image/resource metadata and globally
aggregated storage/provider data. Old backups are not exposed by HTTP;
the new guarded ciphertext catalog is a private operator CLI only.
040 aliases for import status/cancel take Rust-created IDs, not the
old regex's IDs. Those import paths, Campus and redacted operations
are only in ordinary native-live; they never widen the guarded listener.

075 baseline count: GET: 66 · POST: 58; coded-shape: 40 · deliberately-absent: 6 · native-subset: 22 · prior-partial: 10 · provider/auth open: 45 · unresolved: 1.

| Method | Pinned path or regex template | Line | Status |
|---|---|---:|---|
| GET | `/api/account-safety/status` | 3506 | coded-shape |
| GET | `/api/events/messages` | 3522 | coded-shape |
| GET | `/api/avatar` | 3525 | deliberately-absent |
| GET | `/api/sends/([A-Za-z0-9_-]{8,80})/approval-challenge` | 3529 | provider/auth open |
| GET | `/api/wechat-sends/([A-Za-z0-9_-]{8,80})/approval-challenge` | 3534 | provider/auth open |
| GET | `/api/wecom-bot-sends/([A-Za-z0-9_-]{8,80})/attachment-preview` | 3540 | provider/auth open |
| GET | `/api/resource-jobs/([A-Fa-f0-9]{20})/download` | 3575 | coded-shape |
| GET | `/api/qq/forward-resources/([A-Za-z0-9_-]{24,64})` | 3584 | provider/auth open |
| GET | `/api/backups/([^/]+)` | 3597 | native-subset |
| GET | `/api/campus/wechat-accounts` | 3688 | provider/auth open |
| GET | `/api/campus/briefing` | 3690 | coded-shape (exact-date daily or explicit ungrouped all) |
| GET | `/api/campus/items` | 3694 | coded-shape |
| GET | `/api/campus/sources` | 3700 | coded-shape |
| GET | `/api/campus/profile` | 3702 | coded-shape |
| GET | `/api/campus/refresh` | 3704 | coded-shape (offline-disabled status only) |
| GET | `/api/campus/updates` | 3706 | coded-shape |
| GET | `/api/campus/scoring` | 3708 | native-subset (local category scoring only) |
| GET | `/api/campus/items/[a-f0-9]{64}` | 3710 | coded-shape |
| GET | `/api/sources` | 3712 | prior-partial |
| GET | `/api/accounts` | 3714 | coded-shape |
| GET | `/api/backups` | 3716 | native-subset |
| GET | `/api/imports` | 3718 | coded-shape |
| GET | `/api/imports/[A-Za-z0-9_-]{8,40}` | 3720 | coded-shape |
| GET | `/api/settings` | 3725 | prior-partial |
| GET | `/api/wecom-bot/identity` | 3735 | provider/auth open |
| GET | `/api/wecom-bot-sends` | 3737 | provider/auth open |
| GET | `/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}` | 3740 | provider/auth open |
| GET | `/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/approval-challenge` | 3745 | provider/auth open |
| GET | `/api/auto-export` | 3749 | native-subset (private operator list only) |
| GET | `/api/moments` | 3751 | coded-shape (ordinary native-live, exact detached-text account only; no provider/media) |
| GET | `/api/moments/export` | 3751 | coded-shape (ordinary native-live, complete filtered detached text in JSON/CSV only) |
| GET | `/api/mcp` | 3808 | coded-shape |
| GET | `/api/operations` | 3852 | coded-shape |
| GET | `/api/conversations` | 3854 | prior-partial; source alias also on exact ordinary native-live Desktop |
| GET | `/api/contacts` | 3856 | prior-partial; redacted alias also on exact ordinary native-live Desktop |
| GET | `/api/conversation-members` | 3860 | coded-shape |
| GET | `/api/messages` | 3862 | prior-partial; redacted alias also on exact ordinary native-live Desktop |
| GET | `/api/context` | 3889 | prior-partial; normalized ID context alias also on exact ordinary native-live Desktop |
| GET | `/api/quoted-message` | 3912 | prior-partial |
| GET | `/api/search` | 3931 | prior-partial; redacted alias also on exact ordinary native-live Desktop |
| GET | `/api/media` | 3934 | coded-shape |
| GET | `/api/stats` | 4023 | coded-shape |
| GET | `/api/sends` | 4033 | provider/auth open |
| GET | `/api/qq/send-readiness` | 4036 | provider/auth open |
| GET | `/api/wechat-send-readiness` | 4039 | provider/auth open |
| GET | `/api/wechat-sends` | 4042 | provider/auth open |
| GET | `/api/wechat-sends/[A-Za-z0-9_-]{8,80}` | 4045 | provider/auth open |
| GET | `/api/sends/[A-Za-z0-9_-]{8,80}` | 4050 | provider/auth open |
| GET | `/api/analytics/private` | 4055 | coded-shape |
| GET | `/api/analytics/self-sent-daily` | 4060 | coded-shape |
| GET | `/api/analytics/groups` | 4064 | coded-shape |
| GET | `/api/analytics/relationships` | 4079 | coded-shape |
| GET | `/api/analytics/relationships/timeline` | 4084 | coded-shape |
| GET | `/api/analytics/relationships/achievements` | 4091 | coded-shape |
| GET | `/api/reports/annual` | 4097 | prior-partial |
| GET | `/api/reports/pair` | 4102 | prior-partial |
| GET | `/api/qq/local-status` | 4113 | provider/auth open |
| GET | `/api/qq/login-status` | 4113 | provider/auth open |
| GET | `/api/qq/key-discovery` | 4117 | provider/auth open |
| GET | `/api/qq/history-import` | 4119 | provider/auth open |
| GET | `/api/media/resolve` | 4121 | coded-shape |
| GET | `/api/voice/transcription-status` | 4123 | coded-shape (automatic disabled status) |
| GET | `/api/resource-jobs/[A-Fa-f0-9]{20}` | 4125 | coded-shape |
| GET | `/api/export` | 4130 | coded-shape |
| GET | `/api/export/contacts` | 4204 | coded-shape |
| GET | `/api/export/group-members` | 4216 | coded-shape |
| POST | `/api/account-safety/activity` | 4243 | deliberately-absent |
| POST | `/api/account-safety/disable` | 4243 | deliberately-absent |
| POST | `/api/account-safety/lock` | 4243 | native-subset |
| POST | `/api/account-safety/setup` | 4243 | native-subset |
| POST | `/api/account-safety/unlock` | 4243 | native-subset |
| POST | `/api/wecom-bot-sends/attachment` | 4258 | provider/auth open |
| POST | `/api/backups/restore` | 4261 | native-subset |
| POST | `/api/imports` | 4264 | coded-shape (explicit JSON or direction-bearing text CSV raw staging only) |
| POST | `/api/imports/preview` | 4264 | coded-shape (bounded JSON or explicit-direction text CSV) |
| POST | `/api/resource-jobs/([A-Fa-f0-9]{20})/cancel` | 4267 | coded-shape |
| POST | `/api/imports/([A-Za-z0-9_-]{8,40})/cancel` | 4275 | coded-shape |
| POST | `/api/campus/wechat-accounts/import` | 4293 | provider/auth open |
| POST | `/api/campus/profile` | 4295 | native-subset (display preferences only; no student fields) |
| POST | `/api/campus/updates` | 4297 | unresolved |
| POST | `/api/campus/scoring` | 4299 | native-subset (local CAS policy) |
| POST | `/api/campus/scoring/run` | 4301 | native-subset (explicit local bounded result, no model) |
| POST | `/api/campus/sources/[A-Za-z0-9_-]{1,128}` | 4305 | native-subset (known-source detached import registry only) |
| POST | `/api/campus/refresh` | 4307 | provider/auth open |
| POST | `/api/campus/wechat` | 4311 | provider/auth open |
| POST | `/api/campus/items/[a-f0-9]{64}/state` | 4315 | coded-shape |
| POST | `/api/campus/events/[a-f0-9]{64}/state` | 4317 | native-subset |
| POST | `/api/campus/items/[a-f0-9]{64}/fetch` | 4319 | provider/auth open |
| POST | `/api/notifications/test` | 4323 | deliberately-absent |
| POST | `/api/notifications/message` | 4327 | deliberately-absent |
| POST | `/api/notifications/activate` | 4333 | native-subset (signed native pull target, not a browser activation alias) |
| POST | `/api/voice/transcribe` | 4338 | coded-shape (manual read only) |
| POST | `/api/qq/forward-resources` | 4340 | provider/auth open |
| POST | `/api/sends/prepare` | 4357 | provider/auth open |
| POST | `/api/sends/[A-Za-z0-9_-]{8,80}/approve` | 4362 | provider/auth open |
| POST | `/api/sends/[A-Za-z0-9_-]{8,80}/reject` | 4367 | provider/auth open |
| POST | `/api/wechat-sends/prepare` | 4371 | provider/auth open |
| POST | `/api/wechat-sends/send-now` | 4374 | provider/auth open |
| POST | `/api/wechat-sends/[A-Za-z0-9_-]{8,80}/approve` | 4376 | provider/auth open |
| POST | `/api/wechat-sends/[A-Za-z0-9_-]{8,80}/reject` | 4382 | provider/auth open |
| POST | `/api/wecom-bot/identity` | 4386 | provider/auth open |
| POST | `/api/wecom-bot/identity/remove` | 4398 | provider/auth open |
| POST | `/api/wecom-bot-sends/prepare` | 4407 | provider/auth open |
| POST | `/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/approve` | 4423 | provider/auth open |
| POST | `/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/reject` | 4429 | provider/auth open |
| POST | `/api/sync` | 4440 | provider/auth open |
| POST | `/api/data/delete` | 4445 | deliberately-absent |
| POST | `/api/conversation-metadata` | 4447 | coded-shape |
| POST | `/api/contact-identities` | 4451 | coded-shape |
| POST | `/api/contact-identities/unlink` | 4454 | coded-shape |
| POST | `/api/settings` | 4457 | coded-shape (native notification filters only) |
| POST | `/api/auto-export/configure` | 4492 | native-subset (private scoped creation only) |
| POST | `/api/auto-export/run` | 4495 | native-subset (one-shot confirmed local execution only) |
| POST | `/api/auto-export/cancel` | 4498 | native-subset (private scoped cancellation only) |
| POST | `/api/attachments/download` | 4501 | provider/auth open |
| POST | `/api/resource-jobs` | 4503 | native-subset |
| POST | `/api/export/bundle` | 4509 | native-subset (one selected imported conversation) |
| POST | `/api/source/email/accounts/[a-f0-9]{24}/remove` | 4523 | provider/auth open |
| POST | `/api/source/{source}/configure` | 4527 | provider/auth open |
| POST | `/api/qq/start` | 4530 | provider/auth open |
| POST | `/api/qq/key-discovery` | 4533 | provider/auth open |
| POST | `/api/qq/history-import` | 4535 | provider/auth open |
| POST | `/api/qq/history-import/cancel` | 4537 | provider/auth open |
| POST | `/api/backups` | 4539 | native-subset |

044 adds separate scoped MCP HTTP read transport and manual export presets. Neither maps the old MCP POST or automatic scheduled export routes; their classifications stay unchanged.

045 partially maps media resolve to explicitly imported local objects only; the original provider resolution/credentials remain unsupported. The added GET job list is not an original GET template and does not increase the legacy denominator. No qualification changes.

## Freeze / replacement decision matrix

The per-route rows above are authoritative for all 124 pairs. A
code-freeze label means the chosen local product feature is implemented in
source and ready to enter review once approved, **not** that the corresponding
stock route is complete. This is the first candidate for one unified local
pass, not the first full replacement release.

| Route group | Local freeze scope | Not implemented / required evidence |
| --- | --- | --- |
| Conversations, accounts, contacts, context, quotes, messages and search | Exact account projections and redacted history on local native/detached or narrowly guarded transport as listed; distinct profile privileges still apply | Full stock parameters, multi-account presentation, original browser-client wiring and exact combined ordering/count semantics not qualified |
| Archive, import, media, asset jobs and exports | Explicit operator-owned, detached/fresh migrations and encrypted guard backup; narrowly scoped selections, verified imported-media reads/local jobs, manual export and private retention plan | No native-app cache restore, arbitrary provider files/URLs, auto-export, cloud backups or whole-stock export bundle |
| Scoped account safety, settings, discovery and desktop lifecycle | Mode-specific local permissions and opt-in supervisor, portable provenance, saved client preferences, local discovery | Stock global writes/deletes, browser password API and complete client/platform lifecycle not offered; actual packaged Windows/macOS/Linux qualification deferred |
| Campus, statistics, relationships and manual voice metadata | Detached profile, dated local daily view, truthful idle status, explicit bookmarks/groups/preferences, profile-safe analytics | Stock ranked/selected Campus events, source registration and fetch, scoring models, model voice transcription and unmodified browser parity not implemented |
| MCP discovery and read tools | Read-only exact stdio/session scope where existing, separate narrow HTTP snapshot read transport, no process creation by discovery | Not full Streamable HTTP sessions/tools/SSE nor generic browser integration, and no ability beyond granted exact records |
| Provider status, login/sync, raw extraction, remote media, sends | Narrow detached adapters / authorized operator foreground IMAP code; private drafts, review and a local packet acknowledgement that is **not** delivery | QQ/WeChat/WeCom live capabilities need a published/supported upstream data contract and user consent; any live email test needs approved scope/host/mailbox, real sender requires explicit applicable authority and verified delivery semantics |

`GET /api/campus/briefing` accepts
`section=daily&date=YYYY-MM-DD` with optional exact profile and 1–50 rows
(first page only), and [explicit section=all](CAMPUS-BRIEFING-ALL.md) with
ungrouped visible imported article cards, search and pagination. These are
not stock selected, scored or grouped stories. `GET /api/campus/refresh`
reports an idle, unsupported refresh and a scoped imported index status;
there is no refresh POST handler. The stock frontend does not honor the
added support flag and may display an unusable refresh action. Do not use
this as evidence for unchanged WebUI operation or enable Campus behind
the existing chat-only cookie/guard.

A documented supported upstream adapter, explicit permission, and a test
environment with synthetic or authorized provider data are separate gates,
not gaps fixed by additional Rust lines. All compat categories above stay
unqualified until the matching pinned behavior is actually compared.

047 continues the pinned personal WeChat and rendered WeCom local
source contracts in
[WeChat / WeCom integration](WECHAT-WECOM-INTEGRATION.md).
No additional pinned legacy endpoint is declared achieved by that work;
in particular `POST /api/sync`, provider config/credentials and WeCom Bot
outbound operations remain provider/authorization blockers.

048 updates the restored WeCom path so only a complete local metadata
generation can reconcile direct/group hints; no original provider route
changes classification.

049 adds an ordinary-live settings alias for exact desktop notification
filters, plus old voice status and manual-read lookup aliases. They do
not enable Python global settings, delivery or automatic model execution.
The old GET settings count remains historical partial (guarded shape);
the new native shape exposes only grant-permitted local preferences.
Campus deterministic local scoring is native-only; old remote scoring
status, configuration and explicit run are not implemented.

050 adds a bounded native imported-media catalog and a local export
schedule ledger core with no route or execution surface. No pinned old
method/path changes category and the stock automatic export API is absent.

051 adds an explicit local Linux quarantine/release of catalog visibility
only. Direct ciphertext access and provider/HTTP backup routes are not
changed; route categories remain unchanged.

052 adds a private, bounded export artifact render/confirmation/read core.
There is still no operator driver, timer or legacy automatic-export
interface; the corresponding old route categories do not change.

053 offers scoped Campus local display defaults and a separate
visible-only feed. That is a native subset of a local profile capability,
not an alias of old student profile writes. Full qualification stays zero.

054 adds a confirmed one-shot private Linux operator workflow for own
local export schedules and artifacts. The four old auto-export literals
remain **not mounted**; they are native subsets only because a related
opt-in private interface now exists. No timer/startup action or old
WebUI compatibility is claimed.

055 adds a verified imported attachment + selected messages ZIP only in
the native Linux media mode; the old export-bundle route is not mounted.
Mixed jobs are not visible through legacy job aliases.

056 adds separately approved, revocable local recurring export policy and
an explicitly started bounded Linux foreground worker. It supports only
private archive artifact publication; none of the legacy HTTP auto-export
literals or a service/timer is installed. Existing ledger counts stay unchanged.

057 adds a separate native exact-source one-shot job handoff. The original
global sync route still must not be characterized as implemented; no
provider, process, or automatic work is triggered by the handoff.

058 adds durable exact-grant notification metadata pull/ACK. No original
notification test/message/activate route is mounted: these initiate OS
delivery or activation, unlike local preview acknowledgment. Ledger
coverage categories are unchanged.

059 adds an explicit detached text Moments catalog and separate scoped
read and JSON page export. The two old routes remain unmounted and raw
client/provider collection remains unavailable.

060 can bind a current exact-scope claimed WeCom job to one explicitly
confirmed detached import and offline receipt. No acquisition, live
source completion, legacy sync route or change to legacy categories.

061 adds display of generated local artifacts and manually confirmed exact
slot reclamation. No old setting/auto-export/backup or broad delete route
is mounted. Qualification and legacy categories stay unchanged.

062 records a manual, bounded local Campus scoring result in the native
exact-profile interface only. The old remote model run route is not
mounted, and automatic updates/source activation remain unresolved.

063 binds a freshly claimed, exact personal-WeChat source job to one
separately authorized detached text batch. The original acquisition and
global sync route are still unavailable; no old category changes.

064 introduces opt-in native already-imported attachment descriptors on
an exact-message live witness; it cannot acquire media, send bytes or
replace any original media route. No category changes.

065 extends the existing explicit pinned-TLS operator email lane to one
bounded selected UID batch. The pinned legacy account configuration,
automated sync and related provider routes remain unsupported; no ledger
category changes and no network use was performed.

066 adds a disconnected forward-compatible WeCom text protocol state
machine and exact-content review proof only. No sender transport, user
recipient contact, delivery, credential deployment or old send route
is mounted; provider/authorization categories are unchanged.

067 adds private durable WeCom Bot release-attempt storage for a future
verified controller. No actual transport, caller, approval route, external
action or old-provider route is mounted.

068 adds a scoped known-source Campus registration policy for future
detached imports, not old provider source activation. It does not
schedule remote fetching or mutate existing articles; the legacy alias
remains unmounted.

069 extends the managed backend client SDK with typed scoped native
search, centered context and supported WeChat quote context. No new host
route, permission or legacy path is added.

070 adds typed notification preference/pull/ACK helpers for the existing
exact native-live backend API. No host route, delivery, device activation
or original notification interface is enabled.

071 adds a private detached verified voice WAV handoff, not a media
resolver or transcription model. Old route categories are unchanged.

072 adds a typed managed backend client for existing native local import
jobs, with caller-owned bytes and confirmation, not an old import route
or automatic job. Legacy categories unchanged.

073 adds separately authorized verified bytes for a small immutable
already-imported attachment on the optional live link. No legacy route,
provider fetch, raw path, or general resource acquisition is enabled.

074 adds bounded owner-loss draining for the macOS ordinary native-live
managed child and SDK mode selection. No extra authorization, platform
runtime qualification, legacy API or guarded/media supervisor widening.

075 adds exact already-authenticated claim acknowledgement recovery before
the original deadline and a truthful expired state. This is not a second
execution, requeue, renewed lease, external action, or legacy sync.

The guarded bearer annual/pair aliases now also handle omitted year for the
exact granted account/private conversation. Details in [LEGACY-HTTP.md](LEGACY-HTTP.md).
This leaves both already-counted aliases partial: the original unrestricted,
counterpart-selected, and identifying sections are not implemented.

The guarded legacy reports add a fixed-vocabulary `message_types` aggregate for
the pinned type chart/CSV. Both routes remain partial; no classification or
qualification count changes.

086 accepts already authorized original Hub WeChat text exports in the exact
claimed offline workflow without changing old IDs. No acquisition, legacy
`/api/sync`, provider cursor or full route coverage is added.

087 adds deterministic inventory and one-claim-per-page ingestion of larger
already authorized old WeChat JSON exports. No legacy route, automatic
backfill, or provider acquisition is added.

089 adds private exact-proof read-only recovery of an existing claimed
WeChat receipt. No legacy route or provider workflow changes.

090 adds exact-proof read-only recovery of a claimed WeCom detached batch,
including only its verified matching head continuation when current. No
legacy route, old provider cursor or sync was implemented.

095 adds the optional exact legacy raw upload submit literal for staging
only. It requires a native bearer, explicit client-retained idempotency
key and a separate approved native commit; the untouched stock browser
and automatic submit-to-import behavior are not supported.

Guarded v9 legacy conversation paging now preserves relative source tie order,
using an exact account/conversation/filter/grant cursor separate from search.
The original `/api/messages` remains partial (no stock global row-ID tokens,
FTS data, raw media, or browser compatibility). No route count changes.

The guarded native bearer `/api/context` can now resolve an omitted conversation
using an exact-account normalized message ID. The browser/v1 and quote
contracts remain strict; the cookie message page also retains its pre-096
pagination policy. Existing aliases remain partial; no count change.
