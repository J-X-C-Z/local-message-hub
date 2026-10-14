import json
from pathlib import Path
import tempfile
import unittest

from hub.streaming_json import iter_export_messages


class StreamingJsonTest(unittest.TestCase):
    def test_bom_nested_values_and_large_integer_match_standard_parser(self):
        document = {'metadata': {'messages': [1]}, 'messages': [
            {'id': 18446744073709551619, 'text': '\u4e2d\u6587', 'ratio': 1.25,
             'content': {'items': [{'name': 'a'}, None]}}, False, [1, 2]],
            'tail': {'count': 3}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.json'
            path.write_text(json.dumps(document), encoding='utf-8-sig')
            self.assertEqual(list(iter_export_messages(path)), document['messages'])

    def test_invalid_export_is_rejected_before_any_row_is_yielded(self):
        invalid = ['{"messages":[{"id":1}],"tail":',
                   '{"messages":[] } trailing', '{"messages":{}}',
                   '[]', '{}', '{"messages":[],"messages":[]}']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.json'
            for payload in invalid:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        next(iter_export_messages(path))

    def test_empty_array_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.json'
            path.write_text('{"messages":[]}', encoding='utf-8')
            self.assertEqual(list(iter_export_messages(path)), [])
