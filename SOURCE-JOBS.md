# Manual source jobs (ordinary native Hub)

Manual source jobs persist an operator's intended source operation separately from local
detached imports. They do not fetch, scan, schedule, send, save credentials, run a provider
or advance a source cursor. Only the existing normalized ingest endpoint can advance the
real cursor. The operator or an explicitly invoked external client supplies any actual
input through that existing endpoint.

Enable the jobs capability only for an exact `scoped-producer` grant with both
`messages.ingest` and `sources.jobs.manage`. The grant already identifies the source
and account; jobs never accept an account, path, remote URL, token or progress sequence
from the request. A lane belongs to one archive and one source grant/platform/account
combination. Jobs are not available for guarded, snapshot or legacy serving modes.

Under `/api/v1/source/jobs`:

- POST `/start` with `{"request_id":"operator-chosen-stable-key"}` creates an active
  job or returns the current state of the previous job for that key. A paused job still
  holds its lane; a competing start returns a conflict. After completion or cancellation
  a new operation must use a different request key.
- GET `/status?job_id=...` returns the saved job without making any changes.
- POST `/pause`, `/resume`, `/cancel`, `/progress` and `/complete` take
  `{"job_id":"...","expected_version":1}`. Pause, progress and complete require an
  active job; resume requires a paused job; cancel accepts active or paused. Completion
  and cancellation are terminal. Mutations increment the version. Retrying the most
  recent mutation with the original version is harmless; older writes conflict.
- `start_sequence`, `observed_sequence`, and the opaque `observed_checkpoint` are
  observations of the existing scoped source cursor. Start, resume, progress and complete
  take the observation in the same SQLite transaction as the job change. Polling a job is
  not polling the source cursor. Use the existing checkpoint endpoint to decide where to
  continue ingest, and do not treat the job as a lock on normal ingest. Pause/cancel
  record operator intent only; they cannot stop work in an independent process.

There is no queue or automatic restart. Active/paused jobs and their version survive a
process restart. Record the returned ID or repeat start using the same request key to
recover it. An archive retains at most 4,096 jobs, including terminal jobs, and 128 per
lane. Reaching a quota fails closed; there is no implicit deletion or reset of idempotency
history. Job state is stored in the ordinary native SQLite archive, not in normalized
message event streams or import records.
