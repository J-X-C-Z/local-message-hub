# Personal WeChat: observed ordered source position handoff

This child-side helper complements the [safe local source contract](PERSONAL-WECHAT-SOURCE.md).
The original provider's `messages_after` v1 cursor contains `session_id` and
the exact `(sort_seq, shard, local_id)` of its last returned source row.
The approved old Hub JSON export does **not** preserve raw `sort_seq` or
source order. Import those rows through the existing compatibility path;
you cannot recover an old source position from them, displayed timestamps,
IDs or the export receipt. Do not invent or backfill a cursor for old exports.

If an authorized local adapter already holds consecutive ordered source rows
with those actual tuples, it may call `wechat_source::cursor::prepare`. It
supplies the exact session, a verified source SDK resume frame, its own saved
per-conversation checkpoint (or None at bootstrap), and a private page containing:

- `format:"wechat-observed-ordered-text-v1"`, `account_id`, `session_id`;
- explicit `after` (null at the actual start, otherwise the exact saved position)
  and `next_cursor` (the exact position matching the final supplied row);
- ordered `rows` (1–100), each with its observed `cursor` and its normalized
  legacy-compatible `message` containing the original exact IDs and direction.

All positions include `version:1`, `session_id`, nonnegative integer `sort_seq`,
a nonempty safe `shard`, and positive integer `local_id`. The adapter must not
round the integers through a floating-point representation. Validation rejects
scope/ID mismatches, duplicate shard/local IDs, a decreasing sort sequence,
incorrect first/last boundaries, and unsafe or ambiguous messages. Equal sort
sequences retain actual source-supplied ordering without inventing a shard tie
collation. The helper cannot independently prove original tie order or records'
completeness. Do not construct a purported page by sorting an export or
skipping an unsupported row and advancing beyond it.

The pending frame uses the existing source SDK; no message protocol is added.
Before submitting, durably record which exact page is mapped to the next Hub
sequence, and do not replace that mapping on retry. Only a matching sanitized
broker acknowledgement releases a new local `Checkpoint`. Save it by exact
account, namespace and conversation. The **account-wide Hub receipt sequence**
is proof of that batch's archival position, not the **per-conversation source
cursor**; multiple conversations can advance the same Hub sequence. If output
may have been submitted but the acknowledgement was lost, reconcile the next
Hub resume against the saved exact pending mapping before updating the local
cursor. The helper does not persist cursor state or claim Hub and local writes
are atomic. It neither requests messages, accesses app storage nor attaches to
a provider. Source only; not built or tested.

The original continuous reader could independently revisit earlier source
rows when performing historical backfill. This helper does not discover
late arrivals at or before a committed cursor, scan a source, guarantee
gap-free coverage, or merge old export and observed-ordered identities.
