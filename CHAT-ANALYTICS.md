# Desktop chat analytics

Behavioral references (pinned upstream `c7052745`): `/api/analytics/private`,
`/api/analytics/groups` and `/api/analytics/self-sent-daily`.

- The versioned Rust GET endpoints are for an explicitly granted Desktop
  plugin and one exact platform/account scope, not a cross-account browser API.
  Dates are optional inclusive `start` / `end` (YYYY-MM-DD in UTC+08).
- Private and group indexes accept optional `conversation_id`, `limit`
  (1–200, default 50) and `offset` (0–10,000). Only conversations with messages
  in range are returned. Results are ordered by binary conversation ID,
  not by activity ranking. `next_offset` is null on the last page or at the
  paging budget. To narrow broad requests, add a date or conversation filter.
- Indexes return conversation routing IDs, first/last activity and message
  counts by role. The private index additionally returns canonical media
  counts, excluding WeChat sticker raw types; it does not inspect attachment
  metadata or message extras. The group index returns the declared roster
  size when indexed, not member records or a sender-derived roster.
- Self-sent daily reports cover private conversations only; the
  display window is the last 37 calendar days with zero-filled dates, while
  the baseline is the exact total divided by the inclusive first-to-last day
  span of the filtered data. An empty range returns an empty series.
- A query matching more than 200,000 rows fails with the archive's normal
  size-limit error, never a partial aggregation. Group and private filters
  are applied first. Reads do not change archive or contacts, and do not
  read message bodies, titles, sender IDs or member rankings.
- This is a bounded Rust subset, not wire compatibility with the unversioned
  Python analytics API. No legacy routes or MCP analytics tools are implied.
