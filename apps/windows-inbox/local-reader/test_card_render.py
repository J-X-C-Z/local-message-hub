import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'vendor'))
import unittest
from rich_messages import text_field,varint
from message_render import render_message
from export_wxwork import render
from attachments import AttachmentSync

class CardTests(unittest.TestCase):
    def test_video_does_not_expose_transfer_token_cdn_or_mobile_paths(self):
        raw=(text_field(2,'*1*internal-transfer-token')+text_field(7,'https://wework.qpic.cn/private-resource')+
             text_field(202,'file:///private/mobile/secret.MOV')+varint(24)+varint(26665768)+varint(32)+varint(168))
        decoded=render(22,raw,'windows')
        self.assertEqual(decoded,'[卡片]')
        result=render_message({'type_code':22,'_raw':raw,'content':decoded},{},{})
        self.assertEqual(result['Content'],'[视频]')
        self.assertEqual(result['Video']['duration'],168)
        self.assertNotIn('internal',str(result));self.assertNotIn('qpic',str(result));self.assertNotIn('private',str(result))

    def test_type20_file_preserves_filename_without_transfer_token(self):
        raw=text_field(1,'*1*internal-transfer-token')+text_field(2,'讲义.pdf')+varint(32)+varint(123)
        row={'type_code':20,'_raw':raw,'content':render(20,raw,'windows')}
        self.assertEqual(render_message(row,{},{} )['Content'],'[文件] 讲义.pdf')
        sync=AttachmentSync.__new__(AttachmentSync)
        # Verify envelope dispatch without accessing a real profile.
        original=AttachmentSync.attach
        dispatched=[]
        def attach(row, **options):
            if row['type_code']==15:dispatched.append(row);return {'status':'pending','name':'讲义.pdf'}
            return original(sync,row,**options)
        sync.attach=attach
        self.assertEqual(sync.attach(row)['name'],'讲义.pdf')
        self.assertEqual(dispatched[0]['_raw'],raw)

    def test_unknown_cards_do_not_use_opaque_strings_as_titles(self):
        raw=text_field(1,'*1*secret-transfer-credential')+text_field(2,'https://wework.qpic.cn/asset')
        self.assertEqual(render(123,raw,'windows'),'[卡片]')

    def test_web_and_structured_cards_keep_display_fields_and_destination(self):
        raw=text_field(1,'https://example.com/story?a=1&b=2')+text_field(2,'https://wework.qpic.cn/cover')+text_field(3,'文章')+text_field(4,'摘要')
        result=render_message({'type_code':13,'_raw':raw,'content':'opaque'},{},{})
        self.assertEqual(result['Card'],{'title':'文章','description':'摘要','url':'https://example.com/story?a=1&b=2','label':'网页链接'})
        raw=text_field(2,'{"card_header":{"title":"任务"},"card_desc":{"title":"详情","sub_title":"时间"},"card_action":{"report_key":"private"}}')+text_field(4,'https://example.com/task')
        result=render_message({'type_code':573,'_raw':raw,'content':'opaque'},{},{})
        self.assertEqual(result['Content'],'任务');self.assertEqual(result['Card']['description'],'详情\n时间');self.assertNotIn('private',str(result))

    def test_forwarded_record_renders_text_and_media_without_transport_fields(self):
        words=varint(40)+varint(2)+varint(810)+varint(len(text_field(1,'原消息')))+text_field(1,'原消息')
        media=text_field(1,'*1*private-media')
        photo=varint(40)+varint(14)+varint(826)+varint(len(media))+media
        raw=b''.join(varint(10)+varint(len(item))+item for item in (words,photo))
        result=render_message({'type_code':123,'_raw':raw,'content':'opaque'},{},{},lambda _: {'status':'pending'})
        self.assertEqual(result['ChatRecord']['entries'],[{'kind':'文字','text':'原消息'},{'kind':'图片','text':''}]);self.assertNotIn('private',str(result))

    def test_collaboration_card_uses_text_without_editor_markers(self):
        raw=text_field(2,'{"text":"待办\\u0013事项\\n安排", "attribs":"not-for-display"}')
        result=render_message({'type_code':570,'_raw':raw,'content':'opaque'},{},{})
        self.assertEqual(result['Card']['description'],'待办事项\n安排')
        self.assertNotIn('not-for-display',str(result))

if __name__=='__main__':unittest.main()
