# Operator-normalized WeChat text import

This is a local, append-only CLI for a deliberately separate text envelope.
It does not interpret raw vendor exports, database records, Windows or macOS
client data, media, paths to resources, credentials or key material. There is
no invocation of a WeChat client, scrape, connector, background task or
provider request. The older `preview-wechat-provider-page` remains
count-only and cannot be passed to the new import.

Select an existing ordinary Rust-native archive and a bounded operator-prepared
local file under a directory you trust:

```text
local-message-hub-rs preview-wechat-detached --source FILE --directory ARCHIVE --account ACCOUNT --namespace SOURCE_NAMESPACE
local-message-hub-rs import-wechat-detached --source FILE --directory ARCHIVE --account ACCOUNT --namespace SOURCE_NAMESPACE --confirm PREVIEW
```

The envelope must have `format:"wechat-detached-text-v1"`, the exact
`account_id`, the exact `source_namespace` and `messages` (1–100 objects).
Unknown fields, repeated identities or a conflicting title inside one
conversation/batch cause the entire operation to fail. Source and normalized
payloads are limited to 512 KiB each. Each row has:

- `conversation_id` and `sender_id`: 1–256 byte ASCII identifiers using
  letters, digits, underscores, hyphens, dots or `@`. These must be selected
  stable identifiers in the same account/sender domain.
- `shard_id`: a stable, opaque, operator-assigned, nonzero 32-digit lowercase
  hex ID for the logical shard. This is not a database filename or path.
- `local_id`: a nonzero unsigned 64-bit decimal **string** in canonical form
  without a leading zero, avoiding JSON-number rounding.
- `kind:"text"`, bounded `text`, Unix-second `timestamp`, and an explicit
  JSON Boolean `is_self`. `conversation_title` and `sender_name` are optional
  nonblank labels (512 bytes each), not resource locators.

The operator must know the WeChat account identity and each
message's exact direction from trusted normalization outside the Hub. The
CLI does not accept the provider fallback `local`, invent missing IDs, infer
direction from message content, or coerce absent/null direction. It requires
`is_self` to equal the exact predicate `sender_id == account_id` in this
envelope; if that representation is impossible for a row, do not import it.
The Hub checks consistency, not authenticity of the operator's claims.

The `source_namespace` is a nonzero 32-digit lowercase hex ID assigned once
per stable account/source lineage by the operator, not a file name, path or
session token. Reuse it and the same logical shard map only when the source
guarantees that each local ID is immutable within its conversation/shard. If
IDs are rebuilt/recycled or cannot be established reliably, retain the input
as preview-only or assign a separate known lineage; changing these IDs does
not merge copies automatically.

Both native IDs use lowercase SHA-256 with domain separation and unambiguous
lengths. The digest input is the ASCII domain shown below followed by NUL,
then, for each listed operand, a four-byte unsigned big-endian byte count and
the exact operand bytes. The result is the domain string plus the hex digest.

- `wx-detached-conversation-v1:`: source namespace, account, conversation ID.
- `wx-detached-message-v1:`: source namespace, account, conversation ID,
  shard ID, and the canonical ASCII local ID.

The pinned Python repository `c7052745fab3fa1880c6e3a496f40609185668b9`
`integrations/wechat/provider.py` uses a provider-rendered
`session_id:shard:local_id` shape from cache-derived shard labels and derives
direction only if sender identity is known. `hub/server.py` normalization
also carries separate optional server IDs. The new namespace does not claim
these vendor/legacy identifiers are stable across exports or identical to
the new native identifiers. No cross-format deduplication, title merge or
reparenting into existing WeChat conversations is attempted.

Preview returns counts and a digest bound to the exact file bytes, target
archive instance, account and source namespace; it does not promise that the
target has no preexisting conflict. Commit binds the same scope
again and atomically applies the append-only message changes and receipt.
Exact retry reproduces the receipt; matching normalized rows from another
batch under the same owner are unchanged, but mismatched existing data or
another owner's preexisting identity fails the whole batch. The dedicated
owner emits no live incoming events and stores no source path, raw account
resource, raw shard/conversation IDs, or media references outside the allowed
plain message fields. Text is inert; links in text are not interpreted or
fetched. No platform credentials or private provider files are needed.
