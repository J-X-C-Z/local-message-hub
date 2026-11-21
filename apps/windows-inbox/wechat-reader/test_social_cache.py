import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from social_cache import parse_moment
from reader import Reader


class MomentsTests(unittest.TestCase):
    def test_feed_does_not_expose_urls_keys_or_deleted_comments(self):
        xml = '''<SnsDataItem><TimelineObject><username>test-user</username>
        <createTime>1795100000</createTime><contentDesc>一条动态</contentDesc>
        <ContentObject><mediaList><media><type>2</type>
        <url md5="11111111111111111111111111111111" key="private">https://example.test/private</url>
        </media></mediaList></ContentObject></TimelineObject><LocalExtraInfo>
        <comment_user_list><user_comment><nickname>联系人</nickname><content>留言</content></user_comment>
        <user_comment><nickname>已删除</nickname><b_deleted>1</b_deleted><content>不可显示</content></user_comment></comment_user_list>
        </LocalExtraInfo></SnsDataItem>'''
        post = parse_moment(3, 'test-user', xml, lambda username: '发布者', {'test-user': 'a' * 64})
        self.assertEqual(post['author'], '发布者')
        self.assertEqual(post['avatar'], 'a' * 64)
        self.assertEqual(post['comments'], [{'author': '联系人', 'text': '留言'}])
        self.assertNotIn('example.test', str(post))
        self.assertNotIn('private', str(post))
        self.assertIsNone(parse_moment(3, 'another-user', xml, str, {}))

    def test_missing_session_resolves_from_message_shard(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder) / 'message_0.db'
            digest = hashlib.md5(b'older-conversation').hexdigest()
            with sqlite3.connect(filename) as connection:
                connection.execute('CREATE TABLE Name2Id(user_name TEXT, is_session INTEGER)')
                connection.execute('INSERT INTO Name2Id VALUES (?, 0)', ('older-conversation',))
                connection.execute(f'CREATE TABLE Msg_{digest}(local_id INTEGER)')
            connection.close()

            class FixtureReader(Reader):
                def __init__(self):
                    pass

                def get_sessions(self, limit):
                    return [{'username': 'current-conversation'}]

                def _build_md5_index(self):
                    return {}

                def _message_dbs(self):
                    return [filename]

                def _open(self, source):
                    return sqlite3.connect(source)

            self.assertEqual(FixtureReader().inbox_sessions(), [
                {'username': 'current-conversation'}, {'username': 'older-conversation'}])


if __name__ == '__main__':
    unittest.main()
