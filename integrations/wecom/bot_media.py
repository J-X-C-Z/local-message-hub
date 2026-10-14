"""Locally staged media for explicitly approved WeCom bot messages."""
from __future__ import annotations

import hashlib
import codecs
import os
from pathlib import Path
import re
import secrets
import time
from contextlib import contextmanager


MAX_MEDIA_SIZE = 50 * 1024 * 1024
_MEDIA_ID = re.compile(r"^[a-f0-9]{48}$")


class WeComBotMediaStore:
    def __init__(self, root):
        self.root = Path(root).resolve() / "wecom-bot-outbox"
        if self.root.exists() and self.root.resolve() != self.root:
            raise RuntimeError("企微机器人附件缓存目录不可重定向")
        self.root.mkdir(parents=True, exist_ok=True)

    def _ensure_root(self):
        if not self.root.is_dir() or self.root.resolve() != self.root:
            raise RuntimeError("企微机器人附件缓存目录不可重定向")

    @staticmethod
    def validate(data, filename, media_type):
        if media_type not in ("image", "file", "voice", "video"):
            raise ValueError("企微机器人媒体类型无效")
        if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_MEDIA_SIZE:
            raise ValueError("附件为空或超过 50 MiB 限制")
        if (not isinstance(filename, str) or not filename or len(filename.encode("utf-8")) > 255
                or filename in (".", "..") or any(c in filename for c in ("/", "\\", "\x00"))
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
            raise ValueError("附件文件名无效")
        ext = Path(filename).suffix.lower()
        valid = False
        if media_type == "image":
            valid = ((ext in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff")) or
                     (ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
                     (ext == ".gif" and data.startswith((b"GIF87a", b"GIF89a"))) or
                     (ext == ".webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
        elif media_type == "voice":
            valid = ext == ".amr" and data.startswith(b"#!AMR\n")
        elif media_type == "video":
            valid = ((ext in (".mp4", ".m4v", ".mov") and len(data) >= 12 and data[4:8] == b"ftyp") or
                     (ext == ".webm" and data.startswith(b"\x1aE\xdf\xa3")))
        else:
            valid = ((ext == ".pdf" and data.startswith(b"%PDF-")) or
                     (ext in (".txt", ".csv", ".md", ".log") and
                      b"\x00" not in data and _is_utf8(data)) or
                     (ext in (".zip", ".docx", ".xlsx", ".pptx") and
                      data.startswith(b"PK\x03\x04")))
        if not valid:
            raise ValueError("附件扩展名与文件内容不匹配或格式不支持")

    def save(self, data, filename, media_type):
        self.validate(data, filename, media_type)
        self._ensure_root()
        attachment_id = secrets.token_hex(24)
        path = self.root / (attachment_id + ".blob")
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return {"attachment_id": attachment_id, "filename": filename,
                "media_type": media_type, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def save_stream(self, source, length, filename, media_type, chunk_size=1024 * 1024):
        """Stage a selected upload in bounded chunks without a whole-file copy."""
        length = int(length)
        if not 1 <= length <= MAX_MEDIA_SIZE:
            raise ValueError("附件为空或超过 50 MiB 限制")
        if media_type not in ("image", "file", "voice", "video"):
            raise ValueError("企微机器人媒体类型无效")
        if (not isinstance(filename, str) or not filename or len(filename.encode("utf-8")) > 255
                or filename in (".", "..") or any(c in filename for c in ("/", "\\", "\x00"))
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
            raise ValueError("附件文件名无效")
        self._ensure_root()
        attachment_id = secrets.token_hex(24)
        target = self.root / (attachment_id + ".blob")
        temp = self.root / (attachment_id + ".part")
        digest = hashlib.sha256()
        prefix = bytearray()
        ext = Path(filename).suffix.lower()
        is_text = media_type == "file" and ext in (".txt", ".csv", ".md", ".log")
        decoder = codecs.getincrementaldecoder("utf-8-sig")() if is_text else None
        remaining = length
        try:
            with temp.open("xb") as output:
                while remaining:
                    chunk = source.read(min(max(1, int(chunk_size)), remaining))
                    if not chunk:
                        raise ValueError("附件上传不完整")
                    if len(chunk) > remaining:
                        raise ValueError("附件上传长度无效")
                    remaining -= len(chunk)
                    if len(prefix) < 16:
                        prefix.extend(chunk[:16 - len(prefix)])
                    if is_text:
                        if b"\x00" in chunk:
                            raise ValueError("附件扩展名与文件内容不匹配或格式不支持")
                        decoder.decode(chunk, final=remaining == 0)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not self._valid_prefix(bytes(prefix), filename, media_type, length):
                raise ValueError("附件扩展名与文件内容不匹配或格式不支持")
            self._ensure_root()
            os.replace(temp, target)
            return {"attachment_id": attachment_id, "filename": filename,
                    "media_type": media_type, "size": length,
                    "sha256": digest.hexdigest()}
        except UnicodeDecodeError:
            raise ValueError("附件扩展名与文件内容不匹配或格式不支持") from None
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _valid_prefix(prefix, filename, media_type, size):
        ext = Path(filename).suffix.lower()
        if media_type == "image":
            return ((ext in (".jpg", ".jpeg") and prefix.startswith(b"\xff\xd8\xff")) or
                    (ext == ".png" and prefix.startswith(b"\x89PNG\r\n\x1a\n")) or
                    (ext == ".gif" and prefix.startswith((b"GIF87a", b"GIF89a"))) or
                    (ext == ".webp" and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"))
        if media_type == "voice":
            return ext == ".amr" and prefix.startswith(b"#!AMR\n")
        if media_type == "video":
            return ((ext in (".mp4", ".m4v", ".mov") and size >= 12 and prefix[4:8] == b"ftyp") or
                    (ext == ".webm" and prefix.startswith(b"\x1aE\xdf\xa3")))
        return ((ext == ".pdf" and prefix.startswith(b"%PDF-")) or
                (ext in (".txt", ".csv", ".md", ".log")) or
                (ext in (".zip", ".docx", ".xlsx", ".pptx") and prefix.startswith(b"PK\x03\x04")))

    def load(self, attachment):
        self._ensure_root()
        if not isinstance(attachment, dict) or not _MEDIA_ID.fullmatch(str(attachment.get("attachment_id", ""))):
            raise ValueError("企微机器人附件标识无效")
        path = self.root / (attachment["attachment_id"] + ".blob")
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError("企微机器人附件不可用")
        if path.stat().st_size > MAX_MEDIA_SIZE:
            raise ValueError("企微机器人附件已超过 50 MiB 限制")
        with path.open("rb") as stream:
            data = stream.read(MAX_MEDIA_SIZE + 1)
        if len(data) > MAX_MEDIA_SIZE:
            raise ValueError("企微机器人附件已超过 50 MiB 限制")
        self.validate(data, attachment.get("filename"), attachment.get("media_type"))
        if len(data) != attachment.get("size") or hashlib.sha256(data).hexdigest() != attachment.get("sha256"):
            raise ValueError("企微机器人附件已变化，请重新选择")
        return {"type": attachment["media_type"], "filename": attachment["filename"], "data": data}

    @contextmanager
    def open_for_send(self, attachment, chunk_size=1024 * 1024):
        """Validate staged bytes in bounded memory and yield the same open file for upload."""
        self._ensure_root()
        if not isinstance(attachment, dict) or not _MEDIA_ID.fullmatch(str(attachment.get("attachment_id", ""))):
            raise ValueError("企微机器人附件标识无效")
        path = self.root / (attachment["attachment_id"] + ".blob")
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError("企微机器人附件不可用")
        try:
            stream = path.open("rb")
        except OSError:
            raise ValueError("企微机器人附件不可用") from None
        try:
            stat = os.fstat(stream.fileno())
            declared_size = attachment.get("size")
            if (not 1 <= stat.st_size <= MAX_MEDIA_SIZE or
                    isinstance(declared_size, bool) or stat.st_size != declared_size):
                raise ValueError("企微机器人附件已变化，请重新选择")
            digest = hashlib.sha256()
            md5 = hashlib.md5()
            prefix = bytearray()
            ext = Path(attachment.get("filename", "")).suffix.lower()
            is_text = attachment.get("media_type") == "file" and ext in (".txt", ".csv", ".md", ".log")
            decoder = codecs.getincrementaldecoder("utf-8-sig")() if is_text else None
            remaining = stat.st_size
            while remaining:
                chunk = stream.read(min(max(1, int(chunk_size)), remaining))
                if not chunk:
                    raise ValueError("企微机器人附件已变化，请重新选择")
                remaining -= len(chunk)
                if len(prefix) < 16:
                    prefix.extend(chunk[:16 - len(prefix)])
                if is_text:
                    if b"\x00" in chunk:
                        raise ValueError("附件扩展名与文件内容不匹配或格式不支持")
                    decoder.decode(chunk, final=remaining == 0)
                digest.update(chunk)
                md5.update(chunk)
            if (digest.hexdigest() != attachment.get("sha256") or
                    not self._valid_prefix(bytes(prefix), attachment.get("filename"),
                                           attachment.get("media_type"), stat.st_size)):
                raise ValueError("企微机器人附件已变化，请重新选择")
            stream.seek(0)
            yield {"type": attachment["media_type"], "filename": attachment["filename"],
                   "stream": stream, "size": stat.st_size, "md5": md5.hexdigest()}
        except UnicodeDecodeError:
            raise ValueError("附件扩展名与文件内容不匹配或格式不支持") from None
        finally:
            stream.close()

    def remove(self, attachment):
        self._ensure_root()
        if isinstance(attachment, dict) and _MEDIA_ID.fullmatch(str(attachment.get("attachment_id", ""))):
            (self.root / (attachment["attachment_id"] + ".blob")).unlink(missing_ok=True)

    def remove_orphans(self, retained_ids, max_age=24 * 60 * 60):
        self._ensure_root()
        cutoff = time.time() - max_age
        for suffix in (".blob", ".part"):
            for path in self.root.glob("*" + suffix):
                if not _MEDIA_ID.fullmatch(path.stem):
                    continue
                if suffix == ".blob" and path.stem in retained_ids:
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass


def _is_utf8(data):
    try:
        data.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        return False
