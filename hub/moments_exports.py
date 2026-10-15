"""Safe, standalone renderers for local WeChat Moments exports."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape as xml_escape


FORMATS = {
    'json': ('application/json; charset=utf-8', 'json'),
    'csv': ('text/csv; charset=utf-8', 'csv'),
    'md': ('text/markdown; charset=utf-8', 'md'),
    'txt': ('text/plain; charset=utf-8', 'txt'),
    'html': ('text/html; charset=utf-8', 'html'),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'),
}
_CST = timezone(timedelta(hours=8))
_FORMULA = re.compile(r'^(?:[=+@-]|[\t\r\n]|[ \ufeff]+[=+@-])')
_MEDIA_SAFE_KEYS = ('type', 'local_available')
_POST_SAFE_KEYS = ('id', 'tid', 'username', 'nickname', 'createTime', 'contentDesc', 'type')
_COMMENT_SAFE_KEYS = ('id', 'nickname', 'username', 'content', 'refCommentId')
_LOCATION_SAFE_KEYS = ('city', 'country', 'poiName', 'poiAddress', 'poiAddressName', 'label',
                       'latitude', 'longitude')
_SOURCE_SAFE_KEYS = ('platform', 'database', 'scope')
_RESULT_SAFE_KEYS = ('available', 'catalogued', 'total', 'limit', 'offset', 'account_id')


class MomentsExportTooLarge(ValueError):
    pass


def _stamp(value):
    try:
        value = int(value or 0)
        return datetime.fromtimestamp(value, _CST).strftime('%Y-%m-%d %H:%M:%S') if value else ''
    except (TypeError, ValueError, OverflowError, OSError):
        return ''


def _safe_post(post):
    record = {key: post[key] for key in _POST_SAFE_KEYS if key in post}
    record['media'] = [{key: media.get(key, False if key == 'local_available' else 'image')
                        for key in _MEDIA_SAFE_KEYS} for media in post.get('media', [])
                    if isinstance(media, dict)]
    record['likes'] = [value for value in post.get('likes', []) if isinstance(value, (str, int, float))]
    record['comments'] = [
        {key: comment[key] for key in _COMMENT_SAFE_KEYS if key in comment}
        for comment in post.get('comments', []) if isinstance(comment, dict)
    ]
    location = post.get('location')
    record['location'] = ({key: location[key] for key in _LOCATION_SAFE_KEYS if key in location}
                          if isinstance(location, dict) else None)
    return record


def _safe_source(source):
    if not isinstance(source, dict):
        source = {}
    defaults = {'platform': 'wechat', 'database': 'sns.db', 'scope': 'local'}
    return {key: source.get(key, defaults[key]) for key in _SOURCE_SAFE_KEYS}


def _safe_result_metadata(result, source):
    metadata = {key: result[key] for key in _RESULT_SAFE_KEYS if key in result}
    metadata['source'] = source
    return metadata


def _text(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return str(value)


def _csv_cell(value):
    value = _text(value)
    return "'" + value if _FORMULA.match(value) else value


def _flat_row(post, source):
    location = post.get('location') or {}
    return [
        source.get('platform'), source.get('database'), source.get('scope'),
        _stamp(post.get('createTime')), post.get('username'), post.get('nickname'),
        post.get('contentDesc'), post.get('type'),
        ' / '.join(filter(None, (location.get('poiName'), location.get('poiAddress'),
                                  location.get('city'), location.get('country')))),
        len(post.get('media') or []), '、'.join(map(_text, post.get('likes') or [])),
        ' | '.join(f"{item.get('nickname') or item.get('username') or ''}: {item.get('content') or ''}"
                   for item in post.get('comments') or []),
    ]


def _excel_column(number):
    label = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _xlsx_text(value):
    text = _text(value)
    text = ''.join(char for char in text if char in '\t\n\r' or
                   0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or
                   0x10000 <= ord(char) <= 0x10FFFF)
    units = 0
    clipped = []
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > 32767:
            break
        clipped.append(char)
        units += width
    return xml_escape(''.join(clipped))


def _xlsx_entry(name):
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    return entry


class _CappedBytesIO(io.BytesIO):
    def __init__(self, max_bytes):
        super().__init__()
        self.max_bytes = max_bytes

    def write(self, data):
        if max(self.getbuffer().nbytes, self.tell() + len(data)) > self.max_bytes:
            raise MomentsExportTooLarge
        return super().write(data)


def _render_xlsx(items, source, max_bytes=32 * 1024 * 1024):
    buffer = _CappedBytesIO(max_bytes)
    files = {
        '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="朋友圈" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    }
    headers = ['来源平台', '来源数据库', '范围', '时间', '微信号', '昵称', '正文',
               '类型', '地点', '媒体数', '点赞', '评论']
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(_xlsx_entry(path), content)
        with archive.open(_xlsx_entry('xl/worksheets/sheet1.xml'), 'w') as sheet:
            sheet.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
            for row_index, values in enumerate([headers, *_metadata_rows(source)], 1):
                _write_xlsx_row(sheet, row_index, values)
            row_index = len([headers, *_metadata_rows(source)])
            for post in items:
                row_index += 1
                _write_xlsx_row(sheet, row_index, _flat_row(post, source))
            sheet.write(b'</sheetData></worksheet>')
    return buffer.getvalue()


def _metadata_rows(source):
    return (['导出工具', 'Local Message Hub'],
            ['来源', f"{source.get('platform')} / {source.get('database')} / {source.get('scope')}"])


def _write_xlsx_row(sheet, row_index, values):
    cells = []
    for column_index, value in enumerate(values, 1):
        reference = f'{_excel_column(column_index)}{row_index}'
        cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{_xlsx_text(value)}</t></is></c>')
    sheet.write((f'<row r="{row_index}">{"".join(cells)}</row>').encode('utf-8'))


def render_moments_export(result, format='json'):
    """Return (bytes, content type, extension), stripping all media credentials."""
    if format not in FORMATS:
        raise ValueError('不支持的朋友圈导出格式')
    items = [_safe_post(post) for post in result.get('items', [])]
    source = _safe_source(result.get('source'))
    content_type, extension = FORMATS[format]
    if format == 'xlsx':
        return _render_xlsx(items, source), content_type, extension
    if format == 'json':
        payload = {**_safe_result_metadata(result, source), 'items': items}
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    elif format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output, lineterminator='\r\n')
        writer.writerow(['来源平台', '来源数据库', '范围', '时间', '微信号', '昵称', '正文',
                         '类型', '地点', '媒体数', '点赞', '评论'])
        writer.writerows([_csv_cell(value) for value in _flat_row(post, source)] for post in items)
        rendered = output.getvalue()
    elif format in ('md', 'txt'):
        lines = ['# 微信朋友圈导出' if format == 'md' else '微信朋友圈导出',
                 '', f"来源：{source.get('platform')} / {source.get('database')} / {source.get('scope')}", '']
        for post in items:
            author = post.get('nickname') or post.get('username') or '未知作者'
            timestamp = _stamp(post.get('createTime'))
            title = f"{author} · {timestamp}" if timestamp else author
            lines.extend([f'## {title}' if format == 'md' else f'[{title}]',
                          _text(post.get('contentDesc'))])
            row = _flat_row(post, source)
            if row[8]:
                lines.append(f"地点：{row[8]}")
            if row[9]:
                lines.append(f"媒体：{row[9]} 项（未导出远程地址或密钥）")
            if row[10]:
                lines.append(f"点赞：{row[10]}")
            for comment in post.get('comments') or []:
                name = comment.get('nickname') or comment.get('username') or '评论'
                lines.append(f"- {name}：{comment.get('content') or ''}" if format == 'md'
                             else f"评论 {name}：{comment.get('content') or ''}")
            lines.append('')
        rendered = '\n'.join(lines)
    else:
        rows = []
        for post in items:
            author = html.escape(_text(post.get('nickname') or post.get('username') or '未知作者'))
            timestamp = html.escape(_stamp(post.get('createTime')))
            content = html.escape(_text(post.get('contentDesc'))).replace('\n', '<br>')
            row = _flat_row(post, source)
            comments = ''.join(
                f"<li><strong>{html.escape(_text(item.get('nickname') or item.get('username') or '评论'))}</strong>："
                f"{html.escape(_text(item.get('content')))}</li>" for item in post.get('comments') or [])
            rows.append(f'<article><h2>{author}</h2><time>{timestamp}</time><p>{content}</p>'
                         f'<p>地点：{html.escape(row[8])} · 媒体：{row[9]} 项</p>'
                         f'<p>点赞：{html.escape(row[10])}</p><ul>{comments}</ul></article>')
        rendered = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width"><title>微信朋友圈导出</title>'
                    '<style>body{font:15px/1.7 system-ui,sans-serif;max-width:820px;margin:32px auto;padding:0 18px}'
                    'article{padding:18px 0;border-bottom:1px solid #ddd}time{color:#667}</style>'
                    '<h1>微信朋友圈导出</h1><p>来源：' + html.escape(
                        f"{source.get('platform')} / {source.get('database')} / {source.get('scope')}")
                    + '</p>' + ''.join(rows) + '</html>')
    return rendered.encode('utf-8'), content_type, extension


def render_moments_export_bounded(result, format='json', max_bytes=32 * 1024 * 1024):
    """Serialize an item iterable without ever retaining more than max_bytes output."""
    if format not in FORMATS:
        raise ValueError('不支持的朋友圈导出格式')
    output = bytearray()

    def write(value):
        chunk = value if isinstance(value, bytes) else str(value).encode('utf-8')
        if len(output) + len(chunk) > max_bytes:
            raise MomentsExportTooLarge
        output.extend(chunk)

    source = _safe_source(result.get('source'))
    content_type, extension = FORMATS[format]
    items = result.get('items', ())
    if format == 'xlsx':
        return memoryview(_render_xlsx(items, source, max_bytes)), content_type, extension
    if format == 'json':
        metadata = _safe_result_metadata(result, source)
        prefix = json.dumps(metadata, ensure_ascii=False, indent=2)
        write(prefix[:-1].rstrip() + ',\n  "items": [')
        first = True
        for post in items:
            encoded = json.dumps(_safe_post(post), ensure_ascii=False, indent=2)
            write(('' if first else ',') + '\n    ' + encoded.replace('\n', '\n    '))
            first = False
        write('\n  ]\n}')
    elif format == 'csv':
        row = io.StringIO(newline='')
        writer = csv.writer(row, lineterminator='\r\n')
        writer.writerow(['来源平台', '来源数据库', '范围', '时间', '微信号', '昵称', '正文',
                         '类型', '地点', '媒体数', '点赞', '评论'])
        write(row.getvalue())
        for post in items:
            row.seek(0)
            row.truncate(0)
            writer.writerow([_csv_cell(value) for value in _flat_row(post, source)])
            write(row.getvalue())
    elif format in ('md', 'txt'):
        write('# 微信朋友圈导出\n\n' if format == 'md' else '微信朋友圈导出\n\n')
        write(f"来源：{source.get('platform')} / {source.get('database')} / {source.get('scope')}\n\n")
        for post in items:
            author = post.get('nickname') or post.get('username') or '未知作者'
            timestamp = _stamp(post.get('createTime'))
            title = f"{author} · {timestamp}" if timestamp else author
            write(f"## {title}\n" if format == 'md' else f"[{title}]\n")
            write(_text(post.get('contentDesc')) + '\n')
            row = _flat_row(post, source)
            if row[8]:
                write(f'地点：{row[8]}\n')
            if row[9]:
                write(f'媒体：{row[9]} 项（未导出远程地址或密钥）\n')
            if row[10]:
                write(f'点赞：{row[10]}\n')
            for comment in post.get('comments') or []:
                name = comment.get('nickname') or comment.get('username') or '评论'
                write(f"- {name}：{comment.get('content') or ''}\n" if format == 'md'
                      else f"评论 {name}：{comment.get('content') or ''}\n")
    else:
        write('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width"><title>微信朋友圈导出</title>'
              '<style>body{font:15px/1.7 system-ui,sans-serif;max-width:820px;margin:32px auto;padding:0 18px}'
              'article{padding:18px 0;border-bottom:1px solid #ddd}time{color:#667}</style>'
              '<h1>微信朋友圈导出</h1><p>来源：' + html.escape(
                  f"{source.get('platform')} / {source.get('database')} / {source.get('scope')}") + '</p>')
        for post in items:
            author = html.escape(_text(post.get('nickname') or post.get('username') or '未知作者'))
            timestamp = html.escape(_stamp(post.get('createTime')))
            content = html.escape(_text(post.get('contentDesc'))).replace('\n', '<br>')
            row = _flat_row(post, source)
            write(f'<article><h2>{author}</h2><time>{timestamp}</time><p>{content}</p>'
                  f'<p>地点：{html.escape(row[8])} · 媒体：{row[9]} 项</p>'
                  f'<p>点赞：{html.escape(row[10])}</p><ul>')
            for comment in post.get('comments') or []:
                name = html.escape(_text(comment.get('nickname') or comment.get('username') or '评论'))
                body = html.escape(_text(comment.get('content')))
                write(f'<li><strong>{name}</strong>：{body}</li>')
            write('</ul></article>')
        write('</html>')
    return memoryview(output), content_type, extension
