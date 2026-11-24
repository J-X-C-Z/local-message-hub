# Native desktop loopback client

`local_message_hub_rs::desktop_client::Client` is the synchronous, explicit
integration layer for connections accepted by the [descriptor SDK](DESKTOP-CONNECTION-SDK.md).
Use it on a desktop worker, not the UI event loop. Construction does not
launch anything. The `connect` function makes two requests by design:
unauthenticated health, then the exact scoped grant's native manifest.
Both target only the validated numeric `127.0.0.1` port copied from the native
supervisor's private readiness channel. The manifest must match backend,
API v1, plugin protocol v1, archive mode, Desktop grant ID, and exact platform
and account. Native-live/snapshot are chat reads, including archive search and context; the imported-media host
supports exact-message local media listing, reference verification, and explicit bounded download through this client.

The caller should continue to own the supervisor/child generation and pipe
endpoints. Supply an immediate predicate bound to that exact generation and
process handles; do not infer identity from an open port or PID existence.
Readiness, liveness and the manifest are consistency checks, not TLS or OS-level
attestation. This is only for a trusted local native environment, not protection
against hostile local code that can impersonate the listener. The callback is
checked before connecting, before sending the bearer, and before accepting a
successful response. Discard the object on process/pipe exit. To reconnect,
obtain a fresh validated readiness frame from a trusted supervisor and call
`reconnect` with its new liveness predicate. Old socket addresses are not retried.

Illustrative integration inside an already-authorized desktop worker:

```rust,ignore
let ready = launch.accept_ready(&owner.readiness_line()?)?;
let generation = owner.current_generation();
let mut hub = desktop_client::Client::connect(
    ready, &grant, grant_id, exact_account,
    |pid| owner.still_owns(generation, pid),
)?;
let page = hub.conversations(20, 0)?;
let messages = hub.messages(selected_conversation_id, 20, None)?;
```

Here `owner` is the desktop's own process and generation bookkeeping, not a
helper included in this library. Never route grants through argv, environment,
logs, or frontend storage. Returned archive rows are private and may still
contain untrusted text or link fields; never auto-open, execute, or fetch those
values. The client does not display or log row content or remote error bodies.

## Find and open a conversation

`search_conversations(title, limit, offset)` performs the sidebar's literal
title search inside the connection's account. The match is applied before
the count and pagination, not to the rows already on a page. The ordinary
unfiltered `conversations(limit, offset)` flow is unchanged.

For a contact already selected in the same account, try
`lookup_conversation(contact.id())`. The exact ID is checked before
pagination and the client verifies the single result against both the scope
and requested ID. No exact match is `None`, not a signal to guess from
duplicate, mutable names. The client will not send the selector unless the
managed native-live or snapshot manifest advertises `conversation_lookup` on
the fixed conversation route. A missing capability is `Unsupported`. The
existing conversation row projection is unchanged. No ID from another
account, provider lookup, browser support, or new permission is implied.
Archive flags, notes and tags remain separate metadata; the pinned sidebar
does not filter by them.

## Find and inspect a message

The typed client can search literal text within the grant's account, open a
window around the selected hit, and, in a WeChat archive, open the target of
a quote in the same conversation. These are existing native API reads, not
provider calls or a new grant. They work with the snapshot mode offered by
the Windows, macOS and Linux supervisors in source, and with ordinary
native-live on Linux and, in source only, macOS and Windows. They are
unavailable on imported media, legacy or guarded-browser listeners through
this client.

```rust,ignore
let page = hub.search_messages(desktop_client::Search {
    text: "项目交接",
    conversation_id: None,
    cursor: None,
    limit: 20,
})?;
if let Some(hit) = page.items.first() {
    let context = hub.message_context(
        &hit.message.conversation_id,
        &hit.message.id,
        desktop_client::Window { before: 5, after: 5 },
    )?;
    let center = &context.items[context.target_index];
    // Display center.text as untrusted text in the native desktop.
}
```

- Reuse search terms and the same optional conversation when passing the
  opaque `next_cursor` to a later call. Do not decode, combine or share it
  across archives. Literal text is not interpreted as a URL or command.
- Search hit text is only the server's excerpt (at most 1,200 characters);
  context returns the full indexed text where available. Typed rows drop
  raw provider IDs, opaque extra fields, remote/media URLs, and attachments.
- To open a quoted item, pass the referencing archived WeChat message ID to
  `quoted_message_context(conversation_id, reference_message_id, window)`.
  Wrong scope, missing or ambiguous targets do not fall back to another
  conversation or a platform ID. The client validates the returned
  `target_message_id` against the center row and rejects a self-quote.
- The authenticated manifest must announce each exact native route before
  use. Its strings are compared to fixed constants, never followed as
  URLs. Every typed row and context center is checked against the original
  grant and requested target, and a malformed result invalidates the client.

There is no DNS, proxy, redirect following, automatic retry, persistent socket,
compression, chunked decoding, arbitrary URL/query or raw transport API.
Archive navigation uses typed GETs; the separate notification and import
clients use fixed-route POSTs for the operations below. Each operation
uses a new socket, an overall
3-second I/O deadline, capped headers, an exact bounded JSON Content-Length,
and per-route response limits. A miss, permission denial or rate limit returns
only the numeric local HTTP status. Unexpected framing/transport invalidates
the session. Query scope comes from the validated exact account and message rows
are independently checked to match it. No network calls, builds, or execution
are part of this source checkpoint.

## Native-live notification routing

Use `desktop_client::notifications::Client` on a trusted native worker
only. This is a separate typed, fixed-route client over an accepted managed
**native-live** connection, not snapshot, imported media, legacy or browser.
Which OS can launch that mode is determined by the native SDK/supervisor
matrix, not this client. The caller must already have an independently
provisioned exact-account Desktop grant and provide its secret as a
`zeroize::Zeroizing<String>`. This workflow neither creates/changes grants
nor requires message-body permissions. Never send that secret through an
argument, environment, UI, redirect or proxy.

The same exact-process predicate, canonical numeric loopback address,
health, authenticated grant/account/mode manifest, and bounded JSON
transport still apply. The client has no arbitrary POST method; it enables
only the exact native paths advertised in `local_notifications`, matched
as literals, with `delivery:false`. Preferences GET requires
`notifications.read`; changes require `notifications.write` and
`can_write`; pull and ACK additionally require both canonical advertised
paths. No old settings/notification fallback is attempted.

1. `preferences()` returns a session-bound revision and read-only current
   settings. `set_filters(&settings,...)` changes the mode/style and
   conversation lists without changing local enablement. To opt in or
   out of local routing, call `set_enabled(&settings,bool)` explicitly.
   Both writes use compare-and-set. Duplicate/control-containing or
   over-limit conversation IDs are rejected. On `RevisionConflict`,
   reload before making another deliberate change.
2. `pull(limit)` is one explicit request (1–50). It distinguishes
   `Disabled`, `CaughtUp`, and `Pending`. A **pending page can be empty**
   because the server scanned entries excluded by filters. Check
   `has_more_after_ack()`, not item count. The existing server begins
   the first enabled pull at the current head. Matches contain only
   constrained conversation IDs and style. The signed receipt is opaque,
   zeroized, bound to the current client, and not exposed to callers.
3. After handling or deliberately skipping that page, explicitly pass it to
   `acknowledge`. It returns either `Advanced` or
   `AlreadyAcknowledged` (a server-confirmed replay at the immediate
   end). A rejected or stale receipt requires a fresh pull; it is not
   a delivery or display failure. `DrainConflict` and
   `DrainUnavailable` report a pull refusal with no batch, and do not
   assert the server's initialization state. There is no automatic drain,
   read, retry, ACK, notification display, activation or mark-as-read.
4. An ambiguous mutating response is `OutcomeUnknown`; reconcile from
   current preferences or pull state, rather than replaying automatically.
   Reconnect only with accepted fresh supervisor readiness; use a new
   policy revision / pending receipt. A legitimate re-pull can deliver
   the same metadata again; processing remains the caller's responsibility.

No UI, OS delivery, network/device execution, build or tests are part of
this source checkpoint.


## Inspect a detached stock JSON import without staging

On an accepted managed ordinary **native-live** connection, use
`desktop_client::imports::Client` with an already provisioned exact-account
Desktop grant containing `imports.local.manage`. The client cannot open a
file or request/expand a grant. The existing supervisor matrix limits this
to Linux and the macOS source path; this does not add a Windows native-live
launcher or browser support.

If `supports_stock_json_preflight()` is true, pass explicitly selected
detached bytes to `preflight_stock_json(&bytes)`. The authenticated manifest
must advertise the exact literal `local_import_jobs.stock_json_preflight`
route; no route fallback, retry or redirect is used. The client accepts at
most 512 KiB and sends the bytes as `application/octet-stream` with the
scope derived from the grant. The response is capped at 32 KiB and
validates counts, strictly ordered one-based issue rows, up to five bounded
samples and the exact account. Row issues distinguish invalid text records
from duplicate IDs. Render sample text as untrusted private content; do not
log it. If the listener declines the request, the read-only method returns
a local HTTP status rather than an uncertain staged-job outcome.

Preflight reads only the supplied records. It neither stages nor imports
messages, and its typed report cannot authorize a commit. Call the separate
`preview(request_id, bytes)` only after deciding to stage; it has its own
preview/approval and recovery protocol. In particular, a positive report
is not a guarantee that the entire source is admissible as one staged job:
inspect invalid counts and independently review any later job preview.

## Read and save conversation organization

`desktop_client::metadata::Client` uses an accepted ordinary managed
**native-live** connection and a previously provisioned exact-account
Desktop grant with `conversations.metadata.write`. It checks the fixed
manifest resource `resources.conversation_metadata` and uses only
`/api/v1/metadata/conversation`. Neither the SDK nor launcher adds the
permission. Snapshot, guarded, browser and imported-media connections
cannot use this client. There is no Windows native-live expansion.

1. Request `conversation(id)` for one selected local conversation ID. The
   result holds note, tags, archive flag and the actual server revision.
   The response does not echo identity; the SDK ties the returned state to
   the exact scoped request and current managed session. Treat note and
   tags as private untrusted text.
2. After an explicit edit, submit `save(&state, patch)`. Omitted fields
   are untouched, an empty note or tag list clears it, and supplied tags
   use the server's trimming and deduplication rules. The bounded JSON
   body contains the same target and the observed expected revision.
   `Changed` advances exactly one revision. `Unchanged` is distinct: the
   server can report that the desired patch was already in place even
   when another edit advanced the current revision.
3. On `Conflict`, read current metadata and ask the caller to review
   before another save. A malformed or interrupted response can return
   `OutcomeUnknown`; reconcile with an explicit read, after accepting new
   readiness if the session expired or was invalidated. Never automatically
   change a revision or replay a write. A reconnect invalidates prior
   state handles for saves.

Rollback: revert the SDK source change. The server route, permissions,
archive schema, revisions, and any separately authorized edits are not
changed by this rollback. This source checkpoint was not compiled or tested.

## ImportedMedia local attachment workflow (managed Linux)

For a managed `ImportedMedia` connection with the existing exact scoped
Desktop grant, use `media_list(conversation_id, message_id)` to obtain candidate
attachment IDs. Pass the same message and one ID to
`media_reference(conversation_id, message_id, attachment_id)`; the reference
has a `size()` accessor, not a URL. An explicit `download_media(reference)`
consumes it and returns a zeroizing byte buffer only after validating the
complete object against the pinned length and SHA-256 (at most 8 MiB).

A reference cannot be used by another client or across reconnects. The SDK
downloads only via its compiled managed-loopback route and the bound bearer.
It does not follow redirects, issue range requests, retry, use a response
filename, store a file, open another application, or fetch from a provider.
If the caller wants to try again after a denial, it must obtain a fresh
reference; liveness, malformed responses, or digest failures invalidate the
current client in line with the existing private transport.
Native Live, Snapshot, guarded and browser modes are not expanded by this API.

### Explicit deferred filtered exports (managed ImportedMedia)

With Linux's managed `ImportedMedia` listener and both existing
`imported_media` and `export_text` grant options, the typed SDK can queue
one verified local filtered export on an exact WeChat or QQ conversation.
It checks the fixed create/status/cancel/download routes and the deferred
flag in the authenticated manifest. Filter values use the native scoped
reader's literal keyword, message-type and inclusive date semantics.
Missing verified local media is a failure unless the caller explicitly
selects `allow_text_only`.

1. Call `create_deferred_filtered_export(exports::Filters { ... })`
   to request a job and get an opaque handle after validating the queued reply.
2. Call `filtered_export_status(&mut job)` when the caller needs an update.
   The result is a typed stage (queued, running, ready, failed or cancelled)
   and an expiry hint, not a row percentage.
3. After ready, call `download_filtered_export(&mut job)` for the complete
   ZIP in a zeroizing buffer. The SDK consumes the ready pin, checks the ZIP
   MIME type and exact framed length (maximum 4 MiB + 64 KiB), and compares
   the full SHA-256 to the status record before returning any bytes.
4. Alternatively call `cancel_filtered_export(&mut job)` to request
   cancellation. It invalidates any previously observed ready pin. As on
   the server, cancellation cannot recall bytes already transferred.

There is no polling loop, listing or attaching to unrelated jobs, use of
returned URLs, range, extraction, file write, provider, or automatic retry.
The handle is valid only on the same current authenticated SDK client.
A lost or unverifiable create reply yields `CreateOutcomeUnknown`: do not
reissue creation or infer a job ID. The existing ephemeral server reservation
expires without client action. A lost cancellation acknowledgement is
reported separately as `CancelOutcomeUnknown`; inspect the same job where
still possible, not another job. The existing standalone attachment client,
JSON calls, server routes, and grant schema are unchanged.

## Select a contact and edit its local identity label

`desktop_client::contacts::Client` connects to the existing managed
ordinary **native-live** backend with a separately provisioned exact-account
Desktop grant. Listing requires `contacts.read` and the exact advertised
`/api/v1/contacts` resource. A list-only grant cannot edit. Inspecting the
selected identity and editing it also require `contacts.identity.write`
and the exact advertised `/api/v1/metadata/contact` resource. No permission,
launcher or server route changes. No guarded, browser, snapshot or imported
media access through this client.

1. Use `list(Query { search, kind, limit, offset })` for one bounded page.
   It checks the account of every result before exposing only the local ID,
   name and kind. Search uses the native endpoint's literal matching rules.
   Names and kinds are private, untrusted text, not URLs or commands.
2. Select one returned contact from that session and call `identity(&contact)`.
   It returns the server's observed revision and local label (or explicit
   unlinked state). The server's identity response does not echo its target;
   the client binds the state to the exact scoped request and session.
3. After deliberate review, `link(&state, label)` edits only that contact.
   `unlink(&state)` explicitly submits null. Neither action merges people,
   modifies another contact/account, infers identities, or calls a provider.
   The existing server trims labels and checks the expected revision for a
   change. `Changed` advances exactly one revision. `Unchanged` returns the
   current revision, which may already reflect the same requested state.
4. On `Conflict` or `OutcomeUnknown`, explicitly re-read and review the
   same contact before deciding on another edit. No automatic reload,
   overwrite, unlink or retry. Reconnect requires new trusted readiness
   and invalidates old contact selections and revision objects.

Rollback: revert the SDK source patch. The server API, audit records and
any separately authorized earlier edits stay unchanged. No compilation,
tests or actual contact mutations were performed for this source slice.

## Download one filtered conversation as text

On ordinary managed **snapshot** or **native-live** connections, the existing
archive client checks the exact `filtered_message_export` manifest resource
and the pre-existing `messages.read` / `messages.export` permissions.
The archive client's normal conversation-read admission also still applies.
`supports_filtered_text_export()` reports negotiated availability; it does
not generate a grant or claim the server is still running. Snapshot and
native-live availability still follow the existing platform matrix.

Call `download_filtered_text_export(TextFilters { ... })` explicitly for
one selected conversation and one `TextFormat`: JSON, CSV, TXT, Markdown,
HTML, ChatLab JSON or ChatLab JSONL. Configure text keyword, exact message
type and optional inclusive time bounds. Date-only inputs mean UTC+08
calendar days and use the backend's existing date parser; the SDK never
uses the machine timezone. ChatLab supports only text/system rows. The
request cannot omit the conversation, select a second account, compose
grouped contact/member exports, schedule jobs or include attachments.

The fixed authenticated GET expects HTTP 200, the selected serializer's
MIME and fixed attachment header, one bounded Content-Length, and EOF
immediately after the full uncompressed UTF-8 body. Only TXT can be empty.
At most 4 MiB are returned as private zeroizing bytes; a rejected response
never hands back a partial file. The transport neither exposes nor uses
the response filename, and doesn't open, render, save, retry, redirect,
follow links, or fall back to other export routes. Treat all returned
bytes as untrusted private content, particularly HTML, Markdown and CSV.
The separate Linux imported-media ZIP client is unchanged.

Rollback: revert the SDK source patch; the 110 server API and archives
are unaffected. This source change was not compiled or tested.
