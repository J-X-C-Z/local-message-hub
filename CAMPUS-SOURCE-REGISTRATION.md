# Campus detached import source registration

The pinned old Campus source write configured pre-existing public catalog
entries before any articles were observed. Existing native source preferences
only arrange already-observed articles; the registry fills the different
**future detached import** gap.

- `GET /api/v1/campus/source-registrations` is exact-profile Campus read.
  It shows the public-feed IDs and manual public WeChat article lane from
  the pinned `c7052745fab3fa1880c6e3a496f40609185668b9` catalog, with display
  label/category only, no URL, schedule, fetch status or inferred data.
- `POST /api/v1/campus/source-registrations/{id}` requires CampusEditor and
  `campus.sources.registration.write`. Input is
  `{"expected_revision":0,"enabled":false}` (optional exact `profile`).
  It can be used before any Campus import. Only catalog IDs are writable.
  A stale revision conflicts; identical state at that revision is a no-op.
- Untouched entries return revision 0 and `enabled:null`, retaining existing
  detached import behavior. Explicit false blocks new preview/commit for
  a file containing that ID in that profile; explicit true is still subject
  to ordinary file, scope, content and confirmation checks. Unknown source
  IDs in existing detached workflows are unchanged; this is not a global
  allowlist and catalog labels are not proof of origin.
- Commit checks admission in its write transaction. An already committed
  identical receipt can be recovered even after disabling without adding
  rows. Existing articles, imports under other profiles, display visibility,
  score settings, and list ordering are not changed.

Directory and sign-in portals/local account sources are not registered.
These endpoints do not implement stock legacy route aliases, schedule a fetch,
contact any external URL or execute a model; rows honestly say
`fetch_supported:false`. Older binaries without this feature must not be
used when you intend to enforce registration during import. Source-only,
not compiled or tested.
