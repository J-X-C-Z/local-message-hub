# Explicit IMAP read-only preview

This is an opt-in Linux/macOS operator CLI, not a provider job, archive writer,
background synchronizer, or general IMAP client. Run it only against an
account/mailbox you are authorized to access. Provider profiles never trigger
it, and the legacy/HTTP APIs cannot access it.

```text
local-message-hub-rs preview-imap-readonly --allow-network --host DNS_NAME --endpoint IP:PORT --ca-der TRUST_ROOT.der --leaf-sha256 HEX --mailbox INBOX --uidvalidity EPOCH --uid UID
```

The operator must supply the exact numeric endpoint (use brackets for IPv6),
the independently known DNS certificate name, one trusted CA DER file, and
the independently verified lower-case SHA-256 hash of the leaf certificate DER.
There is no DNS resolution, endpoint discovery, proxy, retry, fallback, TLS
downgrade, or trust-on-first-use. Disconnect if the socket, TLS 1.3
chain/hostname, or leaf pin differs; authentication starts only after those
checks. Leaf rotation requires updating verified trust data explicitly.
The socket connect and subsequent wire session use separate overall deadlines
of three and eight seconds.

Use a trusted caller to pass exactly two LF-terminated lines on a **private
anonymous stdin pipe** and promptly close its write end: the canonical
mailbox address (lowercase DNS domain) used as the authentication identity, then its
operator-approved ASCII secret. Never pass either through shell command text,
arguments, environment variables or a file. This CLI rejects terminals,
files and sockets for stdin; the caller is responsible for the pipe being
private and anonymous. Waiting for its EOF is not part of the wire session
deadline. The CLI does not store credentials, echo server lines, or return
message fields. Temporary app-owned secret buffers are cleared on drop; this
is not a guarantee about OS paging/dumps, allocator copies, or TLS buffers.

The sequence is CAPABILITY, AUTHENTICATE PLAIN with an empty challenge inside
verified TLS, EXAMINE INBOX with the expected explicit UIDVALIDITY and
READ-ONLY result, one UID FETCH with BODY.PEEK, and LOGOUT. No SELECT, STORE,
EXPUNGE, COPY, MOVE, IDLE, LIST, SEARCH, SMTP, refresh, or destructive remote
operation is emitted. The single FETCH is limited to a 256 KiB complete
message; the returned exact UID, zero-offset literal length and RFC822.SIZE
must agree. The body is discarded without storage, extraction, or display;
stdout reports only epoch, UID, byte count, and no-archive-write status.
An authentication attempt can still be recorded by or affect the server's
session/security state.

The decoder intentionally accepts only a bounded response subset: an ordinary
OK greeting, advertised IMAP4rev1/rev2 and AUTH=PLAIN, exact expected tagged
progress, a narrow EXAMINE grammar, and one FETCH whose attributes are ordered
as requested. Empty/bigger messages, same-scope drift, unsolicited responses,
alternate legal attribute orders, TLS versions or login methods (including
OAuth and SASL-IR) outside this subset fail closed rather than driving
fallback commands. Do not infer broad compatibility or use as an archive
import. No end-to-end connection or provider account was exercised for this
source-only delivery; the separate loopback synthetic fixture remains unchanged.

Standards: [IMAP4rev2](https://www.rfc-editor.org/rfc/rfc9051.html),
[SASL PLAIN](https://www.rfc-editor.org/rfc/rfc4616.html),
[implicit TLS for mail access](https://www.rfc-editor.org/rfc/rfc8314.html).
