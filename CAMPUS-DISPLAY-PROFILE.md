# Campus local display profile

This is an opt-in presentation profile for the pinned legacy Campus profile
capability, not a port of its student fields. It stores only a revision,
optional category slug already observed in that exact profile's imported
catalog, and page size (10, 25, or 50). It accepts no names, affiliations,
keywords, identifiers for a student, or free-form preferences. No provider
permissions, remote requests, scoring behavior, or scheduled work are changed.

- `GET /api/v1/campus/profile/display` reads the exact grant's display state
  with a default of revision 0, no category and 25 rows. It explicitly reports
  no fetch, model, or scheduled updates support for this display feature.
- `POST /api/v1/campus/profile/display` requires a CampusEditor with the
  separate `campus.profile.display.write` permission. Example:
  `{"expected_revision":0,"default_category":"teaching","page_size":10}`.
  To clear the category pass `null` or omit it. Page size and expected revision
  are required. A stale revision conflicts, and identical current settings
  are a no-op. A category known only in another profile does not resolve.
- `GET /api/v1/campus/profile/feed` reads the resulting display defaults and
  article page in one transaction. It applies the saved category and size to
  the existing visible-items semantics, omitting archived sources. Optional
  `offset` is bounded from 0 to 4,096. A saved category with no visible articles
  simply yields an empty page; it never falls back to other categories or
  exposes archived rows.

All reads need `campus.read` on the same exact native-live Campus profile.
An optional `profile` selector can only match the grant; no query enumerates
profiles. Like existing source flags, this is visibility in one presentation
route, not access control, and it does not change existing endpoints.
`/api/v1/campus/profile` remains a catalog summary; this separately advertised
feature does not emulate the former legacy profile write or source/provider
registration. Existing source preference and local scoring routes remain the
owners of their own state. Source-only, not built or tested.
