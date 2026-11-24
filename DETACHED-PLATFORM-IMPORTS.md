# Detached WeCom imports and WeChat previews

These commands read one already-detached, operator-selected UTF-8 JSON file.
They are not readers for QQ, WeCom, or WeChat client databases, and do not
open provider APIs, contact endpoints, or use credentials. Only a regular
file of at most 512 KiB and at most 100 rows is accepted. On Linux and macOS
the common detached-source reader opens without following a final-component
symlink and without waiting for a writer on a substituted FIFO. It still
checks the opened descriptor for a regular, stable, size-bounded file. These
checks do not establish ownership of an untrusted ancestor directory: supply
the file from an operator-controlled private local hierarchy.
Create the envelopes with your own trusted export workflow using data you
are authorized to copy. The envelope is an operator assertion of account,
not proof of the source's provenance or of ownership of the account.

## WeCom rendered text

Set the envelope's `format` to `wecom-rendered-v1`, `account_id` to the
exact chosen Hub archive account, and `messages` to the selected detached
already-rendered records. No implicit account called `local` is assumed.
If the archive intentionally uses the old single-account convention, pass
`local` explicitly and bind only one trusted source to that account.

The pinned reference is commit
`c7052745fab3fa1880c6e3a496f40609185668b9` of `J-X-C-Z/local-message-hub`:
`integrations/wecom/engine.py`'s `render` supplies field names and the
boolean `me`, `integrations/wecom/extract_chat2.py`'s `CTYPE` labels 0
as text, and `hub/server.py`'s `sync_wecom` stringifies message and
conversation IDs. The envelope itself is new; the upstream repository has
not been shown to export it.

For eligible rows, use `message_id` (positive JSON integer or its
already-rendered, nonempty text representation), `conversation_id` and
`sender_id` (nonempty bounded exact strings), `send_time` (positive
integer Unix seconds), `me` (explicit boolean), `text` (nonempty
bounded string), `content_type: 0`, and `kind: "text"`. Optional
`conversation` and `sender` provide normalized display names.
Contradictory titles in one conversation batch are rejected.
An explicit row scope, if present, must agree with the envelope. Unknown row
fields are not persisted. Entries with a nonempty `media`
array or non-plaintext type are counted as skipped and are not followed
or copied; malformed input or a batch with no eligible text is rejected.

```sh
local-message-hub-rs preview-wecom-json --source /path/to/detached.json --directory /path/to/initialized-native --account exact-account
local-message-hub-rs import-wecom-json --source /path/to/detached.json --directory /path/to/initialized-native --account exact-account --confirm preview-digest
```

Only an ordinary initialized Rust-native archive is supported. Confirmation
binds the exact bytes, mode, destination instance and account. The existing
native offline transaction appends or accepts identical rows, rejects
conflicts without overwriting and preserves the exact retry receipt. A
legacy Hub normalized JSON import can deduplicate only if the entire
normalized row agrees. This route does not store attachments, provider
IDs beyond the established normalized message ID, membership data or
real-time events. Refresh open clients after offline import; a replay
stream does not recover these rows.

## WeChat provider page preview

Set `format` to `wechat-provider-page-v1`, set `account_id` to the
explicit chosen account, and put the selected already-normalized
provider rows in `messages`. The pinned `integrations/wechat/provider.py`
`messages` and `messages_after` shapes provide `platform`,
`session_id`, `shard`, `local_id`, `id`, `timestamp`, `is_self`,
`type`, `local_type`, and `content`. The preview requires a nonempty
unambiguous shard and session, the exact `session_id:shard:local_id`
composite, explicit boolean `is_self` and distinct IDs. Rows are counted
as plaintext candidates only for `type == "text"` and `local_type == 1`.
These conservative checks may reject some upstream pages.

```sh
local-message-hub-rs preview-wechat-provider-page --source /path/to/detached.json --account exact-account
```

Only counts and a source SHA-256 are emitted. The preview does not decode
or output content, resolve paths or infer direction from raw sender
references. It neither opens an archive nor authorizes or performs a
WeChat import. Raw WeChat exports and live client capture are unsupported.
