#!/usr/bin/env python3
"""从解密后的企业微信(macOS)消息库做**结构化提取** → CSV/JSON。

输出每条：时间 / 会话(名) / 发送者(真名) / 类型 / 正文。
- 发送者：sender_id → Session.db `USER`(RID→name) 解析真名；系统/应用号标注。
- 类型：content_type(38种) 分类——文本/图片/语音/文件/卡片/系统/会议/文档链接；媒体显示 [图片]/[语音]/[文件]。
- 正文：文本类走 protobuf 递归抽取；会话名来自 CONVERSATION。
- 跳过 FTS 虚拟表(避免 icu tokenizer)。
用法: export_wxwork.py [--db 解密后的Info.db] [--out 目录]
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

try:
    from .local_io import connect_readonly, quote_identifier, configure_stdio
    from .protobuf_fields import fields, ordered_fields, single_text
except ImportError:
    from local_io import connect_readonly, quote_identifier, configure_stdio
    from protobuf_fields import fields, ordered_fields, single_text

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "decrypted", "Messages1", "Info.db")
OUTDIR = os.path.join(HERE, "export")
_SHADOW = ("_content", "_segdir", "_segments", "_docsize", "_idx", "_data", "_config", "_stat")

# content_type → 类型名（据实测样本归类；未知码回退 type<码>）
TYPE_LABEL = {
    0: "文本", 2: "文本", 6: "文本",
    13: "文档链接", 29: "表情", 4: "复合消息", 14: "图片", 15: "文件", 16: "语音",
    40: "音视频通话", 1073: "会议",
    38: "系统通知", 501: "系统", 503: "系统", 1011: "协作事件",
    1001: "事件", 1002: "事件", 1006: "事件", 1018: "事件", 1022: "事件", 1051: "事件", 1052: "事件",
    10: "卡片", 20: "卡片", 105: "卡片", 123: "卡片", 145: "卡片", 221: "卡片", 516: "卡片",
    561: "卡片", 565: "卡片", 570: "卡片", 573: "卡片", 579: "卡片", 580: "卡片", 581: "卡片", 582: "卡片",
}
# 二进制媒体：不强抽文本，直接给标签(+ URL 若有)
MEDIA = {14: "[图片]", 15: "[文件]", 16: "[语音]", 29: "[表情]", 40: "[音视频通话]"}
# 卡片/应用消息：内嵌 JSON，解析 标题+链接
CARD_TYPES = {10, 20, 105, 123, 145, 221, 516, 561, 565, 570, 573, 579, 580, 581, 582}
WINDOWS_TYPES = {**TYPE_LABEL, 6: "位置", 1018: "语音通话", 1001: "会议", 70: "待办", 215: "笔记",
                 580: "会议", 581: "会议", 582: "会议", 1011: "协作", 565: "协作", 570: "协作",
                 38: "系统", 1022: "系统", 1002: "系统", 1006: "系统", 1017: "系统", 1051: "系统",
                 1052: "系统", 1055: "系统", 1004: "系统", 671: "系统", 132: "系统", 21: "卡片", 22: "卡片"}
WINDOWS_MEDIA = {**MEDIA, 1018: "[语音通话]"}
WINDOWS_CARDS = CARD_TYPES | {21, 22}


def _cols(conn, table):
    return [r[1] for r in conn.execute('PRAGMA table_info(' + quote_identifier(table) + ')')]


def _pick(cols, *keys):
    low = {c.lower(): c for c in cols}
    for k in keys:                       # 精确列名优先(避免 content 命中 content_type)
        if k in low:
            return low[k]
    for k in keys:                       # 再子串匹配
        for lc, orig in low.items():
            if k in lc:
                return orig
    return None


def find_message_tables(conn):
    out = []
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
        if sql and sql.upper().lstrip().startswith("CREATE VIRTUAL"):
            continue
        if any(name.endswith(s) for s in _SHADOW):
            continue
        cols = _cols(conn, name)
        content_c = _pick(cols, "content", "message", "msg", "text", "body", "digest")
        time_c = _pick(cols, "send_time", "sendtime", "createtime", "timestamp", "msgtime", "time")
        sender_c = _pick(cols, "sender_id", "sender", "from", "talker", "sendid", "userid")
        if not (content_c and time_c and sender_c):   # 三要素, 排除会话/配置表
            continue
        try:
            if conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0:
                continue
        except Exception:
            continue
        out.append((name, cols, content_c, time_c, sender_c))
    return out


def _load_conv_names(conn):
    m = {}
    try:
        cols = _cols(conn, "CONVERSATION")
    except Exception:
        return m
    if not cols:
        return m
    namecol = next((c for c in cols if c.lower() == "name"), None)
    idcols = [c for c in cols if c.upper() in ("RID", "LID", "CONV_ID", "CONVID")]
    if not namecol or not idcols:
        return m
    for r in conn.execute('SELECT * FROM "CONVERSATION"'):
        d = dict(zip(cols, r))
        nm = d.get(namecol)
        if isinstance(nm, bytes):
            try:
                nm = nm.decode("utf-8")
            except UnicodeDecodeError:
                nm = None
        if nm and isinstance(nm, str) and nm.strip():
            for ic in idcols:
                if isinstance(d.get(ic), int) and d[ic]:
                    m.setdefault(d[ic], nm.strip())
    return m


def _load_user_names(info_db):
    """sender_id → 真名，来自同目录 Session.db 的 USER(RID→name)。"""
    p = os.path.join(os.path.dirname(info_db), "Session.db")
    m = {}
    if not os.path.exists(p):
        return m
    try:
        c = connect_readonly(p)
        cols = [r[1] for r in c.execute("PRAGMA table_info(USER)")]
        if "RID" in cols and "name" in cols:
            for rid, name in c.execute("SELECT RID, name FROM USER"):
                if name:
                    m[rid] = name if isinstance(name, str) else name.decode("utf-8", "replace")
        c.close()
    except Exception:
        pass
    return m


def resolve_sender(sid, users):
    name = users.get(sid)
    if name:
        return name
    try:
        n = int(sid)
    except (TypeError, ValueError):
        return str(sid)
    if 0 < n < 1_000_000:
        return f"[系统/应用 {n}]"
    return str(n)


# ── protobuf 文本抽取 ────────────────────────────────────────────────
def _rv(d, p):
    v = s = 0
    while p < len(d) and s < 64:
        b = d[p]; p += 1; v |= (b & 0x7F) << s
        if not b & 0x80:
            return v, p
        s += 7
    raise ValueError


def _seg_text(seg):
    if not seg or b"\x00" in seg:
        return None
    try:
        t = seg.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(not ch.isprintable() and ch not in "\r\n\t" for ch in t):
        return None
    if not t or re.fullmatch(r"[0-9a-fA-F]{32,}", t):
        return None
    return t


def _pb_strings(d, depth=0):
    if depth > 4 or not d:
        return []
    out = []
    try:
        for _, seg in ordered_fields(d):
            if not isinstance(seg, bytes):
                continue
            rec = _pb_strings(seg, depth + 1)   # 优先钻到最内层文本(去框架字节)
            if rec:
                out.extend(rec)
            else:
                t = _seg_text(seg)
                if t:
                    out.append(t)
    except ValueError:
        return []
    return out


def decode_text(raw):
    """抽取可读文本；纯二进制返回 ''。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    d = bytes(raw)
    if not d:
        return ""
    ss = _pb_strings(d)
    if ss:
        return "\n".join(ss)
    try:
        text = d.decode("utf-8")
        if all(ch.isprintable() or ch in "\r\n\t" for ch in text):
            return text
    except UnicodeDecodeError:
        pass
    return ""


_URL = re.compile(rb"https?://[\w./%?=&#:+~@!$',;*-]+")


def _first_url(raw, skip_assets=True):
    if not isinstance(raw, (bytes, bytearray)):
        return ""
    for m in _URL.finditer(bytes(raw)):
        u = m.group().decode("utf-8", "replace")
        if skip_assets and ("wwcdn." in u or "/images/" in u or u.endswith((".png", ".bin", ".jpg"))):
            continue
        return u
    return ""


def _filename(raw):
    if not isinstance(raw, (bytes, bytearray)):
        return ""
    try:
        name = single_text(fields(raw), 2)
        if name and not any(ch in name for ch in ("/", "\\", "\x00")):
            return name
    except ValueError:
        pass
    best = ""
    for m in re.findall(rb"(?:[\x20-\x7e]|[\xe4-\xe9][\x80-\xbf][\x80-\xbf]){4,}", bytes(raw)):
        s = m.decode("utf-8", "replace").strip()
        s = re.sub(r"^[^\w一-鿿]+", "", s)         # 去 protobuf 框架前缀字符
        if not s or s.startswith(("http", "www", "30")):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{12,}", s.replace(" ", "")):
            continue
        score = (100 if "." in s else 0) + len(s)
        if score > (100 if "." in best else 0) + len(best):
            best = s
    return best


def _extract_json(b):
    """从 blob 里抠出第一个平衡的 JSON 对象（卡片正文内嵌）。"""
    i = b.find(b"{")
    while i != -1:
        depth = 0; instr = False; esc = False
        for j in range(i, min(len(b), i + 30000)):
            c = b[j]
            if esc:
                esc = False; continue
            if instr:
                if c == 0x5C: esc = True
                elif c == 0x22: instr = False
                continue
            if c == 0x22: instr = True
            elif c == 0x7B: depth += 1
            elif c == 0x7D:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(b[i:j + 1].decode("utf-8", "replace"))
                    except Exception:
                        break
        i = b.find(b"{", i + 1)
    return None


def _card_title(j):
    if not isinstance(j, dict):
        return ""
    for k in ("card_title", "main_title", "title"):
        v = j.get(k)
        if isinstance(v, dict) and v.get("title"):
            return str(v["title"])
        if isinstance(v, str) and v:
            return v
    return ""


def render(content_type, raw, platform="macos"):
    """按类型呈现正文：媒体→标签(+URL)；卡片→标题+链接；文本/系统→抽取文本。"""
    try:
        ct = int(content_type or 0)
    except (TypeError, ValueError):
        ct = 0
    txt = decode_text(raw)
    media = WINDOWS_MEDIA if platform == "windows" else MEDIA
    cards = WINDOWS_CARDS if platform == "windows" else CARD_TYPES
    if ct == 15:                               # 文件: 提文件名
        fn = _filename(raw)
        return f"[文件] {fn}".strip() if fn else "[文件]"
    if ct == 13:                               # 文档链接: 标题+链接
        url = _first_url(raw)
        title = txt.split("http")[0].strip().split("\n")[0][:60] if txt else ""
        return ("[文档] " + " ".join(p for p in (title, url) if p)).strip()
    if ct in media:
        # CDN URLs can contain credentials and are not the human message body.
        return media[ct]
    if ct in cards:
        j = _extract_json(bytes(raw)) if isinstance(raw, (bytes, bytearray)) else None
        title = _card_title(j)
        # Unknown card envelopes may contain transfer credentials, local device
        # paths and CDN URLs. Only explicit card title fields are display text.
        url = _first_url(raw)
        if url and (not j or not title):
            url = ''
        s = "[卡片] " + title if title else "[卡片]"
        return (s + (" " + url if url else "")).strip()
    if txt:
        return txt
    return f"[{TYPE_LABEL.get(ct, '类型' + str(ct))}]"


def type_name(content_type, platform="macos"):
    try:
        ct = int(content_type or 0)
    except (TypeError, ValueError):
        return str(content_type)
    labels = WINDOWS_TYPES if platform == "windows" else TYPE_LABEL
    return labels.get(ct, f"type{ct}")


def fmt_time(ts):
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    if ts > 20_000_000_000:
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return str(ts)


def main():
    configure_stdio()
    import argparse
    try:
        from .local_query import MessageStore, validate_window
        from .transcript_export import export_all, export_chat
        from .macos import wecom_paths
    except ImportError:
        from local_query import MessageStore, validate_window
        from transcript_export import export_all, export_chat
        from macos import wecom_paths
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=wecom_paths.decrypted("Messages1", "Info.db"))
    parser.add_argument("--out", default=os.path.join(HERE, "macos", "export"))
    parser.add_argument("--conversation")
    parser.add_argument("--days", type=int)
    parser.add_argument("--format", choices=("txt", "markdown", "html", "json", "csv"))
    parser.add_argument("--with-media", action="store_true")
    parser.add_argument("--cache-dir")
    options = parser.parse_args()
    try:
        validate_window(days=options.days)
        if (options.with_media or options.format) and not options.conversation:
            raise ValueError("--with-media/--format 需要 --conversation；--out 为导出文件路径")
        conn = connect_readonly(options.db)
        try:
            store = MessageStore(conn, users=_load_user_names(options.db))
            if options.conversation:
                result = export_chat(store, options.db, options.conversation, options.out,
                                     days=options.days, output_format=options.format,
                                     with_media=options.with_media,
                                     cache_root=(options.cache_dir or wecom_paths.caches()) if options.with_media else None)
            else:
                result = export_all(store, options.out, days=options.days, chinese_headers=True)
        finally:
            conn.close()
    except (ValueError, sqlite3.Error) as e:
        sys.exit(str(e))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
