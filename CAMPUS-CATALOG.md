# Offline Campus catalog

These GET routes are available on an ordinary native-live archive. They use the
existing Campus reader/editor bearer grant with `campus.read` and exactly one
configured `campus_profile`; there is no global mode or account substitution.
Omit `profile` to use the grant scope. Passing a different profile returns
404 without revealing whether it exists. Empty authorized archives return empty
counts. No routes write metadata or require a private student profile.

- `/api/v1/campus/profile`: the local profile slug, `detached-local` mode, counts
  of imported articles and observed source IDs, and an explicit
  `preferences_supported: false`. This is an archive scope, not a campus,
  faculty, degree, year, keyword list, or student identifier.
- `/api/v1/campus/sources`: observed source IDs from articles in the granted
  profile, counts, and the latest available publication date per source.
  A source exists in this response only if its ID was in a committed article.
  This is not an authoritative registry, provider setup, registration, or
  verification status. Free-form names, URLs, paths and identifiers from
  provider accounts are omitted. `limit` defaults to 25 (1–50) and `offset` to 0
  (0–4096), ordered by ASCII source ID; `total` is the count before paging.
- `/api/v1/campus/updates`: mutually exclusive counts of rows with imported text,
  with summary only, or with neither, and the latest publication date when
  available. Blank content/summary made solely of ASCII spaces or tabs/newlines
  is treated as empty. Imported text does not establish full article text,
  recency of collection, or anything about what a provider has published.
  `auto_updates_enabled`, `running` and `fetch_supported` are always false.

Source contract: pinned legacy `J-X-C-Z/local-message-hub` commit
`c7052745fab3fa1880c6e3a496f40609185668b9`, `hub/server.py` routes
`/api/campus/profile`, `/api/campus/sources`, `/api/campus/updates` and their
POST handlers, and `hub/campus.py` methods `profile`, `sources` and
`update_settings`. The Python routes return student preferences, scheduled
remote refresh settings and registered source metadata. These deliberately
narrow v1 reads are complementary, not aliases or drop-in compatibility;
the POST routes, source registration/toggling, fetching, models and personal
preference storage remain unsupported. Existing manual import and bookmarks
are unchanged.

## Detached text completion (operator only)

The old POST `/api/campus/updates` changes scheduled remote collection
settings; local catalog status still does not offer that behavior. When an
already imported article has a missing body, the archive owner can instead
explicitly fill it from authorized, already available detached plain text:

```json
{"version":1,"profile":"public-campus","article_id":"<exact 64-character article id>","content":"Public article text"}
```

Run `preview-campus-content --source <file> --directory <native> --profile
<profile>` and inspect the article ID, text byte count and whether the content
is unchanged. After reviewing the exact file, use `import-campus-content`
with the same arguments and `--confirm <preview-digest>`. The digest binds
the archive, profile and exact input bytes. A pending update is rechecked
under the SQLite writer transaction, including the existing source admission
policy. Only a blank body (empty or ASCII space, tab, CR, or LF) can be filled
(16 KiB maximum); identical text is
idempotent, different existing text conflicts, and missing/foreign articles
cannot be created. The change updates the searchable text atomically, leaving
metadata and original import receipts intact. No network, scheduling, source
fetch, HTTP write route or legacy compatibility claim.
