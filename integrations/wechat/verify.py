"""Run local integration checks, printing counts only (never chat/key values)."""

from .provider import WeChatProvider
import argparse
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", action="store_true")
    args = parser.parse_args()
    provider = WeChatProvider()
    reader = provider.reader
    sessions = provider.sessions()
    contacts = provider.contacts()
    count = sum(provider.message_count(session["id"]) for session in sessions[:15])
    rows = [message for session in sessions[:8]
            for message in provider.messages(session["id"], limit=5)]
    assert len({row["id"] for row in rows}) == len(rows)
    rel = next(rel for rel, path, _ in reader._db_files if path.endswith("message_0.db"))
    conn = reader._open(rel)
    try:
        identity_tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Name2Id%'")]
        schemas = {table: [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                   for table in identity_tables}
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
        sender_stats = []
        for table in tables[:4]:
            sender_stats.append(conn.execute(
                f'SELECT count(*),sum(real_sender_id=0),sum(real_sender_id IS NULL),'
                f'sum(real_sender_id IN (SELECT rowid FROM Name2Id)) FROM "{table}"').fetchone())
    finally:
        conn.close()
    print({"sessions": len(sessions), "contacts": len(contacts),
           "sampled_message_count": count, "sender_table_schema": schemas,
           "sender_matching_counts": [list(row) for row in sender_stats],
           "checked_message_identities": len(rows),
           "unresolved_senders": sum(not row["sender_id"] for row in rows)})

    for session in sessions[:40]:
        candidates = [message for message in provider.messages(session["id"], limit=100)
                      if (message["local_type"] & 0xFFFFFFFF) == 3]
        if candidates:
            candidate = candidates[0]
            result = provider.media(session["id"], candidate["local_id"], candidate["shard"])
            print({"media_probe": "image", "available": result["available"],
                   "reason": result["reason"]})
            break

    if args.voice:
        found = False
        for session in sessions[:80]:
            candidates = [message for message in provider.messages(session["id"], limit=200)
                          if (message["local_type"] & 0xFFFFFFFF) == 34]
            for candidate in candidates[:2]:
                result = provider.media(session["id"], candidate["local_id"], candidate["shard"])
                if result.get("playable"):
                    with wave.open(result["path"], "rb") as audio:
                        print({"media_probe": "voice", "playable": True,
                               "sample_rate": audio.getframerate(),
                               "channels": audio.getnchannels(), "frames": audio.getnframes()})
                    found = True
                    break
            if found:
                break
        if not found:
            print({"media_probe": "voice", "playable": False, "reason": "no_cached_voice_in_sample"})


if __name__ == "__main__":
    main()
