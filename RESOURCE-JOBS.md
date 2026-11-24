# Imported-media resource jobs (Linux)

A native Desktop bearer grant needs an exact account and both `messages.read` and
`media.read`. These routes exist only on the existing imported-media snapshot host.
They do not accept filesystem paths, arbitrary URLs, remote credentials or unbound
media identifiers. They do not schedule work or contact providers.

Under `/api/v1/resource-jobs`:

- POST `/create` with
  `{"items":[{"conversation_id":"...","message_id":"...","attachment_id":"..."}]}`.
  The existing media list supplies attachment IDs. Include an optional `sha256` in
  a selection to pin its content to a previously observed digest. Account is derived
  from the grant. Invalid content pins and attachments not bound to the exact message
  return not found. One request can select 1–16 attachments with at most 4 MiB of
  resolved source data in total.
- GET `/list` and GET `/status?job_id=...` return only that grant's current jobs.
- GET `/download?job_id=...` returns a verified, uncompressed ZIP (no byte ranges).
  Check the bundle SHA-256 in job metadata; `manifest.json` maps generated member
  names to attachment IDs, SHA-256 and byte sizes. Original names or paths are never
  included. Treat downloaded media as untrusted content.
- POST `/cancel` with `{"job_id":"..."}` discards a ready artifact. It cannot
  revoke bytes already being delivered.

Creation is explicit and returns only once the bounded artifact is ready. There is no
pending queue, scheduled cleanup or persisted artifact. Job IDs are random and a fresh
create can produce a new job; do not retry blindly after receiving a successful response.
Entries become inaccessible five minutes after creation, are removed from cache on the
next operation, and never survive host restart. At most 8 ready artifacts exist globally
and 2 per exact grant. Two verification/build operations and two output bodies may be
in flight. The existing verified imported media reader checks every blob; the host
checks snapshot and bundle identity before and after each operation.
