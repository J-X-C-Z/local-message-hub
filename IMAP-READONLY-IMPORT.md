# Confirmed import from an explicitly selected IMAP INBOX

The original operator-only Linux/macOS CLI reads one exact IMAP UID.
The explicitly selected batch below shares this boundary. It uses the
same pinned TLS and private credential pipe as [IMAP-READONLY.md](IMAP-READONLY.md).
No server connection is made except by an operator invoking one of these commands
with `--allow-network`. There is no HTTP route, auto-login, job, scheduler,
discovery, SMTP, remote write, account activation, or Windows support.

```text
local-message-hub-rs preview-imap-import --allow-network --host DNS_NAME --endpoint IP:PORT --ca-der TRUST_ROOT.der --leaf-sha256 HEX --mailbox INBOX --uidvalidity EPOCH --uid UID --directory ARCHIVE --account-sha256 ACCOUNT_HASH --after PREVIOUS_UID
local-message-hub-rs import-imap-readonly --allow-network --host DNS_NAME --endpoint IP:PORT --ca-der TRUST_ROOT.der --leaf-sha256 HEX --mailbox INBOX --uidvalidity EPOCH --uid UID --directory ARCHIVE --account-sha256 ACCOUNT_HASH --after PREVIOUS_UID --confirm RECEIPT
```

`ACCOUNT_HASH` is SHA-256 of the exact canonical mailbox address in the private
stdin pipe, without the ending LF. It is an exact operator-selected scope
identifier, not a credential or authentication proof; do not publish it.
The pipe supplies the canonical authentication address and then the secret
as two LF-terminated lines. The account hash is checked before any connection.
The one requested UID must be greater than the supplied previous cursor.
The first successful import for a source uses `--after 0`; later imports
supply the last committed UID. The operator is responsible for selecting UIDs;
gaps can be skipped explicitly, but never inferred from the server.
Only 256 distinct source keys are allowed per ordinary archive.

The transport verifies the chosen TLS hostname, independently pinned leaf and
CA, expects the specified UIDVALIDITY in read-only INBOX, and reads only
one complete, bounded message via UID FETCH/BODY.PEEK. It accepts the narrow
EML decoder subset: directly addressed incoming plain text with canonical
headers and identity. Apparent self-sent mail, aliases, multipart and other
unsupported content are rejected. Being delivered to the authenticated INBOX
does not authenticate From, Message-ID or other untrusted header claims.

Preview never writes. The receipt binds the exact raw octets, archive instance,
verified origin settings and CA, selected account, epoch, previous UID and UID.
The operator must explicitly repeat the command with the receipt and the same
scope; commit performs a new read-only fetch and refuses any mismatch before
writing. No receipt, epoch or identity is inferred. The local message owner,
ingest receipt and cursor are committed in the same append-only transaction;
another writer changing the cursor forces a conflict. Same-receipt retries
still refetch and compare the exact verified bytes before returning the
original receipt. Conflicting normalized messages are not revised.

The database persists the normalized email with its ordinary message fields,
one opaque scope hash and numeric epoch/UID cursor, and the opaque receipt.
It does not retain credentials, raw mail, host, IP, URI, CA path, certificate,
or full provider metadata. Cursor source identity includes the explicit
trust endpoint, DNS name, CA digest and leaf fingerprint; any such change
requires a new preview and starts a new cursor. A UIDVALIDITY change on the
same source is deliberately not reset or auto-imported. Use manual detached
EML import if this unsupported transition is needed. No outbound event is
generated for these append-only archive imports.

This source has not been built or exercised against any real provider and
does not claim general IMAP or email format compatibility.

## Explicit selected-UID batch (065, source only)

The operator can preview and explicitly commit **2–8 declared UIDs** using
`preview-imap-batch` and `import-imap-batch`. Use the same arguments and
private credential pipe as the singleton commands, but replace
`--uid UID` with `--uids UID1,UID2,...`; commit still requires
`--confirm RECEIPT`. The list must be strictly increasing, use canonical
positive decimal UIDs, and start after the exact previous source cursor.
No ranges, wildcard, discovery, automatic selection, or fallback
authentication are implemented.

The transport uses one pinned TLS connection, one authentication, one
read-only EXAMINE INBOX with exact UIDVALIDITY, and one strictly parsed
UID FETCH/BODY.PEEK command per selected number. Each response must match
its requested UID and command tag. The existing connection and session
deadlines remain unchanged for the whole batch; accepted raw mail is capped
at **256 KiB total**. Normalization rejects unsupported mail, conflicting
duplicate normalized IDs within a batch, and output over the existing
ingest limit. No partial batch is written.

Preview reports only the ordered selected UID and byte counts, resulting
high-water UID, and exact receipt. A distinct receipt domain binds the
archive, source trust identity, mailbox account fingerprint, epoch,
previous UID and every ordered UID/raw digest. Commit re-fetches those
same items and refuses a mismatch. On success, all accepted messages,
the idempotency receipt and advancement to the last selected UID are
recorded in one transaction shared with the original singleton cursor.
An exact retry re-fetches and verifies the same bytes before returning
the existing receipt. The original singleton command and receipt remain
compatible.

The cursor is the **operator-selected high water**, not a claim that all
other mailbox UIDs were fetched. A sparse list deliberately skips gaps;
UIDs at or below an advanced cursor cannot later be filled by this lane.
A changed or missing item, stale cursor, conflicting content, oversized
message or TLS/epoch failure aborts the entire batch. No background sync,
source discovery, credentials in logs, send, mailbox mutation or HTTP
route is added. No mailbox/network access, build or tests were performed
for this source increment.
