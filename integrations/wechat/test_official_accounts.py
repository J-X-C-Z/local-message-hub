import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.wechat.official_accounts import get_cached_official_articles, read_official_articles


class CachedOfficialArticlesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.contact_path = root / "contact.db"
        self.message_path = root / "biz_message_0.db"
        self.contact_id = "gh_0123456789ab"
        self.table = "Msg_" + hashlib.md5(self.contact_id.encode()).hexdigest()
        with sqlite3.connect(self.contact_path) as db:
            db.execute("CREATE TABLE contact(username TEXT, local_type INTEGER)")
            db.executemany("INSERT INTO contact VALUES(?,?)", [(self.contact_id, 1), ("gh_aaaaaaaaaaaa", 0)])
        db.close()
        with sqlite3.connect(self.message_path) as db:
            db.execute(f'''CREATE TABLE "{self.table}"(
                local_id INTEGER, server_id TEXT, local_type INTEGER, create_time INTEGER,
                message_content BLOB, compress_content BLOB)''')
            db.executemany(f'INSERT INTO "{self.table}" VALUES(?,?,?,?,?,?)', [
                (1, "server-1", 21474836529, 1_800_000_000,
                 b'<msg><appmsg><mmreader><pub_time>1800000000</pub_time><category><item><title>First article</title><url>http://mp.weixin.qq.com/s?__biz=x&amp;mid=1&amp;idx=1&amp;sn=a&amp;scene=4</url><digest>Summary</digest></item><item><title>Second article</title><url>https://mp.weixin.qq.com/s/second?uin=secret</url></item></category></mmreader></appmsg></msg>', None),
                (2, "server-2", 21474836529, 1_800_000_100,
                 b'<!DOCTYPE x [<!ENTITY y "bad">]><msg/>', None),
                (3, "server-3", 21474836529, 1_800_000_200,
                 b'<msg><appmsg><title>Fallback article</title><url>https://mp.weixin.qq.com/s?__biz=x&amp;mid=2&amp;idx=1&amp;sn=b&amp;pass_ticket=secret</url></appmsg></msg>', None),
            ])
        db.close()

        class Reader:
            _db_files = [("contact/contact.db", str(self.contact_path), 1),
                         ("biz_message/biz_message_0.db", str(self.message_path), 1),
                         ("message/message_0.db", str(self.message_path), 1)]
            _keys = {"biz_message/biz_message_0.db": b"fixture"}

            def _contact_conn(inner):
                return sqlite3.connect(self.contact_path)

            def _open(inner, rel):
                return sqlite3.connect(self.message_path)

        self.reader = Reader()

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_only_exact_official_table_and_normalizes_articles(self):
        result = read_official_articles(self.reader, self.contact_id, limit=20)
        self.assertEqual(result["total"], 3)
        self.assertEqual([item["title"] for item in result["items"]],
                         ["Fallback article", "First article", "Second article"])
        first = result["items"][1]
        self.assertEqual(first["url"], "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=a")
        self.assertEqual(first["published_at"], "2027-01-15")
        self.assertEqual(first["summary"], "Summary")
        self.assertEqual(first["source_message_id"], "server-1")
        self.assertEqual(first["content_status"], "summary_only")
        self.assertEqual(first["timestamp_kind"], "received_at")
        self.assertEqual(result["invalid"], 1)
        self.assertNotIn("pass_ticket", result["items"][0]["url"])

    def test_rejects_non_official_or_malformed_identity_and_caps_limit(self):
        self.assertEqual(read_official_articles(self.reader, "gh_aaaaaaaaaaaa")["items"], [])
        self.assertEqual(read_official_articles(self.reader, "gh_0123456789ab/extra")["invalid"], 1)
        result = read_official_articles(self.reader, self.contact_id, limit=1)
        self.assertEqual(result["total"], 1)

    def test_configured_reader_requires_exact_account_identity(self):
        with patch("integrations.wechat.official_accounts.CONFIG") as config, \
             patch("integrations.wechat.official_accounts.LocalReader") as local_reader:
            config.read_text.return_value = '{"account":"configured-account_b097"}'
            result = get_cached_official_articles("other-account", self.contact_id)
        self.assertEqual(result["warning"], "account_mismatch")
        local_reader.assert_not_called()

    def test_incremental_drains_over_500_same_timestamp_without_skipping_cards(self):
        with sqlite3.connect(self.message_path) as db:
            db.executemany(f'INSERT INTO "{self.table}" VALUES(?,?,?,?,?,?)', [
                (i, str(i), 1, 1_900_000_000,
                 f'<msg><title>Article {i}</title><url>https://mp.weixin.qq.com/s/article-{i}</url></msg>', None)
                for i in range(4, 1104)])
        db.close()
        cursor, urls, pages = {}, set(), 0
        while True:
            report = read_official_articles(self.reader, self.contact_id, cursor=cursor)
            pages += 1
            urls.update(item['url'] for item in report['items'])
            self.assertNotEqual(cursor, report['next_cursor'])
            cursor = report['next_cursor']
            if not report['has_more']:
                break
            self.assertLess(pages, 5)
        self.assertEqual(len(urls), 1103)
        self.assertEqual(pages, 3)
        self.assertEqual(read_official_articles(self.reader, self.contact_id, cursor=cursor)['items'], [])
        with sqlite3.connect(self.message_path) as db:
            db.execute(f'INSERT INTO "{self.table}" VALUES(?,?,?,?,?,?)',
                       (1104, 'next', 1, 1_900_000_000,
                        '<msg><title>Next</title><url>https://mp.weixin.qq.com/s/next</url></msg>', None))
        db.close()
        self.assertEqual(read_official_articles(self.reader, self.contact_id, cursor=cursor)['total'], 1)

    def test_cached_reader_accepts_only_the_canonical_configured_account(self):
        with patch("integrations.wechat.official_accounts.CONFIG") as config, \
             patch("integrations.wechat.official_accounts.WeChatDB._collect_db_files", return_value=[]), \
             patch("integrations.wechat.official_accounts.read_official_articles",
                   return_value={"items": [], "total": 0, "invalid": 0}) as read:
            config.read_text.return_value = '{"account":"configured-account_b097","data_dir":"fixture"}'
            result = get_cached_official_articles("configured-account", self.contact_id)
        self.assertEqual(result["invalid"], 0, result)
        self.assertEqual(read.call_args.args[0].account, "configured-account_b097")


if __name__ == "__main__":
    unittest.main()
