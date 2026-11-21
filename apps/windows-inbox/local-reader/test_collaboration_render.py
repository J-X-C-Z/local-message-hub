import unittest
from message_render import render_message


class CollaborationTest(unittest.TestCase):
    def test_native_utf8_notice_is_readable_system_event(self):
        text = '张三提交了结果'
        self.assertEqual(render_message({'type_code':1011, 'content':'[协作事件]', '_raw':text.encode()}, {}, {}),
                         {'Content':text, 'System':True})

    def test_plain_text_notice(self):
        self.assertEqual(render_message({'type_code':1011, 'content':'[协作事件]', '_raw':'你加入了此表格'}, {}, {})['Content'], '你加入了此表格')

    def test_invalid_binary_never_shown_as_text(self):
        result = render_message({'type_code':1011, 'content':'[协作事件]', '_raw':b'\xff\x00'}, {}, {})
        self.assertTrue(result['System'])
        self.assertEqual(result['Content'], '协作通知（内容暂无法解析）')


if __name__ == '__main__': unittest.main()
