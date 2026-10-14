"""Explicit, bounded Windows QQ memory read; never injects or dumps memory."""
import ctypes
from ctypes import wintypes as wt
import hashlib
import hmac
from itertools import chain
import math
import os
from pathlib import Path
import re
import struct
import time

from integrations.qq.local_provider import HEADER, PAGE, RESERVE, _keys

MAX_PROCESS_CANDIDATES = 256
MAX_SCAN_SECONDS = 180
MAX_KEY_CANDIDATES = 20_000
MAX_SCAN_BYTES = 512 * 1024 * 1024


def _token_user_sid(data):
    sid = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p)).contents.value
    if not sid:
        raise RuntimeError('qq_process_owner_unavailable')
    return sid


def candidates(data, priority=False):
    if priority:
        for match in re.finditer(rb'#[\x20-\x7e]{15}', data):
            if b'@' in match.group():
                yield match.group()
        for match in re.finditer(rb'(?:[\x20-\x7e]\x00){16}\x00\x00', data):
            yield match.group()[:-2:2]
        return
    for match in re.finditer(rb'(?<![\x20-\x7e])[\x20-\x7e]{16}\x00', data):
        value = match.group()[:-1]
        if priority == (value.startswith(b'#') and b'@' in value):
            yield value


def matches_page(value, page):
    _, mac_key = _keys(value.decode('ascii'), page[:16])
    digest = hmac.new(mac_key, page[16:PAGE - RESERVE + 16] + struct.pack('<I', 1), hashlib.sha1).digest()
    return hmac.compare_digest(digest, page[PAGE - RESERVE + 16:PAGE - RESERVE + 36])


def context_keys(data, read, page=None):
    # SQLCipher cipher_ctx: derive_key, pass_sz, key*, hmac_key*, pass* (x64).
    pointer = rb'.{5}[\x01-\x7f]\x00\x00'
    pattern = rb'[\x00\x01]\x00{3}(?:\x00{4}|\x10\x00{3})' + pointer + pointer + rb'(?:\x00{8}|' + pointer + rb')'
    for match in re.finditer(pattern, data, re.DOTALL):
        key_address, mac_address = struct.unpack('<QQ', match.group()[8:24])
        if key_address % 8 or mac_address % 8 or key_address == mac_address:
            continue
        if page is not None:
            mac_key = read(mac_address, 32)
            if mac_key is None:
                continue
            digest = hmac.new(mac_key, page[16:PAGE - RESERVE + 16] + struct.pack('<I', 1), hashlib.sha1).digest()
            if not hmac.compare_digest(digest, page[PAGE - RESERVE + 16:PAGE - RESERVE + 36]):
                continue
        key = read(key_address, 32)
        if key and len(key) == 32:
            yield b'hex:' + key.hex().encode('ascii')


class MemoryReader:
    def __init__(self):
        if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError('qq_memory_reader_requires_windows_x64')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            'CloseHandle': ([wt.HANDLE], wt.BOOL),
            'GetCurrentProcess': ([], wt.HANDLE),
            'QueryFullProcessImageNameW': ([wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            'ReadProcessMemory': ([wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
            'VirtualQueryEx': ([wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_size_t),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
        self.advapi.OpenProcessToken.restype = wt.BOOL
        self.advapi.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
        self.advapi.GetTokenInformation.restype = wt.BOOL
        self.advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.advapi.EqualSid.restype = wt.BOOL
        self.owner = self._owner(self.kernel.GetCurrentProcess())
        self.bytes_read = 0
        self.bytes_scanned = 0
        self.scan_budget = 512 * 1024 * 1024
        self.budget_limited = False
        self.process_limit_reached = False

    def _owner(self, handle):
        token = wt.HANDLE()
        if not self.advapi.OpenProcessToken(handle, 8, ctypes.byref(token)):
            raise RuntimeError('qq_process_owner_unavailable')
        try:
            size = wt.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            data = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
                raise RuntimeError('qq_process_owner_unavailable')
            sid = _token_user_sid(data)
            return data, sid
        finally:
            self.kernel.CloseHandle(token)

    def pids(self):
        psapi = ctypes.WinDLL('psapi')
        psapi.EnumProcesses.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
        psapi.EnumProcesses.restype = wt.BOOL
        array = (wt.DWORD * 65536)()
        size = wt.DWORD()
        if not psapi.EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(size)):
            raise RuntimeError('qq_process_list_unavailable')
        count = size.value // ctypes.sizeof(wt.DWORD)
        self.process_limit_reached = count >= len(array)
        return array[:min(count, len(array))]

    def open_qq(self, pid):
        handle = self.kernel.OpenProcess(0x410, False, pid)
        if not handle:
            return None
        keep = False
        try:
            name, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                return None
            path = Path(name.value)
            if (path.name.lower(), path.parent.name.lower(), path.parent.parent.name.lower()) != ('qq.exe', 'qqnt', 'tencent'):
                return None
            owner = self._owner(handle)
            if not self.advapi.EqualSid(ctypes.c_void_p(owner[1]), ctypes.c_void_p(self.owner[1])):
                return None
            keep = True
            return handle
        except RuntimeError:
            return None
        finally:
            if not keep:
                self.kernel.CloseHandle(handle)

    def read(self, handle, address, amount):
        if amount <= 0 or self.bytes_scanned + amount > self.scan_budget:
            self.budget_limited = True
            return None
        self.bytes_scanned += amount
        buffer = ctypes.create_string_buffer(amount)
        size = ctypes.c_size_t()
        if self.kernel.ReadProcessMemory(handle, address, buffer, amount, ctypes.byref(size)) and size.value == amount:
            self.bytes_read += amount
            return buffer.raw
        return None

    def chunks(self, handle, deadline, budget):
        class Region(ctypes.Structure):
            _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                        ('AllocationProtect', wt.DWORD), ('PartitionId', wt.WORD),
                        ('RegionSize', ctypes.c_size_t), ('State', wt.DWORD),
                        ('Protect', wt.DWORD), ('Type', wt.DWORD)]
        address, consumed = 0, 0
        region = Region()
        buffer = ctypes.create_string_buffer(1024 * 1024)
        while time.monotonic() < deadline and consumed < budget:
            if not self.kernel.VirtualQueryEx(handle, address, ctypes.byref(region), ctypes.sizeof(region)):
                break
            base, size = region.BaseAddress or 0, region.RegionSize
            if not size or base + size <= address:
                break
            address = base + size
            if region.State != 0x1000 or region.Type != 0x20000 or region.Protect & 0x101 or not region.Protect & 0xEE:
                continue
            tail = b''
            for offset in range(0, size, len(buffer)):
                remaining = min(budget - consumed, self.scan_budget - self.bytes_scanned)
                if time.monotonic() >= deadline or remaining <= 0:
                    self.budget_limited |= remaining <= 0
                    return
                amount = min(len(buffer), size - offset, remaining)
                read = ctypes.c_size_t()
                ok = self.kernel.ReadProcessMemory(handle, base + offset, buffer, amount, ctypes.byref(read))
                consumed += amount
                self.bytes_scanned += amount
                if ok and read.value:
                    self.bytes_read += read.value
                    data = tail + buffer.raw[:read.value]
                    yield data
                    tail = data[-64:]
                else:
                    tail = b''
        if consumed >= budget or self.bytes_scanned >= self.scan_budget:
            self.budget_limited = True


def find_key(provider, seconds=180, max_candidates=20000, max_bytes=512 * 1024 * 1024):
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not 0 <= seconds <= MAX_SCAN_SECONDS):
        raise ValueError('qq_memory_scan_duration_invalid')
    if type(max_candidates) is not int or not 0 <= max_candidates <= MAX_KEY_CANDIDATES:
        raise ValueError('qq_memory_scan_candidate_budget_invalid')
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SCAN_BYTES:
        raise ValueError('qq_memory_scan_budget_invalid')
    deadline = time.monotonic() + seconds
    tested, seen, processes = 0, set(), 0

    def limited(reason='time_limit'):
        return None, {'state': 'limit_reached', 'limit_reason': reason,
                      'processes': processes, 'tested': tested,
                      'read_mib': reader.bytes_read // 1024**2}

    source, _ = provider._discover()
    if time.monotonic() >= deadline:
        return None, {'state': 'limit_reached', 'limit_reason': 'time_limit',
                      'processes': processes, 'tested': tested, 'read_mib': 0}
    with source.open('rb') as stream:
        header = stream.read(HEADER)
        if header[:16] != b'SQLite header 3\x00' or b'QQ_NT DB' not in header[:256]:
            raise RuntimeError('qq_encrypted_database_required')
        page = stream.read(PAGE)
    if len(page) != PAGE:
        raise RuntimeError('qq_database_page_incomplete')
    if time.monotonic() >= deadline:
        return None, {'state': 'limit_reached', 'limit_reason': 'time_limit',
                      'processes': processes, 'tested': tested, 'read_mib': 0}
    reader = MemoryReader()
    reader.scan_budget = max_bytes
    if time.monotonic() >= deadline:
        return None, {'state': 'limit_reached', 'limit_reason': 'time_limit',
                      'processes': processes, 'tested': tested, 'read_mib': 0}
    handles = []
    process_limit_reached = False
    try:
        pids = reader.pids()
        process_limit_reached = bool(getattr(reader, 'process_limit_reached', False))
        if time.monotonic() >= deadline:
            return limited('time_limit')
        for pid in pids:
            if time.monotonic() >= deadline:
                return limited('time_limit')
            if len(handles) >= MAX_PROCESS_CANDIDATES:
                process_limit_reached = True
                break
            handle = reader.open_qq(pid)
            if handle:
                handles.append(handle)
                processes = len(handles)
            if time.monotonic() >= deadline:
                return limited('time_limit')
        processes = len(handles)
        for handle in handles:
            remaining = max_bytes - reader.bytes_scanned
            if remaining <= 0:
                break
            for chunk in reader.chunks(handle, deadline, remaining):
                # Try database-context and documented key forms before generic strings
                # without rereading process memory or spending the shared scan budget twice.
                values = chain(
                    context_keys(chunk, lambda address, size: reader.read(handle, address, size), page),
                    candidates(chunk, True),
                    candidates(chunk, False),
                )
                for value in values:
                    fingerprint = hashlib.sha256(value).digest()
                    if fingerprint in seen:
                        continue
                    expired = time.monotonic() >= deadline
                    if expired or tested >= max_candidates:
                        reason = 'time_limit' if expired else 'candidate_limit'
                        return limited(reason)
                    seen.add(fingerprint)
                    tested += 1
                    if matches_page(value, page):
                        return value.decode('ascii'), {'state': 'verified', 'processes': processes, 'tested': tested, 'read_mib': reader.bytes_read // 1024**2}
        limit_reason = None
        if time.monotonic() >= deadline:
            limit_reason = 'time_limit'
        elif reader.budget_limited or reader.bytes_scanned >= max_bytes:
            limit_reason = 'memory_budget'
        elif process_limit_reached:
            limit_reason = 'process_limit'
        state = 'limit_reached' if limit_reason else 'not_found' if processes else 'no_accessible_qq'
        result = {'state': state, 'processes': processes, 'tested': tested,
                  'read_mib': reader.bytes_read // 1024**2}
        if limit_reason:
            result['limit_reason'] = limit_reason
        return None, result
    finally:
        for handle in handles:
            reader.kernel.CloseHandle(handle)
