# Personal WeChat: authorized local text ingress

This source companion does not collect WeChat messages. It does not connect to
the app, read a client database, extract a key, scrape, send messages or claim
full personal account history. Supply only text records obtained through a
legitimate export/workflow whose use you are authorized to control.

An operator-reviewed local adapter can link the `wechat_source` Rust module
and use `exchange` on the private anonymous stdin/stdout provided by the existing
[Linux source broker](source-sdk/README.md). The broker handles process supervision,
deadlines and the ordinary native-live Hub calls. This helper is Rust library
source and does not start a child or use the network. Give the broker only a
trusted adapter you control. The broker is not a sandbox. Use an exact
`scoped-producer` grant limited to `messages.ingest`, platform `wechat`, and the
specific configured account. The bearer stays on the broker side; never place
it in the adapter or in detached pages.

The adapter fixes one source namespace (a previously chosen nonzero lowercase
32-hex identifier) and validates the broker resume account against its own
configuration. Its selection callback returns `Idle` or one explicit page in
the same `wechat-detached-text-v1` format as the [detached importer](DETACHED-PLATFORM-IMPORTS.md).
Historical and subsequent pages use the same normalized identifiers and field
checks. The page must supply stable reviewed conversation, source shard and
source-local IDs; timestamps, array order and text are not identity substitutes.
Do not change the namespace or reshuffle assigned IDs between runs. The companion
accepts only text, 1–100 distinct messages, and 512 KiB detached/normalized
payload limits. It does not look up records or decide what the local operator
has authorized.

Use a durable local mapping from consecutively numbered Hub sequences to the
exact pages selected by the adapter. The callback receives the current Hub
sequence; `after_sequence` must be the selected page's persisted predecessor,
not an arbitrary sequence stamped onto a stale page. One invocation writes at
most one explicit successor batch. A changed payload under an already-used
idempotency key is rejected by the Hub. After any failure once output may have
been sent, reconcile the fresh Hub checkpoint and your page mapping before
retrying; a lost acknowledgement is not proof of rollback. This helper neither
advances an upstream cursor nor automates replay or acquisition.

The detached CLI importer and the scoped source adapter are distinct native
write owners. Choose one for overlapping rows in a given archive/namespace.
The shared ID derivation prevents silent duplicates between those paths; the
archive rejects an attempt by a different owner to write an existing message.
A source grant uses existing generic ingest semantics, including valid message
revisions by that same owner, not the detached CLI's append-only behavior.
This source checkpoint was not built or exercised against a broker or app.

## Using the original Hub’s already-exported JSON

For work already verified with the pinned Python Hub, choose
`Selection::LegacyHubExport` (or `Session::prepare_legacy_export`), not the
new-namespaced detached mode. Supply a bounded selected array from an
**already authorized** `/api/export?format=json` file. This library does not
call the endpoint or choose how to obtain, authorize, or split that file.
The pinned `hub/exports.py` JSON renderer copies each normalized record;
`hub/store.py` projects its ID, exact account, conversation ID, text, Unix
timestamp, sender and direction; `integrations/wechat/provider.py` emits
`session_id:shard:local_id`. Eligible original IDs and bounded display
labels are preserved byte-for-byte in the normalized projection (not rehashed).

Only declared personal-WeChat text rows for the exact explicit account are
accepted. The provider-shape ID must agree with the supplied conversation,
have a nonempty safe shard label and a canonical positive decimal local ID.
If conversation title is not present in the exported row it remains absent;
the bridge never invents one. Python’s store can coerce an unknown direction
to `false`, so an explicit Boolean is necessary but insufficient: a valid
sender ID is mandatory and must corroborate `is_self == (sender_id == account)`.
Do not submit an export for which the authorized source cannot establish
sender identity or direction. Unsupported rows fail the entire selected
page; nonprojected fields (including resources, URLs, IDs in opaque metadata,
and raw provider metadata) are not read as locations or retained.

This preserves **existing exported identities**, not vendor guarantees across
reindex/re-export, IDs from a different Hub version or a profile using the
legacy `local` fallback. Use the same producer for overlapping rows already
submitted through that source grant: a different native write owner may
conflict. Do not combine the same messages with the new hashed detached
namespace, which derives different IDs. No cross-format de-duplication or
cross-source trust is implied.
