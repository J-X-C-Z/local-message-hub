# Manual local voice annotations

The ordinary native-live endpoint `/api/v1/voice/transcription` records
human/operator-provided text for an already indexed voice message. It never
opens, resolves, decodes, uploads or transcribes audio, calls a model, or
contacts a provider. The optional legacy-shaped bridge below does not
provide the pinned Python transcriber's model/readiness behavior.

A scoped Desktop grant needs `messages.read` plus
`voice.transcription.read`. Writing additionally requires
`voice.transcription.write`. The authenticated scope is the only possible
account; no override is accepted. Both the exact message and its exact
indexed conversation must match, and the message must have native type
`voice`. Outside that identity, non-voice, unknown, or copied targets do not
resolve. There is no list route or mounting on guarded archives, projections,
legacy modes, or standalone media bundles.

- **GET** with `message_id` and `conversation_id`: returns `revision` and
  `status` (`not_recorded`, `manual`, or `cleared`); only `manual` includes
  the supplied `text` and optional `language`. No original message body,
  media locators, URLs, paths, file names, raw voice metadata, or credentials
  are queried or returned.
- **POST** with `operation: "set"`, both IDs, `expected_revision`, and
  `text` accepts up to 16 KiB of nonblank, plain text. Optional `language`
  uses a limited canonical format, such as `en` or `zh-CN`. With
  `operation: "clear"`, do not send text or language. The response is only
  the revision and status. GET revision zero means no record. Matching
  revision and unchanged content is a no-op; a stale revision conflicts.
  After an interrupted write, GET to reconcile before retrying.
- Writes are serialized and capped at 4,096 annotations/tombstones per exact
  account. Clearing removes live annotation text while retaining its revision.
  This does not erase earlier database backups, filesystem snapshots or
  SQLite page history. Annotations do not modify the indexed message and are
  not searched, exported, or mixed into its media metadata.

The annotation belongs to the indexed message identity and is not proof of
audio content; the caller must confirm it against the relevant recording.
Treat operator-provided text as private plain text and render it without HTML
or script execution.

## Partial legacy request bridge

A scoped Desktop grant with both the existing transcription-read capability
and explicit legacy HTTP opt-in can use these exact-account, ordinary
native-live routes. Only the original archived voice identity is accepted;
neither route writes the transcription store.

- `GET /api/voice/transcription-status` reports
  `available:false`, `automatic:false` and
  `reason:"automatic_transcription_unavailable"`. An explicit
  `manual_annotations_available:true` advertises the scoped lookup capability,
  not that a model, audio resolver or transcript exists.
- `POST /api/voice/transcribe` accepts the four pinned fields
  `platform`, `account_id`, `message_id`, `conversation_id`, no extras.
  If the exact indexed voice has an existing annotation, it returns
  `available:true`, `text`, `source:"manual_local_annotation"`,
  `automatic:false`, `revision` and optional `language`. If it is a valid
  voice with no annotation, the response is `available:false` and
  `reason:"manual_annotation_not_found"`. Missing, non-voice or out-of-scope
  targets use the same not-found response. Use the native API to enter or
  change a manual annotation.

Pinned upstream's POST resolves cached audio and calls a transcriber, while
its GET delegates to the engine's status. This bridge reuses the exact
request identity and availability envelope only. It never opens audio from
a path, URL, or provider cache, starts any executable or model, downloads
data, pretends to be a complete result-shape alias, or enables automatic
transcription in a stock client.

## Explicit offline operator handoff

On Unix, an operator with private ordinary archive access can review and
apply an already prepared UTF-8 annotation as a private, singly linked,
regular JSON file (no symlinks; maximum 128 KiB). It has the exact fields
`version:1`, `platform`, `account_id`, `message_id`, `conversation_id`,
`expected_revision`, `text`, and optional `language`. No audio path, model
selection, arguments, URLs, or extra fields are accepted.

Run `preview-voice-annotation --source <private-file> --directory <native>
--platform <platform> --account <account>` for a read-only preflight and a
fingerprint covering that exact file, account, and archive instance. Review
the private source file yourself; stdout reports only a prospective
revision/status and a confirmation, never the text. To apply, explicitly
run `import-voice-annotation` with the same arguments and
`--confirm <fingerprint>`. It rechecks the selected voice message and
revision in the write transaction. A retry of an already-applied change may
reconcile an exact next revision with identical text/language; it makes no
second change. Any later revision or mismatched content conflicts.

This lets the operator hand off human-written or independently produced
transcript text. It neither runs nor validates an inference engine, opens
audio, or verifies when/how the detached text was produced. The stored
result remains a manual local annotation.

## Typed managed Desktop SDK

`desktop_client::transcription::Client` opens the fixed native-live path only
after verifying its exact-account managed connection, read permissions and
manual-only manifest capability. `get(conversation_id, message_id)` returns a
private session-bound annotation. `set(&annotation, text, language)` and
`clear(&annotation)` require a separate write grant and use that exact
reviewed target and revision. A different session must read the target again.

The returned annotation exposes only manual text and language, not the
indexed message body. A conflict requires a fresh read and review; an
ambiguous write requires reconnecting and reading before retrying. There is
no automatic retry or reconciliation, no audio/provider call and no grant
installation.
