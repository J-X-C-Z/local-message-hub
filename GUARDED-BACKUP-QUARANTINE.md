# Reversible guarded catalog quarantine (Linux, offline)

This command logically hides one existing, fully authenticated LMHENC01
backup from the current verified catalog’s list/copy and retention-preview
commands. It changes only a private visibility marker, **not** the manifest,
ciphertext files or active profile. It never deletes backups, moves them,
expires them, or frees disk space. Older software or direct operator file
access can still see the encrypted file. No HTTP service exists.

```text
local-message-hub-rs guarded-backup-quarantine --catalog DIR --platform P --account A --action quarantine --id SHA256 [--confirm PREVIEW]
local-message-hub-rs guarded-backup-quarantine --catalog DIR --platform P --account A --action release --id SHA256 [--confirm PREVIEW]
```

Use the same private piped backup credential frame as the [catalog](GUARDED-BACKUP-CATALOG.md).
A dry-run omits `--confirm` and makes no writes. It authenticates every unique
ciphertext in the bounded private catalog against the exact account and
returns an opaque confirmation. To apply, explicitly pass that confirmation
with the same other arguments. Apply reacquires an exclusive nonblocking
directory lock and reauthenticates the full inventory. The confirmation pins
trusted root identity, manifest bytes, ordered ciphertext digest/size
inventory, exact scope, action, selection and marker generation. A restore
of visibility requires its own separately confirmed `release` operation.
Only one member can be hidden and at least one member must remain listed.

The new catalog readers take a shared copy of the same advisory lock. The
visibility marker is a single, bounded, owner-private regular file published
by a same-directory atomic rename. Its transition is the only mutation. A
crash before rename can leave an inert hidden staging file, not a half-written
marker or an altered backup; old or new visibility remains readable according
to the marker that survived. `parent_synced:false` indicates publication
already occurred but directory sync failed, so re-open rather than assuming
rollback. Invalid active markers or an active manifest-fingerprint mismatch
fail closed. Keep the catalog files/manifest stable until release; these
advisory locks do not control external editors, older Hub builds, or hostile
same-user processes. Always retain independently verified backups. The code
was not built, tested, or applied against any catalog in this checkpoint.
