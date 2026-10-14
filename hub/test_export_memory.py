"""ChatLab keeps one projected record at a time, not a second message list."""

import json
import tempfile
import tracemalloc
import unittest

from .exports import render_export, write_export


class ChatLabMemoryTest(unittest.TestCase):
    def test_large_chatlab_exports_keep_projection_memory_bounded(self):
        rows = [{"id": str(index), "sender_id": "sender", "platform": "qq",
                 "account_id": "account", "conversation_kind": "private",
                 "text": "x" * 1024, "timestamp": index}
                for index in range(3000)]
        for format in ("chatlab", "chatlab-jsonl"):
            with self.subTest(format=format):
                tracemalloc.start()
                try:
                    payload = render_export(rows, format)[0]
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, len(payload) * 1.6 + 512 * 1024)
                if format == "chatlab":
                    records = json.loads(payload)["messages"]
                else:
                    records = [record for line in payload.splitlines()
                               if (record := json.loads(line))["_type"] == "message"]
                self.assertEqual(len(records), len(rows))
                self.assertEqual([r["platformMessageId"] for r in records],
                                 [r["id"] for r in rows])

    def test_stream_writer_memory_does_not_scale_with_export_bytes(self):
        formats = ("json", "csv", "md", "txt", "html", "xlsx", "sql", "chatlab", "chatlab-jsonl")

        def rows():
            for index in range(3000):
                yield {"id": str(index), "sender_id": "sender", "sender_name": "Sender",
                       "platform": "qq", "account_id": "account", "conversation_id": "room",
                           "conversation_kind": "private", "text": "x" * 1024,
                           "timestamp": index}

        for format_id in formats:
            with self.subTest(format=format_id):
                tracemalloc.start()
                try:
                    with tempfile.TemporaryFile("w+b") as output:
                        write_export(rows(), output, format_id, count=3000,
                                     source_platforms=("qq",),
                                     members=({"platformId": "sender", "accountName": "Sender",
                                               "platform": "qq", "accountId": "account"},))
                        output.flush()
                        output.seek(0, 2)
                        output_bytes = output.tell()
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertGreater(output_bytes, 100_000)
                self.assertLess(peak, 2 * 1024 * 1024)

    def test_chatlab_empty_and_single_pass_inputs(self):
        for rows in ([], [{"id": "one", "conversation_kind": "private",
                           "text": "hello", "timestamp": "bad"}]):
            document = json.loads(render_export(iter(rows), "chatlab")[0])
            lines = [json.loads(line) for line in
                     render_export(iter(rows), "chatlab-jsonl")[0].splitlines()]
            self.assertEqual(len(document["messages"]), len(rows))
            self.assertEqual(document["messages"],
                             [{k: v for k, v in item.items() if k != "_type"}
                              for item in lines if item["_type"] == "message"])


if __name__ == "__main__":
    unittest.main()
