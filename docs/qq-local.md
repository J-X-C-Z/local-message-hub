# QQNT Local Database Reader

QQ history now uses a read-only local database snapshot. Discovery checks the
current user's Documents location (including redirected Windows Documents) for
one QQNT account. Multiple accounts require choosing a directory. No QR login,
QQ restart, client injection or online history request is part of this path.

Encrypted databases require a 16-character printable ASCII passphrase (symbols
are valid) or `hex:` followed by a 32-byte derived key in hexadecimal. On
Windows, `qq-local.json` stores new keys with current-user DPAPI protection;
they cannot be unlocked under a different Windows user. A legacy plaintext key
is migrated only after the encrypted first page authenticates. Public status
excludes paths, account identifiers and keys. Package/Git scanners reject this
settings file.

## Sending Targets From The Local Reader

The local reader indexes private conversations by QQNT `uid` (for example
`u_-PBswiplK...`) because that is what `c2c_msg_table` stores. OneBot 11 only
accepts a numeric uin/group code, so NapCat resolves the two itself: a group
uses `group_id` verbatim as `peerUid` (chatType 2), and a private chat converts
`user_id` into a uid through `UserApi.getUidByUinV2` before sending
(`napcat.mjs` `F2` and `Ty.base_handle`). The Hub keeps sending on the NapCat
OneBot path and mirrors that mapping locally so a locally opened conversation
can be prepared for delivery:

- `integrations/qq/send_target.py` holds the single rule set: a 5-20 digit
  identifier is already an OneBot target, a private uid is mapped through the
  reader's own `nt_uid_mapping_table`/`profile_info_v6` uid column, and a
  group that is not already a group code is refused.
- `hub/server.py` resolves at prepare time and prefers a `peerUin`/`uin`
  already recorded in the index, then falls back to the reader's snapshot. The
  resolved number is what is stored, reviewed and delivered; the original
  local id is shown alongside it as `source_conversation_id` for review.
- Anything that stays non-numeric is refused with `send_target_private_unresolved`
  or `send_target_group_unresolved` before a send request is created. The Hub
  never guesses a target, and no online lookup is involved.
- Delivery remains the existing `QQAccountBoundSender` check: the NapCat
  account must be online and its `get_login_info` uin must equal the request's
  account id. Local reading and NapCat sending therefore stay bound to the
  same QQ account, which on this machine is the account directory name.

Group numbers require no mapping. Every indexed group peer already equals its
group code, matching NapCat's group behaviour.

## Read-Only Key Discovery

The QQ data-source page exposes an explicitly confirmed discovery action only
when the configured database needs a key. It checks QQNT executable paths and
the current Windows user's process owner, uses read/query-only handles, and
authenticates candidates against the encrypted first page before saving only
to current-user DPAPI-protected Hub configuration. One scan is allowed at a
time and is hard-bounded to 180 seconds, 20,000 candidates and 512 MiB total
across processes, including direct SQLCipher
context key/MAC reads used to authenticate candidates. The status API and UI never
return candidate material. It is not an automatic background job or MCP tool.
It never attaches a debugger, suspends QQ, injects code, dumps memory or logs
keys. Context matching is version-dependent: a failed/bounded search does not
prove that a key is absent. Original passphrases may have been cleared by the
client.

## Snapshots and Media

Encrypted database/WAL files are copied with bounded buffers and stable-file
checks. Each database page is authenticated; WAL checksums/salts are validated
and only committed frames enter the owned snapshot. Source files are not
written, checkpointed or truncated. Failed snapshots are not published.
Unknown elements remain explicit placeholders. Attachments use the selected
account's existing local cache and an owned copy, never online downloads.
QQ schema and media-cache formats can vary by client version.

The QQNT_Export schema/cache reference adds row-level group ID fallbacks,
repeated quoted elements, media MD5 variants and hashed local avatars. Peer/time
indexes are created only in the owned snapshot, never in QQ's database.
Avatar reads are bounded to eight contacts per batch and 2 MiB per file, stay
inside the selected account, and never fall back to remote downloads.

## ARK-1 Findings

QQNT-Database-Export-Tool (ARK-1) is the star-picker repository below. It needs
an existing pass key even in direct SQLCipher mode; it is not a key finder.
Its configuration agrees with this reader's SQLCipher parameters. Direct
decryption without a valid key has not been demonstrated.

The linked QQBackup Windows helper launches QQ under a debugger, patches an
INT3 breakpoint, reads the 16-character printable key (spaces are valid), and
terminates the launched process. That is outside read-only authorization.
Its static-only mode identifies code locations, not a key. Android UID/header
formulas are not assumed to work on Windows.

Following explicit user authorization, a separate local debugging workflow
captured and verified a key. First-page authentication, encrypted DB/WAL
snapshot validation and SQLite quick_check passed before it was saved only in
private Hub configuration. No key value, raw memory, source path, account
identifier or chat content is recorded here. Debugging is not an automatic
reader/MCP feature.

## Primary References

- [QQNT encryption parameters](https://github.com/DaizeDong/qq-history-recovery/blob/main/tools/qqnt_decrypt.py)
- [Numeric message schema](https://github.com/anpingjing/qqnt-chat-export/blob/main/scripts/export_messages.py)
- [SQLite WAL format](https://www.sqlite.org/fileformat2.html#walformat)
- [SQLCipher context definitions](https://github.com/sqlcipher/sqlcipher/blob/master/src/sqlcipher.c)
- [Windows extraction notes](https://github.com/QQBackup/QQDecrypt/blob/main/docs/decrypt/extract/NTQQ%20%28Windows%29.md)
- [Windows memory-read API](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory)
- [QQNT_Export schema/cache reference](https://github.com/Tealina28/QQNT_Export/tree/714d228dcd52f74b06bec91d4dba6ab856f16cd1)
- [ARK-1](https://github.com/star-picker/QQNT-Database-Export-Tool)
- [ARK-1 SQLCipher parameters](https://github.com/star-picker/QQNT-Database-Export-Tool/blob/main/decrypt.config)
- [QQBackup Windows key helper](https://github.com/QQBackup/qq-win-db-key/blob/master/scripts/windows/ntqq/windows_ntqq_get_key.ps1)

The read-only reader never invokes debugger/hook methods. Earlier bounded scans
returned `not_found` or `limit_reached`; those results did not verify a key or
prove that one was absent. Later, separately authorized local work verified a
key, protected it in Hub configuration, and completed a read-only history sync.
The current Hub reports the QQ source as connected and the database readable.
The latest approved discovery retry was short-circuited because the configured
key already authenticated the database; that retry did not scan process memory
or change configuration. Key discovery remains an explicit action when a key
is actually required; real attachment-cache coverage remains separate from
message-history availability.
