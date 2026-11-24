# Manual import jobs (source checkpoint; not yet tested)

This local, opt-in desktop plugin operation is available only for ordinary
Rust-owned live archives, not for guarded/locked profiles, browser sessions,
MCP, or producer grants. Its single exact account is taken from the grant's
`account_scope`; the grant requires `imports.local.manage`. No request supplies
a filesystem path, credentials, provider command, or delivery target. Imports
are never processed automatically.

1. **Prepare**: POST `/api/v1/import-jobs/preview` with
   `{"request_id":"unique-request-for-this-plugin","input_base64":"..."}`.
   Input is standard padded base64 of bounded text-only legacy Hub JSON export
   bytes. The existing normalized parser and limits apply; a member outside the
   plugin account causes the entire preview to fail. No message is added or
   changed by preview. An exact repeated prepare reads the existing job.
2. **Status**: GET `/api/v1/import-jobs/status?job_id=<id>` returns only the
   caller's job's phase, row count, and confirmation digest (no staged
   messages). A foreign or unknown ID returns the same failure.
3. **Commit**: POST `/api/v1/import-jobs/commit` with
   `{"job_id":"...","confirmation":"..."}` after explicit operator confirmation.
   Commit uses the established append-only idempotent legacy import mechanism:
   the batch and the durable receipt commit together. A crash can leave the
   job in `committing`, including after the data became durable; repeat this
   exact commit to recover it. Conflicting data produces `conflict` and
   doesn't replace an existing message. No SSE is synthesized. Refresh the
   desktop view explicitly.
4. **Cancel**: POST `/api/v1/import-jobs/cancel` with `{"job_id":"..."}` before
   commit begins. Cancelling a prepared job clears its payload. An operation
   in `committing` cannot be cancelled: do not guess whether it applied;
   retry the same commit or inspect its status. Cancelling an already
   cancelled job is idempotent.

The native SQLite archive temporarily includes the plaintext normalized
payload; it is removed from the job row on terminal transition (not secure
erase). There are at most eight staged jobs per grant/scope, 64 staged jobs
per archive, and 4,096 durable job records total. There is no automatic
tombstone eviction or maintenance command in this version. A failed storage
operation can leave a staged job for explicit recovery. No background runner,
legacy /api/imports wire parity, media conversion, or production verification
is claimed.

## Typed desktop client (source only)

`desktop_client::imports::Client` uses a managed native-live supervisor's readiness
frame and liveness predicate, the shared bounded JSON loopback transport, and the
separately operator-provisioned exact-account Desktop credential. It validates the
server's principal and import manifest and never enables the permission itself.
The client does not generate grants, read files, support public URLs or legacy
aliases, or trigger work from a background task.

- `preview` takes caller-supplied bytes and a retained request ID. It does not
  attempt to reopen a path or ingest messages. On an uncertain outcome, an explicit
  retry must reuse the exact same request ID **and bytes**; the server also exposes
  a paged, redacted `list` for finding the original job.
- `status` matches the requested opaque ID against the server result. The client
  surfaces confirmation and row count, not staged text. Present the exact import
  selection, account, job and digest to the operator before calling `Job::approve`;
  automatically echoing a digest does not count as confirmation. After a client
  process restart, retrieve the original job and obtain explicit confirmation
  again if it still needs to commit.
- A caller-initiated `commit` or `cancel` checks the same job first. An approval
  from another client cannot be submitted. On a managed reconnect, the same
  logical client's existing approval remains usable, but a fresh status read
  must match the original archive-derived ID, digest and row count before any
  write. A completed or conflicting commit reports the stored terminal state
  without another POST. Cancel only accepts prepared jobs (or observes an
  already-cancelled job); the server remains authoritative if a race occurs.
- A write timeout, broken response or server 5xx is not proof that nothing changed.
  Reconcile the original job; do not create a replacement with a different ID.
  Neither reconnect, status, nor list retries an operation. No UI was added.

This client slice was not compiled or functionally verified separately.

## Read-only reconciliation of committing jobs

For a lost response or interruption, explicitly GET
`/api/v1/import-jobs/apply-evidence?job_id=<id>` under the same exact
`imports.local.manage` grant. This is separate from ordinary polling and
available via `desktop_client::imports::Client::observe_apply` when advertised.
No state is finalized, no data is retried, and no row text is returned.

- `not_committing`: use ordinary status, which already reports terminal
  results. No staged payload or ingest receipt was read for this evidence.
- `receipt_not_observed`: this archive read snapshot did not contain the exact
  receipt. It is **not** proof of failure, rollback, or safe resubmission.
- `receipt_verified`: the exact normalized batch was durably applied (including
  rows which were already identical). Counts show `created` and `unchanged`.
  It does not assert that this particular HTTP attempt applied it; an identical
  import could already hold the receipt. Job phase stays `committing`.

The proof binds the current job owner and confirmation to the ordinary offline
legacy import producer and the same canonical normalized digest used for
ingest, all in a single read snapshot. A mismatching digest or malformed counts
fails closed. The bounded staged payload is read only when verifying a
matching candidate receipt. Phase, contents and confirmation are not changed.
Legacy job aliases and their shapes remain unchanged. This subset restores
useful completion/count visibility, not the old worker/scheduler.

## Raw stock JSON staging counterpart (095)

An existing exact ordinary NativeLive Desktop import grant may optionally
use `POST /api/imports?format=json` with a single `Content-Type:
application/octet-stream` and exactly one `Idempotency-Key`. The caller must
retain its chosen 1–80 ASCII alphanumeric, dot, dash or underscore key. The
optional `platform`, `account` and ignored display `filename` query fields
have the same constraints as the raw stock preflight. This reuses the pinned
stock browser's octet upload transport shape, not its automatic staging/write
or browser session contract.

Send the original approved text JSON bytes directly, no Base64. The whole
request is checked as strict object-array JSON, at most 512 KiB and 100 rows;
no foreign scope, unsupported or invalid row, duplicate ID, coercion, or
partial staging. Raw and projected payload limits both apply. An exact key
and bytes refer to the same existing job, also across the scoped v1 Base64
prepare; a different admissible body under that key conflicts. The existing
owner, archive, normalized fingerprint, quotas and transaction are preserved.
No file operation follows from filename or unrelated row data.

HTTP 202 returns the native Job state and confirmation, never proof that
messages were imported. After separately reviewing the exact staging
selection, submit the existing native commit with its exact confirmation.
No endpoint auto-commits, launches a job, approves on behalf of the caller,
or adds SSE. Missing or invalid headers/queries, unauthenticated browser
requests and other methods/modes do not stage. Existing raw read-only
preflight and native prepared-job behavior are otherwise unchanged.

Rollback: revert this source change to remove the raw entry point. It uses
only existing import-job tables and does not undo any previously staged or
committed job; continue to inspect/cancel/approve those through native routes.
No compilation, test, runtime, benchmark or external action performed.

### Typed raw upload (097)

The existing exact-account desktop imports client can call
`supports_stock_json_stage()` and opt into `stage_stock_json(&request_id,
&bytes)` when the managed native-live manifest advertises the literal route.
Use the already validated `RequestId` and the original selected bytes. The
SDK sends a single fixed `Idempotency-Key` header and derives account query
values from the connection's grant, without a filename, Base64 fallback or
arbitrary headers. Only HTTP 202 with a valid bounded typed native Job is
accepted for this operation; all other successful shared JSON routes still
expect HTTP 200. A repeat can return the current phase rather than Prepared.

Treat `OutcomeUnknown(StageStockJson)` as indeterminate. If the caller
already knows the exact job ID, inspect it through `status`. Otherwise retain
the exact same key and bytes and explicitly repeat only against the same
trusted managed archive and grant, after re-establishing readiness if
needed. The SDK does not retain uploaded bytes, retry or infer archive
identity from a prior port. Never substitute a new key or switch formats to
recover an unknown outcome. After a successful typed stage, use the same
explicit approve/commit and optional apply-evidence paths; staging never
imports or accepts approval on the caller's behalf.

Rollback 097 by reverting the SDK source patch. The existing server route,
stage records and native status/cancel/commit paths are unaffected. This
source checkpoint was not compiled or tested.
