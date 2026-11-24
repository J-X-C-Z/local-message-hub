# Managed Desktop account journal replay

`local_message_hub_rs::desktop_client::journal::Client` provides bounded
synchronous JSON checkpoint and replay for the managed **ordinary native-live**
backend. Use it on an existing trusted Desktop worker with an independently
provisioned exact-account Desktop grant containing `events.read`. Neither
message-body permission nor grant generation is required. This code does not
mount any new server route or change the OS supervisor support matrix.

Construction uses the existing canonical numeric loopback address, health,
exact managed child generation predicate, authenticated mode/principal/account
manifest and request boundaries. It requires the two fixed advertised
`resources.event_checkpoint` and `resources.events` routes. An unsupported
mode/manifest is not a signal to try a legacy route. This is finite JSON only,
not SSE, an automatic poller, a scheduler, an OS notification path or a
provider connector. No body, sender field, attachment, provider locator,
or HTML is returned as part of journal events.

## Applying changes

For an initial view, take `checkpoint()` **before** reading the initial
archive view using that view's independently granted read permissions.
Then call `replay(checkpoint.cursor(), limit)` (1–100). If events arrived
during initial loading, refresh the affected local rows or conversations
idempotently. Each bounded page contains exact-account `created` or `revised`
metadata. Repeated message IDs are valid because a message can be revised.
A full page may have more; inspect `has_more` rather than guessing from the
page length. The caller decides whether and when to fetch another page.

Only move the local position after all changes in that page have been applied.
Keep `Page::next_cursor` in memory for the next call, and persist
`next_cursor.as_str()` separately in caller-owned private storage if needed.
Snapshot and replay may overlap; a crash between applying and saving may
replay a page. Do not deliver side effects merely because an event was seen.
An empty caught-up page does not advance the position. There is no automatic
cursor persistence or server acknowledgment.

After reconnect or restart, construct a new client, obtain a fresh checkpoint,
then use `restore_cursor(&checkpoint, last_applied)` to bind the saved
position to the new connection. It rejects malformed, foreign archive/grant,
and future positions locally. Do not make the current head your new position
if restoring fails; explicitly rebuild the initial view to prevent silently
skipping changes. A cursor is a position, not authentication. Keep it out of
logs and unrelated storage. A running-session Cursor cannot be passed to a
new client directly.

## Failure boundaries and rollback

The server's exact account journal allocates consecutive ordinals even when
multiple accounts are interleaved. The client checks every event's scope,
ordinal and selectors, the echoed position, checkpoint bounds, and page
continuation before returning the page. A protocol/scope violation invalidates
the client and supplies no new position. A server status error leaves the
position unchanged; for example an archive without account event sequence
support can return HTTP 503, not an empty journal. A transient refusal can be
retried only explicitly by the caller. The shared loopback transport does not
follow redirects or retry requests.

Source only. No builds, tests, runtime calls or provider operations were
performed. Reverting the journal module, its two manifest fields and this
document removes only the typed SDK surface; it requires no migration and
leaves the existing server, grants, storage and routes untouched.

## Observe one event on the managed stream

After obtaining or restoring a cursor, a managed NativeLive event client may
call `wait_one(&cursor)` if its exact manifest advertises the fixed native
`/api/v1/events/stream` path. Run this synchronous, three-second-max wait off
the UI thread. It opens and closes one local socket, validates the starting
checkpoint and can return only one new account-local metadata event. Treat
the returned cursor like one from `replay`: persist it only after applying
the UI update. `Idle` means no new frame was available at the deadline and
does not alter the caller's position; a heartbeat does not extend this
deadline. There is no automatic reconnect, polling loop or acknowledgement.

Incomplete frames, stream closure and a no-longer-current supervisor are
errors, not idle. Reconnect explicitly and restore the last applied cursor.
Responses must use the bounded canonical chunked framing of this managed
stream; generic archive/API response framing is unchanged. The old
incoming and notification streams are not accepted by this SDK path.
