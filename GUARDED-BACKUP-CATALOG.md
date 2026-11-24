# Guarded encrypted backup catalog (Linux, offline)

The existing LMHENC01 format authenticates the platform/account **inside** its encrypted
payload. A filename, catalog label or envelope header is not proof of account ownership.
No HTTP listing or downloading is enabled. No profile unlock, backup creation, provider,
network or background operation is performed by these commands.

Place existing ciphertext files in an operator-owned private directory (mode 0700).
Create a private regular file named `catalog.json` (mode 0600), with this schema:

```json
{"version":1,"files":["my-backup.lmhenc"]}
```

The 4 KiB manifest admits at most 16 unique single-component names ending in
`.lmhenc`. Catalog files and the manifest must be single-link regular files owned
by the command's effective user without group or world permissions. The catalog
directory cannot itself be a symlink. Its parent path and the output path are chosen
by the local operator, not by a plugin or network request. Do not repurpose a live
profile as a catalog.

Use `list-guarded-backups --catalog DIR --platform PLATFORM --account ACCOUNT` or
`copy-guarded-backup --catalog DIR --platform PLATFORM --account ACCOUNT --id ID --output NEW_FILE`.
Provide exactly one LF-terminated `{"backup_passphrase":"..."}` frame and EOF via
private piped stdin; do not place a real passphrase on a command line. Account scope is
checked against the decrypted and validated restore payload, not the manifest.

The list contains only SHA-256 ciphertext IDs and encrypted sizes for verified
matches, never paths, row counts or plaintext. Missing, invalid, wrong-scope and
wrong-passphrase entries are indistinguishable from no verified match. Different
passphrases require separate invocations. Copy verifies the selected ID and embedded
scope, then atomically publishes those same original ciphertext bytes at a new leaf
in a private directory. It does not replace an existing file or output plaintext.
