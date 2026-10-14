import unittest
import ctypes
import struct
from unittest.mock import patch
from integrations.qq.key_reader import MAX_PROCESS_CANDIDATES, MemoryReader, candidates, context_keys, matches_page
from integrations.qq.key_reader import find_key, _token_user_sid
from integrations.qq import test_local_provider as fixtures
from integrations.qq.local_provider import _keys


class KeyReaderTests(unittest.TestCase):
    @staticmethod
    def synthetic_provider():
        fixture = fixtures.LocalProviderTests()
        fixture.setUp()
        header = b'SQLite header 3\x00' + b'\x00' * 16 + b'QQ_NT DB'
        password = 'SyntheticKey1234'
        fixture.source.write_bytes(header.ljust(1024, b'\x00') + fixture.encrypted_page(password))
        return fixture

    def test_token_user_sid_reads_pointer_value_not_token_buffer_address(self):
        sid = ctypes.create_string_buffer(b'synthetic sid')
        token_user = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p))
        pointer = ctypes.c_void_p(ctypes.addressof(sid))
        ctypes.memmove(token_user, ctypes.byref(pointer), ctypes.sizeof(pointer))

        self.assertEqual(_token_user_sid(token_user), ctypes.addressof(sid))

    def test_candidate_priority_and_page_authentication(self):
        password = '#123456789012@uJ'
        ordinary = b'SyntheticKey1234'
        data = b'\x00' + password.encode() + b'\x00' + ordinary + b'\x00' + b'longSyntheticKey1234\x00'
        self.assertEqual(list(candidates(data, True)), [password.encode()])
        self.assertEqual(list(candidates(data, False)), [ordinary])
        page = fixtures.LocalProviderTests().encrypted_page(password)
        self.assertTrue(matches_page(password.encode(), page))
        self.assertFalse(matches_page(ordinary, page))

    def test_derived_context_key_and_utf16(self):
        password = '#123456789012@uJ'
        page = fixtures.LocalProviderTests().encrypted_page(password)
        key, mac_key = _keys(password, page[:16])
        address = 0x0000012345678000
        data = struct.pack('<iiQQQ', 0, 0, address, address + 64, 0)
        found = list(context_keys(data, lambda pointer, size: key if pointer == address and size == 32 else None))
        self.assertEqual(len(found), 1)
        self.assertTrue(matches_page(found[0], page))
        read = lambda pointer, size: {address: key, address + 64: mac_key}.get(pointer)
        self.assertEqual(list(context_keys(data, read, page)), found)
        self.assertEqual(list(context_keys(data, lambda pointer, size: b'x' * size, page)), [])
        self.assertEqual(list(candidates(password.encode('utf-16le') + b'\x00\x00', True)), [password.encode()])

    def test_printable_key_with_space_is_not_excluded(self):
        password = b'Synthetic Key123'
        self.assertEqual(len(password), 16)
        self.assertEqual(list(candidates(b'\x00' + password + b'\x00')), [password])
        self.assertTrue(matches_page(password, fixtures.LocalProviderTests().encrypted_page(password.decode())))

    def test_context_memory_reads_share_the_scan_byte_budget(self):
        reader = object.__new__(MemoryReader)
        reader.bytes_read = 0
        reader.bytes_scanned = 6
        reader.scan_budget = 10
        reader.budget_limited = False
        calls = []

        class Kernel:
            @staticmethod
            def ReadProcessMemory(_handle, _address, buffer, amount, size):
                calls.append(amount)
                ctypes.memmove(buffer, b'x' * amount, amount)
                ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t)).contents.value = amount
                return True

        reader.kernel = Kernel()
        self.assertEqual(reader.read(1, 0x1000, 4), b'x' * 4)
        self.assertIsNone(reader.read(1, 0x2000, 1))
        self.assertEqual(calls, [4])
        self.assertEqual(reader.bytes_scanned, 10)
        self.assertEqual(reader.bytes_read, 4)
        self.assertTrue(reader.budget_limited)

    def test_scan_byte_budget_is_shared_across_processes_and_passes(self):
        fixture = fixtures.LocalProviderTests()
        fixture.setUp()
        password = 'SyntheticKey1234'
        header = b'SQLite header 3\x00' + b'\x00' * 16 + b'QQ_NT DB'
        fixture.source.write_bytes(header.ljust(1024, b'\x00') + fixture.encrypted_page(password))

        class FakeReader:
            budgets = []
            bytes_read = 0
            bytes_scanned = 0
            budget_limited = False

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return [1, 2]

            def open_qq(self, pid):
                return pid

            def chunks(self, _handle, _deadline, budget):
                self.budgets.append(budget)
                amount = min(5, budget)
                self.bytes_scanned += amount
                self.budget_limited = self.bytes_scanned >= 8
                yield b'\x00' * amount

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=2, max_bytes=8)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'limit_reached')
            self.assertEqual(result['limit_reason'], 'memory_budget')
            self.assertEqual(FakeReader.budgets, [8, 3])
        finally:
            fixture.tearDown()

    def test_generic_candidate_is_checked_within_the_single_scan_budget(self):
        fixture = self.synthetic_provider()
        password = b'SyntheticKey1234'

        class FakeReader:
            bytes_read = 16
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False
            scans = []

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return [1]

            def open_qq(self, pid):
                return pid

            def chunks(self, _handle, _deadline, budget):
                self.scans.append(budget)
                chunk = b'\x00' + password + b'\x00'
                self.bytes_scanned += len(chunk)
                yield chunk

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=2, max_bytes=18)
            self.assertEqual(key, password.decode('ascii'))
            self.assertEqual(result['state'], 'verified')
            self.assertEqual(FakeReader.scans, [18])
        finally:
            fixture.tearDown()

    def test_deadline_includes_process_enumeration(self):
        fixture = self.synthetic_provider()
        now = [0]

        class FakeReader:
            bytes_read = 0
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False
            opened = []

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                now[0] = 6
                return [1]

            def open_qq(self, pid):
                self.opened.append(pid)
                return pid

        try:
            with patch('integrations.qq.key_reader.time.monotonic', side_effect=lambda: now[0]), \
                 patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=5)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'limit_reached')
            self.assertEqual(result['limit_reason'], 'time_limit')
            self.assertEqual(FakeReader.opened, [])
        finally:
            fixture.tearDown()

    def test_deadline_includes_each_process_open(self):
        fixture = self.synthetic_provider()
        now = [0]

        class FakeReader:
            bytes_read = 0
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False
            closed = []
            opened = []

            class Kernel:
                @staticmethod
                def CloseHandle(handle):
                    FakeReader.closed.append(handle)

            kernel = Kernel()

            def pids(self):
                return [11, 12]

            def open_qq(self, pid):
                self.opened.append(pid)
                now[0] = 5
                return pid

        try:
            with patch('integrations.qq.key_reader.time.monotonic', side_effect=lambda: now[0]), \
                 patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=5)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'limit_reached')
            self.assertEqual(result['limit_reason'], 'time_limit')
            self.assertEqual(result['processes'], 1)
            self.assertEqual(FakeReader.opened, [11])
            self.assertEqual(FakeReader.closed, [11])
        finally:
            fixture.tearDown()

    def test_process_candidate_cap_reports_limit_reached(self):
        fixture = self.synthetic_provider()

        class FakeReader:
            bytes_read = 0
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False
            opened = []

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return list(range(1, MAX_PROCESS_CANDIDATES + 2))

            def open_qq(self, pid):
                self.opened.append(pid)
                return pid

            def chunks(self, _handle, _deadline, _budget):
                return iter(())

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=10)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'limit_reached')
            self.assertEqual(result['limit_reason'], 'process_limit')
            self.assertEqual(result['processes'], MAX_PROCESS_CANDIDATES)
            self.assertEqual(len(FakeReader.opened), MAX_PROCESS_CANDIDATES)
        finally:
            fixture.tearDown()

    def test_qq_after_system_processes_is_not_truncated_by_candidate_cap(self):
        fixture = self.synthetic_provider()
        password = b'SyntheticKey1234'
        opened = []

        class FakeReader:
            bytes_read = 16
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return list(range(1, MAX_PROCESS_CANDIDATES + 1)) + [9999]

            def open_qq(self, pid):
                opened.append(pid)
                return pid if pid == 9999 else None

            def chunks(self, _handle, _deadline, _budget):
                self.bytes_scanned += len(password) + 2
                yield b'\x00' + password + b'\x00'

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=2)
            self.assertEqual(key, password.decode('ascii'))
            self.assertEqual(result['state'], 'verified')
            self.assertEqual(opened[-1], 9999)
            self.assertEqual(len(opened), MAX_PROCESS_CANDIDATES + 1)
        finally:
            fixture.tearDown()

    def test_many_non_qq_processes_do_not_report_qq_process_limit(self):
        fixture = self.synthetic_provider()

        class FakeReader:
            bytes_read = 0
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return list(range(1, MAX_PROCESS_CANDIDATES + 2))

            @staticmethod
            def open_qq(_pid):
                return None

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=2)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'no_accessible_qq')
            self.assertNotIn('limit_reason', result)
        finally:
            fixture.tearDown()

    def test_candidate_cap_reports_limit_reason_without_exposing_candidate(self):
        fixture = self.synthetic_provider()
        candidate = b'WrongCandidate12'

        class FakeReader:
            bytes_read = 1
            bytes_scanned = 0
            budget_limited = False
            process_limit_reached = False

            class Kernel:
                @staticmethod
                def CloseHandle(_handle):
                    pass

            kernel = Kernel()

            def pids(self):
                return [1]

            def open_qq(self, pid):
                return pid

            def chunks(self, _handle, _deadline, budget):
                chunk = b'\x00' + candidate + b'\x00'
                self.bytes_scanned += len(chunk)
                yield chunk

        try:
            with patch('integrations.qq.key_reader.MemoryReader', FakeReader):
                key, result = find_key(fixture.provider, seconds=2, max_candidates=0)
            self.assertIsNone(key)
            self.assertEqual(result['state'], 'limit_reached')
            self.assertEqual(result['limit_reason'], 'candidate_limit')
            self.assertNotIn(candidate.decode(), str(result))
        finally:
            fixture.tearDown()

    def test_scan_limits_are_hard_bounded_before_provider_access(self):
        invalid_limits = (
            {"seconds": -1}, {"seconds": 181}, {"seconds": True},
            {"seconds": float("nan")}, {"max_candidates": -1},
            {"max_candidates": 20_001}, {"max_candidates": True},
            {"max_candidates": 1.5}, {"max_bytes": 0},
            {"max_bytes": 512 * 1024 * 1024 + 1}, {"max_bytes": True},
        )
        for limits in invalid_limits:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                find_key(None, **limits)


if __name__ == '__main__':
    unittest.main()
