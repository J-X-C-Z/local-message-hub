# Managed Desktop local drafts and review requests

Use `local_message_hub_rs::desktop_client::drafts::Client` on a trusted
Desktop worker with an already accepted managed **ordinary native-live**
connection and an installed exact-account Desktop grant. It uses the shared
numeric loopback transport, current owned child predicate and strict
authenticated manifest identity. This client creates no process, file, or
grant. It performs no provider request, handoff, release, or delivery.

- Draft reads require `conversations.read`, `drafts.read` and the fixed
  advertised `local_drafts` path. Reads apply only to an already indexed
  exact conversation and return a private session-bound revision and optional
  body. Revision zero with no body is an unset draft, not a missing conversation.
- Explicit saves also require the advertised write flag and `drafts.write`.
  Call `save(&draft, &request_id, Change::Set(text))` or select
  `Change::Clear` explicitly. Text remains plain data, including any URLs or
  markup; do not interpret, open, or execute it. The body is nonblank 16 KiB
  maximum with only CR/LF/TAB permitted as controls. The server compares the
  exact revision. Its receipt contains only the revision and presence after
  that request, not a fresh read or any provider result. A same-value save can
  keep its revision.
- After saving, explicitly read the current draft again before choosing whether
  to queue a review. A revision or retained request-key conflict never merges
  or overwrites the current draft automatically. Keep the same key with its
  original target, revision, and body in private caller state. If a response
  was lost, confirm the same archive with the supervisor and inspect current
  local state before deciding whether to submit another change. The client
  does not rebuild an old write from the new revision or retry it on reconnect.
- `queue_review(&draft)` requires a present exact revision, the independently
  advertised Unix review route with `requests_only:true` and `delivery:false`,
  and `drafts.review.queue`. It returns only an owner-bound opaque review ID,
  the draft revision and a fixed status. A repeated explicit request for the
  same grant, conversation and current revision can return the same review.
  A conflict can mean the draft changed or the operator review setup is not
  provisioned; the client neither provisions it nor attempts a fallback.
- `review_status(&review)` reads a known review from the current session.
  After reconnect, `restore_review(id, revision)` is an explicit read-only
  lookup of a previously retained exact pair, not a resubmission. The
  backend independently verifies the grant and account ownership. A mismatched
  returned pair or protocol violation fails closed. If queueing failed with
  unknown outcome and no ID was retained, do not manufacture a status or retry
  automatically. An explicit request on the same still-current revision can
  recover the same review during retention if the server had admitted it.

`AcceptedForReview` is only a local annotation about the recorded revision.
It is not consent to send, evidence that a current draft still matches, or a
provider status. `Rejected`, `Expired`, and `DraftChanged` also stop at the
existing local review boundary. There is no operator challenge or decision API
in this SDK. Windows native-live, when available, can use draft reads/saves
subject to grant and manifest; server-side draft review remains Unix-only.
There are no snapshot, guarded, imported-media, legacy or browser fallbacks.

Source only. Reverting this SDK module, its two manifest readers and this
document leaves storage, routes, operator tools and existing approvals
untouched. No build, tests, process or service use was performed.
