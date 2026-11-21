import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
import sqlite3
import tempfile
import unittest
from attachments import AttachmentSync
from rich_messages import document_card, StickerSync, ImageSync, text_field


class RichTests(unittest.TestCase):
    def test_online_sheet_title_and_link_come_from_native_fields(self):
        raw=text_field(1,'https://doc.weixin.qq.com/sheet/example')+text_field(3,'值班表')+text_field(4,'[表格]')
        card=document_card({'type_code':13,'_raw':raw})
        self.assertEqual(card['title'],'值班表');self.assertEqual(card['kind'],'spreadsheet')
        self.assertIsNone(document_card({'type_code':13,'_raw':text_field(1,'https://example.com/sheet/a')}))
        self.assertIsNone(document_card({'type_code':13,'_raw':text_field(1,'https://user:secret@doc.weixin.qq.com/a')}))

    def test_unknown_reference_does_not_create_a_document_link(self):
        self.assertIsNone(document_card({'type_code':0,'_raw':'[表格]无标题表格'.encode()}))

    def test_sticker_uses_exact_cached_key_and_preserves_gif(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);profile=root/'account'/'Data';profile.mkdir(parents=True)
            private=root/'inbox'/'local-reading';private.mkdir(parents=True)
            emotions=profile.parent/'Emotion';(emotions/'files').mkdir(parents=True)
            source=emotions/'files'/'cached';source.write_bytes(b'GIF89a'+b'\x00'*32)
            conn=sqlite3.connect(emotions/'cache.db');conn.execute('create table mapping(type integer,key text,file_name text)');key='https://p.qpic.cn/emotion/example'
            conn.execute('insert into mapping values(0,?,?)',(key,'files/cached'));conn.commit();conn.close()
            files=AttachmentSync(profile,private,lambda p:Path(p).read_bytes(),[root])
            sync=StickerSync(profile,private,lambda p:Path(p).read_bytes(),files)
            result=sync.sync({'type_code':29,'_raw':text_field(1,key)})
            self.assertEqual(result['status'],'ready');self.assertEqual(result['type'],'GIF')
            target=private.parent/'attachments'/result['id']/'content'/result['name']
            self.assertEqual(target.read_bytes(),source.read_bytes())
            self.assertEqual(sync.sync({'type_code':29,'_raw':text_field(1,'not-cached')})['status'],'pending')


if __name__=='__main__':unittest.main()


class ImageTests(unittest.TestCase):
    def test_thumbnail_upgrades_to_original_and_copied_image_survives_cache_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);profile=root/'account'/'Data';profile.mkdir(parents=True)
            private=root/'inbox'/'local-reading';private.mkdir(parents=True)
            image_root=profile.parent/'Cache'/'Image';image_root.mkdir(parents=True)
            maps=profile.parent/'CacheMapping';maps.mkdir()
            db=maps/'cache.db'
            conn=sqlite3.connect(db);conn.execute('create table mapping(type integer,key text,file_name text)')
            thumb=image_root/'thumb';thumb.write_bytes(b'\x89PNG\r\n\x1a\nthumbnail')
            key='native-image-key'
            conn.execute('insert into mapping values(2,?,?)',(key+'_thumb','thumb'));conn.commit();conn.close()
            def sync():
                files=AttachmentSync(profile,private,lambda p:Path(p).read_bytes(),[root])
                return ImageSync(profile,private,lambda p:Path(p).read_bytes(),files)
            row={'type_code':14,'_raw':text_field(1,key)}
            preview=sync().sync(row);self.assertEqual(preview['status'],'ready');self.assertEqual(preview['quality'],'preview')
            original=image_root/'original';original.write_bytes(b'\xff\xd8\xfforiginal')
            conn=sqlite3.connect(db);conn.execute('insert into mapping values(2,?,?)',(key,'original'));conn.commit();conn.close()
            full=sync().sync(row);self.assertEqual(full['quality'],'original');self.assertNotEqual(full['id'],preview['id'])
            original.unlink();thumb.unlink()
            saved=sync().sync(row);self.assertEqual(saved['status'],'ready');self.assertEqual(saved['quality'],'original')
            self.assertEqual(sync().sync({'type_code':14,'_raw':text_field(1,'different-key')})['status'],'pending')

    def test_non_image_rows_are_not_interpreted_as_images(self):
        sync=ImageSync.__new__(ImageSync)
        self.assertIsNone(sync.sync({'type_code':0,'_raw':'[图片]'.encode()}))
