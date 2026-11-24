# Read-only stock JSON upload preflight

Under the ordinary native-live listener, POST `/api/imports/preview` supports
the original upload request shape for **explicitly scoped text-only Hub JSON
arrays**. A native Desktop bearer grant must include `imports.local.manage`
and authorize one exact platform/account. Use exactly one
`Content-Type: application/octet-stream` header and the query `format=json`.
The optional stock keys `platform`, `account`, and `filename` are accepted.
The stock defaults `platform=all` and blank account provide no authority and
do not get copied into rows. An explicit non-default mismatching value, a
foreign row, or an object with incomplete/ambiguous scope fails the whole
request without revealing uploaded contents. The filename is solely tolerated
syntax, not stored, returned or opened.

The response follows the original JSON fields `total`, `valid`, `invalid`,
`errors`, `platforms`, `accounts`, and `sample`. Row errors are generic codes
and one-based array positions, not raw data. The sample contains up to five
valid uploaded rows and projects only platform/account, conversation ID,
sender display name, timestamp, type, and text, with UTF-8 byte caps of 96
for sender display name and 256 for text. Counts use the bounded native
normalization and reject duplicate message IDs after the first valid row.
The report examines only the submitted content. It does not read message
records from the archive, write data or a staging job, produce a confirmation,
or predict write conflicts. A report with some valid rows does not make the
existing all-or-nothing staging path accept the entire upload.

Compared with Python `hub/server.py::import_upload` and
`hub/imports.py::parse_import` at the pinned base, this deliberately accepts
fewer formats. Maximum 512 KiB (input and normalized representation), up to
100 rows, UTF-8 top-level JSON array only, explicit exact row scope, exact text
type, native field types and no guessed IDs or timestamps. CSV, Telegram,
EML, MBOX, wrappers, legacy field aliases, missing/default row identifiers,
format coercion, and arbitrary media import are not supported here. None of
the public browser/cookie auth paths are added. The original UI's separate raw
submit/commit is not implemented by this endpoint. JSON/base64 with no query
retains the old Rust staged job behavior, and other write requests still
reject query strings.

## Explicit-direction CSV (105)

The same exact-account ordinary NativeLive Desktop grant
(`imports.local.manage` contract, unchanged) may POST detached bytes to
`/api/imports/preview?format=csv` or `/api/imports?format=csv` using one
`Content-Type: application/octet-stream`. Stage additionally requires one
retained `Idempotency-Key`; it returns 202 and requires separate native
consent/commit as before. Preflight is read-only and reports only counts
and the exact scope, with empty error and sample arrays on success.
The entire file is refused on any invalid row; no partial job or
message samples are returned. File names are ignored except for the
existing query length bound and never opened, stored or echoed.

Use UTF-8 with or without BOM, normal doubled CSV quotes, and complete LF
or CRLF records. Required semantic columns, in any order, are
`timestamp,platform,account_id,conversation_id,id,type,text,is_self`.
The pinned canonical/Chinese equivalents are supported, for example
`时间,平台,账号,会话ID,消息ID,类型,内容,自己发送`. Duplicate aliases,
unknown columns, missing explicit account or IDs, foreign rows, and
multiple rows with the same message ID are refused. Only text is
accepted; optional sender/name/title columns are preserved, but
media/filename must be empty and attachments absent, empty or `[]`.
Direction must be 1/true/yes/是 or 0/false/no/否 (ASCII case-insensitive);
missing/blank/unknown does not imply received. Time is a non-negative
decimal epoch (same seconds/milliseconds split as the pinned parser),
a local UTC+08 `YYYY-MM-DD HH:MM:SS`, or blank for zero. Text is stored as
literal data, not evaluated. At most 100 rows and 512 KiB each for the
uploaded and normalized bodies.

Pinned `webui/app.js:1029,1045`, `hub/server.py:4696–4712` and
`hub/imports.py:13–29,33–42,71–75,94–133` establish the old manual octet
upload and CSV semantics. This subset is **not** a drop-in replacement:
the unmodified Hub CSV writer lacks explicit direction, source fields
are not inferred, original permissive/default/mixed/nontext formats are
not admitted, and this Rust workflow uses an exact bearer and explicit
staged consent rather than the browser's legacy create-job behavior.
The existing QQ operator CSV command with its explicit sender mapping
and the JSON/SDK contracts are unchanged. The literal octet interface and manifest are the basis for the typed
Desktop SDK in 106 below.

CSV approval digests bind exact bytes, format, archive and account,
in a domain separate from JSON. Reusing a stage key with different
bytes or a different format conflicts, and finalized job receipts
continue to use the existing storage and commit path. There is no
schema change. Reverting 105 removes new CSV preflight and staging
but does not erase already staged normalized jobs or completed
imports. Functionality is source-only pending unified verification.


### Typed Desktop CSV workflow (106)

Use the existing exact-account managed native-live
`desktop_client::imports::Client`. Feature discovery for CSV preflight
and staging is independent of each other and of JSON. Only the literal
paths in `stock_csv_preflight` and `stock_csv_stage` enable those methods.

- Call `preflight_stock_csv(&bytes)` explicitly to inspect counts without
  staging anything. The bounded successful reply must contain 1–100 valid
  rows, zero invalid, empty errors/samples and exactly the authenticated
  platform/account; the application receives counts only. A rejected file
  does not produce a partial report. The SDK does not parse or repair CSV,
  infer direction or timestamps, or fall back to JSON.
- If explicitly selected for staging, call
  `stage_stock_csv(&request_id, &bytes)` with a retained typed `RequestId`
  and the original bytes. The request is raw octet-stream, scoped by the
  validated grant, with one `Idempotency-Key`. Only HTTP 202 and the
  existing checked typed Job envelope are accepted. Inspect the returned
  phase and digest before deliberately using the same approval, commit,
  status and apply-evidence APIs. Staging is not importing.
- With `OutcomeUnknown(StageStockCsv)`, use status if the exact Job ID is
  known. Otherwise explicitly repeat only with the same original CSV bytes,
  key, format and same trusted managed archive/grant after restoring
  readiness as needed. No automatic retry, cross-format fallback or
  approval.

Rollback 106 by reverting the SDK source patch. The 105 endpoint, grants,
staged records and prior imports are unchanged. No build or tests ran.
