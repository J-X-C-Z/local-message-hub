# Revisioned local Campus article read/bookmark state

The ordinary native-live Hub supports
`GET /api/v1/campus/items/{id}/state` for an exact Campus profile reader
with `campus.read`. Use an existing imported article's 64-hex ID. The response
is `{bookmarked,read,revision}`, defaulting to both flags false and revision 0.

An exact Campus editor with the existing `campus.bookmarks.write` grant can
POST to that route or the existing `/api/campus/items/{id}/state` alias.
The same grant controls this adjunct of article bookmark/state, not
model/remote/group permissions. Supply `expected_revision` and one or both
Boolean flags, optionally with the exact granted `profile`:

```json
{"expected_revision":1,"read":true,"bookmarked":false}
```

Omitted flags remain unchanged; explicit null or an empty flag selection is
not accepted. The write is one atomic compare-and-set; a stale revision
conflicts even if values would happen to match. An identical current update
is a no-op. The existing bookmark revision is shared: an article read change
invalidates stale bookmark-only revisions too. The native bookmark endpoint,
its payload/response, and the old alias's bookmark-only response are unchanged;
read writes do not change the bookmark flag or add a bookmark audit entry
unless the bookmark actually changes. A read-only change may cause a gap
between bookmark audit revisions.

The explicit all briefing article cards now project actual `read` and
`state_revision` in their existing archive snapshot. Native daily and manually
created event read/bookmark state do not change. No implicit marking, group
propagation, fetch, profile personalization, or synchronization occurs.
The pinned Python `set_item_state` supports both flags but its old unversioned
writer and stock entire-article response are not reproduced; never send an
unversioned write on the alias. This is source-only.
