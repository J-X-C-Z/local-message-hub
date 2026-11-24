# Private detached PCM voice handoff (Linux)

The foreground operator can select a single **already imported** attachment
from a private verified detached media bundle and pass it to their own
separately operated offline transcriber. The Hub never invokes a transcriber,
runs a shell or codec, contacts a network, accepts an audio path/URL from a
client, or activates a job. Nothing is mounted on native live, guarded,
browser, MCP or other HTTP listeners.

The operator's preview command takes the bundle, platform, account,
conversation, message and opaque attachment ID. The current owner must own
the private bundle. The projected index must contain a unique exact **voice**
row and the media catalogue must own the attachment under the exact same
scope. The full bounded blob is verified and parsed **before** giving a
preview. The preview prints selection, sample metadata, audio SHA-256, an
immutable projected-index/catalog + message witness, and a confirmation,
never the audio itself. Only a canonical little-endian `RIFF/WAVE` with a
16-byte PCM format chunk then one data chunk is accepted: signed 16-bit PCM,
mono/stereo, common sample rates 8–48 kHz, at most 8 MiB and 300 seconds.
Padding junk, metadata chunks, SILK, MP3, compressed WAV and other media
formats are deliberately unsupported.

Using the same explicit selection, the separate confirmed export command
checks the supplied confirmation against the catalog/index witness, exact
message and attachment, and full audio digest again. It then writes only
the unchanged WAV bytes on **anonymous operator-owned stdout pipe**. Files,
terminal, named FIFOs and sockets are rejected; no plaintext staging file
is created. The operator must connect a trusted local consumer and protect
the preview/pipe. A pipe's shape cannot establish what its receiver does.
The consumer must discard partial bytes if the command reports failure.
On timeout this foreground process must exit, not be reused as a worker.

The detached snapshot has no live message revision; the immutable
catalog/index witness has that role here, and an altered scope, message,
attachment, catalog/index or audio cannot reuse the confirmation. The WAV export command itself does **not** accept transcriber output.
Ordinary native Live ingestion normalizes text and the optional live media
link deliberately does not coerce a detached typed voice into that text
row, so importing output into native manual voice annotations without true
typed provenance would create a false linkage. Engine integration remains outside this workflow. Detached result retention is described below.

## Private operator result retention (093)

This separate Linux CLI closes the handoff-to-annotation gap. It never
starts, contacts, or judges a transcriber. It stores explicitly supplied
plain text in a private ordinary local archive selected as the annotation
journal, **not inside the immutable media bundle**. Journal and bundle
directories must be separately owned by the effective OS owner and private,
and must not contain each other. No credentials, new grants or network
routes are introduced. Same-UID processes with that filesystem access
are part of the operator trust boundary.

All three calls use the identical ordered selectors:
`--bundle <imported-bundle> --directory <ordinary-private-journal>
--platform <platform> --account <account> --conversation <id> --message <id>
--attachment <id>`.

- `preview-detached-voice-result` adds `--source <private-result.json>`.
  The current owner must own the bounded (128 KiB), singly linked regular
  file, with no group/other access and no symlink acceptance. The strict
  document accepts `version:1`, `platform`, `account_id`,
  `conversation_id`, `message_id`, `attachment_id`, `audio_sha256`,
  `message_snapshot_witness`, `audio_confirmation` (the WAV preview
  confirmation), `expected_revision`, `text` (up to 16 KiB), and optional
  `language` (a lower-case 2–3 letter language, optional upper-case
  two-letter region). Use expected revision zero for a new result.
- `import-detached-voice-result` uses the same source plus
  `--confirm <result-preview-confirmation>`; it validates exact current
  media again and uses a single CAS transaction. A different or stale
  revision fails. The immediately applied exact document is idempotent
  on retry; once superseded, it cannot restore an older result. Both
  preview and commit output metadata only. The result fingerprint binds
  exact source bytes, selected journal archive, verified bundle instance,
  snapshot voice witness, exact selector and full audio digest.
- `read-detached-voice-result` takes only the common selectors and
  returns only the current exact result to a private anonymous stdout
  pipe, never a terminal or file. It rechecks the original owned bundle
  instance, unique indexed voice relation, complete audio digest, and
  journal identity before emitting text. Replacing the bundle root or
  changing its catalog, indexed message witness, attachment, or audio
  cannot reveal the previously bound annotation.

The journal records no credentials, operator filesystem paths or audio.
It is limited to 4,096 exact annotations and adds a private table lazily on
the first confirmed result. The stored text is not proof that an operator
or model heard, transcribed, or accurately understood the recording. It
is detached-only; the existing ordinary live transcript APIs and legacy
voice routes are unchanged. Revert the 093 commit to remove the CLI; the
private dormant table can be left intact, with no deletion or migration
needed. No compile/run verification was performed.
