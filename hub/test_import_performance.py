"""Regression checks for indexed conversation lookup during batched import."""
import json
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hub.server import Hub


class ImportBatchTest(unittest.TestCase):
    def test_indexed_lookup_preserves_order_duplicates_and_resume(self):
        with tempfile.TemporaryDirectory() as directory, closing(Hub(directory, resume_imports=False)) as hub:
            conversations = [{'id': str(i), 'title': str(i)} for i in range(1200)]
            conversations.append({'id': '700', 'title': 'last duplicate'})
            messages = [{'platform': 'email', 'account_id': 'fixture', 'id': str(i),
                         'conversation_id': str(i)} for i in range(1200)]
            job = hub.store.create_import_job({
                'id': 'import-perf', 'fingerprint': 'fixture', 'filename': 'fixture.json',
                'staging_file': 'import-perf.json', 'created_at': 1,
                'total': 1200, 'valid': 1200, 'invalid': 0, 'errors': [],
            })
            hub.store.update_import_job(job['id'], processed=500)
            stage = Path(directory) / 'imports' / 'import-perf.json'
            stage.write_text(json.dumps({'messages': messages, 'conversations': conversations}))
            batches = []
            def ingest(cancel, platform, account, convs, rows):
                expected = [c for c in conversations if c['id'] in {m['conversation_id'] for m in rows}]
                self.assertEqual(convs, expected)
                batches.append(len(rows))
                return {'messages': len(rows), 'conversations': len(convs)}
            with patch.object(hub, '_import_ingest', side_effect=ingest):
                hub._run_import_job(job['id'], threading.Event())
            self.assertEqual(batches, [500, 200])
            self.assertEqual(hub.store.import_job(job['id'])['state'], 'completed')
            self.assertFalse(stage.exists())


if __name__ == '__main__':
    unittest.main()
