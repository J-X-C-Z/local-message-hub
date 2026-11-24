# Private WeCom bot text release journal

This source-only release kernel does not open connections, send a message,
start any command, or expose a route/CLI. It wraps the disconnected official
WeCom bot protocol, not personal WeChat, QQ, the incoming WeCom import
adapter, or a generic webhook. No draft-review decision or local handoff
receipt is a send grant.

A **trusted private operator controller** must explicitly select a native
ordinary archive, exact WeCom account/conversation, existing accepted-for-
review draft ID and revision, official bot ID, and explicit official recipient
kind and ID. No correspondence between an indexed conversation and a bot
recipient is inferred. A fresh review presents the exact text + destination
and a 120-second challenge; an authenticated explicit confirmation must
restate its scope and body digest. This output contains private drafts and
is not a plugin, log, or HTTP payload. The protocol's shorter connection
deadline applies in addition to this journal window.

The controller owns a no-follow exclusive lease on the private native archive.
A successful confirmation only enters `reserved` and releases the initial
authentication frame in memory. When the verified same official connection
has accepted subscription, the current operator proof, exact body/revision
and journal binding are independently checked again. The journal commits
`attempted` with FULL SQLite durability **before** handing off the one text
frame. If any later transport, expiry, storage, or process failure makes the
result uncertain, never reconnect or resend it. No credential, unredacted
body, bot/recipient identity, or raw provider frame is persisted by the
journal. Rows bind exact values by digests; draft text remains in its original
private archive and should be shown only to the operator.

Status distinguishes:

- `pending` / `expired`: stage only, no reserved protocol;
- `reserved`: single active acknowledgement to review, text not released;
- `closed_before_attempt`: a new independent operator review may be prepared;
- `attempted`: the text frame could have been attempted; recovery converts
  it to `delivery_unknown`, never reschedules or offers the body again;
- `provider_accepted`: only the same active protocol exchange received a
  correlation-matched success with a bounded provider message identifier;
  persist only a receipt digest, not a human delivery/read signal;
- `provider_rejected` / `delivery_unknown`: terminal for that draft revision.

Reopening with exclusive operator authority recovers abandoned reservations
before access. An already attempted draft revision cannot be re-approved,
including via a different old review, bot, or destination. A future real
adapter is required to verify the hard-coded TLS endpoint, enforce the
protocol deadline and single connection, and only feed authenticated raw
frames from that connection. No API permits externally constructed success
or receipt data to be recorded. These invariants are source-only pending
verification; no actual provider transcript or outbound action was used.
