"""Resolve native WeCom resource IDs to local cache files without filename guesses.

Observed macOS profiles: 14=image, 15=file, 16=voice (field 10 resource ID,
file name in field 2); 29=emotion (field 6). Windows uses the same payload
profile with singular cache directory names. Unknown profiles stay explicit.
"""

import hashlib
import os
import re
import shutil
import uuid
from collections import defaultdict
from pathlib import Path

try:
    from .local_io import atomic_output
    from .protobuf_fields import fields, single_text
except ImportError:
    from local_io import atomic_output
    from protobuf_fields import fields, single_text

MEDIA_TYPES = {4: "image", 14: "image", 15: "file", 16: "voice", 29: "image"}
DIRECTORIES = {14: ("Images", "Image"), 4: ("Images", "Image"), 15: ("Files", "File"),
               16: ("Voices", "Voice"), 29: ("Emotions", "Emotion")}
HEX_ID = re.compile(r"[0-9a-fA-F]{32}\Z")


def contained(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def image_suffix(header):
    for magic, suffix in ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"),
                          (b"GIF87a", ".gif"), (b"GIF89a", ".gif")):
        if header.startswith(magic):
            return suffix
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    return ""


def resource(row):
    ct = int(row["type_code"])
    try:
        payload = fields(row["_raw"])
    except ValueError:
        return None
    identifier = single_text(payload, 6 if ct == 29 else 10)
    name = single_text(payload, 2) if ct != 29 else ""
    if not HEX_ID.fullmatch(identifier):
        return None
    if name and (name in (".", "..") or any(c in name for c in ("/", "\\", "\x00"))):
        return None
    return identifier.lower(), name


def _fingerprint(path):
    return _stat_signature(path.stat())


def _stat_signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _digest(path):
    before = _fingerprint(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if before != _fingerprint(path):
        raise OSError("cache file changed during read")
    return digest.digest()


class CacheIndex:
    def __init__(self, root, *, exclude=None):
        self.root = Path(root).expanduser().resolve()
        self.by_id = defaultdict(list)
        if not self.root.is_dir():
            return
        for parent, directories, files in os.walk(self.root, followlinks=False):
            directories[:] = [d for d in directories if not (Path(parent) / d).is_symlink()
                              and (exclude is None or not contained(Path(parent) / d, exclude))]
            for name in files:
                path = Path(parent) / name
                if path.is_symlink() or not path.is_file() or name == ".DS_Store":
                    continue
                if not contained(path, self.root) or (exclude is not None and contained(path, exclude)):
                    continue
                relative = path.relative_to(self.root)
                if len(relative.parts) < 2:
                    continue
                identifiers = {p[:32].lower() for p in relative.parts[1:]
                               if HEX_ID.fullmatch(p[:32]) and (len(p) == 32 or p[32] in "_.")}
                for identifier in identifiers:
                    self.by_id[(relative.parts[0], identifier)].append(path)

    def resolve(self, row):
        reference = resource(row)
        if reference is None:
            return "unsupported", "unsupported_message_profile", None
        identifier, name = reference
        paths = [p for sub in DIRECTORIES[int(row["type_code"])]
                 for p in self.by_id.get((sub, identifier), [])]
        if name:
            named = [p for p in paths if p.name == name]
            if named or int(row["type_code"]) == 15:
                paths = named
        # Prefer the original resource directory to _thumb / _hd variants.
        originals = [p for p in paths if identifier in [c.lower() for c in p.relative_to(self.root).parts[1:]]
                     or p.stem.lower() == identifier]
        paths = originals or paths
        if not paths:
            return "not_found", "resource_not_cached", None
        try:
            if len(paths) > 1 and len({_digest(p) for p in paths}) > 1:
                return "ambiguous", "multiple_distinct_cached_files", None
        except OSError:
            return "not_found", "cache_changed_or_unreadable", None
        return "resolved", "resource_id_match", min(paths)


def copy_cached(source, cache_root, destination_root, *, image=False):
    source, cache_root, destination_root = Path(source), Path(cache_root), Path(destination_root)
    if not contained(source, cache_root) or source.is_symlink():
        raise ValueError("unsafe_source_path")
    relative = source.relative_to(cache_root)
    before = _fingerprint(source)
    with source.open("rb") as stream:
        # Compare stat with stat and fstat with fstat. Windows exposes different
        # timestamp precision through the two APIs for the same unchanged file.
        opened = _stat_signature(os.fstat(stream.fileno()))
        header = stream.read(16)
        suffix = image_suffix(header) if image else ""
        if image and not suffix:
            return None
        if suffix and relative.suffix.lower() not in (suffix, ".jpeg" if suffix == ".jpg" else suffix):
            relative = relative.with_name(relative.name + suffix)
        destination = destination_root / relative
        if not contained(destination, destination_root) or not contained(destination_root, destination_root.parent):
            raise ValueError("unsafe_output_path")
        if destination.exists():
            if destination.is_symlink() or _digest(destination) != _digest(source) or before != _fingerprint(source):
                raise OSError("resource changed within this export")
            return relative
        with atomic_output(destination, binary=True) as output:
            stream.seek(0)
            shutil.copyfileobj(stream, output)
            after = os.fstat(stream.fileno())
            if opened != _stat_signature(after):
                raise OSError("cache file changed during copy")
            if before != _fingerprint(source):
                raise OSError("cache file replaced during copy")
    return relative


def export_media(rows, database, cache_root, output_path):
    output_path = Path(output_path)
    directory = output_path.parent / (output_path.stem + ".assets-" + uuid.uuid4().hex[:16])
    snapshot = hashlib.sha256()
    database = Path(database).resolve()
    for path in (database, Path(str(database) + "-wal")):
        snapshot.update(str(path).encode("utf-8", "surrogateescape"))
        try:
            snapshot.update(repr(_fingerprint(path)).encode())
        except FileNotFoundError:
            pass
    snapshot_id = snapshot.hexdigest()
    manifest = {"version": 1, "source": "wecom-local-cache", "snapshot": snapshot_id,
                "database": database.name, "assets_directory": None, "entries": []}
    index = CacheIndex(cache_root, exclude=directory) if cache_root else None
    for row in rows:
        if row["type_code"] not in MEDIA_TYPES:
            continue
        entry = {"message": {"snapshot": snapshot_id, "database": database.name, "table": row["table"],
                             "conversation_id": row["conv_id"], "local_id": row["local_id"],
                             "server_id": row["server_id"]},
                 "kind": MEDIA_TYPES[row["type_code"]], "files": []}
        status, reason, source = index.resolve(row) if index else ("not_found", "cache_root_unavailable", None)
        if source is not None:
            try:
                relative = copy_cached(source, index.root, directory, image=entry["kind"] == "image")
                if relative is None:
                    status, reason = "encrypted_unsupported", "unrecognized_or_encrypted_image"
                else:
                    entry["files"] = [(Path(directory.name) / relative).as_posix()]
                    manifest["assets_directory"] = directory.name
            except (ValueError, OSError):
                status, reason = "not_found", "cache_changed_unreadable_or_unsafe"
        entry.update(status=status, reason=reason)
        manifest["entries"].append(entry)
    return manifest
