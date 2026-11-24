# Local Campus source visibility

A Campus profile owner can arrange their **offline visible-items view** by
marking an already imported source `visible` or `archived`. The setting
describes only local presentation. It neither connects/enables/disables a
provider nor controls any refresh, inference, scoring, fetch, delivery or
import. No remote credentials, URLs, models or arbitrary source metadata may
be supplied.

The ordinary native-live API uses a Campus grant's exact profile.
`campus.read` permits reads. A CampusEditor with explicit
`campus.sources.preferences.write` may change that profile's preferences.
Any optional `profile` selector must match the grant exactly. The following
routes are native-only:

- `GET /api/v1/campus/source-preferences` lists observed sources and current
  preference records in binary source-ID order. It accepts `limit`
  (1–50, default 25), `offset` (0–4096) and optional `profile`; returns
  `{items,total}`. A source appears only if an operator-approved detached
  import has indexed at least one article for it in the exact profile.
- `GET /api/v1/campus/source-preferences/{id}` returns the exact source's
  `source_id`, `revision` and `visibility`. Unset is revision 0
  and `visible`.
- `POST /api/v1/campus/source-preferences/{id}` takes
  `{"expected_revision":0,"visibility":"archived"}` plus optional
  `profile`. It returns the same record shape; stale revisions conflict
  and an unchanged state at the correct revision is a no-op. Unknown and
  cross-profile sources do not resolve and cannot be preregistered here.
- `GET /api/v1/campus/visible-items` accepts the normal native article-list
  filters but excludes articles belonging to archived sources in that
  profile, in the same transaction as the count and page. Unset sources and
  explicitly restored sources remain visible.

Archiving is **not access revocation or deletion**. The existing article
list/detail, source catalog, bookmarks, digests, events, state and detached
import routes work as before. Data is still accessible to already-authorized
clients via those unfiltered routes. No stock `/api/campus/sources/{id}`
write alias: upstream `enabled` affects provider refresh and these
preferences do not.
