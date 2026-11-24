# Local guarded-backup retention preview (Linux)

This read-only command plans retention for a private [guarded backup
catalog](GUARDED-BACKUP-CATALOG.md) already created by the operator. It does
not create, move, download, delete, register, or rewrite backups or the
manifest. No HTTP service or scheduler is added.

```text
local-message-hub-rs plan-guarded-backup-retention --catalog DIR --platform P --account A --oldest-first --keep-latest N [--target-bytes N]
```

Provide the existing single LF-terminated backup credential frame on private
piped stdin. The whole catalog must consist of authenticated, structurally valid
LMHENC01 files for one exact account and common backup passphrase. Missing,
bad, foreign, wrong-password or duplicate-ciphertext entries fail the entire
preview with the same generic error. Do not combine accounts or passphrases in
one retention catalog. Regular catalog listing/copy retain their prior behavior.

**The manifest's `files` array must already be in operator-verified
oldest-to-newest order.** LMHENC01 does not authenticate chronological metadata.
The command does not infer age from a name, modification time, or decrypted
messages. `--oldest-first` confirms that the operator intentionally supplied this
order, rather than letting the planner infer it. It also checks the manifest
contents still match once the scan finishes. Run offline with private catalog
paths stable throughout; the plan is not a filesystem snapshot.

`--keep-latest` protects at least the last N existing backups (1–16) regardless
of space. Without a target, every earlier backup is listed for review. With
`--target-bytes`, the planner walks earlier entries oldest-first and lists only
enough of them for review to meet that target if possible. It reports hypothetical
bytes remaining and any excess if the target is incompatible with the protected
floor. Output includes the manifest SHA-256, verified ciphertext IDs/sizes and
the protected/retained/review groups; never file names, account data or content.

`review_only` is not a deletion approval or proof that a backup is redundant.
Take an independent verified copy and re-evaluate anything that changed before
making a manual retention decision. No package or device has been exercised
in this source checkpoint.
