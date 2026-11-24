# One claimed WeCom job to one detached import (060, source only)

This is a foreground Linux operator composition of the existing exact native
source job and WeCom rendered local snapshot import. No provider connection,
watching, process launch, callback, HTTP file access, remote request, login,
protected data extraction, background execution or message send occurs. It
does not implement the old Python `/api/sync`. The detached file is assumed
independently and lawfully acquired. Hashes attest exact selected bytes,
not their provider origin or the sender's identity.

1. A trusted launcher obtains the existing exact WeCom
   `scoped-producer` grant with both `messages.ingest` and
   `sources.jobs.manage`. The ordinary native host issues its normal
   source job, and the existing local opt-in endpoint publishes and claims
   the job. Only WeCom claims made under this version bind the actual
   host-authenticated claimant bearer (as a domain-separated digest, never
   plaintext). Historical claims without the binding are not eligible;
   create and claim a fresh manual job through normal policy.
2. The same trusted launcher invokes:
   `preview-claimed-wecom-sync --source FILE --directory ARCHIVE --grants PRIVATE_GRANTS`.
   The source, archive and grants paths are explicitly chosen local CLI
   arguments only, never sent to an HTTP service. Private grants file
   validation uses the existing Linux no-follow, regular/current-owner,
   one-link, bounded reader. Both stdin and stdout must be separate
   anonymous pipes; the launcher is responsible for privacy.
3. Send a single private stdin JSON line with exactly `token` (the original
   claimant bearer), `job_id`, `expected_version`, and `lineage`. The
   launcher checks the returned metadata against its intent and obtains
   operator approval. The preview refuses unclaimed, changed, expired or
   already linked jobs, mismatched scope/checkpoint and invalid detached
   bytes. Fresh grant authentication is separate from the bearer binding
   inside the claimed job. Neither the job ID nor a preview digest can
   authenticate or authorize a grant.
4. Invoke `import-claimed-wecom-sync` with the same local paths and frame,
   additionally passing the preview's `confirmation` in the private stdin
   frame. The domain-separated digest binds archive, exact scoped lane,
   job ID/version and confirmed detached bytes (including lineage). In the
   write transaction, Rust rechecks the active claimed job, grant token
   binding, deadline, explicit lineage/previous checkpoint and batch scope.
   It then records one immutable job-to-detached-receipt relation, the
   existing ordinary offline ingest receipt, the existing progress cursor
   and any rows/metadata together, or commits none of them. Prior standalone
   imports cannot be retrospectively relabeled as linked receipts.

One job may authorize at most one detached batch. A successful linked import
does **not** complete the ordinary source job, advance the live producer
cursor, trigger live notifications, launch an adapter, or promise another
batch. Only the existing offline WeCom cursor advances. The job remains an
operator coordination record; a subsequent batch needs a different claimed
job under the normal limits and the next detached checkpoint.

If the result is lost, an exact already-committed retry reauthenticates with
the original bearer and returns only its exact same bound receipt, even
after job expiry or a successful later stored cursor. No retry performs a
new write after expiration, and a different job, bearer (including a
rotation), version, lineage or byte hash cannot reuse the receipt. A stale
job or expired uncommitted request requires new operator coordination.
No secrets or file contents are returned. The local operator still controls
the archive and its private registry as before.

Compilation and runtime validation are deferred to the unified phase.

## Exact read-only recovery and continuation position (090)

If the claimed import response was interrupted, `status-claimed-wecom-sync`
accepts the same three local path arguments, private pipe frame, original
bearer, selected source file and exact operator confirmation as import. It
validates the current grant and re-derives the original receipt request.
The verification uses a read-only SQLite snapshot: both the immutable
claimed receipt and current matching lineage head must agree with the
original writer replay rules, including the account, exact source bytes,
normalized ingest digest, bound claimant, job/version and checkpoint.
No claim or database record is created, replayed or amended.

A confirmed receipt returns `state:"detached_batch_recorded"` and scoped
counts, plus `position:"at_head"` or `position:"superseded"`. Only
`at_head` supplies the exact verified next *offline snapshot* checkpoint;
it is no guarantee that later requests will observe the same head, and the
next commit must independently validate its own current starting point.
A superseded receipt is still proof that this batch committed; it exposes
neither any newer cursor nor a stale checkpoint to reuse. Metadata-only or
zero-new-row successes still count as committed if bound evidence exists.
No HTTP, credential output, provider cursor, adapter run or job completion
is implied. The old writer recovery and public local status are unchanged.

Wrong, missing, unavailable or inconsistent evidence (including a missing
claim, receipt or progress table) produces only `state:"not_confirmed"`,
which cannot be taken as proof of rollback or permission to try again.
A valid committed receipt remains recoverable after expiry with the
unchanged exact authorization. Source-only; no compiler, tests, real input
or provider operation has been used. Reverting removes this command, not
any committed records.

Claimed previews, apply, and exact receipt lookup also accept the explicit
[rendered v2 plaintext-notice subset](WECOM-DETACHED-SYNC.md#explicit-v2-original-rendered-plaintext-notices).
Its original format and bytes remain part of the existing confirmation;
this does not change claim authority, provider progress, or job completion.

## Frozen original rendered JSONL, incremental only

The pinned [engine append writer](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/integrations/wecom/engine.py#L978-L995)
writes one rendered record per line and deduplicates by the stringified
message ID. It is not a full snapshot of conversations or a provider
position. The CLI does not locate this file or open a live integration's
data directory. Supply only a **separately authorized, frozen, detached
copy** as `--source`, never an active log, protected database/cache,
application directory or provider path.

Linux private-pipe preview accepts this optional field in the existing
claimed operator frame:

```json
{
  "rendered_jsonl_page": {
    "account_binding": "authorized_frozen_wecom_rendered_copy",
    "account_id": "<exact scoped grant account>",
    "source_sha256": "<independently approved SHA-256 of the whole frozen copy>",
    "start_line": 0,
    "row_count": 25
  }
}
```

The rest of the preview (private token, claim job/version, explicit lineage)
stays the same. The account and hash are the trusted operator's explicit
attestation, not provider authentication; older original lines have neither
field. Any explicit per-row account or platform must match, even outside the
selected page. A whole-file hash mismatch, mixed explicit scope, malformed
line, repeated stringified message ID, or missing/invalid Boolean `me`
fails the preview. No account, sender or message identity is inferred from
a label, line number, filename or the string `local`. The preview requires
the current valid claim before processing the detached copy. It emits
review metadata only, never body text, raw records, or a credential.

The frozen copy is limited to 8 MiB and 10,000 LF/CRLF-terminated records.
A selected contiguous line range (zero-based) is at most 100 records and
512 KiB raw, and passes the existing normalized byte and field limits.
The original normalized message/conversation IDs, time and direction are
retained. Unsupported media/placeholder lines within that span are counted,
not decoded, followed or copied; no-text pages cannot advance. The same
v2 plaintext-notice subset applies. Unknown extra fields and provider
identifiers are not stored. No roster, generation-completion marker, live
event, provider history position, or implicit completion of a claim is produced.

Use this only for a separately authorized **incremental lineage with no
preexisting rendered-sync head for the account**. Preview derives the
exact next local position; it never derives one from a provider, file
offset or current time. Copy the returned `rendered_jsonl_page` including
its generated `position` verbatim into apply or receipt-status alongside
the explicit preview confirmation. Status reconstructs that original
selection rather than using the latest head, so it can distinguish an old
receipt from a new proposal after later imports. A subsequent apply
compares that position with the current head in its existing writer
transaction. A competing write or an already-spent nonmatching claim
cannot advance the same position. An exact completed retry uses the
existing durable receipt.

The first successful import records the incremental mode and lineage in
the same transaction as the messages, receipt and local checkpoint; mixed
ordinary-envelope/JSONL heads and other lineages are refused. This mode
stays in generation 1 with `complete:false`; selecting distinct line ranges
does not attest that no lines were omitted. Do not downgrade a writer once
this mode is used: older writers do not recognize the mode marker. A source
rollback before use requires no migration; afterward retain a compatible
writer and the exact copied artifact for recovery. No runtime or provider
verification was performed.

## One frozen rendered full snapshot (117)

For the default old local use case, the pinned
`hub/server.py:1264–1297` calls refresh once, then lists conversations
and iterates the already rendered messages. The pinned
`integrations/wecom/adapter.py:98–131` defines both emitted row shapes;
the original does not emit a single combined JSON file for that call.
An independently authorized operator can package those **already rendered
outputs from one refresh** into one detached UTF-8 JSON object:

- `format: "wecom-frozen-rendered-pair-v1"`
- `account_binding: "authorized_single_rendered_refresh"`
- `account_id`: exact account granted to the local producer, explicitly
  supplied (the old Hub used `local`).
- `conversations`: original objects with `conversation_id`, `name`, and
  numeric `participants`. Original additional read-only fields are ignored.
- `messages`: the original rendered row shape including Boolean `me`.
  Present platform/account fields must match, and each row must refer to
  a conversation in this file.

An account/source attestation is not vendor authentication, live access,
or proof the operator obtained every upstream row. Do not supply raw
client files, credentials, keys, media bytes, or network endpoints.

Run Linux `inventory-wecom-frozen-snapshot --source FILE --directory ARCHIVE
--grants PRIVATE_GRANTS` via the existing separate anonymous private pipes,
with a single input frame containing `token` and `lineage`. The exact
scoped producer must have ingest and source-jobs management capabilities.
The read-only result contains the whole file SHA-256 and compact page
arrays in the declared `page_fields` order: zero-based index, raw token
bytes, conversation count, message count, normalized text count, skipped
count, normalized byte count, and SHA-256 of the bounded normalized
message array. No source data or paths are returned and no job is created.

The single file is limited to 8 MiB, 10,000 combined rows, and 128
deterministic pages. The original list order is retained: conversation
rows precede message rows; each page is limited to 100 combined rows and
512 KiB of raw row tokens/array overhead. The existing normalizer caps
serialized text messages to 512 KiB per page. Repeated message IDs or
conversation IDs are rejected globally, as is a message outside the
declared exact account or roster. All pages must pass the existing v2
text/selected-notice projection and safe metadata checks before any page
is applied. Metadata-only pages are allowed; an explicitly empty pair
has one empty completion and does not clear existing rows or hints.

Use the existing claimed preview/import/status command with **one
independent claim per page** and `frozen_snapshot_page` containing
`file_sha256` and zero-based `index`. Preview derives the exact next
local generation and prior local checkpoint, returns the same selector
with `position`, and returns the usual confirmation. Preserve that
selector and confirmation for import or later read-only status. The
proof binds these fields to the exact file bytes, account, lineage,
archive and claim. The file cannot change between pages.

The first transaction pins the mode, hash, total page count and committed
ordinal for an account with no rendered-sync head. In one writer
transaction each later page checks the pinned hash, ordinal and existing
local checkpoint, and atomically advances them with the messages and
immutable claim receipt. **Only the last exact page** can publish the
existing complete-generation roster kind/member-count hints. Previous
page messages are intentionally durable, not whole-file rolled back if
a later page conflicts. A new generation starts at page zero from a
different frozen hash only after the previous one completes; omissions
do not remove indexed messages or conversations, and changed existing
message content or foreign ownership fails closed rather than rewriting
history. Replays remain exact-claim/exact-file bound; a superseded receipt
does not disclose an unrelated head.

This is a distinct mode, never silently adopted by old standalone
envelopes or the incremental JSONL mode. No HTTP endpoint, automated
refresh, completion of a source job, provider cursor, acquisition,
client/cache access, or actual live provider action is added. If you
revert this code after starting this mode, do not use any earlier writer
on that archive: pre-117 writers cannot enforce its mode marker. Keep
the original archive backup for rollback, or retain this writer to
finish the frozen import. Source only, pending unified verification.

### Check whether the exact snapshot needs any work (118)

The pinned old refresh submits again on request
(`hub/server.py:1264–1297`), but its stock message UPSERT only updates rows
whose values differ (`hub/store.py:699–723`). The bounded local workflow
can now distinguish an exact completed snapshot from a refresh that needs
a new claim, without submitting a no-op job.

Run Linux `inspect-wecom-frozen-snapshot --source FILE --directory ARCHIVE
--grants PRIVATE_GRANTS` over the existing separate private pipes. Supply
one private frame containing `token`, `lineage`, and the exact
`file_sha256` returned by the inventory. It verifies the current exact
WeCom producer grant and the complete detached source, then compares the
single coherent local marker and local checkpoint in one verified read
transaction. It never reserves a job, emits a checkpoint value, or starts
an adapter.

- `local_projection_already_applied`: the current completed local marker
  matches this exact file, page layout and lineage. No new claim needed.
- `resume_matching_snapshot`: the current incomplete marker matches
  this exact file and layout. `next_index` is the next uncommitted
  zero-based page to claim separately.
- `ready_first_generation` or `ready_new_generation`: no rendered-sync
  head yet, or this file differs from the same lineage's completed current
  file. Page zero can be submitted separately; nothing is reserved.
- `continuation_unavailable`: the current head cannot safely support
  continuation. Incomplete different snapshots, other lineages, competing
  modes and inconsistent markers share this result; no unrelated current
  identifier, hash, checkpoint or progress is disclosed.

The result describes the current Rust projection, not a supplier's
coverage, vendor history, or an audit of every stored message. It says
nothing about an older snapshot that is no longer the current head,
and another writer may advance the state before a later claim. Existing
per-page claims and immutable receipt checks still govern all writes.
The inspection introduces no schema/data changes; source rollback removes
it without affecting existing snapshots and claims. Source only pending
unified verification.

### Replace an interrupted frozen snapshot without losing committed rows (119)

The pinned original local operation takes a new rendered snapshot on the next
manual refresh (`hub/server.py:1264–1297`); it has no resumable upstream
position. If the current Rust generation is still incomplete and the exact
original file is unavailable, an operator who retained its whole-file SHA
can explicitly submit a **different independently authorized frozen snapshot**.

Inventory the replacement as usual. In the existing private, claimed
`preview-claimed-wecom-sync` frame for the replacement's page zero,
include `replace_incomplete_sha256` inside `frozen_snapshot_page` with **the
old file's hash**. Use the same exact account/lineage and a fresh page-zero
claim. The private preview derives the new generation and prior local
checkpoint. Preserve the returned selection, including its unchanged
replacement hash and returned `position`, and pass the explicit confirmation
to the normal import command. The new confirmation domain binds both file
hashes, the exact old local checkpoint, successor generation, first page,
archive, account, lineage and active claimant. If the old hash was not
retained, do not guess: no recovery command reveals the current file hash.

The writer rechecks that the exact old generation is still incomplete, that
its marker and local progress agree with the reviewed proof, and that the
authorized replacement is page zero of the successor generation. It then
retires only the old account/generation's **unpublished staged roster** in
the same SQL transaction that appends the replacement page, records the
claim receipt and replaces the active marker. If that transaction fails,
the original generation, staging and receipts remain intact. Existing
messages and committed receipts are never deleted or revised; exact old
claim receipts remain available as `superseded`. The old generation is
never marked complete. A replacement with more pages can only publish
its own roster once its last page commits normally.

There is no standalone abandon/delete step, no automatic reset, and no
interaction with the source app or provider. Other modes, completed heads,
stale/nonmatching proofs, nonzero first pages and identical hashes cannot
take this path. Previous selectors/confirmations remain unchanged when
the replacement field is omitted. No schema change is required. Reverting
source code leaves the already recorded successor head and old receipts
intact, but pre-119 writers cannot replay a receipt using the new proof;
retain the current writer for exact retry or restore a pre-replacement
archive backup to undo the state. Source only pending unified verification.

### Last completed conversation roster titles (120)

The pinned refresh passes its rendered conversation list to the Hub
(`hub/server.py:1279–1294`), and the pinned archive upsert replaces the
conversation title with the new source title (`hub/store.py:662–686`).
The Rust archive keeps its existing canonical titles, which may reflect
older messages or local data. Instead, an approved final page of a
frozen generation now publishes a separate **source display roster**:
original conversation ID, rendered source title, participant count and
the old participant-threshold classification. It does not change
canonical titles, write message revisions, expose a member list, copy
avatars, or treat roster omissions as deletion of indexed history.

Run Linux `list-wecom-frozen-roster --directory ARCHIVE --grants PRIVATE_GRANTS`
over the same separate private input/output pipes, with exact WeCom
producer `token` and `lineage` in the private frame. Optionally set `limit`
(1–8, defaults to 8). Results are stably ordered by binary conversation ID.
Continue using the returned `next_after` plus the exact returned
`generation` as input `after` and `generation`; a changed generation
requires reopening at its first page. This uses the existing exact grant
and local frozen-mode marker, not a provider, desktop/global endpoint or
a general roster service.

The last completed publication remains readable while a later generation
is pending, explicitly labeled `pending_generation: true`. Nothing from
the pending roster is emitted. Another completed generation atomically
replaces the previous display roster, including an explicitly empty
roster. The archive retains at most 10,000 display rows per account
and 100,000 total; a limit conflict rolls back the final page. A head
completed before 120 has no reconstructed roster. A mismatched lineage,
stale paging generation or missing publication returns
`completed_roster_unavailable` without related raw state.

The roster is published from the exact stage only in the final page's
existing SQLite transaction, before the private stage is retired. It is
not proof of upstream coverage. Additive tables are created only at the
first qualifying final page. Source rollback removes the new roster
read and publication, leaving already recorded display rows inert;
keeping the current writer is required for current roster publication.
Source only pending unified verification.

### Show the finalized display of one existing conversation (121)

When the Linux ordinary native-live manifest advertises
`resources.wecom_rendered_display`, an exact WeCom Desktop grant already
holding `conversations.read` can GET
`/api/v1/conversations/wecom-rendered-display?conversation_id=...`.
The only query field is one normalized conversation ID; account and
platform come from the authenticated grant. Unknown, outside-scope and
not-yet-finalized targets return the same 404. The result echoes the exact
platform/account/conversation and supplies only the last finalized source
title, participant count and the pinned threshold classification (more
than two is group). The canonical archive row must still exist under the
same binary key. A verified read transaction validates the local finalized
publication against the frozen marker/progress and rejects ambiguous
matching rows. A later unfinished refresh cannot publish or change the
view and is not reported by this API. A newly finalized empty roster
produces no results.

In an already managed and authorized Linux ordinary native-live Desktop
connection, use `supports_wecom_rendered_display()` and make an explicit
`wecom_rendered_display(selected_conversation_id)` request. The SDK
verifies the exact target and classification, and returns `None` on a
404 without automatic fallback. Treat the source title as untrusted local
display text. It is not a provider-verified title or member list. Existing
conversation reads and canonical titles are not changed. The operator's
private listing, lineage, receipt, source file, current refresh state,
archive paths and staged rows are not exposed.

There is no new grant, schema, provider operation or non-Linux mode.
Rollback 121 by reverting this source patch; previously finalized display
data and the private operator path from 120 are retained. Source-only:
no real archive reads, SDK execution, build or tests were run.

### Read-only fit check against the target archive (122)

The pinned Python refresh may update a stored message with changed values
under the same stable ID (`hub/store.py:699–723`). The Rust frozen mode
instead refuses an incompatible ID, owner, direction, conversation or
message projection. Ordinary file inventory validates the source format
but cannot detect an inconsistent ID already indexed in the target.
Without a target check a later page could fail after earlier pages commit.

Linux `preflight-wecom-frozen-target --source FILE --directory ARCHIVE
--grants PRIVATE_GRANTS` takes one private `token`, `lineage`, and
`file_sha256` frame, just like frozen inspection. It revalidates the exact
grant, authorized file/hash and scoped ordinary archive, then compares
each page's bounded normalized messages against the exact account's
current index. All comparisons use one verified read snapshot and the
writer's same stable-key, owner and normalized equality rules. Reports
contain only counts and zero-based page ordinals of the selected file,
never the old text, IDs, owner, account, path, cursor or credential. A
different mode or lineage returns `target_fit_unavailable`.

A result with no conflicts means only that the checked message rows fit
the append-only policy **at the time of the read**. It is not an approval,
claim, rollover or replacement confirmation; it does not test final roster
capacity/compatibility or other write-time policy. Concurrent updates may
invalidate it. Use the original explicit page approval workflow to write.
No stored row, staging, claim, receipt or archive schema changes. Revert
the source commit to remove this check. Source only pending unified
verification.

### Conversation rename does not revise historical messages (123)

The pinned WeCom renderer copies the current conversation name into every
rendered message's `conversation` field, independently of its stable
`conversation_id` (`integrations/wecom/engine.py:308–316,352–359`).
The pinned Hub writes that name from the roster, not from each archived
message (`hub/server.py:1279–1294,1310–1320`). In Rust the rendered title
also sits in the normalized per-message ownership record. Treating a
changed title as a message change could therefore reject an otherwise
identical full refresh before reaching final roster publication.

Only the explicitly claimed frozen mode now treats an already indexed
message as unchanged when **only** its rendered conversation title differs.
The same ordinary offline owner, exact platform/account/stable IDs,
direction, sender ID and name, timestamp and message body must still
match. No stored message or owner's title is rewritten or adopted;
no event or new message revision is emitted for that existing row.
The independently published display roster still changes only when the
snapshot's final page commits. Writes of genuinely new messages and
canonical index behavior are unchanged.

The 122 target fit check uses the same comparison as frozen admission.
All other import modes keep strict title equality. Exact input bytes
and previous receipt digests retain their current proof rules. Reverting
this source change restores the older conflict behavior; no data/schema
migration or receipt rewrite is needed. Source only pending unified
verification.

### Sender directory label changes do not revise archived messages (124)

The pinned renderer obtains the display name from the current directory
and emits it as `sender`, separately from the stable `sender_id`
(`integrations/wecom/engine.py:275–286,352–359`). A directory rename
therefore changes the projection of historical rows on a new refresh
without proving their authors or bodies changed.

The frozen writer and target fit check now treat a sender-label change as
unchanged **only** when both rows carry the exact same canonical positive
decimal sender ID. An absent ID, zero, placeholder (including the original
renderer’s stringified missing sender), an opaque/noncanonical ID, or a
different ID does not qualify. No matching display name is used to infer
identity. The original append-only owner and all durable content and
identity fields must still match. Apart from the separately allowed
conversation title hint, other changes still conflict. This exception
does not rewrite the archived sender name or infer a directory update.

Other modes keep the original full row comparison, and stored receipts,
raw input binding, schemas and ownership are unchanged. Reverting source
restores the strict sender-label conflict. Source only pending unified
verification.

### Exact finalized local snapshot outcome counts (125)

The pinned Hub shows completion of the requested refresh
(`hub/server.py:1056–1078`). The pinned WeCom engine also reports
counts for a whole refresh (`integrations/wecom/engine.py:1004–1034`).
This private Rust equivalent reports **local import effects for one
authorized file**, not live source status or the engine's coverage,
people, media, clock or cache statistics.

Start with Linux `read-wecom-frozen-outcome --source FILE --directory ARCHIVE
--grants PRIVATE_GRANTS` over the existing separate private pipes. The
frame contains the current exact WeCom producer `token`, `lineage` and
reviewed `file_sha256`. The file must match the current **completed**
frozen generation, exact account, lineage, full raw SHA and deterministic
page layout. The read validates all ordinal receipts in one verified
SQLite read snapshot, rederives each bounded normalized page's digest and
skipped count, checks immutable append-only and claimed receipt records for the same grant lane,
and confirms the final checkpoint matches the active completed marker.
It returns only generation, page count, projected message count, created,
unchanged and skipped non-text counts. The result does not expose stored
message data, claim IDs, credentials or checkpoint values.

This proof exists only for a generation whose first page committed under
125 or later. Older and partial records cannot be reconstructed or mixed
with a newer generation. A missing proof, wrong current file/mode/lineage, mixed grant lanes,
unfinished or superseded generation returns `finalized_outcome_unavailable`
instead of inferred progress/counts. The existing per-page immutable
receipt commands still work under their original proof rules. Counting
does not rerun an import, reserve a task, call providers or assert
upstream completeness.

An additive active-page index is created inside the existing page
transaction. On starting another generation (including a 119 replacement)
only the prior auxiliary page index is retired atomically; actual messages,
roster state, claim records and per-page receipts retain their established
rules. Prior raw-input domains, confirmation fields and result shapes
are unchanged. A source revert removes the report and leaves its index
inert; do not expect later reinstalls to backfill gaps introduced by
pre-125 writers. Source only pending unified verification.
