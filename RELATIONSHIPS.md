# Scoped relationship activity

References: pinned upstream `c7052745` relationship footprint, timeline and
achievements. The Rust version takes one explicit `platform`, `account_id`
and `conversation_id` belonging to a locally consented exact-account Desktop
grant. Only indexed private/direct conversations are eligible; missing,
non-private and out-of-scope conversations do not return data.

- `GET /api/v1/analytics/relationships` reads one footprint: indexed
  inbound/outbound message and canonical media totals, first/last timestamps,
  and the number of active local dates.
- `GET /api/v1/analytics/relationships/timeline` reads per-active-date counts,
  newest first. `limit` is 1–366 (default 366), `offset` is 0–10,000;
  `next_offset` stops at the end or the budget. Both endpoints take optional
  inclusive `start` / `end` calendar dates (`YYYY-MM-DD`, UTC+08).
- `GET /api/v1/analytics/relationships/achievements` takes no dates and
  returns pinned objective milestone identifiers, observed counts, thresholds
  and unlock flags over the complete eligible indexed range. Client labels
  and presentation are outside this API.
- The SQLite read is on demand and bounded to 200,000 matching rows; it fails
  instead of returning partial counts when exceeded. Canonical indexed media
  types exclude WeChat sticker raw types; the WeChat call milestone uses raw
  type 50. There is no reading attachment extras, sender names/IDs, titles or
  message bodies. No scheduled activity, writes, rankings, device actions
  or notifications are involved. Counts describe local coverage, not a
  complete history, participant identity or inferred relationship quality.
