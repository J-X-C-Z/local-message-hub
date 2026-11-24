# Detached Moments text catalog

Source-only counterpart to the pinned stock `/api/moments` and
`/api/moments/export` read/export policy. There is no implementation for
original raw caches or protected client data. No association from a detected
account, acquisition, media access, URLs-as-resources, publication, or
automatic transfer.

An operator supplies one already-authorized regular file (512 KiB max) with
`format:"wechat-moments-detached-text-v1"`, exact `account_id`, and `posts` (1–100).
Each post carries exact `id`, `author_id`, numeric `created_at` Unix seconds,
and inert, nonblank `text` (8192 bytes max). Unknown fields including
attachments fail validation. Source identities are operator-attested;
nothing infers account or author ownership. The confirmation is bound to
the exact bytes, target native archive instance and explicit account. No
file paths or extra source records are persisted.

- `preview-moments-json --source FILE --directory ARCHIVE --account ACCOUNT`
- `import-moments-json --source FILE --directory ARCHIVE --account ACCOUNT --confirm DIGEST`

Both require an ordinary native archive. Account-local ID matching is
append-only: identical rows are unchanged; any differing existing ID
rejects the entire transaction. The immutable receipt and rows commit
atomically. There are at most 4,096 posts and receipts per account.
A preview does not guarantee there is no preexisting conflict.

Ordinary native-live `GET /api/v1/moments` requires a Desktop grant bound to
one WeChat account with `moments.read`. The page-only
`GET /api/v1/moments/export` additionally requires `moments.export`; it returns
JSON to that exact client, not a file, URL or delivered export. Either can
filter by exact `author_id`, literal case-lowered text `q` (256 bytes), inclusive
Unix-second `start`/`end` and bounded `limit` (1–50, default 25) / `offset`
(0–4096, default 0). Optional `account_id` must exactly match the grant. The
order is timestamp descending then exact ID ascending; each page computes
total and rows within one snapshot. No enumeration of accounts, no aliases
on old routes and no guarded/snapshot/MCP support. No tests/build performed.

## Complete filtered download

`GET /api/v1/moments/export/archive` restores the full filtered-download
workflow for the already imported detached-text catalog. Same exact WeChat
Desktop account and both `moments.read` and `moments.export` are required.
It is separate from the existing JSON page and not an alias for the old
browser endpoint. The client explicitly requests a download; the server
does not create a file, send an attachment externally, or launch a client.

- Optional `account_id` must exactly match the grant.
- `author_id` (or the pinned filter spelling `username`, not both) matches
  one exact detached author ID; `q` is a literal lower-cased substring,
  limited to 256 UTF-8 bytes. Date-only `start`/`end` use the existing
  charting/archive parser's UTC+08 00:00:00 / 23:59:59; supported explicit
  timestamps (including Unix seconds) remain inclusive, and reversed
  ranges are rejected. Blank dates are unbounded.
- No client `limit` is accepted. The optional `offset` (0–4,096,
  default zero) deliberately skips matching rows, and the response then
  contains every remaining match in that transaction, newest timestamp
  first and exact ID ascending for ties. No matches yields an empty JSON
  items array or a CSV header only.
- `format=json` (default) includes `mode`, `items`, exported
  `rows` count and `offset`; `format=csv` contains four columns:
  `id,author_id,created_at,text`. CSV uses the shared spreadsheet formula
  neutralizer on every cell. Each format contains only the four already
  imported fields. Fixed safe filenames are `hub-moments.json` and
  `hub-moments.csv`, no caching or content sniffing.
- A single bounded read snapshot is used throughout. Over 4,096 remaining
  rows or over 4 MiB of actual encoded output returns 413 with no partial
  export. Invalid/corrupted storage fails closed before sending any bytes.

The pinned UI also offers other formats and the original stock source can
project media and social metadata. There is no such data access here, no
provider, HTML/Markdown/Excel generation, history outside the detached
catalog, or claim of unmodified browser parity. Revert this source change
and restart to remove the route; no new schema or persisted export state.
Compilation and runtime verification stay deferred.

## Pinned desktop list projection

Ordinary native-live `GET /api/moments` accepts only a Desktop bearer
bound to one WeChat account with `moments.read`. It is not mounted in
native snapshot, guarded bearer or browser-cookie modes. The exact
`account_id` may be omitted or empty (use the granted account); any other
nonempty account receives 404 without consulting another account.

The pinned [desktop list call](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/webui/app.js#L1612-L1615)
sends `username`, `q`, `start`, `end`, `offset` and `limit`.
The bounded projection maps the known detached fields to
`items[].id/username/createTime/contentDesc`; no inferred names, media,
location, likes, comments or avatars are sent. Search matches literal
case-lowered body text only (up to 256 UTF-8 bytes), not comments. Date
bounds use the established detached archive parser. Limit defaults to 50
and clamps to 1–200; negative offset selects the start, and offset over
4,096 is unsupported. Like the pinned [store order](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/store.py#L1582-L1611),
ties use descending exact ID. The local catalog cap (4,096 posts) and
4 MiB generated response cap fail closed without partial results.
Matching rows, total and exact-account catalog readiness are read in the
same verified transaction. An imported catalog with no matching filter
rows produces an empty available page. No catalog for the authorized
account returns 503 and `moments_not_indexed`, rather than inspecting or
enumerating other accounts. A successful page lists only its own account.

This is a list projection for an already-authorized local catalog; it
does not enable the old global Moments toggle, open the provider/cache,
or restore the old WebUI as a whole. No schema or provider changes.
Revert this source to remove only the legacy list. Compilation and runtime
validation are deferred to the unified pass.

## Pinned filtered download, detached JSON and CSV only

Ordinary native-live `GET /api/moments/export` is a local attachment
response for an exact WeChat Desktop grant with both `moments.read` and
`moments.export`. The account, `username/q/start/end` and offset rules
are the same as the list. The export reads **all** filtered matches from
the chosen offset in one verified read snapshot (not the first page
only); a supplied numeric `limit` is ignored like the original.
A filtered empty result is an attachment with an empty items array or
CSV header only. An absent exact-account catalog returns 503 instead.

The default `format=json` uses the [pinned export keys](https://github.com/J-X-C-Z/local-message-hub/blob/c7052745fab3fa1880c6e3a496f40609185668b9/hub/moments_exports.py#L244-L281)
for known data, with `source.platform:"wechat"` and
`source.scope:"detached_text"`. It does not claim the source was
`sns.db`, or include unknown social or media fields. `format=csv`
uses the pinned ordered Chinese headers and the UTC+08 timestamp;
unavailable fields (including database name and media counts) are blank
rather than fabricated. The shared spreadsheet formula neutralizer
protects CSV cells. Both formats use fixed safe filenames with
`Content-Disposition: attachment` and no caching. Markdown, plain
text, HTML, XLSX and other format requests return 400; there is no
fallback. The per-account cap remains 4,096 rows; output beyond 4 MiB
returns 413 without partial attachment bytes. This is not stock media,
social or export-format parity.

The native `/api/v1/moments/export` and `.../export/archive` are
unchanged. No browser-cookie/snapshot/guarded route, background effects,
global settings, file writes, or external data are added. Reverting this
source removes only the legacy download and leaves the detached catalog
untouched. Source-only; builds and runtime verification remain deferred.
