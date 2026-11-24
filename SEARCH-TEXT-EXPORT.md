# Exact-account search text export

`GET /api/v1/messages/export/search?q=<literal>&format=json|csv` exports
the matches for one explicit text search. This is a separate native route;
it does not change conversation exports or mount the old `/api/export`.

- Only the ordinary Snapshot and NativeLive modes are eligible. The current
  Desktop grant must authorize one exact account and include both
  `messages.read` and `messages.export`. The route never accepts platform,
  account, conversation or pagination overrides. Capability discovery names
  the route `resources.search_text_export`.
- `q` is required, at most 512 UTF-8 bytes, and cannot be blank or contain
  control characters. Leading/trailing noncontrol spaces remain part of
  the literal; `%`, `_`, backslashes and quotes are search data, not patterns
  or FTS operators. Matching uses the native text search's LIKE/optional
  quoted FTS selection, with exact binary account scoping.
- JSON is default. CSV uses the existing escaped, spreadsheet-safe text
  serializer. Each row retains the actual stored conversation and message IDs;
  no snippets, attachments or media references are retrieved. The export
  sorts newest first, with binary message and conversation IDs breaking ties.
- Selection and rendering read one database transaction. The ordinary archive
  verification wraps the operation. A valid search with no matches returns
  `[]` or the CSV header. Beyond 1,000 rows, 64 KiB in one returned text cell,
  or 4 MiB serialized output, the whole request fails rather than returning
  a partial attachment. There are no server-side file writes, cloud calls,
  SQL output, XLSX output or global export.
