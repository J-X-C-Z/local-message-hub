# WeCom local rendered sync bridge

This rewrites the safe boundary of the pinned original
`J-X-C-Z/local-message-hub` `c7052745fab3fa1880c6e3a496f40609185668b9`,
`hub/server.py::sync_wecom`. The Rust side consumes bounded, already
authorized/normalized local data. It does not launch the old adapter or
access client databases, application memory, credentials, keys, media paths,
network/provider APIs, official callback ingress or public listeners. The
provider's original acquisition is outside this Rust bridge and must remain
an independently authorized operator input. No callback or public endpoint
was created; the official third-party [WeCom CloudBase template](https://github.com/TencentWeCom/wecom-cloudbase-template-vue)
is not a first-party client-history or callback protocol authority.

```text
local-message-hub-rs wecom-sync-status --directory ARCHIVE --account ACCOUNT --source-id LINEAGE
local-message-hub-rs preview-wecom-sync --source FILE --directory ARCHIVE --account ACCOUNT --source-id LINEAGE
local-message-hub-rs import-wecom-sync --source FILE --directory ARCHIVE --account ACCOUNT --source-id LINEAGE --confirm RECEIPT
```

An operator prepares `format:"wecom-rendered-sync-v1"`, exact `account_id`
and `source_id`, positive `generation` and `sequence`, exact
`previous_checkpoint`, Boolean `complete`, and `conversations`/`messages`
arrays. Max 512 KiB and 100 combined rows. The original single-account Hub
used the literal `local`; select that exact account for its original identity,
never default to it or pool distinct origins under it. `LINEAGE` is a stable,
operator-assigned nonzero 32-character lowercase hex identifier, not a path or
secret. Once used, an account cannot switch lineages. The preexisting
standalone rendered import is unchanged and still requires the operator to
keep its inputs scoped.

Each conversation row takes the original `conversation_id`, `name` and
bounded integer `participants`. Each message carries the rendered
`message_id`, `conversation_id`, `content_type`, `kind`, `send_time`, `text`,
explicit Boolean `me`, optional `sender_id`/`sender` and `conversation`,
and optional inert `media` array. Present `platform` or `account_id` must
match the envelope. String IDs keep their exact spelling; nonnegative integer
IDs are stringified exactly as in the original Hub. Direction is never
guessed or defaulted. The bridge only writes raw type 0 non-system,
attachment-free text in v1; unsupported rows are counted and skipped rather
than converted or fetched. An explicitly opt-in v2 additionally accepts the
restricted notice text described below. Conflicting titles or sender names in one batch fail
closed. Unknown source metadata, paths, avatar/media and contact membership
data are not persisted.

The same offline owner as legacy/standalone text import preserves canonical
account, message and conversation IDs. Exact normalized overlap is unchanged;
different content, identity, direction or ownership fails the atomic batch
instead of overwriting data. Conversation list entries stage the exact declared title and participant
count by local generation. Only the first sequence admitted by this version
starts staging; only an explicitly complete generation applies category
and member count, using the pinned threshold (`participants > 2` means
group, otherwise direct). Both group-to-direct and direct-to-group are
allowed for listed rows. Neither an incomplete generation, a message-only
page, nor absence from a later generation can replace those hints or
delete any indexed conversation/message. Older in-progress generations
can finish applying messages and contact metadata but cannot use their
partial list to reconcile conversation kind/count. Conflicting metadata
for the same exact conversation within a generation fails instead of
choosing a partial page. The completed-hint update fills only empty titles; existing normalized
message-derived title updates are unchanged. Existing populated contact
names are not overwritten. Unsupported existing conversation kinds fail closed. The bridge fills missing named contacts
from original sender IDs and does not project the provider's latest timestamp
for rows it did not import. A batch can contain only metadata or unsupported
rows, or an empty explicit completion, without inventing a fake message.

The pinned provider exposed a fully materialized snapshot and explicitly had
no resumable source cursor. The Rust sequence is only a **local apply
checkpoint**; it must not be presented as a provider position. Start with
the `initial` checkpoint, generation 1, sequence 1. Continue with the prior
committed receipt's checkpoint and the next sequence in the same generation.
A new generation starts at sequence 1 only after the prior generation
commits an explicit completion. It does not delete rows missing from a
later snapshot or assert that the upstream snapshot was complete. The
operator can resume by supplying the already detached next batch; no reader,
secret, fetch or automatic replay is triggered. Cursor and exact batch
confirmation are archive/account/lineage-bound. A successful retry gives
the old receipt; refresh status for the latest checkpoint. Rows, staged metadata, receipt and checkpoint commit in one transaction.
At the final sequence, publishing the completed generation's hints and
retiring the private staging rows are also in that transaction. Staging is
bounded to 10,000 observations per account generation and 100,000 total
rows including completion markers. Preview reports its metadata policy
separately; it is not proof that the operator supplied an authoritative
upstream inventory and does not reserve any rows. No live or outgoing event
is emitted. No real source was accessed and this source-only change has not
been built or tested.

## Explicit v2: original rendered plaintext notices

Supply `format:"wecom-rendered-sync-v2"` only when the same detached source
contains real rendered notice text. The pinned
[renderer](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/integrations/wecom/engine.py#L313-L341)
labels absent/non-text content as synthetic `kind:"sys"`. Of its
[notice types](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/integrations/wecom/engine.py#L57-L72),
38, 1001, 1011 and 1022 can instead render as actual `kind:"text"`; the
[original Hub](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/server.py#L1233-L1255)
indexes those rows as standard text. V2 keeps only that additional subset,
with an empty media array, at most 8 KiB, readable text, and no bracket
placeholder, standalone opaque/hex token, or common path/URI/markup syntax.
The pinned renderer suppresses bare hex at 24 or more characters, but its
text selection can return shorter values. V2 therefore rejects a standalone
ASCII hex token at **any** length, including an optional `0x` prefix; short
words made entirely of hex letters (such as `face`) are deliberate false
negatives. No text is synthesized, substituted, decoded, or used as a
resource to open. The operator is still responsible for supplying authorized detached
plain text; this is not a detector for arbitrary sensitive text. Provider
IDs beyond the existing normalized row identity, raw structures, file names,
URLs as fields, attachment records and executable markup are not projected.

Accepted notice text is stored as the same normalized `text` type and
counts in `text_rows`. System placeholders, numeric-only labels, raw
link/image/file/voice/video types and all media continue to be
skipped. The format's eligibility check does not bypass existing validation
of ID, timestamp, ownership, direction, sender, or conversation.

V1 parsing, batch/claim confirmation, local checkpoint and prior receipt
behavior are unchanged. A format selection is part of the exact hashed
batch bytes, not a migration of an existing indexed row or checkpoint.
On the same authorized lineage, either format can be selected for the
next valid sequence (or first sequence of a new generation after completion)
with the exact current checkpoint and new confirmation. A format change
cannot relabel a committed batch, rerun the same sequence or restore skipped
content by substituting its receipt. Old readers can still read normalized
archive rows, but an older importer cannot parse or recover a v2 envelope;
retain the exact envelope and a v2-capable importer for receipt lookup.
No schema or existing reply fields changed.
