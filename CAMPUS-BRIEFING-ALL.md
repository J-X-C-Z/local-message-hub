# Explicit local Campus briefing: all article cards

`GET /api/campus/briefing?section=all` on the ordinary native-live listener
requires an exact Campus profile grant with `campus.read`. It provides a
read-only projection deliberately distinct from the raw item list: dated
publication indicators, dates safe to offer a day picker, material counts,
a bounded text excerpt, the article read/bookmark flags and shared revision, and one actual local
report descriptor per card. An archived source is omitted using existing
per-profile source preferences. Counts and available dates come from the
same visible archive snapshot, ignoring the page's text search; `total`
is the visible count matching search before pagination.

- ISO date-only entries are the source's civil publication dates; the
  current day and `generated_at` use fixed UTC+08. All includes future and
  unknown-date articles and flags them without guessing precise times.
  `available_dates` contains nonfuture known dates, descending.
- As in the pinned Python all section, a provided valid `date` (or blank)
  is parse-and-echo metadata, not a filter. Use the existing explicit
  daily section for filtering by date. Daily is unchanged.
- `q` searches literal title plus source name and the up-to-240-character
  summary/body excerpt, with SQLite ASCII case folding rather than Python's
  Unicode case folding and whitespace normalization. Topic filters are
  unsupported; blank/omitted `topic` is allowed for stock request shape.
- Offset is 0–4096 and limit 1–50 (default 50). Sort is stored date then
  article ID, both descending, with unknown-date items last. No title-only
  materials are excluded, and briefing does not affect scoring.
- `material_status` reflects imported field presence (`imported_body`,
  `summary_only`, `title_only`), not proven provider completeness. Every card
  represents one article and only its own report; there is no inferred
  grouping, duplicate matching, group read-state propagation, heat, selected fallback,
  topic classifier, source-rank claim, model or refresh. No student profile,
  network calls or writes are performed.

Pinned Python `hub/campus_briefing.py::build_briefing` groups reports and
supplies more pipeline/profile/relevance fields; `webui/app.js` calls the
explicit all section with search, limit and offset. This is a scoped article
card subset, not unchanged stock WebUI or complete legacy JSON parity.
