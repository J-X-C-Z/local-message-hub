# Local operator pause for ordinary message ingress

An existing Rust-owned ordinary archive starts open (revision 0). Its local
Linux OS owner can observe or change a single archive-wide policy from a
trusted private filesystem. No HTTP, desktop plugin permission, source
credential, scheduler, provider or public administration route is added:

```text
local-message-hub-rs message-ingress-policy --directory DIR --archive-id ID
local-message-hub-rs set-message-ingress-policy --directory DIR --archive-id ID --expected-revision REV --state paused
local-message-hub-rs set-message-ingress-policy --directory DIR --archive-id ID --expected-revision REV --state open
```

Use the already trusted installation's exact archive ID and last observed
revision. The command checks effective UID ownership and private regular
storage, ordinary archive identity and the expected revision. A same-state
request at current revision does not change the revision or allocate an
initial row. Reported state is authoritative only for this archive and
policy-aware running writers. Do not rely on this as an OS ACL against
older binaries, direct database writers or other processes.

The policy is stored transactionally and checked in the same SQLite
immediate writer transaction as each admission. After a pause commits,
new ordinary message ingest is rejected across ordinary live producers
and detached, claimed, and IMAP apply paths sharing native EventStore
ingest. Newly staged import jobs, prepared-to-committing transitions,
source job creation/resume, and new handoff queue/claim are also blocked.
Existing writer transactions admitted ahead of the pause may finish
before it commits. A job may already have entered `committing` when the
pause wins before its separate message transaction; no new message or
receipt is then written, and the job remains explicitly recoverable.

Validated exact prior receipt replay and metadata-only terminal job
reconciliation remain available. So do read-only preflights/status, job
pause/cancel/complete/progress and exact idempotent replays that admit no
new work. A paused archive reports `423 message_ingress_paused` on those
HTTP admissions; some offline wrappers report their existing generic
apply error, and the operator can query this policy. Reopening does not
run, renew, replay, or start any job automatically.

This is not the stock auto-sync checkbox or a universal database lock. It
does not stop an independent reader, already issued local adapter CLI
preflight, or external process; permission fails at ordinary Hub apply.
Guarded archives, separate Campus or Moments stores, configuration,
fresh-target migrations/restores, and other non-message domains are not
covered. Never downgrade a running paused writer to a non-policy-aware
build as a way to preserve the pause.

## Exact effective-settings observation

Only the already-authorized exact Desktop account on the ordinary live
listener, with both the effective-settings `messages.read` permission and
`imports.local.manage`, can see `manual_import_admission: "open" | "paused"`
on `GET /api/v1/settings/effective`. The response reports the effective
admission observed for its own authorized import lane, not the administrator,
policy revision, archive ID, other accounts or their activity. The field is
omitted for other grants and modes. `manual_import_jobs` still describes
capability presence, not the instantaneous state of admission. No legacy
settings read/write permission, API setter, or control is added.

An open observation is not a reservation or authorization, and a pause is
not approval/lease revocation. Existing explicit approvals or claims remain
subject to unchanged scope, digest and expiry checks; a new apply can proceed
after reopening only if still valid under those checks. Copying or rolling
back a database is not policy synchronization. The persisted row survives a
normal restart and is consulted by each participating writer after startup.
A client receiving HTTP 423 must not automatically resubmit or assume no
prior commit; observe the specific job or exact receipt as appropriate.
