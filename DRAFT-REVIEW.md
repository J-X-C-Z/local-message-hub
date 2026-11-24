# Local draft review, never delivery

An ordinary native-live Desktop grant with `conversations.read`,
`drafts.read`, and the explicit `drafts.review.queue` permission can
`POST /api/v1/draft-review` with just
`{"conversation_id":"local-id","expected_revision":7}`. This requires an
existing, nonempty current local draft in that grant's exact indexed account and
conversation. Repeating the same grant/conversation/revision returns the same
record during retention. `GET /api/v1/draft-review?review_id=<id>` looks up the
caller grant's own record. Neither route takes an account override or returns
the body, body hash, challenge, or operators' credentials.

An archive operator (with access to the private profile directory) runs the
local executable; the desktop/HTTP API cannot invoke or proxy its operator
commands. On Unix each operator command takes `--directory <native>` and
one bounded JSON line followed by EOF on private piped stdin, and a private
piped stdout for the result. Both descriptors must be supplied by the trusted
operator parent (the pipe type alone is not proof of private ownership):

- `draft-review-init`: `{"passphrase":"<separate strong secret>"}` once,
  before enabling the grant. Save this secret in an operator-controlled store,
  never in plugin configuration.
- `draft-review-inspect`: `{"passphrase":"…","scope":{"platform":"qq","account_id":"…"},"review_id":"…"}`.
  The local JSON output includes exact conversation, body, revision and a
  single-use review challenge. Keep stdout in the trusted operator boundary
  and present the body as plain untrusted text, never as terminal commands or
  instructions to the launcher.
- `draft-review-decide`: same passphrase, scope and review ID, with
  `"challenge":"…"` and `"decision":"accept_for_review"` or `"reject"`.
  This writes only the local annotation after rechecking its scope,
  expiration, exact revision and body digest in the same transaction.

Challenges last up to five minutes; a fresh inspection supersedes the old
challenge. Pending requests expire after one day. The journal allows at most
eight active requests per grant and 4,096 total records, trimming rows older
than 30 days on new submissions. Changing/clearing the draft invalidates a
pending review; an accepted/rejected record is history about its old revision,
not transferable to new text. This is an application-layer boundary, not
OS isolation against programs that can alter the database or read the
operator's private descriptors. Run untrusted plugins without such access.
There is no sent/dispatch state, provider link, callback, scheduler, or external
action in any decision path.
