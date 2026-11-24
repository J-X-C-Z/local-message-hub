# Filtered conversation export with verified local media (rebuild-078 candidate)

The pinned WebUI's “附带已解析媒体” export calls
`POST /api/export/bundle` with a conversation filter and later downloads its
job. This implementation restores **filter-driven selection**, not original
WebUI authentication, search-wide export, remote retrieval or other output
formats. It is native-only, on Linux's existing imported-media listener.

`POST /api/v1/resource-jobs/create-filtered-bundle` requires the existing
exact-account Desktop `messages.read`, `media.read` and `messages.export`
permissions. Send a JSON object containing `query` with exact `platform`,
`account_id` and `conversation_id`; optional `q`, `type`, `start`, `end`
and `format` use the scoped message reader's semantics. `format` may be
omitted or be `json`. Date-only bounds are inclusive at UTC+08 local day
start/end; explicit timestamps use the same reader parsing. Inverted bounds
are rejected. Keyword is a literal substring (including SQL wildcard
characters) with the same optional FTS intersection as the message reader;
order is timestamp descending, then message ID descending.

The request has no pagination cursor, batch fallback, wildcard account,
caller-supplied attachment ID, external URI, or source-cache path. At most
16 matching messages and at most 16 imported catalog attachments are allowed.
The server stops at one extra matching ID without a full count query, on the
existing read-only bounded SQL connection. It selects only already bound
exact catalog associations. The shared serializer caps each body at 64 KiB
and total message JSON at 768 KiB. Full known blob content is verified and
shares the original 4 MiB source and 4 MiB + 64 KiB ZIP limits. A limit or
verification failure returns no job; nothing is truncated.

No matching rows returns not found. If there are matching messages but zero
qualified catalog associations, default is also not found. To **explicitly**
accept a text-only ZIP for that latter case, set top-level
`allow_text_only:true`. It cannot bypass a broken, oversized or unverifiable
selected blob. Absence of qualified catalog media is not proof the original
messages had no media. The archive uses the existing redacted
`messages.json` and manifest; it does not guess filenames or disclose raw
metadata fields.

The job uses the existing v1 list/status/download/cancel routes and
short-lived owner-bound cache. No legacy create alias or legacy access to
mixed job contents was added; the stock browser redirects to a download
URL without this listener's bearer authorization. No changes to guarded,
browser, snapshot-only, ordinary native-live or provider access.

## Static rollback and verification handoff

This is an additive route with no new database/schema/credentials dependency.
Revert its source commit to remove it and restore the original filter helper
layout. Restarting the media listener clears all ephemeral jobs (as before);
no on-disk export or pending provider action needs cleanup.

When the deferred consolidated test phase is approved, cover duplicated
conversation IDs across accounts, filter dates at the UTC+08 boundaries,
literal `%`, `_` and backslash, equal-timestamp order, exact result cap and one
past it, no attachments with/without explicit opt-in, late corrupted blob
with no publication, and rejection by legacy and unauthorized mixed-job
retrieval. No builds, tests, benchmarks or runtime checks were run here.

## Optional deferred local job (085)

Supply top-level `deferred:true` on the same v1 filtered create request.
The selector, three existing permissions, Linux imported-media listener,
hard limits and artifact are unchanged. Omitting it preserves the
synchronous ready-only contract, and no other create operation changes.
A deferred request first reserves an owner-bound job and returns
`state:"queued"` with its v1 `job_id` before local SQL/hash/ZIP work.
Poll native status/list for `queued`, `running`, `ready`, `failed`,
or `cancelled`. This is stage status, not a claimed row percentage.
A pending or failed job is not downloadable; for those entries the
existing `bytes`/`entries` are zero and `sha256` empty. Failure adds a
coarse fixed `failure` code without SQL details, paths, filters or text.

One deferred work slot per listener is independent of the normal status
and transfer limits; a second deferred request returns busy. The existing
global eight-job / exact-owner two-job ledger caps also apply; all stages
expire from the original reservation after five minutes, and no polling
extends that time. Jobs, request filters and artifacts stay in process:
restart or shutdown loses them, with no replay or queued external action.

Use existing native cancel with that exact job ID. Cancellation marks
queued/running entries cancelled under the same lock as publication;
worker checkpoints stop further selection or ZIP work between bounded
operations. An already running SQLite query/read may finish its bounded
step but cannot publish after cancellation wins the lock. If publication
wins first, cancel removes the ready artifact as before; a transfer
already begun cannot be recalled. Stale/expired reservations cannot be
recreated by a late worker. Transfer/list/status/cancel continue requiring
the exact owner and mixed export permission. Legacy job routes still do
not handle mixed jobs and their semantics are unchanged.

Static rollback: revert the 085 source commit and restart the media
listener to discard any in-memory reservation. No DB schema, filesystem
artifacts, external scheduler or runtime migration. Deferred verification
should include cancel-before-start, cancel-while-checking the last blob,
finish-before-cancel ordering, worker join failure, exact-account denial,
expiry during work, waiting status under worker saturation and unchanged
synchronous/public legacy compatibility. No runtime checks were performed
for this increment.
