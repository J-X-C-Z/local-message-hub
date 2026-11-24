# Conversation media catalog (Linux)

On the existing imported-media serving host, opt an exact-account native
Desktop grant into `messages.read` and `media.read`. No additional provider
permission is implied. The read-only
`GET /api/v1/messages/media/catalog?conversation_id=…` lists attachment
references already bound to that exact conversation in the local imported
catalog. Account and platform come from the grant, not the query.
Unsupported platforms, broad grants or other archive modes cannot use it.

Optional query parameters: `limit` (1–100, default 30), inclusive
`min_bytes` / `max_bytes` (0 to 8 MiB), and `after` (the previous
`next_cursor`). Order is normalized message ID then attachment ID, both bytewise
ascending, not chronology. The continuation is bound to the imported catalog,
grant/account, conversation and size filters; changing those requires starting
again. It is a pagination marker, never a credential. No-match results and
missing conversation IDs return the same empty page.

Each item contains `conversation_id`, `message_id`, `attachment_id` and
`size`. The first three fields can be copied to an explicit resource job
selection. `provenance:"imported_catalog_exact_binding"` means the attachment
was unambiguously linked to this normalized message by the imported bundle.
It does **not** mean bytes were reread: `blob_rechecked:false` is explicit.
Use the existing per-message verification or resource job to open and verify
the actual file/digest. Catalog and snapshot identity are rechecked around
each bounded page. The result never contains host paths, object filenames,
provider locators, raw cache keys or an external URL.

This is useful parity for navigating already imported media only. Availability
is partial: ambiguous, absent or oversized objects and unsupported platforms
are not remotely fetched, decoded or recovered. This module does not implement
original provider media calls, downloads from URLs, previews/thumbnails,
raw client cache acquisition or scheduling.
