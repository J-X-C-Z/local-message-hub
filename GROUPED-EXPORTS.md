# Exact-account export selection and observed participants

Unverified code-first snapshot. No files are created by these HTTP handlers,
and there is no background job, client script or provider connection.

- `GET /api/v1/messages/export/manifest` takes one exact desktop account
  with `messages.read` and `messages.export`; give `conversation_ids` as a
  URL-encoded JSON array of 1–20 distinct IDs. Optional bounds `start` and
  `end` are inclusive Unix seconds. A missing or wrong-scope ID fails the
  whole selection. Counts stop at 1,001 per conversation and the response
  reports at most 1,000 plus an over-limit flag. The manifest is discovery,
  not a snapshot token, an export receipt, or a promise about byte size.
  Clients explicitly download each conversation via the existing endpoint.
- `GET /api/v1/contacts/export`: exact desktop account with
  `contacts.read`, `messages.read`, `messages.export`. JSON/CSV (default)
  with only normalized ID, platform, account, name and kind. Optional
  literal name/ID `q` and exact `kind`. No aliases, avatars, linkage IDs,
  account enumeration or arbitrary extras. At most 1,000 rows and 4 MiB
  output. CSV uses the native formula-neutralizing writer.
- `GET /api/v1/conversations/participants` using `conversations.read` and
  `messages.read` on one exact desktop account; add `messages.export` to
  use `/api/v1/conversations/participants/export` for CSV/JSON. Only
  indexed group conversations; at most 25,000 message rows scanned and
  1,000 participant rows returned. Optional literal `q` matches normalized
  sender ID or last observed sender name. Sender counts and times come
  from the archive, not live membership. IDs are sorted by binary value;
  a tied last observation resolves by binary message ID. No role, owner,
  remark, account linkage or guarantee that all group members are present.

Native versioned routes are on ordinary snapshot/native-live and unlocked
guarded-native listeners. Partial guarded bearer aliases are
`/api/export/contacts`, `/api/conversation-members` and
`/api/export/group-members`. The latter two require
`source=observed` explicitly: they are not wire-equivalent to the
original provider-backed roster. No browser cookie routes were added.

## Exact filtered text export (110)

On ordinary native-live and snapshot listeners an exact-account Desktop
grant with both `messages.read` and `messages.export` can use the manifest's
`filtered_message_export` resource, `GET /api/v1/messages/export/filtered`.
Supply one exact `conversation_id`, optional literal `q` (4,096 UTF-8 bytes),
exact message `type` (128 bytes), and inclusive reader `start`/`end` strings
(128 bytes each; date-only inputs are UTC+08). An explicit different account
or platform fails before storage access. The filters use the same literal
LIKE escaping and optional trigram FTS intersection as the scoped message
reader, including literal wildcard characters.

The read transaction enforces binary account/conversation identity, rejects
ambiguous matched rows, and renders only the
established normalized text-export fields. It uses existing JSON, CSV,
TXT, Markdown, HTML, ChatLab JSON and ChatLab JSONL output (and their
existing aliases) in ascending timestamp and binary message-ID order.
An unknown conversation fails; a known conversation with no matches
produces an empty export in the chosen format. More than 1,000 matching
rows, a cell over 64 KiB or output over 4 MiB fails the whole request.
One bounded SQLite scan/limit strategy is used without pre-count or
multiple pages. CSV formula neutralization and sandboxed HTML handling
are unchanged.

This does not export cross-conversation search results or raw/provider
metadata. The unfiltered and guarded export contracts and
the selected-ID verified-media ZIP job are unchanged. The filtered ZIP
shares the binary selector and rejects ambiguous matching message lookups;
its contract and limits are unchanged. Revert this single
source commit to remove the route and manifest field; no schema, export
cache, provider, or file migration is involved. Runtime and unified
testing are deferred.

## PostgreSQL text attachment (112)

The [pinned stock SQL writer](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/exports.py#L447-L501)
writes a PostgreSQL-style message and resource schema. This port makes
its text-only subset available using `format=sql` only on the filtered
route. It retains the original message/table/column names and ordering
within the schema. Its attachment is `application/sql; charset=utf-8`
with the fixed name `export.sql`. No SQL is accepted, interpreted,
executed or saved via this route or the Desktop SDK.

Only the established text projection is emitted: unsupported media URLs,
filenames and raw types are NULL, and the compatibility resource table
has no exported rows. All schema, table and index identifiers and SQL
statements are fixed; message data is represented only as typed primitive
values or quoted PostgreSQL escape string literals. Quotes and backslashes
are escaped independently, Unicode is UTF-8, and control/newline bytes
are represented as escapes. A NUL in a text field fails the whole export
rather than silently stripping data. The normal row/cell/output caps include
escaped output bytes. This is a PostgreSQL UTF-8 interchange file, not a
SQLite file or SQL execution service.

The authenticated manifest independently advertises
`filtered_message_sql_export`. Managed desktop callers can choose
`TextFormat::Sql` only when that exact capability is present. Returning
typed bytes does not render, save, auto-open or import the result.
The unfiltered, guarded, browser and media export contracts are not changed.

## Spreadsheet text attachment (113)

`format=xlsx` is available on the same ordinary filtered route under the
same exact Desktop grant and row limits. The
[pinned original writer](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/exports.py#L816-L843)
defines the one-sheet `聊天记录` workbook, three information rows and
13 Chinese column headers. Those are kept, including the matched message
count and UTC+08 timestamp text. The media URL and filename cells are empty,
and the attachment cell contains the literal text `[]`; source metadata
and provider media are not included.

Cells are UTF-8 XML inline strings only: no formula, macros, embedded
objects, external relationships or auto-links are generated. XML-special
characters and literal Excel escape markers are encoded as data and CR is
preserved. A value containing impermissible XML characters or exceeding
Excel's 32,767 UTF-16-unit cell limit rejects the whole attachment without
truncation. The final ZIP is deterministic and STORED rather than the
original DEFLATED packaging. The 4 MiB output cap includes all package
members, ZIP headers and directory overhead.

`filtered_message_xlsx_export` must be advertised for the SDK to request
`TextFormat::Xlsx`. The SDK verifies the fixed MIME, disposition, bounded
complete length and ZIP prefix, and returns bytes only. It does not parse
or open the workbook, save a file, or execute spreadsheet content.
Existing unfiltered/guarded/browser/media modes and grant policy are not
expanded. The shared internal ZIP writer preserves the previously generated
flat media member names, order, record fields and caps. Revert this change
to remove XLSX and restore the former private media-only writer; there is
no persistent workbook or schema to migrate.
