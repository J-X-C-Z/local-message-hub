import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.qq.local_wal import (
    LocalWALError, inspect_qq_wal, snapshot_qq_database,
)


PAGE = 4096


def _test_checksum(data, state, endian):
    values = struct.unpack(endian + str(len(data) // 4) + "I", data)
    a, b = state
    for i in range(0, len(values), 2):
        a = (a + b + values[i]) % (2 ** 32)
        b = (b + a + values[i + 1]) % (2 ** 32)
    return a, b


def _wal(frames, endian="<"):
    salts = (123, 456)
    header = struct.pack(">6I", 0x377f0682 if endian == "<" else 0x377f0683,
                         3007000, PAGE, 0, *salts)
    checksum = _test_checksum(header, (0, 0), endian)
    result = header + struct.pack(">2I", *checksum)
    for page_no, size, page in frames:
        prefix = struct.pack(">2I", page_no, size)
        checksum = _test_checksum(prefix + page, checksum, endian)
        result += prefix + struct.pack(">4I", *salts, *checksum) + page
    return result


class LocalWALTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source.db"
        self.target = self.folder / "owned.db"
        self.wal = Path(str(self.source) + "-wal")
        self.source.write_bytes(b"H" * 1024 + b"A" * PAGE + b"B" * PAGE)

    def snapshot(self, decrypt=lambda page, _number: page):
        return snapshot_qq_database(self.source, self.target, decrypt)

    def test_commits_only_and_last_frame_wins_both_endians(self):
        for endian in ("<", ">"):
            self.wal.write_bytes(_wal([(2, 2, b"C" * PAGE), (2, 2, b"D" * PAGE),
                                       (2, 0, b"E" * PAGE)], endian))
            before = self.source.read_bytes(), self.wal.read_bytes()
            self.assertEqual(inspect_qq_wal(self.source)["wal_frames"], 3)
            info = self.snapshot()
            self.assertEqual(info["wal_committed_frames"], 2)
            self.assertEqual(self.target.read_bytes(), b"A" * PAGE + b"D" * PAGE)
            self.assertEqual(before, (self.source.read_bytes(), self.wal.read_bytes()))

    def test_truncates_at_commit_and_no_commit_preserves_base(self):
        self.wal.write_bytes(_wal([(1, 1, b"A" * 16 + b"C" * (PAGE - 16))]))
        self.snapshot()
        self.assertEqual(self.target.stat().st_size, PAGE)
        self.wal.write_bytes(_wal([(2, 0, b"D" * PAGE)]))
        self.snapshot()
        self.assertEqual(self.target.read_bytes(), b"A" * PAGE + b"B" * PAGE)

    def test_obsolete_reset_tail_not_applied(self):
        first = _wal([(2, 2, b"C" * PAGE)])
        stale = bytearray(_wal([(2, 2, b"D" * PAGE)])[32:])
        stale[8:12] = struct.pack(">I", 122)
        self.wal.write_bytes(first + stale)
        self.snapshot()
        self.assertEqual(self.target.read_bytes(), b"A" * PAGE + b"C" * PAGE)

    def test_bad_checksum_truncated_frame_and_page1_salt_leave_target_untouched(self):
        valid = _wal([(2, 2, b"C" * PAGE)])
        corrupt = bytearray(valid)
        corrupt[-1] ^= 1
        self.target.write_bytes(b"existing")
        for data in (bytes(corrupt), valid[:-1], _wal([(1, 2, b"Z" * PAGE)])):
            self.wal.write_bytes(data)
            with self.assertRaises(LocalWALError):
                self.snapshot()
            self.assertEqual(self.target.read_bytes(), b"existing")

    def test_page_authentication_failure_prevents_publish(self):
        self.wal.write_bytes(_wal([(2, 2, b"C" * PAGE)]))
        def decrypt(page, _number):
            if page.startswith(b"C"):
                raise ValueError("fixture HMAC mismatch")
            return page
        with self.assertRaisesRegex(ValueError, "HMAC"):
            self.snapshot(decrypt)
        self.assertFalse(self.target.exists())

    def test_busy_database_retries_and_fails_closed(self):
        with patch("integrations.qq.local_wal._stamp", side_effect=lambda _path: object()), \
                patch("integrations.qq.local_wal._copy_file"), \
                patch("integrations.qq.local_wal.time.sleep"):
            with self.assertRaises(LocalWALError) as caught:
                self.snapshot()
            self.assertEqual(caught.exception.code, "qq_database_busy")
        self.assertFalse(self.target.exists())

    def test_no_wal_and_source_target_refused(self):
        self.snapshot()
        self.assertEqual(self.target.read_bytes(), b"A" * PAGE + b"B" * PAGE)
        with self.assertRaises(LocalWALError):
            snapshot_qq_database(self.source, self.source, lambda page, _number: page)


if __name__ == "__main__":
    unittest.main()
