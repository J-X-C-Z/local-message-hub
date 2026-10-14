"""Synthetic WeChat provider tests; never inspect the user's account data."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .provider import WeChatProvider, avatar_database_status
from .vendor.db import WeChatDB


class WeChatContactTests(unittest.TestCase):
    def provider_for(self, connection):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        provider.reader._contact_conn.return_value = connection
        return provider

    def test_contacts_read_verified_avatar_url_columns_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contact.db"
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("""CREATE TABLE contact (
                username TEXT, nick_name TEXT, remark TEXT, alias TEXT,
                big_head_img_url TEXT, small_head_img_url TEXT, profile_url TEXT
            )""")
            conn.executemany("INSERT INTO contact VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("wxid-one", "One", "Remark", "alias-one", "",
                 "https://wx.qlogo.cn/mmhead/one?size=100", "https://evil.invalid/avatar"),
                ("wxid-two", "Two", "", "", "", "https://wx.qlogo.cn/mmhead/two", ""),
            ])
            conn.commit()

            contacts = self.provider_for(conn).contacts()

        self.assertEqual(contacts, [
            {"id": "wxid-one", "platform": "wechat", "name": "Remark",
             "nickname": "One", "alias": "alias-one", "kind": "contact",
             "avatar_url": "https://wx.qlogo.cn/mmhead/one?size=100"},
            {"id": "wxid-two", "platform": "wechat", "name": "Two",
             "nickname": "Two", "alias": "", "kind": "contact",
             "avatar_url": "https://wx.qlogo.cn/mmhead/two"},
        ])

    def test_exact_display_name_lookup_returns_all_matching_local_contacts(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, remark TEXT)")
        conn.executemany("INSERT INTO contact VALUES (?, ?, ?)", [
            ("wxid-one", "Friend", "Shared"),
            ("wxid-two", "Shared", ""),
            ("wxid-three", "Other", ""),
        ])
        conn.commit()
        reader = WeChatDB.__new__(WeChatDB)
        reader._contact_conn = lambda: conn

        self.assertEqual(reader.usernames_for_display_name("Shared"), ["wxid-one", "wxid-two"])

    def test_contacts_ignore_unknown_or_unsafe_avatar_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contact.db"
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, profile_url TEXT)")
            conn.execute("INSERT INTO contact VALUES (?, ?, ?)",
                         ("wxid-one", "One", "https://wx.qlogo.cn.evil.invalid/avatar"))
            conn.commit()

            contacts = self.provider_for(conn).contacts()

        self.assertEqual(contacts, [{"id": "wxid-one", "platform": "wechat",
                                     "name": "One", "nickname": "One", "alias": None,
                                     "kind": "contact"}])

    def test_contacts_classify_only_verified_local_type_and_normalize_biz_kind(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE contact (username TEXT, nick_name TEXT, local_type INTEGER);
            CREATE TABLE biz_info (username TEXT, type INTEGER, secret TEXT);
        """)
        conn.executemany("INSERT INTO contact VALUES (?, ?, ?)", [
            ("gh_subscription", "订阅", 1), ("gh_not_official", "未验证", 0),
            ("wxid-user", "好友", 1),
        ])
        conn.executemany("INSERT INTO biz_info VALUES (?, ?, ?)", [
            ("gh_subscription", 0, "must-not-leak"),
            ("gh_not_official", 1, "must-not-leak"),
        ])
        conn.commit()

        contacts = self.provider_for(conn).contacts()

        self.assertEqual(contacts[0]["kind"], "official")
        self.assertEqual(contacts[0]["official_account_kind"], "subscription")
        self.assertEqual(contacts[1]["kind"], "contact")
        self.assertNotIn("official_account_kind", contacts[1])
        self.assertEqual(contacts[2]["kind"], "contact")
        self.assertNotIn("local_type", contacts[0])
        self.assertNotIn("secret", str(contacts))

    def test_contacts_without_database_return_empty(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        provider.reader._contact_conn.return_value = None

        self.assertEqual(provider.contacts(), [])

    def test_avatar_database_status_reports_only_presence_and_key_state(self):
        self.assertEqual(avatar_database_status(Mock(_db_files=[], _keys={})), {
            "state": "missing", "message": "未发现头像数据库"})
        locked = Mock(_db_files=[("Contact/head_image.db", "C:/private/head_image.db", 8192)],
                      _keys={})
        self.assertEqual(avatar_database_status(locked), {
            "state": "locked", "message": "头像数据库存在，但当前没有可用密钥"})
        unlocked = Mock(_db_files=[("Contact/head_image.db", "C:/private/head_image.db", 8192)],
                        _keys={"Contact/head_image.db": b"synthetic-key"})
        self.assertEqual(avatar_database_status(unlocked), {
            "state": "available", "message": "头像数据库密钥可用；同步时读取本机头像"})

    def test_avatar_buffers_require_cached_key_verified_schema_and_exact_username(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE head_image (username TEXT, md5 TEXT, image_buffer BLOB, update_time INTEGER)")
        conn.executemany("INSERT INTO head_image VALUES (?, ?, ?, ?)", [
            ("wxid-one", "not-used", b"\xff\xd8\xffone", 1),
            ("wxid-two", "not-used", b"GIF89a", 1),
            ("wxid-large", "not-used", b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024), 1),
        ])
        conn.commit()
        reader = SimpleNamespace(_db_files=[("Contact/head_image.db", "head_image.db", 1)],
                                 _keys={"Contact/head_image.db": b"cached"},
                                 _open=Mock(return_value=conn), account="wxid-self")
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = reader

        self.assertEqual(provider.avatar_buffers(["wxid-one", "wxid-two", "wxid-large", "missing"]),
                         {"wxid-one": b"\xff\xd8\xffone"})
        reader._open.assert_called_once_with("Contact/head_image.db")
        self.assertRegex(provider.local_avatar_url("wxid-one"), r"^hub-avatar://wechat/[a-f0-9]{64}$")

    def test_avatar_buffers_prefers_newest_valid_cached_avatar(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE head_image (username TEXT, md5 TEXT, image_buffer BLOB, update_time INTEGER)")
        conn.executemany("INSERT INTO head_image VALUES (?, ?, ?, ?)", [
            ("wxid-one", "new", b"\xff\xd8\xffnew", 20),
            ("wxid-one", "old", b"\xff\xd8\xffold", 10),
        ])
        conn.commit()
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(
            _db_files=[("Contact/head_image.db", "head_image.db", 1)],
            _keys={"Contact/head_image.db": b"cached"},
            _open=Mock(return_value=conn), account="wxid-self")

        self.assertEqual(provider.avatar_buffers(["wxid-one"]),
                         {"wxid-one": b"\xff\xd8\xffnew"})
        conn.close()

    def test_avatar_buffers_never_open_without_cached_key_or_exact_schema(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(_db_files=[("Contact/head_image.db", "head_image.db", 1)],
                                          _keys={}, _open=Mock())
        self.assertEqual(provider.avatar_buffers(["wxid-one"]), {})
        provider.reader._keys["Contact/head_image.db"] = b"cached"
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE head_image (username TEXT, image_buffer BLOB)")
        provider.reader._open.return_value = conn
        self.assertEqual(provider.avatar_buffers(["wxid-one"]), {})
        conn.close()

    def test_avatar_buffers_allow_additive_head_image_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE head_image (username TEXT, md5 TEXT, image_buffer BLOB, "
                     "update_time INTEGER, source_version INTEGER)")
        conn.execute("INSERT INTO head_image VALUES (?, ?, ?, ?, ?)",
                     ("wxid-one", "unused", b"\xff\xd8\xffsynthetic", 1, 2))
        conn.commit()
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(
            _db_files=[("Contact/head_image.db", "head_image.db", 1)],
            _keys={"Contact/head_image.db": b"cached"},
            _open=Mock(return_value=conn), account="wxid-self")

        self.assertEqual(provider.avatar_buffers(["wxid-one"]),
                         {"wxid-one": b"\xff\xd8\xffsynthetic"})
        conn.close()


class WeChatMediaDispatchTests(unittest.TestCase):
    def test_image_key_is_loaded_only_for_image_media(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(wxid='wxid-self', account='fallback')
        provider.reader.get_message_row = Mock(return_value={'local_type': 34})
        provider.image_key_store = Mock()
        downloader = Mock()
        downloader.download_voice.return_value = 'synthetic.amr'
        with patch('integrations.wechat.provider.HubMediaDownloader', return_value=downloader) as factory:
            self.assertTrue(provider.media('room', 7, 'message.db')['available'])
        provider.image_key_store.get.assert_not_called()
        self.assertIsNone(factory.call_args.kwargs['image_key'])

    def test_image_media_injects_current_account_key(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(wxid='wxid-self', account='fallback')
        provider.reader.get_message_row = Mock(return_value={'local_type': 3})
        provider.image_key_store = Mock()
        provider.image_key_store.get.return_value = 'ImageKey12345678'
        downloader = Mock()
        downloader.download_image.return_value = 'synthetic.jpg'
        with patch('integrations.wechat.provider.HubMediaDownloader', return_value=downloader) as factory:
            self.assertTrue(provider.media('room', 7, 'message.db')['available'])
        provider.image_key_store.get.assert_called_once_with('wxid-self')
        self.assertEqual(factory.call_args.kwargs['image_key'], 'ImageKey12345678')

    def test_missing_image_key_is_reported_as_unavailable(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(wxid='wxid-self', account='fallback')
        provider.reader.get_message_row = Mock(return_value={'local_type': 3})
        provider.image_key_store = Mock()
        provider.image_key_store.get.return_value = None
        downloader = Mock()
        downloader.download_image.side_effect = RuntimeError('无法获取图片 AES 密钥：无可用密钥')

        with patch('integrations.wechat.provider.HubMediaDownloader', return_value=downloader):
            result = provider.media('room', 7, 'message.db')

        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'image_key_unavailable')

    def test_image_key_is_validated_before_account_scoped_persistence(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = SimpleNamespace(wxid='wxid-self', account='fallback')
        provider.image_key_store = Mock()
        provider.image_key_store.set.return_value = {'configured': True}
        downloader = Mock()
        downloader._validate_key.return_value = True
        with patch('integrations.wechat.provider.HubMediaDownloader', return_value=downloader):
            result = provider.set_image_key('ImageKey12345678')
        self.assertEqual(result, {'configured': True})
        provider.image_key_store.set.assert_called_once_with('wxid-self', 'ImageKey12345678')

        downloader._validate_key.return_value = False
        with patch('integrations.wechat.provider.HubMediaDownloader', return_value=downloader):
            with self.assertRaisesRegex(ValueError, '校验'):
                provider.set_image_key('ImageKey12345678')
        provider.image_key_store.set.assert_called_once()

    def test_video_variant_resolves_cached_poster_without_changing_video_path(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        provider.reader.get_message_row.return_value = {"local_type": 43}
        downloader = Mock()
        downloader.download_video_thumbnail.return_value = "synthetic-poster.jpg"
        downloader.download_video.return_value = "synthetic-video.mp4"
        with patch("integrations.wechat.provider.HubMediaDownloader", return_value=downloader):
            poster = provider.media("room", 7, "message.db", video_variant="poster")
            video = provider.media("room", 7, "message.db")

        self.assertEqual(poster["path"], "synthetic-poster.jpg")
        downloader.download_video_thumbnail.assert_called_once_with(
            "room", 7, shard="message.db", thumbnail=False)
        self.assertEqual(video["path"], "synthetic-video.mp4")
        downloader.download_video.assert_called_once_with("room", 7, shard="message.db")

    def test_video_variant_is_bounded_and_only_applies_to_video(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        provider.reader.get_message_row.return_value = {"local_type": 3}
        with patch("integrations.wechat.provider.HubMediaDownloader"):
            with self.assertRaisesRegex(ValueError, "Video variant"):
                provider.media("room", 7, "message.db", video_variant="remote")
            with self.assertRaisesRegex(ValueError, "only available for WeChat videos"):
                provider.media("room", 7, "message.db", video_variant="poster")

    def test_message_pages_preserve_server_id_as_exact_string(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        row = {"local_id": 3, "local_type": 1, "shard": "message_0.db",
               "sender_username": "wxid-a", "is_self": False, "create_time": 10,
               "sort_seq": 10, "type": 1, "content": "hello",
               "server_id": 6116895530414915131}
        provider.reader.get_messages.return_value = [row]
        provider.reader.get_messages_after.return_value = [row]

        self.assertEqual(provider.messages("room")[0]["server_id"], "6116895530414915131")
        self.assertEqual(provider.messages_after("room")["messages"][0]["server_id"],
                         "6116895530414915131")

    def test_dispatches_all_weflow_file_local_types_without_truncating(self):
        for local_type in (49, 34359738417, 103079215153, 25769803825):
            with self.subTest(local_type=local_type):
                provider = WeChatProvider.__new__(WeChatProvider)
                provider.reader = Mock()
                provider.reader.get_message_row.return_value = {"local_type": local_type}
                downloader = Mock()
                downloader.download_file.return_value = "synthetic-file"
                with patch("integrations.wechat.provider.HubMediaDownloader",
                           return_value=downloader):
                    result = provider.media("session", 7, "message.db")

                self.assertEqual(result["path"], "synthetic-file")
                self.assertTrue(result["available"])
                downloader.download_file.assert_called_once_with(
                    "session", 7, shard="message.db")


class WeChatEmojiCacheTests(unittest.TestCase):
    def provider_for(self, root, account_dir, account='wxid-self_ab12'):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.emoji_cache_dir = Path(root)
        provider.reader = SimpleNamespace(account_dir=str(account_dir), account=account)
        return provider

    def test_resolves_only_current_account_files_with_supported_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Emojis'
            own_account = Path(directory) / 'account-self'
            provider = self.provider_for(root, own_account)
            gif_md5 = 'a' * 32
            png_md5 = 'b' * 32
            cache = provider.emoji_cache_account_dir()
            cache.mkdir(parents=True)
            gif = cache / f'{gif_md5}.gif'
            png = cache / f'{png_md5}.png'
            gif.write_bytes(b'GIF89a')
            png.write_bytes(b'\x89PNG\r\n\x1a\n')

            self.assertEqual(provider.resolve_cached_emoji(gif_md5), gif.resolve())
            self.assertEqual(provider.resolve_cached_emoji(png_md5), png.resolve())
            self.assertIsNone(provider.resolve_cached_emoji('../' + gif_md5))
            self.assertIsNone(provider.resolve_cached_emoji('c' * 32))

    def test_rejects_other_account_namespace_and_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'Emojis'
            current = self.provider_for(root, Path(directory) / 'account-current')
            other = self.provider_for(root, Path(directory) / 'account-other', 'wxid-other_cd34')
            md5 = 'd' * 32
            other_dir = other.emoji_cache_account_dir()
            other_dir.mkdir(parents=True)
            (other_dir / f'{md5}.gif').write_bytes(b'GIF89a')
            self.assertIsNone(current.resolve_cached_emoji(md5))
            own_dir = current.emoji_cache_account_dir()
            own_dir.mkdir(parents=True)
            outside = Path(directory) / 'outside.gif'
            outside.write_bytes(b'GIF89a')
            try:
                (own_dir / f'{md5}.gif').symlink_to(outside)
            except OSError:
                self.skipTest('symlinks are unavailable in this environment')
            self.assertIsNone(current.resolve_cached_emoji(md5))


if __name__ == "__main__":
    unittest.main()
