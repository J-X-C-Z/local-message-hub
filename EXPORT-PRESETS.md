# Saved text export presets

Saved presets live in the ordinary native archive, isolated by archive, Desktop
grant ID, platform and exact account. The local operator must opt into
`exports.presets.manage` and hold existing `messages.read` and `messages.export`
permissions. No snapshot, guarded, browser, source or remote route is added.

Under `/api/v1/exports/presets`:

- POST `/save` with `expected_revision:0` and a `spec`. The spec has `label`,
  `format` (`json`, `csv`, `txt`, or `md`), `conversation_ids` (ordered,
  1–20 distinct exact IDs), and optional inclusive Unix-second `start` and
  `end`. Use a short display label made of letters/digits, spaces, hyphens or
  underscores. The server verifies all IDs within the grant before saving.
- GET the base path for summaries, or GET `/details?preset_id=...` for the
  explicitly requested selection details from your own grant. Details contains
  only the saved spec, not conversation titles or text. The save result and
  the list omit conversation identifiers and date ranges.
- To replace the whole spec, pass its returned `preset_id` and current
  `expected_revision` to `/save`. POST `/remove` takes the same ID/revision
  and permanently deletes the saved selection. Stale revisions return 409.
  If a create response is lost, check the list before retrying, as create
  generates a new ID rather than using a label as a key.
- POST `/plan` with `preset_id` and `expected_revision` invokes the existing
  bounded batch manifest calculation immediately. Results contain ordinal,
  capped row count and over-limit indicators for each selection, format, and
  the fixed existing download route; they omit titles, IDs, paths and text.
  To obtain content, the desktop reads the saved details and explicitly invokes
  `/api/v1/messages/export` once per selected conversation using the matching
  format/time bounds. The estimate is not tied to a persistent snapshot, is not a
  byte estimate, and never overrides the existing export row/output ceilings.

The store allows 16 presets per grant/account and 1,024 overall. There are no
schedules, output locations, automatic exports, merged files, background jobs,
or provider calls. This replaces none of Python's auto-export scheduler and
does not persist message content.
