# Bundled WeChat reader

Python 3.12 x64 on Windows. Install the dependencies into the application Python
runtime with `python -m pip install -r integrations/wechat/requirements.txt`.
Keep WeChat logged in for initial key discovery. Later queries reuse validated
local keys. The provider does not start a separate service or WeFlow process.

```python
from integrations.wechat.provider import WeChatProvider

provider = WeChatProvider()
status = provider.status()
sessions = provider.sessions()
contacts = provider.contacts()
messages = provider.messages(sessions[0]["id"], limit=200, offset=0)
media = provider.media(messages[0]["session_id"], messages[0]["local_id"],
                       messages[0]["shard"])
```

- `status()` returns platform, state (`connected`/`partial`), database_count,
  unlocked_count, unavailable_databases (database names only), sessions count,
  and message_count (all message tables).
- `sessions(limit=100000, include_counts=True)` returns id, platform, title,
  kind (`group`/`direct`), unread, last_message, last_timestamp, message_count.
  Counts use aggregate SQL per table, across every message shard. They do not
  enumerate message bodies. `include_counts=False` avoids aggregate work.
- `contacts()` returns id, platform, name, nickname, alias.
- `messages(session_id, limit=200, offset=0)` returns newest first, with id,
  platform, session_id, sender_id, is_self, timestamp (seconds), sort_seq, type
  (human-readable label), content, shard, local_id, and local_type (original
  numeric compound type). `id` includes conversation, shard, and local ID;
  local IDs repeat in different conversations and shards. `is_self` is null
  if the sender cannot be resolved.
- `message_count(session_id)` returns an integer count across message shards.
- `message_counts()` returns a conversation-MD5-to-count dictionary for every
  message table, including chats not visible in the current session list.
- `media(session_id, local_id, shard)` returns available (bool), path (absolute
  local path or null), reason (null or machine-readable string). It extracts
  already-cached images, voice, video, or files. Cached SILK voices are locally
  decoded to mono 24kHz WAV for browser playback (`playable=True`,
  `mime_type=audio/wav`); `original_path` preserves the SILK. Decoder failure
  retains the original path with `playable=False` and a `voice_decode_failed`
  or `voice_decoder_unavailable` reason. It never drives the GUI.

Local keys and discovered account settings stay in ignored `.local/wechat.json`;
decrypted databases, media and the private development venv stay under ignored
`integrations/wechat/runtime/`. Do not log returned chat records or key files.

For a non-sensitive diagnostic run: `python -m integrations.wechat.verify`.
For local JSON RPC style CLI calls: `python -m integrations.wechat.provider status`.
The sessions/messages/contacts CLI commands print private data and are intended
only as internal process transport, not diagnostics.
