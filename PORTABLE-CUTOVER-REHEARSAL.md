# Verify and rehearse a portable guarded cutover (Linux synthetic only)

This acceptance workflow runs **only generated data**. It does not take a
production profile, install anything, change startup configuration, or
launch a provider. It qualifies a bounded **Linux guarded legacy reader**
path. The ordinary NativeLive supervisor and its write/sync safety have
separate release requirements. macOS/Windows packages need native checks;
this script does not run them or claim platform parity.

Obtain the exact expected source commit and SHA-256 of the candidate binary
and portable manifest over an independently trusted channel. Independently
pin the already trusted local verifier executable too. Do not derive the
accepted hashes by querying the candidate. The reusable verifier is the
existing `verify-portable-core` command from a trusted prior Hub executable,
not the candidate binary. A manifest authenticates neither the publisher
nor unverified release metadata by itself.

Use a trusted local filesystem with no concurrent modifications from other
processes of the same UID. Extract the package yourself under a private
location first; do not verify a path through a symlink. The packaged root
and scratch parent must already be owner-only directories (mode 0700 is
recommended). The packaged `bin` directory must be owner-controlled and
not writable by group or other users. The script does not change their
permissions, nor install anything.

Run Python with assertions enabled (no `-O` or `PYTHONOPTIMIZE`).
Check out or otherwise independently verify the pinned Python oracle at
`c7052745fab3fa1880c6e3a496f40609185668b9`. Use absolute, canonical paths.
The existing stock HTTP test helper loads the pinned Handler with inert
application/provider wiring. The rehearsal verifies four known Git blob
hashes for the original store, handler, account safety and capabilities.

```sh
PYTHONPATH="$PINNED_PYTHON_ROOT" python3 scripts/rehearse-portable-cutover.py \
  --verifier "$TRUSTED_VERIFIER" --verifier-sha256 "$VERIFIER_PIN" \
  --package "$EXTRACTED_PACKAGE" --manifest-sha256 "$MANIFEST_PIN" \
  --binary-sha256 "$CANDIDATE_PIN" --source-revision "$EXACT_COMMIT" \
  --target linux-x64 --scratch-parent "$PRIVATE_SCRATCH"
```

Use `linux-arm64` instead only with a matching native Linux ARM64 artifact.
The script first verifies native package layout, manifest digest, candidate
digest and size, pinned revision, and executable type using the trusted
verifier. Candidate code is not invoked if verification fails.

With a verified package it creates both archives in disposable scratch.
The Python source produces its normal detached v9 SQLite backup, then closes.
The script verifies refusal of the active source, detached source with a WAL
sidecar, an existing target and a nonprivate target parent. The guarded
migration is a single exact-account first load. Synthetic counts distinguish
the selected-account projection, omitted other-account rows and omitted
sync state. Raw media paths and other unprojected fields are not imported.
The core's exact preview confirmation binds the actual file and scope.

The full rehearsal launches the guarded reader and verifies start/lock,
one exact-account conversation list, history, search, surrounding context,
and a denied foreign account. The stock list/search results are filtered
to the selected account for this differential, because Python may return a
wider scope; raw response equality and client compatibility are not claimed.
The rehearsal stops the reader, verifies original
profile and detached input file hashes, then reopens the original Python
test store and resumes a synthetic write. The Rust profile remains unchanged
during fallback. This is selecting a preserved original again, **not**
restoring an old HTTP/ZIP backup, deleting or modifying the original in
place, or carrying Rust-era writes back into the Python archive.

Existing client authentication and UI, media/attachment indexes and files,
all-account migration, provider state, sync/send, and write/replay history
are not qualified. A passing rehearsal is not authorization to perform a
production cutover; keep the old archive and application available.
