"""Read-only QQNT snapshots; WAL layout follows sqlite.org/fileformat2.html#walformat."""

import os
import shutil
import struct
import tempfile
import time
from pathlib import Path


class LocalWALError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _checksum(data, state, endian):
    s0, s1 = state
    for x0, x1 in struct.iter_unpack(endian + "II", data):
        s0 = (s0 + x0 + s1) & 0xffffffff
        s1 = (s1 + x1 + s0) & 0xffffffff
    return s0, s1


def _wal_header(stream, page_size):
    header = stream.read(32)
    if not header:
        return None
    if len(header) != 32:
        raise LocalWALError("qq_wal_invalid", "QQ database WAL header is incomplete")
    magic, version, size, _, salt1, salt2, check1, check2 = struct.unpack(">8I", header)
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or size != page_size:
        raise LocalWALError("qq_wal_unsupported", "QQ database WAL format is unsupported")
    endian = "<" if magic == 0x377f0682 else ">"
    if _checksum(header[:24], (0, 0), endian) != (check1, check2):
        raise LocalWALError("qq_wal_invalid", "QQ database WAL header checksum failed")
    return endian, (salt1, salt2), (check1, check2)


def inspect_qq_wal(source_db, page_size=4096):
    """Cheap format probe only; snapshots additionally authenticate all valid frames."""
    wal = Path(str(source_db) + "-wal")
    try:
        with wal.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            header = _wal_header(stream, page_size)
    except FileNotFoundError:
        return {"wal_bytes": 0, "wal_frames": 0}
    if header is not None and (size - 32) % (24 + page_size):
        raise LocalWALError("qq_wal_invalid", "QQ database WAL frame is incomplete")
    return {"wal_bytes": size, "wal_frames": max(0, (size - 32) // (24 + page_size))}


def _stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _copy_file(source, target):
    with source.open("rb") as reader, target.open("wb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)


def _stable_pair(source, folder, retries):
    wal = Path(str(source) + "-wal")
    copied = folder / "encrypted.db"
    copied_wal = folder / "encrypted.db-wal"
    for attempt in range(retries):
        before = _stamp(source), _stamp(wal)
        if before[0] is None:
            raise LocalWALError("qq_database_missing", "QQ database file does not exist")
        try:
            _copy_file(source, copied)
            if before[1] is not None:
                _copy_file(wal, copied_wal)
            else:
                copied_wal.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        else:
            if before == (_stamp(source), _stamp(wal)):
                return copied, copied_wal if before[1] is not None else None
        if attempt + 1 < retries:
            time.sleep(0.05)
    raise LocalWALError("qq_database_busy", "QQ database changed during read-only snapshot; retry later")


def _apply_wal(wal, writer, decrypt_page, page_size, spool_path, page1_prefix):
    if wal is None:
        return {"wal_frames": 0, "wal_committed_frames": 0}
    last_commit = valid_frames = final_size = 0
    with wal.open("rb") as reader, spool_path.open("w+b") as spool:
        header = _wal_header(reader, page_size)
        if header is None:
            return {"wal_frames": 0, "wal_committed_frames": 0}
        endian, salts, checksum = header
        max_pages = writer.tell() // page_size + max(0, (wal.stat().st_size - 32) // (24 + page_size))
        while True:
            frame_header = reader.read(24)
            if not frame_header:
                break
            page = reader.read(page_size)
            if len(frame_header) != 24 or len(page) != page_size:
                raise LocalWALError("qq_wal_invalid", "QQ database WAL frame is incomplete")
            page_no, db_size, salt1, salt2, check1, check2 = struct.unpack(">6I", frame_header)
            # A WAL reset can leave obsolete frames beyond the current generation.
            if (salt1, salt2) != salts:
                break
            checksum = _checksum(page, _checksum(frame_header[:8], checksum, endian), endian)
            if not 0 < page_no <= max_pages or db_size > max_pages or checksum != (check1, check2):
                raise LocalWALError("qq_wal_invalid", "QQ database WAL frame checksum failed")
            if page_no == 1 and page[:16] != page1_prefix:
                raise LocalWALError("qq_wal_invalid", "QQ database WAL page salt does not match database")
            decrypted = decrypt_page(page, page_no)
            if len(decrypted) != page_size:
                raise LocalWALError("qq_wal_invalid", "QQ database WAL decrypted page has invalid size")
            spool.write(struct.pack(">II", page_no, db_size))
            spool.write(decrypted)
            valid_frames += 1
            if db_size:
                last_commit = valid_frames
                final_size = db_size
        spool.seek(0)
        for _ in range(last_commit):
            page_no, db_size = struct.unpack(">II", spool.read(8))
            page = spool.read(page_size)
            writer.seek((page_no - 1) * page_size)
            writer.write(page)
            if db_size:
                writer.truncate(db_size * page_size)
    return {"wal_frames": valid_frames, "wal_committed_frames": last_commit,
            "wal_database_pages": final_size}


def snapshot_qq_database(source_db, target_db, decrypt_page, header_bytes=1024,
                         page_size=4096, retries=3):
    """Publish an owned decrypted snapshot; callback must authenticate each page."""
    source = Path(source_db).resolve()
    target = Path(target_db).resolve()
    if target in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm")):
        raise LocalWALError("qq_snapshot_target_invalid", "QQ snapshot must not overwrite source files")
    if retries < 1 or header_bytes < 0 or page_size < 512 or page_size & (page_size - 1):
        raise ValueError("Invalid QQ snapshot parameters")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qq-snapshot-", dir=target.parent) as folder:
        folder = Path(folder)
        copied, wal = _stable_pair(source, folder, retries)
        payload_size = copied.stat().st_size - header_bytes
        if payload_size <= 0 or payload_size % page_size:
            raise LocalWALError("qq_database_invalid", "QQ database encrypted page layout is invalid")
        plain = folder / "plain.db"
        with copied.open("rb") as reader, plain.open("w+b") as writer:
            reader.seek(header_bytes)
            page1_prefix = reader.read(16)
            reader.seek(header_bytes)
            for page_no in range(1, payload_size // page_size + 1):
                page = decrypt_page(reader.read(page_size), page_no)
                if len(page) != page_size:
                    raise LocalWALError("qq_database_invalid", "QQ database decrypted page has invalid size")
                writer.write(page)
            metadata = _apply_wal(wal, writer, decrypt_page, page_size, folder / "frames.bin", page1_prefix)
            writer.flush()
        os.replace(plain, target)
    return metadata
