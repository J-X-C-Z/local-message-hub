# Manual local Campus events

An event is an explicit group of 1–16 distinct articles already imported into
a **single exact Campus profile**. It is a private catalog entry, not a
calendar invitation, reminder, recommendation, or fetched provider record.
There is no automatic grouping, title matching, text classification, model,
network request, background processing or delivery.

All native routes below require `campus.read` on a Campus profile grant.
Writes require a CampusEditor with the separately consented
`campus.events.write` capability. An optional `profile` selector must match
the grant exactly. The profile cannot be selected by changing an event ID.
Routes are exposed only on the ordinary native live archive.

- `GET /api/v1/campus/events` lists summaries in binary event ID order
  (`limit` 1–50, default 25; optional `after` and `bookmarked`).
  The returned `next_after` continues the same profile and filter. Entries
  include `id`, `article_count`, `revision`, `bookmarked` and `read`.
- `POST /api/v1/campus/events` with `article_ids` creates an event and returns
  its summary and member IDs. Unknown or cross-profile article IDs reject the
  entire operation. A repeat with the same unordered set returns the existing
  event and current state, without changing it. Member sets are immutable; an
  article may appear in several explicit sets. There is a maximum of 1,024
  distinct groups per profile.
- `GET /api/v1/campus/events/{id}` reads the event summary and ordered member
  IDs. It does not project article body, URL, author or user data; the known IDs
  can be looked up through separately scoped article access.
- `POST /api/v1/campus/events/{id}/state` requires `expected_revision` and
  at least one boolean `bookmarked` or `read`. New groups begin at revision
  zero with both flags false. An unchanged state at the correct revision is a
  no-op; a stale revision returns a conflict. These flags belong only to the
  event. Article bookmark state and other events are never modified.

Pinned Python `/api/campus/events/{id}/state` uses an article ID, derives a
group, then applies state changes to its member articles. There is no legacy
alias: those semantics are intentionally not emulated by the manual grouping
and distinct event state.
