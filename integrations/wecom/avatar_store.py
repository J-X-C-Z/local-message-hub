"""Read conversation avatars from a verified WeCom snapshot database."""

from pathlib import Path
import sqlite3


MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_TOTAL_BYTES = 32 * 1024 * 1024
MAX_AVATARS = 50_000


def read_conversation_avatars(decrypted_files, conversation_ids):
    """Return bounded avatar blobs for exact conversation IDs from this snapshot."""
    path = next((Path(value) for relative, value in decrypted_files.items()
                 if str(relative).replace("\\", "/").casefold() == "data/avatar_store_v3.db"), None)
    if path is None or path.is_symlink() or not path.is_file():
        return {}

    requested = list(dict.fromkeys(str(value).strip() for value in conversation_ids
                                   if value is not None and str(value).strip()))[:MAX_AVATARS]
    if not requested:
        return {}

    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        columns = tuple(row[1] for row in connection.execute(
            'PRAGMA table_info("conversation_avatar_table")'))
        if columns != ("conversation_id", "hash", "buffer"):
            return {}

        avatars = {}
        total_bytes = 0
        for offset in range(0, len(requested), 500):
            batch = requested[offset:offset + 500]
            marks = ",".join("?" for _ in batch)
            rows = connection.execute(
                f'SELECT conversation_id, buffer FROM conversation_avatar_table '
                f'WHERE conversation_id IN ({marks}) AND length(buffer) BETWEEN 1 AND ?',
                (*batch, MAX_AVATAR_BYTES),
            )
            for conversation_id, raw in rows:
                key = str(conversation_id)
                if key in avatars or raw is None:
                    continue
                payload = bytes(raw)
                if total_bytes + len(payload) > MAX_AVATAR_TOTAL_BYTES:
                    return {}
                avatars[key] = payload
                total_bytes += len(payload)
        return avatars
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {}
    finally:
        if connection is not None:
            connection.close()
