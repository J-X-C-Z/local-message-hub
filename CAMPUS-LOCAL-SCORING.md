# Campus local topic ranking

The native-live Campus scoring routes replace only the local,
deterministic part of ranking. Legacy `hub/campus.py` at
`c7052745fab3fa1880c6e3a496f40609185668b9` has relevance checks, an opt-in
scoring switch and status, but the scoring worker calls remote models and
consumes a student profile. Those behavior and endpoints, including the
legacy model-run endpoints, are **not** reproduced here. No fetch, search
engine, user/student profile, free-form interest text, scorer process,
third-party model, cache, scheduler or background work is used.

Ordinary native-live readers with the exact `campus_profile` grant and
`campus.read` can access:

- `GET /api/v1/campus/scoring`: policy, current revision and explicit category
  preferences; counts of visible scored, unprioritized, insufficient-summary,
  or disabled articles. These counters partition the visible set. `running`
  and `remote_models` are always false.
- `GET /api/v1/campus/scored-items`: the current policy's page of ranked
  article IDs and bounded public summaries of identity (title, category,
  source and date), score, and its fixed reason code. The row does not reveal
  content or make model/inferred relevance claims. `limit` defaults to 25
  (1–50), `offset` to 0 (0–4096); an optional exact `source_id` slug filters the
  same visible set. Use the existing item route to read an article.

The settings are disabled and at revision 0 on an untouched profile. An
explicit Campus editor grant with `campus.scoring.write` can
`POST /api/v1/campus/scoring` with a JSON object (optionally also
`"profile":"exact-profile"`):

```json
{"expected_revision":0,"enabled":true,"priorities":[{"category":"teaching","weight":4}]}
```

There may be at most 16 distinct categories, each a slug already observed in
the exact imported profile (unique canonical category keys), and weights
range 1–5. Enabling requires at least one priority. Configure with the current
revision; a stale update returns conflict, and an identical current update
keeps its revision. Categories use canonical order; a reorder is not a
change. The source visibility preferences affect only view selection and
must not be treated as access controls. Archived sources are omitted from
status counts and ranked rows, without fetching or deleting articles.

Policy `local-topic-weight-v2` scores only a visible article with an explicit
category weight and a nonblank imported summary that is not just its title.
It strips leading/trailing ASCII spaces, tabs and line endings and compares
using SQLite's ASCII case-insensitive collation; punctuation, interior
whitespace and non-ASCII case are not normalized. Body-only articles are
still not eligible. An eligible-category title-only or title-echo article
counts as insufficient summary, but remains available in updates and the
dated daily view under the existing visibility/category rules. The integer
score equals that category's weight. Ties sort by publication date descending, then exact article ID
ascending. Unprioritized, insufficient and disabled rows are not returned
by the scored-items route. There is no learned output and no claim about an
article's quality, correctness or relevance to an individual. Each read
uses one coherent local archive snapshot. No real provider was used; this
change is source-only and untested.

## Explicit local materialization

`POST /api/v1/campus/scoring/run` uses the same exact Campus editor grant
and requires `expected_settings_revision` (and optionally exact `profile`).
Scoring must already be enabled at that revision. This operation synchronously
records the actual first 50 ranked article IDs/scores and the total eligible
count, without duplicating titles, content, or student data. One latest local
result is stored per profile. Identical results at the same revision preserve
the original materialization time. Nothing is queued or run in the background.

`GET /api/v1/campus/scoring/run` requires `campus.read` and compares that
stored bounded result with the current deterministic result in one archive
snapshot. It returns `ready`, `not_run`, `stale`, or `disabled`; only `ready`
exposes the materialized result. If settings change or an import/visibility
edit affects the ranked IDs, scores, or total, the old result is not served.
Materialization is bound to the policy revision: a stored v1 result becomes
`stale` under v2 even if the output would match, and can be rematerialized
with the existing settings revision. No scoring settings or archive rows
are migrated or rewritten on read.
This is not a cache acceleration or a claim of remote model parity.

The earlier native source preference write already changes local visibility
for an observed source; it is not duplicated as a legacy source override.
Legacy auto-refresh and model toggling/run semantics remain unsupported.
