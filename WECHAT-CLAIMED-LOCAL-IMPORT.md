# One claimed WeChat job to one detached text import (063)

Native Linux local composition, not a provider client or legacy
`POST /api/sync` implementation. Its unchanged default accepts the existing
`wechat-detached-text-v1` normalized envelope and uses the same canonical
detached identities and offline owner as `import-wechat-detached`. An
explicit separate old Hub export format is described below. This does not accept opaque provider pages, raw
cache/database files, client credentials, media, callbacks or live ingress.
The detached input is independently prepared by the operator; exact-byte
confirmation does not prove a file's original provider authenticity.

1. The existing ordinary native source handoff API must create/publish/
   claim an active job. It must be the exact `wechat` scoped producer with
   existing `messages.ingest` and `sources.jobs.manage`; no cross-account
   selectors. Fresh WeChat claims now atomically store a domain-separated
   hash of the actual host-authenticated claimant bearer, never the bearer.
   Historical WeChat claims without that binding cannot authorize an import.
   WeCom keeps its previous credential domain and table.
2. A trusted local launcher invokes `preview-claimed-wechat --source FILE
   --directory ARCHIVE --grants PRIVATE_GRANTS` with separate private
   anonymous stdin/stdout pipes. Source, archive and private registry are
   explicit local command arguments only, never accepted by an HTTP endpoint.
   Send one bounded JSON line containing `token`, `job_id`,
   `expected_version`, and `namespace`. The token is the original
   claimant bearer and must never be put on the command line or printed.
   Authenticated scope supplies the account. Review the returned metadata:
   counts, job/version and an archive/claim/exact-byte confirmation digest.
3. Invoke `import-claimed-wechat` with the same paths and private frame,
   also supplying the operator-approved `confirmation`. Fresh registry
   authentication, original claimant binding, current active claimed job
   and unexpired deadline, namespace/account and exact detached bytes all
   have to match. The existing ingest transaction commits the message rows,
   native ingest receipt and one immutable binding from the claimed job to
   that exact offline receipt, or commits none of them.

One claim makes at most one offline detached batch eligible. An old
standalone receipt cannot be labeled with a job. An exact previously
committed retry may return that same receipt after expiry without changing
data; it still requires the original bearer, fresh registry authentication
and the identical confirmation, claim, namespace and file bytes. A changed
or rotated bearer, old unlinked receipt, new bytes, or a different
job/version cannot reuse the binding. No write is attempted after expiration
on an uncommitted job.

The handoff status and offline receipt are operational coordination, not a
provider sync cursor. Neither the optional SDK's producer checkpoint nor
the personal WeChat provider tuple is read or advanced. No live journal
or notification is published. A linked offline receipt is not job
completion, an indication that any adapter ran, or authority to read
provider data. No process is launched and there is no remote/network
operation. Protected archive modes remain unavailable.

Code-only; build and functional tests are deferred to the unified phase.

## Explicit original Hub JSON export (086)

For already authorized text exported by the pinned Python Hub at
`c7052745fab3fa1880c6e3a496f40609185668b9`, add exactly
`"format":"legacy_hub_export"` to the existing private operator frame for
both preview and import. Omission or explicit `"format":"wechat-detached-text-v1"`
selects the old default unchanged. No content sniffing or fallback. Only the
legacy-mode preview and receipt add a format label. Standalone and SDK paths
are unchanged.

The old JSON export projects normalized rows (`hub/exports.py` and
`hub/store.py`); the pinned provider's message ID includes the exact
conversation ID, shard and local ID. The claimed import uses the existing
[strict legacy JSON projection](PERSONAL-WECHAT-SOURCE.md). Both uploaded
and normalized payloads are capped at 512 KiB, with at most 100 text rows.
Every row needs the authenticated account, unique unambiguous IDs, valid
numeric time and corroborated sender direction. Any unsupported or foreign
row rejects the whole batch. Unrelated JSON fields and resource references
are not stored or followed. The operator namespace is required to bind the
receipt, but does not rehash any row IDs. No upstream cursor is inferred.

The legacy mode uses its own offline owner and input/approval hash domains.
Approval binds format, exact file bytes, archive instance, account, namespace,
job version and original claimant. Both formats share the existing one-batch
claim table, transaction and replay rules. A different format or changed bytes
cannot replay the approval. Existing rows with another owner conflict, even
if the text matches; do not use this lane to relabel a standalone or SDK
receipt. An exact committed retry still needs the original claimant and fresh
registry authorization and cannot add rows after expiry.

Rollback: no new table or migration. Reverting source disables the new format
and its receipt-replay entry point but does not undo committed rows/receipts.
Keep this revision and an archive backup for receipt reconciliation; do not
edit stored history to reclaim a spent claim. No compile, tests, real data or
client operation ran.

## Deterministic frozen-file page selection (087)

`inventory-wechat-legacy-export --source FILE --grants PRIVATE_GRANTS` is a
Linux private-pipe command. Send one stdin JSON frame containing only `token`
from the current exact scoped WeChat producer grant with `messages.ingest` and
`sources.jobs.manage`. It opens the independently authorized detached source
read-only and does not open an archive or require, create, or claim a job. It
returns only a whole-file `source_sha256`, counts, and page metadata; no text,
identifiers, filesystem paths, or projected rows are emitted or written.

The input is bounded to 4 MiB and 1,000 text rows. Before producing any
inventory or accepting any selected page, the whole file must pass the strict
pinned account, identity, direction, title consistency and shape projection.
Even a duplicate or foreign row on a different page rejects the entire file.
Page order is the original array order, not guessed chronology. Zero-based
pages contain at most 100 rows, 512 KiB of raw JSON row tokens plus array
punctuation, and 512 KiB of normalized JSON. File-level whitespace is still
included in the whole-file hash. The manifest includes each page's normalized
SHA-256 and sizes. At most 64 pages can be reported.

To process exactly one page, keep the same frozen file and reuse the existing
`preview-claimed-wechat` / `import-claimed-wechat` commands. In addition to
the ordinary fields of the private operator frame, submit
`"format":"legacy_hub_export_page_v1"` and
`"page":{"source_sha256":"EXACT_INVENTORY_SHA256","index":0}`.
Both commands revalidate the entire file and the chosen page. Format, exact
file bytes, normalized page, ordinal, archive, namespace, account and exact
claimed job/authority bind the approval. Each page needs its own active claim
and explicit preview and approval. No process loops over pages, tracks
completion, starts a job, emits intermediate plaintext, or advances an
upstream cursor. This is not a snapshot-wide atomic operation: earlier agreed
pages remain committed when a later page has not been approved.

Use one stable namespace for the full selection. The paged form shares the
086 strict offline owner but uses distinct approval and receipt domains; older
086 and default input formats do not accept a non-null page selector. No table
or retention policy was added. Source rollback disables inventory and paged
replay but leaves committed rows/receipts unchanged. No real files, accounts,
compiler or tests were used in this source checkpoint.

## Read-only receipt recovery (089)

If the confirmed import response was interrupted, `status-claimed-wechat`
accepts the same `--source`, `--directory`, `--grants`, private operator frame
and original approval used for the exact import. All three existing formats
are supported, including the original frozen file and selected page. The
same authorizing bearer must still match the current private registry and
the stored exact claimant. The command checks the same archive instance,
namespace, job/version, normalized request digest and linked offline receipt
inside one verified read-only SQLite transaction. It neither reserves a
writer nor creates, repairs or edits a job/receipt. An already committed
receipt may be recovered even after the original handoff deadline; status
never treats an expired or uncommitted claim as usable authorization.

A confirmed result reports only `state: "detached_batch_recorded"` and
`rows`, `created`, `unchanged` counts. Invalid proof, missing receipt, or
unavailable verification produces the same `{"state":"not_confirmed"}`;
this is not proof that an attempted import rolled back. No job, message,
archive, batch, or source IDs, raw contents, or cursor are printed. There
is no lookup by ID alone, listing or endpoint. The existing write/preview
commands and receipt formats are unchanged. No table was added; reverting
source removes only this lookup. No build, tests, real data or runtime
operations performed for this source checkpoint.

### Original-export reply display (107, opt-in)

The private claim operator may explicitly select
`"format":"legacy_hub_export_reply_display_v1"` for one independently
authorized original Hub JSON message array (no page selector). The
same `preview-claimed-wechat`, `import-claimed-wechat` and
`status-claimed-wechat` commands apply. It is not the existing plain export
or paged format. Preview returns the regular bounded metadata and
`reply_display_rows`, not the message body or quote text. Its separate
confirmation binds the exact archive, grant, account, namespace, claim,
format and file bytes. No upstream cursor or read state changes.

Pinned `hub/server.py:181–213,216–252,319–340` renders the reply's body
from its title/description and produces `wechat_quote` from app message
subtype 57 under raw type 49. The resulting reply is stored as `link` in
the old database. Pinned `hub/store.py:531–541` strips provider target
identifiers, `hub/exports.py`'s JSON renderer preserves display extras,
and `webui/app.js:355–356` reads the quote. The detached file does not
contain independent raw subtype evidence: the old serializer's typed
`wechat_quote` object is the only accepted indication. This is for a
trusted authorized export, not authentication of a vendor assertion.

This format admits only text rows and rendered `link`/raw `49` rows
with an exact typed `extra.wechat_quote` containing only strings
`sender` and `content`. At least one such reply is required. A reply body
must be nonempty bounded plain text. XML/HTML delimiters, disallowed
controls, URL-like schemes, nonempty media URLs and exported resources
fail validation. Other provider/export fields are not projected.
The accepted reply is deliberately flattened to `text` with no link,
resource or target. Conversation and message IDs, exact account,
direction corroboration and text validation come from the original
claimed export rules. There is no inferred lookup, navigation, sender
resolution, OS/browser behavior or provider access.

Each display is stored with the corresponding append-only message and
claim receipt in one transaction under a dedicated owner. Existing rows
from another owner are not adopted or retroactively enriched; avoid
importing overlapping old-format batches into this owner. Use the same
file and claim credentials when recovering a committed receipt. Only an
exact scoped Linux ordinary native Desktop `messages.read` request for
one WeChat conversation can see `extra.wechat_quote` as plain display
data in the native message page. Search, exports, guarded/browser
routes and quote-target lookup do not project this new data.

The new table is additive and is created only in the same transaction
as an approved reply import. Reverting the source removes new import
admission and display projection; it does not erase imported messages,
their atomic receipts or the display table. Existing formats, defaults
and approvals are unchanged. Source only, pending unified verification.

### Preserve replies while paging unmodified original exports (114)

Pinned `hub/store.py:1282–1328` enumerates export rows in source batches
(default 500, up to 2,000), and the pinned JSON renderer emits an
original array with both ordinary text and rendered replies. 087's
text-only pagination rejects such a mixed array, and 107 accepts only
one import-sized file. This new mode avoids manually repartitioning or
changing the authorized source bytes.

Pass `"format":"legacy_hub_export_reply_display_page_v1"` in the existing
private inventory request alongside its token, then in each claimed
preview/import/status input with the same `page` selection
(`source_sha256` and zero-based `index`). Use one independently
authorized claim per selected page. Inventory reads only the one
provided regular detached file; it does not grant admission or echo
any IDs, paths or message fields. It validates the entire file before
partitioning: one exact account, unique original message IDs across
all pages, consistent conversation titles, and the same strict 107
plain-text reply projection. The source as a whole must contain at
least one valid reply; some individual pages may contain none.

Limits: one 4 MiB file; up to 1,000 rows and 64 pages; at most 100 rows
and 512 KiB each for raw token array size and combined normalized
message/display JSON per page. The latter is the serialized array of
`[message,display-or-null]` tuples in source order. Its SHA-256 (reported
as the page's `normalized_sha256`) binds the approved sanitized display
as well as the text. The report includes each page's display count
(including zero), size, ordinal, and hashes only. A selection and its
confirmation recheck the unchanged whole-file SHA, ordinal, combined
digest and existing private claim/credential. No upstream cursor or
provider link is inferred. Previous inventory defaults, shape and
hash domains are unchanged.

The 107 dedicated append-only owner and same atomic receipt-plus-display
transaction are reused. Overlap with a different owner conflicts; an
identical same-owner projection can remain unchanged, but changed or
removed display information conflicts and rolls back the selected page.
An exact committed retry returns the same evidence without adding
another write; prior successful pages remain committed if a later
page fails. This is not multi-page atomic import or permission to
automatically advance. Reverting this source commit removes the new
inventory/selection mode without altering already committed messages,
display data or receipts. 107 single-file admission and existing native
display remain available. Source-only pending unified verification.
