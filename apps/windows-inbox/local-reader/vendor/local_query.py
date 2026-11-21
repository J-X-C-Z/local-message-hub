"""Shared bounded queries over the native macOS / Windows message schemas."""

import argparse
import time

try:
    from . import export_wxwork as ex
    from .local_io import quote_identifier
except ImportError:
    import export_wxwork as ex
    from local_io import quote_identifier

MAX_LIMIT = 2000


def validate_window(limit=None, offset=0, days=None):
    if limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_LIMIT):
        raise ValueError("limit 必须是 1..2000 的整数")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if days is not None and (type(days) is not int or days <= 0):
        raise ValueError("days 必须是正整数；不传则读取全部日期")


def parse_window(args, *, positional=None, export=False):
    parser = argparse.ArgumentParser(add_help=False)
    if positional:
        parser.add_argument(positional)
    parser.add_argument("--days", type=int)
    if not export:
        parser.add_argument("--limit", type=int)
        parser.add_argument("--offset", type=int, default=0)
    else:
        parser.add_argument("--out", required=True)
        parser.add_argument("--format", choices=("txt", "markdown", "html", "json", "csv"))
        parser.add_argument("--with-media", action="store_true")
    options = parser.parse_args(args)
    validate_window(getattr(options, "limit", None), getattr(options, "offset", 0), options.days)
    if positional and not getattr(options, positional).strip():
        raise ValueError(positional + " 不能为空")
    return options


def resolve_conversation(query, conversations):
    """Prefer an ID, then an exact name. Never merge multiple name matches."""
    query = str(query).strip()
    if not query:
        raise ValueError("conversation 不能为空")
    by_id = [c for c in conversations if str(c["id"]) == query]
    exact = [c for c in conversations if str(c["name"]) == query]
    candidates = by_id or exact or [c for c in conversations if query.casefold() in str(c["name"]).casefold()]
    if not candidates:
        raise ValueError(f"没找到会话 '{query}'")
    if len(candidates) > 1:
        choices = "; ".join(f"{c['name']} (ID={c['id']})" for c in candidates)
        raise ValueError("会话匹配不唯一，请使用会话 ID: " + choices)
    return candidates[0]


class MessageStore:
    def __init__(self, conn, *, users=None, conversations=None, platform="macos", sender=None):
        self.conn = conn
        if not conn.in_transaction:
            conn.execute("BEGIN")
        self.platform = platform
        self.users = users or {}
        self.names = conversations if conversations is not None else ex._load_conv_names(conn)
        self.sender = sender or (lambda sid: ex.resolve_sender(sid, self.users))
        self.table = "message_table" if platform == "windows" else "MESSAGE"
        columns = ex._cols(conn, self.table)
        self.conv = "conversation_id" if platform == "windows" else "conv_id"
        if not {self.conv, "sender_id", "content_type", "send_time", "content"}.issubset(columns):
            raise ValueError("消息表 schema 不受支持，缺少会话/发送者/类型/时间/正文列")
        self.local_id = "message_id" if platform == "windows" else "LID"
        if self.local_id not in columns:
            raise ValueError("消息表缺少稳定的本地消息 ID")
        self.server_id = "server_id" if platform == "windows" else "RID"
        self.has_server_id = self.server_id in columns

    def conversations(self):
        q = (f"SELECT {quote_identifier(self.conv)}, COUNT(*), MAX(send_time) "
             f"FROM {quote_identifier(self.table)} GROUP BY {quote_identifier(self.conv)}")
        rows = [{"id": str(cid), "name": str(self.names.get(cid, self.names.get(str(cid), cid))),
                 "messages": count, "last_timestamp": stamp or 0,
                 "last_time": ex.fmt_time(stamp)} for cid, count, stamp in self.conn.execute(q)]
        return sorted(rows, key=lambda r: (-r["last_timestamp"], r["id"]))

    def conversation_page(self, limit=None, offset=0):
        validate_window(limit, offset)
        rows = self.conversations()
        return page(rows, len(rows), limit, offset, "conversations")

    def target(self, conversation):
        return resolve_conversation(conversation, self.conversations())

    def iter_messages(self, *, conversation=None, days=None, descending=False):
        validate_window(days=days)
        where, params = [], []
        if conversation is not None:
            where.append(quote_identifier(self.conv) + "=?")
            params.append(conversation)
        if days is not None:
            # Native send_time may be seconds or milliseconds.
            where.append("(CASE WHEN send_time>20000000000 THEN send_time/1000.0 ELSE send_time END)>=?")
            params.append(time.time() - days * 86400)
        selected = [self.local_id, self.conv, "sender_id", "content_type", "send_time", "content"]
        if self.has_server_id:
            selected.append(self.server_id)
        sql = "SELECT " + ",".join(quote_identifier(c) for c in selected)
        sql += " FROM " + quote_identifier(self.table)
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = "DESC" if descending else "ASC"
        sql += f" ORDER BY send_time {order}, {quote_identifier(self.local_id)} {order}"
        for row in self.conn.execute(sql, params):
            data = dict(zip(selected, row))
            cid, sid, ct = data[self.conv], data["sender_id"], data["content_type"]
            yield {
                "table": self.table, "local_id": data[self.local_id],
                "server_id": data.get(self.server_id), "conv_id": str(cid),
                "conversation": str(self.names.get(cid, self.names.get(str(cid), cid))),
                "sender_id": sid, "sender": str(self.sender(sid)),
                "send_time": data["send_time"], "time": ex.fmt_time(data["send_time"]),
                "type_code": ct, "type": ex.type_name(ct, self.platform),
                "content": ex.render(ct, data["content"], self.platform), "_raw": data["content"],
            }

    def search(self, keyword, *, limit=None, offset=0, days=None, conversation=None):
        validate_window(limit, offset, days)
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("keyword 不能为空")
        target = self.target(conversation) if conversation else None
        needle = keyword.casefold()
        rows, total = [], 0
        for row in self.iter_messages(conversation=target["id"] if target else None, days=days):
            if not any(needle in str(row[k]).casefold() for k in ("content", "sender", "conversation", "type")):
                continue
            if total >= offset and (limit is None or len(rows) < limit):
                rows.append(public_message(row))
            total += 1
        result = page(rows, total, limit, offset, "messages", sliced=True)
        result.update(keyword=keyword, days=days)
        return result

    def read_chat(self, conversation, *, limit=50, offset=0, days=None):
        validate_window(limit, offset, days)
        target = self.target(conversation)
        rows, total = [], 0
        for row in self.iter_messages(conversation=target["id"], days=days, descending=True):
            if total >= offset and (limit is None or len(rows) < limit):
                rows.append(public_message(row))
            total += 1
        result = page(rows, total, limit, offset, "messages", sliced=True)
        result.update(conversation=target, days=days)
        return result


def public_message(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


def page(rows, total, limit, offset, key, *, sliced=False):
    selected = rows if sliced else rows[offset:offset + limit] if limit is not None else rows[offset:]
    following = offset + len(selected)
    return {"count": len(selected), "total": total, "limit": limit, "offset": offset,
            "next_offset": following if following < total else None, key: selected}


def print_messages(result):
    for row in result["messages"]:
        print(f"[{row['time']}] {row['conversation']} | {row['sender']} [{row['type']}]")
        print(row["content"])
        print()
    print(f"返回 {result['count']} 条，共 {result['total']} 条；下一页 offset={result['next_offset']}")
