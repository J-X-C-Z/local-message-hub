"""聊天记录提取 v2：正确解析 protobuf 正文路径。

正文结构（content_type 0）：
  0a <len>            field1
    08 <varint>       field1: flag
    12 <len>          field2
      0a <len> <utf8> field1: 正文文本
      (可选) 0a <len> <utf8>  # 表情/引用等
"""
import sqlite3, os, sys, datetime, re
from .config import load_local_config, local_data_dir

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = load_local_config()['extract_paths']['root']
MSG = load_local_config()['extract_paths']['messages']
SES = load_local_config()['extract_paths']['sessions']
USR = load_local_config()['extract_paths']['users']
OUT = os.path.join(local_data_dir(), 'extracted')
os.makedirs(OUT, exist_ok=True)


def conn(p):
    return sqlite3.connect('file:' + p + '?mode=ro', uri=True)


def read_varint(b, i):
    v = 0; sh = 0
    while i < len(b):
        c = b[i]; i += 1
        v |= (c & 0x7F) << sh; sh += 7
        if not (c & 0x80):
            return v, i
    raise ValueError('eof')


def parse_fields(b):
    """返回 [(field_no, wire_type, value)]；value: varint→int, len→bytes"""
    out = []
    i = 0
    while i < len(b):
        try:
            key, i = read_varint(b, i)
        except ValueError:
            break
        if key == 0:
            break
        fn, wt = key >> 3, key & 7
        if wt == 0:
            try:
                v, i = read_varint(b, i)
            except ValueError:
                break
            out.append((fn, wt, v))
        elif wt == 2:
            try:
                ln, i = read_varint(b, i)
            except ValueError:
                break
            if ln < 0 or i + ln > len(b):
                break
            out.append((fn, wt, b[i:i+ln])); i += ln
        elif wt == 5:
            i += 4
        elif wt == 1:
            i += 8
        else:
            break
    return out


def looks_text(s):
    if not s:
        return False
    cjk = sum(1 for ch in s if '\u4e00' <= ch <= '\u9fff')
    if cjk >= 1:
        return True
    printable = sum(1 for ch in s if ch.isprintable() and not ('\x00' <= ch < ' ')
                    or ch in '\n\t')
    return len(s) >= 2 and printable / max(len(s), 1) > 0.9


def deep_texts(b, depth=0, path=()):
    """递归收集 (depth, path, text)"""
    res = []
    if depth > 8:
        return res
    for fn, wt, val in parse_fields(b):
        if wt != 2:
            continue
        try:
            s = val.decode('utf-8')
            if looks_text(s):
                res.append((depth, path + (fn,), s))
        except UnicodeDecodeError:
            pass
        res += deep_texts(val, depth + 1, path + (fn,))
    return res


def best_text(buf, ctype=None):
    if buf is None:
        return ''
    if isinstance(buf, str):
        return buf.strip()
    b = bytes(buf)
    # 直连：field1 → field2 → field1
    try:
        f1 = [v for fn, wt, v in parse_fields(b) if fn == 1 and wt == 2]
        if f1:
            f2 = [v for fn, wt, v in parse_fields(f1[0]) if fn == 2 and wt == 2]
            if f2:
                f3 = [v for fn, wt, v in parse_fields(f2[0]) if fn == 1 and wt == 2]
                if f3:
                    s = f3[0].decode('utf-8')
                    if s.strip():
                        return s.strip()
    except Exception:
        pass
    # 回退：最深且含中文的
    cands = deep_texts(b)
    if not cands:
        try:
            s = b.decode('utf-8', 'replace')
            return s.strip()
        except Exception:
            return ''
    cands.sort(key=lambda x: (-x[0], -sum(1 for ch in x[2] if '\u4e00' <= ch <= '\u9fff')))
    return cands[0][2].strip()


CTYPE = {
    0: '文本', 2: '图片', 4: '文件', 13: '链接', 14: '小程序', 15: '图文',
    20: '位置', 22: '名片', 23: '语音', 29: '引用', 36: '视频',
    1001: '系统', 1002: '系统', 1011: '撤回', 1022: '会议',
    1052: '合并转发', 1055: '日程', 2101: '公告',
}


def load_names():
    names = {}
    try:
        c = conn(USR)
        for (n,) in c.execute("select name from sqlite_master where type='table'"):
            cols = [r[1] for r in c.execute(f'pragma table_info("{n}")')]
            idc = next((x for x in cols if x.lower() in ('user_id', 'vid', 'id', 'uin')), None)
            nc = next((x for x in cols if x.lower() in ('name', 'nick_name', 'nickname',
                                                        'display_name', 'english_name')), None)
            if idc and nc:
                try:
                    for i, nm in c.execute(f'select "{idc}","{nc}" from "{n}"'):
                        if i is not None and nm:
                            names.setdefault(str(i), nm)
                except Exception:
                    pass
        c.close()
    except Exception as e:
        print('user.db:', e)
    return names


def load_convs():
    convs = {}
    try:
        c = conn(SES)
        for (n,) in c.execute("select name from sqlite_master where type='table'"):
            if 'conversation' not in n.lower():
                continue
            cols = [r[1] for r in c.execute(f'pragma table_info("{n}")')]
            idc = next((x for x in cols if x.lower() in ('conversation_id', 'conv_id', 'id')), None)
            nc = next((x for x in cols if x.lower() in ('name', 'title', 'conversation_name',
                                                        'nick_name', 'display_name')), None)
            if idc and nc:
                try:
                    for i, nm in c.execute(f'select "{idc}","{nc}" from "{n}"'):
                        if i and nm and str(nm).strip():
                            convs.setdefault(str(i), nm)
                except Exception:
                    pass
        c.close()
    except Exception as e:
        print('session.db:', e)
    return convs


def main():
    names = load_names()
    convs = load_convs()
    print(f"用户名 {len(names)} / 会话名 {len(convs)}")

    c = conn(MSG)
    rows = list(c.execute(
        'select message_id,server_id,sender_id,conversation_id,content_type,send_time,content '
        'from message_table order by send_time'))
    print(f"消息 {len(rows)} 条\n")

    lines = []
    skipped = 0
    for mid, sid, sender, conv, ctype, st, content in rows:
        txt = best_text(content, ctype)
        txt = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', txt).strip()
        if not txt:
            tag = CTYPE.get(ctype, f'type{ctype}')
            txt = f'[{tag}]'
            skipped += 1
        try:
            ts = datetime.datetime.fromtimestamp(st).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ts = str(st)
        cname = convs.get(str(conv), str(conv))
        sname = names.get(str(sender), str(sender))
        lines.append((st, f"[{ts}] ({cname}) {sname}: {txt}"))

    outp = os.path.join(OUT, '聊天记录.txt')
    with open(outp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(l for _, l in lines))
    print(f"已写 {outp}（{len(lines)} 条，其中 {skipped} 条非文本）")

    # 按会话分组
    byc = {}
    for _, l in lines:
        m = re.match(r'\[\S+ \S+\] \((.*?)\) ', l)
        byc.setdefault(m.group(1) if m else '未知', []).append(l)
    with open(os.path.join(OUT, '按会话分组.txt'), 'w', encoding='utf-8') as f:
        for k, v in sorted(byc.items(), key=lambda x: -len(x[1])):
            f.write(f"\n{'='*70}\n会话: {k}  ({len(v)} 条)\n{'='*70}\n")
            f.write('\n'.join(v) + '\n')
    print(f"按会话分组: {len(byc)} 个会话")

    with open(os.path.join(OUT, '会话清单.txt'), 'w', encoding='utf-8') as f:
        for k, v in sorted(convs.items()):
            f.write(f"{k}\t{v}\n")
    with open(os.path.join(OUT, '用户名清单.txt'), 'w', encoding='utf-8') as f:
        for k, v in sorted(names.items()):
            f.write(f"{k}\t{v}\n")

    print('\n--- 预览 ---')
    for _, l in lines[:15]:
        print(l[:180])


if __name__ == '__main__':
    main()
