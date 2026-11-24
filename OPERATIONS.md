# Redacted local operation status

On an ordinary native live profile, `GET /api/v1/operations/status` combines
four local read views behind a common request shape. This is not a provider or
global event feed. Every request requires an explicit `operations.read` grant
and an existing per-view permission for the exact authenticated account.
Optional `platform` and `account_id` selectors, when supplied, must match the
grant exactly. The service never opens caller-controlled file paths.

| `kind` | Existing capability | Extra request fields | Returned data |
| --- | --- | --- | --- |
| `metadata` | Desktop, `metadata.audit.read` | Optional `cursor`, `limit` | Account metadata operation type and revision, without identifiers or fields |
| `draft` | Desktop, `drafts.read` | Required `conversation_id`; optional `cursor`, `limit` | This grant's deduplicated receipt revisions and observed present/empty state, not audit attribution |
| `import_job` | Desktop, `imports.local.manage` | Required `job_id` | Exact owner's current coarse import phase, not the job token, confirmation secret, payload or counts |
| `source_checkpoint` | ScopedProducer, `messages.ingest` | None | Exact producer's indexed sequence, revision and initial/indexed status |

Timeline pages allow 1–64 entries (default 32). Resume with `next_cursor`;
`has_more` indicates another immediately available page. Cursors are scoped
to archive instance, grant, exact account, operation view and draft
conversation when relevant. They are position markers, not authorization.
Revision-zero draft no-op receipts are omitted. A draft receipt may come from
a no-op write, so its presence never implies that this grant authored the
shared draft revision. The endpoint reads no draft body or shared draft audit.

No account receive/incoming journal, message text, notes, contacts, timestamps,
raw request identifiers, credentials, upstream state or environment paths are
returned. No mutations, automatic pollers, MCP or legacy HTTP endpoints.
