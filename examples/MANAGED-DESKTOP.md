# Managed Desktop Rust integration sample (Linux)

Build with `cargo build --locked --offline --example managed_desktop`.
The example launches, verifies, and stops one foreground Linux
`desktop-supervise` child. It does not install an app, set up an archive,
provision a grant, acquire any credential, modify a grant registry, access a
provider, perform writes, or retry commands. It accepts only a trusted
native-live archive and exact scoped preinstalled Desktop grants.

A trusted local launcher passes these **non-secret** arguments:

```text
managed_desktop ABS_HUB SHA256 ABS_ARCHIVE ABS_PRIVATE_REGISTRY PLATFORM ACCOUNT READ_GRANT [JOURNAL_GRANT]
```

Use an independently verified Linux ELF digest. The SDK checks the absolute
path components before the example hashes and launches the binary; this
assumes a trusted tree without concurrent changes. A hash is not publisher
authentication. Use only an owned isolated synthetic archive when trying
this sample, and do not point at an active app installation.

The launcher creates distinct private **anonymous Linux stdin/stdout pipes**
and passes an existing read-grant bearer, taken from the launcher's credential
custody, as a single compact JSON object followed by LF and pipe EOF. It
must capture the bounded output privately. Never paste actual bearers into
shell options, environment variables, source code, examples, terminal
commands, or logs. Illustrative field names, **not a literal shell command**:

```text
{"reader_token":"<exact installed read bearer>","search":"<optional explicit local search>"}
```

The `READ_GRANT` must have exactly `conversations.read` and `messages.read`
and must match the declared Desktop grant ID, platform and account. The
example reports only a conversation count by default. Explicit `search`
runs the typed local search, then opens a short typed context around the
first exact hit. This optional branch receives message text in local
memory, but prints neither the query nor any conversation or message text.

For an optional journal operation, pass a **separate** grant ID for the
same exact account, provisioned independently for `events.read`, and add:

```text
"journal":{"token":"<exact separately installed journal bearer>","previously_applied":"<optional last applied cursor>","wait_one":false}
```

This revalidates the separate grant and current checkpoint. Omitting the
saved cursor starts at the *current head*, not at archived history.
`wait_one:false` returns at most one previously unseen journal item using
bounded typed JSON replay; `true` waits on the typed SSE resource for at most
one item or idle. It does **not** ACK, advance, persist, or exactly-once
deliver an application change. After applying the event in its own UI,
the launcher may persist `next_cursor_if_applied` privately. The cursor in
the example's report is a candidate and marked `applied:false`. A fresh
process restores an application-saved position against its own checkpoint,
never silently skipping to the head.

Readiness and control use separate supervisor pipes, a bounded deadline
and the owned child handle, not a PID-existence probe. An explicit clean
stop is required for a success report. Error cleanup closes child custody;
the example never stops any externally owned Hub process. This sample does
not qualify Windows/macOS, original WebUI/cookies, a secure credential
vault, the OS notification layer, or installer and service lifecycle.

This is a Rust managed-client development example. It is not the original
Hub shipping path: the pinned original Hub README (lines 17–25) and
`docs/windows-package.md` (lines 1–8) describe a Windows x64 portable
executable plus stock WebUI. A retained Electron WeFlow dependency's OS
matrix is not the original Hub's product support matrix. Independent
Rust desktop targets likewise require their own platform acceptance.
