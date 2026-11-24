# Local export scheduler and artifact core (source-only)

This ordinary-native-archive core has a private foreground Linux entry
point. There is no HTTP route, filesystem destination, daemon, operating-system
startup action, or network. The optional runner polls only during an
explicitly launched bounded foreground invocation. Opening an archive
neither creates tables nor starts a driver. The legacy API is not implemented.

## Ledger

- The trusted caller supplies an already authorized local principal and exact
  account. Creating a schedule is explicit and pins an existing, owned preset
  at one revision. The first slot is one interval in the future. An explicit
  claim coalesces missed intervals to the newest due slot.
- An immediate transaction admits one unexpired attempt per account, even
  across principals. An owner can retry its expired record up to three total
  attempts under the same schedule/slot key. The account clock watermark
  rejects regressions that could revive expired authority after restart.
- Cancellation fences the outstanding attempt. Disabled schedules and
  terminal jobs still count toward their caps until retention exists.

## Prepare, confirm, publish

1. On explicit local request, `prepare` checks the exact scope, active lease,
   preset ID and revision. It reads all selected conversations from the same
   SQLite snapshot, using the ordinary serializers and existing DB deadlines.
   Preset order is preserved; rows within each conversation are ordered by
   timestamp and ID. One JSON, CSV, TXT or Markdown text artifact is bounded
   to 20 conversations, 1,000 rows total, and 1 MiB. No media bytes or URLs
   are fetched or added; message text itself may contain untrusted links.
2. The private request returns only metadata and an in-memory confirmation
   receipt bound to archive, account, principal, slot, claim attempt,
   preset revision and exact rendered bytes. Preview changes no scheduler
   metadata and writes no files or blobs. The operator/runtime must
   obtain applicable confirmation rather than echo it automatically.
3. `publish` accepts only the original prepared object and exact
   confirmation. It rechecks scope, preset and unexpired lease in an
   immediate writer transaction, enforces 32 artifacts / 8 MiB per
   account and 256 artifacts / 64 MiB per archive, inserts the bounded
   BLOB and consumes the attempt in the same commit. Failure rolls back
   both. Publication does not imply a download, transfer, or delivery.
4. An authorized caller can reconcile a lost reply with `published` by
   exact slot. It reads only committed artifacts for that principal and
   account and checks the size and digest. No raw path is accepted.

Still needed: general client job listing. A long-running service or
login-time scheduling is deliberately not offered. The private BLOB belongs
to the ordinary archive's confidentiality boundary; it is not separately
encrypted or copied to an export directory. No builds or tests were run.

## Explicit operator session (Linux)

A trusted native parent can spawn a fresh foreground process:

```
local-message-hub-rs export-schedule-operator --directory <owned-ordinary-native> --grants <private-current-owner-file>
```

Stdin and stdout must be separate anonymous pipes owned exclusively by the
trusted parent/child (the Linux checks cannot prove endpoint exclusivity).
Do not capture either stream in logs. Each JSON input line is at most 4 KiB.
Each pipe exchange has a 40-second timeout. The only arguments are the
operator-selected archive and grant-file paths; the command does not open a
file destination or accept input from a terminal, HTTP, browser, or env.

The first private JSON line contains `installation_id` (the expected
32-character archive instance ID from the trusted installation),
`grant_id`, `scope:{"platform":...,"account_id":...}`, `token`, and
`action`. The token must authenticate the **current** named exact-account
Desktop grant with `messages.read`, `messages.export` and
`exports.presets.manage`; that grant is re-read before each confirmed
effect. The operator should serialize policy changes with sessions; grant
file replacement is not a cross-filesystem transaction with SQLite.

| Action `kind` | Additional fields | Effect / private reply |
| --- | --- | --- |
| `list` | none | `schedule_list`, no data mutation |
| `inspect_schedule` | `schedule_id` | `schedule_inspection`: named schedule and attempt-ledger status, metadata only |
| `create` | `preset_id`, `preset_revision`, `interval_seconds` | Confirmed create; `schedule_created` |
| `cancel` | `schedule_id` | Confirmed disable of the schedule; `schedule_cancelled` |
| `cancel_attempt` | `target`: exact `schedule_id`, `due_at`, `attempt`, `lease_until` | Confirmed fencing of one live attempt; `schedule_attempt_cancelled` |
| `run_due` | `schedule_id` | Confirm claim first, then receive `no_due_run` or metadata-only `confirm_artifact`; only a second explicit confirmation can publish |
| `retrieve` | `schedule_id`, `due_at` | Confirm the named artifact metadata; `artifact_bytes` JSON line then exactly `byte_count` verified raw bytes, then EOF |

For each `confirm_operation` or `confirm_artifact` output, the trusted
parent must show the exact operation to its operator and only send
`{"confirm":"<returned digest>"}` on explicit approval. An automated echo is
not authorization. On EOF, denial or timeout after a run is claimed, no
artifact is published; the attempt can expire and be reclaimed under the
same slot. A lost publication reply can be reconciled by separately
requesting retrieval with the exact slot shown in the preview.

The manual operations above remain one-shot and never auto-publish.
The operator chooses whether and when to start it. It does not add an
HTTP path, an old WebUI contract, a filesystem publisher, or a new legacy
compatibility claim. No builds or tests run.

## Optional recurring publication (Linux foreground only)

This separate mode lets the operator expressly authorize future **scoped
text artifacts inside this archive**, without approving every future
digest. It is not a global auto-export setting or a change to manual
confirmation. Approval covers the exact installation, current grant,
exact account, existing schedule and preset revision, selected conversation
IDs, date bounds, format, interval, first authorized slot, expiry, output
cap and maximum number of artifacts. The confirmation frame explicitly
says that future archive content may be included without per-artifact
confirmation, publication is into the private local archive only, and
there is no automatic retrieval or delivery.

Use the same private command and grant requirements. Added actions:

| Action `kind` | Extra fields | Effect |
| --- | --- | --- |
| `enable_recurring` | `schedule_id`, `expires_at` (UTC Unix seconds), `max_artifacts` | Read and present a `recurring_offer` in `confirm_operation`; only explicit informed approval persists it. Maximum 30 days, 32 artifacts, and an unchanged future first slot. A schedule with an outstanding lease cannot be enabled. |
| `recurring_status` | `schedule_id` | Return the still-active allowance for the same current credential. |
| `revoke_recurring` | `schedule_id` | Confirm and remove the local allowance. A generation already in flight can no longer publish. Cookies, an HTTP route, and other accounts cannot invoke it. |
| `run_recurring` | `schedule_id`, `max_wait_seconds` (0–900) | Explicitly start a **foreground** worker. Poll every 15 seconds at most until the named schedule has one due run or the wait ends. Zero does one immediate check. Return metadata only. A trusted parent exits/stops it like any foreground child. |
| `run_recurring_due` | `schedule_ids`: 1–8 distinct exact IDs | One foreground pass in caller order, no sleep, no discovery. Each schedule requires its own unchanged and still-valid recurring approval under the same exact grant/account. At most one artifact per listed ID; the private archive remains the only destination. |

The due dispatcher reports one status per checked ID: `published` (metadata
only), `not_due`, `busy`, `unavailable` (including missing, unauthorized or
no longer usable), or `quota`. All IDs are checked for format and
uniqueness before the first claim. The 30-second admission budget caps
starting more work, not already admitted SQLite operations. Clock, grant
or unexpected storage errors stop the pass, reporting completed outcomes
and a `stopped` reason, with the failed/remaining slots omitted. Earlier
publications are not rolled back; use the metadata inventory to reconcile
a lost response. An interrupted claim can hold a lease until it expires.
This is not legacy global auto-export: pinned Python starts its manager
from server startup, exposes global status/configure/run/cancel under
`auto_export`, and cancels it when that capability is disabled; this new
action opens no browser route and does not start a daemon or enable rules.


This mode cannot mint a manual digest or pass a synthesized confirmation.
The renderer checks the live allowance in the same read snapshot before
reading the records. A writer transaction rechecks the current schedule,
preset revision, lease, allowance generation, deadline and quota,
decrements the allowance, inserts the private BLOB, and consumes the
lease atomically. Failures do not spend the allowance or publish. An
interrupted attempt can still occupy its lease until expiration. Rotating
credentials invalidates the prior authorization; revoke and reauthorize
under the new current credential. The private grants file is re-read on
each poll and before publication; use the trusted parent and serialize
credential changes with active operations (there is no atomic transaction
between grant file edits and SQLite).

Intervals and expiration use UTC seconds, independent of local timezone
and DST. During a foreground run a wall-clock change beyond two seconds
relative to its monotonic elapsed time aborts; the persisted account
watermark rejects backward regressions on later operations. Forward
clock changes while the process is stopped are indistinguishable from
elapsed time: the runner never replays every missed interval, and never
admits publication using an expired allowance. The final UTC/lease check
is at writer-transaction admission, not a guarantee about the wall-clock
instant of the storage commit. The wait bound governs polling;
a claimed run may still use its bounded SQLite and pipe response time.
The parent is mandatory and Linux terminates its child on parent death.
No shell startup tasks, no out-of-process renderer, no automatic file
writes, no cloud, no provider/network access and no body in runner logs
or replies. Retrieval still requires its separate exact artifact approval.
This source does not enable an operator's schedule or execute any run.

## Generated-artifact inventory and manual retention

The existing private Linux operator protocol also accepts:

| Action `kind` | Extra fields | Effect |
| --- | --- | --- |
| `list_artifacts` | Optional `limit` (1–16; default 16) and `after` (the previous `next_after` object) | `artifact_list` page from the same archive/account/principal, newest slot first with a stable schedule-ID tie-break. Shows only schedule ID, due slot, format and bytes. No text, checksum, preset, or destination. |
| `remove_artifacts` | `slots`: 1–8 distinct `{"schedule_id":"…","due_at":…}` objects | Read-only `artifact_removal` preview in `confirm_operation`. Only explicit approval of this exact preview can commit removal. |

The preview describes the exact slots, body digests, sizes, and already
removed records. The existing private challenge binds that preview to the
installation, account, grant and action. After confirmation the operator
process reauthenticates the current grant; a single writer transaction
checks the original generated export run, rechecks each selected live
BLOB's digest, and removes only those matching BLOBs. Any mismatch rolls
back the batch. A metadata-only tombstone is written alongside each
removal and an exact repeat reports it as already removed without removing
anything else. No run is replayed and previously spent permission or
schedule/job limits are not restored.

Only the active artifact-count/byte quota is reclaimed. This is logical
SQLite deletion, not secure erasure, guaranteed file shrinking, or
revocation of already retrieved copies. It does not delete source records,
application data, backups, exports elsewhere, schedules, or files. There is
no implicit or timed deletion and no HTTP route. Builds and tests deferred.


## Read-only schedule and attempt inspection

The exact operator may explicitly request `inspect_schedule` without a
confirmation frame. It reads one private archive snapshot after current
grant/account validation. The response includes the UTC observation second,
enabled state, interval, whether the pinned preset still matches, the next
unclaimed slot, the current lease record (or null) and the latest terminal
ledger slot (or null). Unknown and foreign IDs are equivalent. The current
lease includes its recorded expiry and whether it has expired at the
observation time. The terminal record reports the slot's due time and state,
not a completion time, and does not prove an artifact is still retrievable.

`ledger_status` is disabled, preset_changed, leased,
lease_expired_retry_available, lease_expired_attempts_exhausted, slot_due or
waiting_for_slot. These are observations of this schedule's ledger alone.
They are **not** worker liveness, a recurring allowance, a guarantee your
next claim will succeed, or a promise that a task will run. In particular,
a due schedule may still require manual approval, be affected by time
changes, or run into local limits. No other principal's leases are examined.
Existing recurring status and execution commands remain separate.

This restores a narrow local equivalent of the old previous-outcome/status
view, not the Python global auto-export service or HTTP/UI wire shape. No
schedule is enabled or claimed, no expiry is reconciled, no clock ledger is
advanced, and no output, path, token, message or error payload is returned.


## Cancel only the selected active attempt

An exact owner can use `inspect_schedule` to find its own still-unexpired
lease record. In a separate private operator connection, the caller may
explicitly choose `cancel_attempt` using the precise schedule ID plus the
observed due slot, attempt counter and lease-until values in `target`.
The operator must confirm the resulting challenge; a read-only preflight
first checks that all fields still describe the current active lease. The
grant and exact lease are checked again after confirmation. This is
separate from `cancel`, which disables the entire schedule.

If the compare-and-set wins before publication, that exact attempt cannot
publish: both the manual and recurring publishers revalidate the lease in
their own writer transaction before writing an artifact or spending the
recurring allowance. It does not stop an in-progress CPU operation or kill
another process. If an attempt has expired, been replaced, already published,
or otherwise become terminal, it is no longer cancellable through these
fields. Publication that won the race remains untouched. No existing
artifact is fetched, overwritten, or removed. Other slots, the schedule
itself, and any recurring allowance are unchanged. A lost cancellation
response can be inspected through a fresh read; observing `cancelled` proves
the slot is terminal, not which action made it so.

The pinned original WebUI's current-job Cancel behavior motivates this
local operator capability. The global legacy HTTP action, stock background
daemon, OS process cancellation and deployment are not implemented.

## Reconcile one known run after a lost response

An exact operator can send `{"kind":"inspect_run","slot":{"schedule_id":"…",
"due_at":1900}}` as the action in the existing private command. It uses the
same current grant, archive and account, makes no claim or write, and does
not require a confirmation. The resulting `schedule_run_inspection` contains
only the exact slot, observation time, state, attempt counter, optional
lease deadline, and optional artifact state. An unrecorded slot under
an owned schedule yields `not_recorded`; unknown and foreign schedules
both return unavailable. `lease_elapsed` is an observation of the still
leased row, not a terminal `expired` state. A `published` run is reported
only with consistent retained-artifact metadata or its removal tombstone.
The distinction `retained` / `removed` does not prove byte integrity,
delivery, or secure erasure. Separate confirmed retrieval still verifies
bytes and is not invoked by this read. Use this after losing a response
rather than re-claiming or inspecting only the latest run. No route,
worker, path, digest, source content, or new permission is added.
