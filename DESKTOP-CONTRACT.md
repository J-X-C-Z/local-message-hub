# Redacted desktop protocol contract

A scoped Desktop client can query **GET `/api/v1/desktop/contract`** without
a body or query parameters on native live, snapshot, imported media, and
guarded bearer listeners (native or legacy read adapter). The isolated
guarded browser listener serves **GET `/api/desktop-contract`** only under a
current authorized browser session, in either browser mode. It is not
served by health, browser bootstrap, Campus, producer or MCP authorization.
The guard must already be open; the contract does not unlock or refresh it.

The typed descriptor includes descriptor and native API versions, binary
package version, listener mode, transport, and available operation records
with `id`, `path`, `method`, `request`, `response` and `content_type`.
`openapi` points to the native schema when that listener serves it. The
contract is a conservative Desktop bootstrap list, **not** a complete
endpoint inventory. Missing operations cannot be distinguished as
ungranted, unsupported, or simply outside this descriptor.

Request/response classes help clients select compatible API models. For
example, a guarded legacy message selector requires exact platform,
account, conversation; the legacy response projection suppresses
`media_url`, `file_name`, `raw_type` and `sender_avatar_url` values.
Native archived pages use the native projection and need not be treated as
legacy-redacted. Imported media is separately scoped and distinguishes
attachment metadata, verification, opaque reference and bytes; the route
template never embeds locally stored IDs or paths. QoL operations distinguish
local metadata streams, revisioned draft text or manual transcripts from
their write receipts, and review annotations from delivery.

No grant ID, raw permission list, platform/account identifier, archive
contents, path, credential, proof, or source state is returned. There is no
account selector; mode and effective routes describe only the authenticated
identity's listener access. Templates are not health probes, and cannot
promise data, provisioned state, or archive availability. Unsupported action
identifiers describe **this Desktop listener**, not every CLI or tool;
there is no provider login/action/delivery, automatic transcription,
operating-system notification delivery, web unlock or remote backup here.
Every listed request still undergoes its ordinary permission, scope,
archive identity, and (when guarded) session checks.
