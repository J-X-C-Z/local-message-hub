# Guarded browser HTTP foundation (rebuild-030)

Linux only. This is a guarded, session-cookie **HTTP contract** for a future
native-parent webview, not a WebUI, browser installation, browser qualification,
old login screen, or replacement for the 014 native bearer listener.
Use a trusted local computer, a trusted archive directory and a dedicated,
isolated cookie jar per listener/profile. Do not use an ordinary browser jar,
allow navigation to other origins, forward the port, or run a proxy on it.
HTTP cookies are **not port scoped**. These cookies use plain IPv4 loopback
HTTP (no TLS or `Secure` attribute), and this does not defend against hostile
local processes, browser extensions, or someone who can read the private pipes.

## Operator/parent launch

Launch `serve-guarded-browser --directory <guarded-native> --grants <private-json>
--grant-id <exact-id>` and pass trusted anonymous private stdin **and** stdout
pipes. The OS FIFO check is not evidence that your pipe is exclusive.
Only the selected desktop ID can back the listener, and it must opt in to
`legacy.browser.read`, `legacy.http.read`, `conversations.read`, and
`messages.read` with a single exact account. Add `contacts.read` and/or
`legacy.search.read` only if needed. Do not forward device bearer tokens
(the listener rejects them); grant ID is not a secret. Use the HTTP origin
returned in the private ready frame, never an assumed port.

1. Control initially reports `guarded-browser-read-only`, locked. Unlock via
   the existing private stdin `unlock` frame. The passphrase never belongs
   in a webpage, URL, argv, log, or HTTP request.
2. The parent sends `{"command":"issue_browser_ticket"}` on that trusted
   control pipe and reads a `browser_ticket` frame (ticket, expiry 30s).
   The ticket is intentionally output **only** on private stdout; do not
   capture it in analytics, logs, JS, session history, URL, or argv.
3. The trusted native parent may exchange it with an **empty** same local
   origin POST to `/_local/browser/bootstrap`, in header `X-LMH-Bootstrap`.
   No query, Cookie, Origin, Referer or Authorization headers; site metadata
   may be absent or `none`. A successful exchange returns 204, no body, and
   an opaque 256-bit `lmh_guarded_browser` cookie, `HttpOnly; SameSite=Strict;
   Path=/api; Max-Age=300`. This contract still needs an approved native
   webview integration to set its isolated jar without exposing credentials
   to page code. Do not implement this step with a script on the page.
4. The chosen jar uses the cookie for GET of the eight legacy read
   projections listed in `LEGACY-HTTP.md`, from `/api/sources` and
   `/api/settings` through `/api/quoted-message`. The account and optional
   read capabilities are those of the selected grant; no alternative
   account/token is accepted. Annual/pair and all other routes, including
   static pages, writes, providers, imports, SSE, and fetch, are absent
   unless explicitly noted below. The authenticated
   `GET /api/desktop-contract` returns the redacted protocol descriptor
   described in [DESKTOP-CONTRACT.md](DESKTOP-CONTRACT.md), not browser
   identity, credentials, archive data, or a session renewal.
   `/healthz` is unauthenticated liveness, not session/readiness.

## Session and request boundary

Only the exact IPv4 loopback Host and origin-form requests are accepted.
Reads deny differing Origin/Referer where present and deny cross-site and
same-site-but-different-origin fetch metadata. There is no CORS or
preflight grant. Cookie is accepted only as the single exact cookie in
the dedicated jar. Requests and errors reflect no credentials; responses
are no-store, nosniff, no-referrer, non-framable, and same-origin only.
Old native bearer mode and its separate policy remain unchanged.

One pending ticket and one active session per listener. Issuing a ticket
replaces the old pending one; only a successful single redemption replaces
the active session. `revoke_browser`, lock, failed/new unlock generation,
private pipe EOF and process restart revoke authority. Sessions expire in
five minutes or at the earlier guard expiry; there is no implicit refresh.
Buffered data is checked again before writing the response. Bytes already
sent before revocation cannot be recalled. No session state is persisted.

### Private session status and revocation

Send `{"command":"browser_session_status"}` over the supervisor's existing
private control pipe. Browser mode replies with
`{"event":"browser_session_status","status":{...}}`. The fields are
`locked` and the guard, ticket and session `*_expires_in` values (seconds,
rounded up, or null). Ticket and session time is capped by the current guard
window. A stale, expired, missing or different-generation ticket/session
appears as null. This is a point-in-time diagnostic, not a lease extension
or a credential. No proof, generation ID, passphrase, bearer token, browser
artifact, account or data is read out. A non-browser invocation returns
`browser_session_status_unavailable` on the same control output.

The operator can send `{"command":"revoke_browser"}` (or
`{"command":"revoke_browser_sessions"}`) to clear **both** the unexchanged
ticket and the active session. The guard itself is unchanged; use
`{"command":"lock"}` to shut the entire guarded window. The existing
`browser_revoked` acknowledgment is unchanged; a status frame immediately
after reports null ticket and session fields. No HTTP route, activity
keepalive, idle renewal or browser-side control is added.

## Optional embedded read harness (rebuild-031)

Pass `--reader` at the end of the same explicit native command to opt in.
The private ready frame reports `guarded-browser-reader-read-only` and
`reader_path: "/api/_reader/"`. After the normal private parent bootstrap,
use only this exact path at the returned origin in the isolated cookie jar.
The caller still must wire that jar securely; the executable neither opens
nor installs a browser.

Only these three embedded, authored assets are served, after cookie/session
checks: `/api/_reader/`, `/api/_reader/reader.js`, and
`/api/_reader/reader.css`. They have exact MIME types, no cache, no
filesystem-derived paths, query aliases, vendor scripts, asset host or
third-party dependencies. The HTML permits only same-origin script/style
and read connections, with no inline script, images, objects, frames,
forms, cross-origin access or external subresources. It never implements
or receives the native ticket/password handoff.

The harness reads sources/settings and shows up to 40 archived conversations
and 40 text messages for a selected conversation. It renders literal text,
not HTML, downloads or external links. It keeps no browser storage,
polling or application caches. On the next unsuccessful authorized read it
clears displayed data; the parent should also clear or close the pane when
locking. It cannot erase information already received/rendered.

This is not the old WebUI. The original pinned frontend needs Lucide and
calls unsupported operations, account-safety HTTP POST and SSE routes
(including during ordinary behavior). Those pinned assets were inspected,
not copied, modified, or served. Only HTTP responses and a synthetic
Node DOM/GET protocol test were exercised. No browser/desktop UI,
cookie-jar internals, visual layout or Chromium sandbox were tested.
