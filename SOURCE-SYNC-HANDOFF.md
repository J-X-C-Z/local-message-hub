# One-shot native source handoff (057)

Pinned Python `POST /api/sync` directly invoked local provider sync.
The Rust host does not claim to implement or emulate that wire contract.
This is a **separate native, granted, pull-only handoff** so a trusted
operator who already manages an exact source lane can pass one existing
manual job to an independently invoked local runner without separately
sharing a job ID. It does not replace the source job start/status API,
nor does it run an adapter.

Only an ordinary native-live `scoped-producer` grant with both
`messages.ingest` and `sources.jobs.manage` can use these routes. Scope is
the existing archive, grant ID, platform and account, never a request field.
There is no adapter discovery or installed-process attestation. The runner
must already be separately approved and launched by the operator; the
Hub does not send it anything or validate the binary. A shared bearer
cannot distinguish two processes presenting the same grant.

1. Create the ordinary active job as before, using the audited source job
   start command/API.
2. POST `/api/v1/source/sync-intent` with its exact current
   `{"job_id":"…","expected_version":1}`. HTTP 202 means a local queue
   record was written, not that data collection started. A retry while
   still pending returns the same record and does not renew expiry.
3. The local runner can GET `/api/v1/source/sync-intent` without query
   parameters. Results are `no_intent`, `pending_operator`, `expired`,
   `no_longer_current`, or `claimed_by_grant`. Discovery does not mutate
   anything. The response explicitly denies adapter launch, installation
   observation, and source job completion.
4. The runner may POST `/api/v1/source/sync-intent/claim` with the
   returned job ID/version. SQLite atomically validates the current
   active job and consumes the one-shot handoff. It returns
   `claimed_by_grant`, not a claim that work ran, completed or advanced
   a checkpoint. First claim returns `claim_replayed:false`. A retry
   for the same existing binding, authenticated bearer and still-active
   exact job version before expiry returns `claim_replayed:true` with
   the original deadline and performs no mutation. Use this only to
   reconcile an uncertain claim response, never as authorization for
   another runner or a repeat execution. A rotated credential cannot
   replay the prior claim.
   Bearers and credentials must stay in the independently trusted
   local launcher. The normal source SDK/ingest/checkpoint protocol is
   unchanged; neither operation modifies it.

The 120-second deadline uses the Hub system Unix timestamp and is never
extended by an API retry. Backwards time before the creation time
refuses pickup; operators must keep the system clock correct. A change
to the underlying job version, cancellation, completion, or pause
invalidates that handoff. One marker per existing lane persists across
restarts, and only a different active job can replace it. This prevents
implicit requeue and needs no cleanup worker. Expired current claims now
report `expired` rather than a usable claim state. Preexisting claims
without a credential binding cannot be replayed; existing bound WeChat
and WeCom detached receipt verification is unchanged. A fresh handoff
requires a different authorized active job. The stored claiming bit is
not a lease, an ingest lock or an exactly-once execution guarantee.

No request body accepts executable, path, provider URL, credentials,
account selector or message content. Bearer authorization is already
required and only a domain-separated digest is retained for claim binding. No provider access, OS process, login,
remote request, autostart, queue polling in the Hub, or automatic retry is added.
The old global `/api/sync` remains unimplemented. Guarded, browser,
snapshot and imported media are unchanged. Code-only; build and
functional verification are deferred to unified testing.
