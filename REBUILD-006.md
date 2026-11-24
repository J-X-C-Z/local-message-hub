# Local Message Hub Rust rebuild-006

Local successor of rebuild-005. Python remains the default; nothing was pushed
or changed in any provider app.

- Linux-only `import-media-backup` verifies and imports the same bounded
  legacy ZIP32 subset as preview into a fresh private target. It validates
  one open archive end-to-end and stages a read-only projected v9 index,
  content-addressed media objects and a hashed-reference catalog.
- Each media payload is verified against manifest, CRC and actual index
  references. Shared digest objects are stored once. No archive-defined path
  is used as a destination and no filenames are in the catalog. Digests are
  identifiers, not authentication or encryption.
- Atomic directory publication uses Linux renameat2 with NOREPLACE relative
  to the checked private parent FD. Pre-commit validation errors leave no
  final leaf, and existing targets are preserved. Stage creation/internal
  writes use trusted paths; do not use a same-UID/ancestor-mutable hierarchy.
  Interrupted processes may leave private scratch. If final fsync fails
  after publication, parent_synced=false reports durability uncertainty,
  not a failed publish or deleted archive.
- Not an in-place restore, credential migration, provider/cache operation,
  HTTP media API or non-Linux runtime qualification.

## Verification
- Rust 1.100.0 / Linux x86_64:
  `cargo test --release --locked --offline --all-targets`: 56 standard cases.
  Includes shared digest, v9 reopening, concurrent target winner, rejection
  of an existing or redirected target, late reference failure/size cap and
  read-only source/previously-published archive byte preservation.
- `cargo test --release --locked --offline --all-targets -- --ignored`:
  five pinned legacy comparisons with modules from
  `c7052745fab3fa1880c6e3a496f40609185668b9` on `PYTHONPATH` (see 005 for
  exact module blob IDs). In the unmodified Python generator's mixed ZIP
  case the native import separately reads exact synthetic QQ and WeChat
  payload bytes, excludes the orphan, and leaves both source caches and ZIP
  unchanged. Only the pinned pure WeChat cache-ID function is used, not its
  provider imports or runtime.
- No Windows, macOS, browser, real data, account credentials or provider
  integration tested.

## Progress
5,010 production Rust lines / 26,429 old Python lines = 19.0% source
volume (005: 18.2%), not functional completion. 20 native HTTP operations
including health; 0/124 old HTTP operations fully qualified.
