# Optional exact-account imported media references in Live

On Linux, a private ordinary-native operator may prepare an explicitly
selected pair of an existing Live archive and an imported verified media
bundle. The latter remains a separate read-only directory, neither nested
in nor containing the live archive. No implicit pairing, snapshot mode
conversion, provider connection, cache path, authentication material, remote
fetch or arbitrary URL is enabled. Byte retrieval is off by default and
requires the separate binding and grant below.

Use the local `preview-live-media-link` command with the exact native
directory, private verified bundle directory, platform (`qq` or `wechat`)
and account. It shows a confirmation fingerprint bound to the archive
instance, validated bundle/catalog and exact account; it does not list
messages or paths. Review and copy that value into a one-account,
owner-private JSON file for the serving process:

```json
{
  "version": 1,
  "bundle_directory": "/absolute/private/imported-bundle",
  "platform": "qq",
  "account_id": "<the confirmed account>",
  "binding": "<the exact preview binding>"
}
```

Pass it explicitly as `--live-imported-media-link` with `serve --live`.
The config must be a private singly linked regular file owned by the
effective user. The bundle/snapshot/catalog must retain their verified
identity; changing archive, account or catalog requires a new preview.
Running without this flag leaves all existing routes/modes unchanged.
The extra routes additionally require a Desktop grant for this **one
exact account** with `messages.read`, `media.read` and
`media.live_imported.read`. No non-Desktop, guarded, browser, snapshot,
media-only or MCP listener inherits them.

- **GET `/api/v1/messages/live-imported-media`** with `conversation_id`,
  `message_id`, and optional `limit` (1–50; default 25) / `offset`
  returns verified catalogue ownership metadata (attachment ID and size).
  It does not reread each blob and says so.
- **GET `/api/v1/messages/live-imported-media/{id}/reference`** with the
  same two message selectors verifies the complete bounded attachment
  from the bundle before returning its size and SHA-256. No download
  URL, filename, path, message body, sender, or raw media data is returned
  by the reference call.

The operator confirmation alone is not proof that a media item belongs to a
live message. A request must also match a single unambiguous tuple and have
identical bounded canonical text, sender ID, self role, and positive
timestamp in both indexes; the live row is checked again after media work.
A lookup supplying guessed IDs, matching only the tuple, targeting an
unsupported type, or observing changed live content cannot surface a
reference. Live ingestion at this point indexes normalized **text**; an
incompatible typed detached voice/image/file entry is intentionally not
coerced, so no broad media or automation parity is implied.

## Separately approved verified bytes

The link preview includes an `optional_verified_bytes_binding` and the
hard `verified_bytes_limit` (1 MiB). To opt in, explicitly add the preview's
byte binding as `verified_bytes_binding` to the same owner-private config
and restart the ordinary listener. This is a separate exact account,
catalog and archive confirmation; the existing link binding alone does
not enable it. The route is absent when the field is omitted. The scoped
Desktop grant needs all existing link permissions and an independent
`media.live_imported.bytes.read` permission.

1. Use the existing reference operation to obtain the attachment's
   checked SHA-256 and size.
2. **GET `/api/v1/messages/live-imported-media/{id}/bytes`** with the exact
   `conversation_id`, `message_id` and stated `expected_sha256`. The whole
   matched blob is size-capped and verified from the private bundle before
   release, and the live witness, archive and bundle are checked again
   after that work. Range and If-Range are rejected. Digest mismatch and
   temporary failures never return a partial body from an unverified
   object.
3. A successful response is a bounded octet-stream attachment with no
   filename, media MIME, provider address, cache path or redirect. Cache
   and inline sniffing are prohibited. The bytes may themselves contain
   private data; clients are responsible for their explicit local use.
   An already published response cannot be retracted after a later live
   revision change.

This is byte retrieval only from the already verified local bundle, never
a request to a platform or provider and never a guarded/browser extension.
