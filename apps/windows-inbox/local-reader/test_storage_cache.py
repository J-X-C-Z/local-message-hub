import os
from pathlib import Path
import tempfile
import time
import unittest

from storage_cache import cleanup_snapshots, managed_snapshot


class StorageCacheTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows snapshot locks')
    def test_stale_snapshot_removed_and_open_snapshot_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            stale = parent / 'snapshot-12345678'
            stale.mkdir()
            marker = stale / '.inbox-lease'
            marker.write_bytes(b'1')
            old = time.time() - 7200
            os.utime(marker, (old, old))
            legacy = parent / 'snapshot-87654321'
            legacy.mkdir()
            with managed_snapshot(parent) as current:
                current = Path(current)
                self.assertFalse(stale.exists())
                self.assertTrue(legacy.exists())
                os.utime(current / '.inbox-lease', (old, old))
                cleanup_snapshots(parent)
                self.assertTrue(current.exists())
            self.assertFalse(current.exists())
