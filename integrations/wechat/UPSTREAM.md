# WeChat Read-Only Engine

Vendored `vendor/db.py` and `vendor/media.py` from
https://github.com/fanyuantaier/wechatauto-replica
at commit `d6e61bea7f9c75906faa8b71d37ef3572fba2454`, version 1.2.6.
License: Apache-2.0; the complete license is in `vendor/LICENSE`.

The upstream logger import is replaced with module-local sanitized diagnostics
to avoid loading UI automation and altering process-wide logging. Both vendored
modules discard private arguments, paths and exception details from diagnostic
logs and prints; query return values are untouched. No process-wide output
redirection is used. The provider subclasses
the reader to keep key material in the ignored `.local/wechat.json` file and
decrypted cache in this integration's ignored runtime folder. The provider only
uses read-only database and cached-media operations. No process injection, DLL
hooking, or client database writes are invoked by the provider or the reader.
The upstream media module has optional GUI helpers; the provider disables
`download_image_original` and does not import or expose those GUI dependencies.

Sending is a separate, default-off module: `sender.py` implements
`WeChatGUISender`, which drives the already-logged-in local WeChat 4.x client
through public Win32 `ctypes` calls (`EnumWindows`, `SetForegroundWindow`,
`SendInput`, `PrintWindow`) to identify an open conversation window and enter
text into it. It does not import the upstream `wechatauto` package, does not
inject into the WeChat process, does not hook DLLs, and never writes to the
client database. It reads only the target conversation's cached message rows to
verify that its own message was recorded. Unlike the reader, sending is a
write-side, human-mimicking action and therefore stays behind the
`wechat_send` capability and per-message local UI approval; the MCP surface
can prepare and inspect requests but never approves or delivers them.

The reader uses `ReadProcessMemory` with `PROCESS_VM_READ` and
`PROCESS_QUERY_INFORMATION`, validates candidates against SQLCipher page HMAC,
decrypts database snapshots and merges WAL into separate cache files, and opens
those cache files for read-only SQLite queries. Original databases are read only.

The native key DLL exposes `InitializeHook`, so it is not used. The bundled
source reader avoids dependence on closed native WCDB/key binaries.

The Hub media adapter disables the vendored image-AES process-memory scanner.
It uses only explicit/offline-derived keys or an account-scoped key cache under
the Hub runtime directory; image keys are not written to `wechat.json`. Image
decryption output also remains under that Hub runtime directory. WeChat's
database reader may still read process memory for SQLCipher database keys as
documented above; this is separate from image-key discovery.

The provider also resolves each sender from that message shard's `Name2Id`
table, as WeFlow's chat service does. It does not assume a fixed numeric sender
ID for the local account. Media IDs include the shard because local IDs repeat
across shards. Unified message IDs also include the conversation because local
IDs restart in each `Msg_<conversation>` table within the same shard.

Voice decoding uses https://github.com/MelodyYuuka/pilk-nogil version 0.4.3
(GPL-3.0), a compatible fork of https://github.com/foyoux/pilk. The application
installs its packaged Windows wheel into the bundled Python runtime; all SILK
decoding and WAV generation happen locally. License is distributed in the
package metadata. The original SILK is retained when a decoder is unavailable
or fails; neither the audio nor its content is sent to a service.
