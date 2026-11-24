# Selected messages plus verified imported attachments (Linux)

This is a bounded local subset of the original export-bundle task, **not** the
old `POST /api/export/bundle` wire format. The imported-media host offers:

```text
POST /api/v1/resource-jobs/create-message-bundle
```

Provide a single exact `conversation_id`, 1–16 distinct `message_ids`, and
1–16 explicitly selected `attachments` (`message_id`, `attachment_id`, optional
lowercase SHA-256 pin `sha256`). Every attachment must belong to one of those
selected messages in the existing imported catalog. No files or other
attachments are discovered automatically. The account and platform come
from the exact native Desktop bearer grant with `messages.read`, `media.read`,
and `messages.export`. Other archive modes cannot create one.

The same verified imported snapshot supplies bounded `messages.json`
(`message_id`, `timestamp`, `is_self`, `text`, optional data represented as
null) in request order. There are no sender profiles, stored resource fields,
metadata dumps or host paths. Message text is data, which may itself contain
untrusted links or path-like strings; it is never executed or fetched.
The existing bundle reader verifies each selected attachment's current bytes
and digest. The ZIP is uncompressed and uses generated member names. Its
`manifest.json` identifies format `lmh-selected-message-bundle-v1`, provides
the selected message file count/size/SHA-256, and associates each attachment
member with the message ID, attachment ID, size and digest.

The combined selected data must fit 4 MiB (text JSON at most 768 KiB and
individual message bodies at most 64 KiB). Missing or ambiguous selections,
changed digests or oversized data fail the whole operation. Use native
`/api/v1/resource-jobs/{list,status,download,cancel}` for the ready job;
those routes recheck the text-export permission for mixed jobs. Existing
legacy resource-job aliases intentionally do not discover or open mixed
jobs. The ready job is memory-only, shares the existing quota and five-minute
expiry, and is not a queued or scheduled export. Its entry count excludes
the manifest.

No compatibility is claimed for legacy query-wide exports, automatic
media discovery, raw cache reads, provider downloads, additional formats
or filesystem publication. No archives were created or run in this
source-only checkpoint.
