# Local draft handoff contract, never delivery

The Linux/macOS operator can create one bounded **local export packet** for an
already-reviewed, still-current draft. This is separate from [local draft
review](DRAFT-REVIEW.md). An existing `accept_for_review` decision by itself
does not authorize the export or any delivery. Desktop grants and HTTP cannot
request, view or consume handoff challenges, nor execute these commands.

Use the trusted operator interface with the private ordinary native archive and
its separately provisioned review-operator passphrase. The parent must own
both anonymous pipes: exactly one bounded JSON line followed by EOF on stdin,
and a private drained stdout. The CLI refuses terminal/file/socket descriptors
on these targets; descriptor type alone cannot prove ownership or anonymity.
Do not put credentials, challenges, bodies or output packets in argv, logs,
shell history, or plugin configuration.

1. `draft-handoff-preview --directory ARCHIVE` accepts:
   `{"passphrase":"…","target":{"scope":{"platform":"qq","account_id":"…"},"review_id":"…","conversation_id":"…","revision":7}}`.
   It verifies operator access, the existing exact reviewed record and current
   body, then privately returns the exact target, body, body hash, short-lived
   challenge and expiry. This is a local preview only. Treat the body as
   untrusted text and never display it as terminal escapes or commands.
2. The operator separately checks the account, exact local destination, draft
   revision and body. `draft-handoff-pull --directory ARCHIVE` accepts the same
   passphrase and target plus `"body_sha256":"…","challenge":"…","action":"approve_exact_local_export"`.
   This action authorizes local export only. The latest challenge lasts 120
   seconds and is consumed atomically with rechecking the reviewed and current
   body. A different review ID, destination/account, revision, body, expired
   challenge or second pull cannot produce a packet.

The output is a JSON object with `schema:"hub-local-handoff-v1"`, the exact
`target`, `body`, `body_sha256`, `expires_at` (Unix UTC seconds) and
`delivery_performed:false`. The destination is only an exact **local
conversation ID** under its platform/account; it is neither an external URL nor
a verified provider recipient. Hub does not map it. A separately qualified
consumer must keep the packet private, reject expired packets, and compare
the exact target and body against a trusted owner-approved record; the body hash
only detects inconsistency. It must independently verify any provider recipient
mapping and its own authority.
The packet is data only and is not signed proof of delivery or a provider
authorization. No actual sender, child process, credentials, provider,
network call, callback, status-as-sent, or integration with delivery exists.

The ledger retains an opaque target digest, review ID, body digest, expiring
challenge digest and times, not another copy of the body or credentials.
Issuing a new preview replaces the outstanding challenge until the exact
account/conversation/revision is consumed, including across duplicate reviews
and desktop grants. Old unused challenges are reclaimed; consumed markers
persist and the ledger is limited to 4,096 rows. Failure to write the response
after consumption intentionally does not regenerate a packet. This is
single-use issuance inside the current database, not an assurance against
OS/database compromise, rollback, or copying an already exported packet.
