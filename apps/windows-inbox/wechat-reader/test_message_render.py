import unittest
from message_render import message_reference, render_message


class RenderingTest(unittest.TestCase):
    def test_pat_template_resolves_names_and_is_system_notice(self):
        row = {'local_type': 266287972401, 'content': '<msg><appmsg><title>我拍了拍某人</title><type>62</type><patinfo><template>我拍了拍 "${wxid_friend}"</template></patinfo></appmsg></msg>'}
        self.assertEqual(render_message(row, lambda _: '小刘'), {'Content': '我拍了拍 "小刘"', 'System': True})

    def test_pat_missing_name_does_not_display_internal_id(self):
        row = {'local_type': 49, 'content': '<msg><appmsg><type>62</type><patinfo><template>${wxid_missing}拍了拍我</template></patinfo></appmsg></msg>'}
        self.assertEqual(render_message(row)['Content'], '一位成员拍了拍我')

    def test_text_xml_is_preserved(self):
        source = '<msg><appmsg><title>代码示例</title></appmsg></msg>'
        self.assertEqual(render_message({'local_type': 1, 'content': source})['Content'], source)

    def test_link_summary_and_no_transport_fields(self):
        row = {'local_type': 49, 'content': '<msg><appmsg><type>5</type><title>资料</title><des>摘要</des><url>https://example.org/a</url><aeskey>secret</aeskey></appmsg></msg>'}
        self.assertEqual(render_message(row)['Content'], '[链接] 资料\n摘要\nhttps://example.org/a')

    def test_malformed_and_entity_xml_never_leaks(self):
        for source in ['<msg><appmsg>bad', '<msg><!DOCTYPE x>bad</msg>']:
            self.assertNotIn('<', render_message({'local_type': 49, 'type': '卡片', 'content': source})['Content'])

    def test_image_and_revocation(self):
        self.assertEqual(render_message({'local_type': 3, 'content': '<msg><img aeskey="private"/></msg>'})['Content'], '[图片]')
        self.assertEqual(render_message({'local_type': 10000, 'content': '<sysmsg><revokemsg><replacemsg>对方撤回了一条消息</replacemsg></revokemsg></sysmsg>'})['Content'], '对方撤回了一条消息')

    def test_group_prefix_removed_before_structured_parsing(self):
        self.assertEqual(render_message({'local_type': 47, 'content': 'wxid_sender:\n<msg><emoji md5="abc"/></msg>'})['Content'], '[表情包]')
        self.assertEqual(render_message({'local_type': 1, 'content': 'wxid_sender:\n你好'})['Content'], '你好')

    def test_official_article_cards_only_accept_wechat_article_host(self):
        row = {'local_type': 49, 'content': '<msg><appmsg><mmreader><category><item><title>文章标题</title><digest>摘要</digest><url>http://mp.weixin.qq.com/s/abc</url></item><item><title>bad</title><url>https://evil.example/</url></item></category></mmreader></appmsg></msg>'}
        result = render_message(row)
        self.assertEqual(len(result['Articles']), 1)
        self.assertEqual(result['Articles'][0]['url'], 'https://mp.weixin.qq.com/s/abc')

    def test_reply_is_separate_and_keeps_literal_quoted_text(self):
        source = '''<appmsg><type>57</type><title>收到</title><refermsg><type>1</type>
            <displayname>小林</displayname><svrid>9212345678901234567</svrid>
            <content>示例 &lt;msg&gt;\n第二行</content></refermsg></appmsg>'''
        row = {'local_type': 244813135921, 'content': source}
        result = render_message(row, scope='account:room')
        self.assertEqual(result['Content'], '收到')
        quote = result['Quote']
        self.assertEqual(quote['sender'], '小林')
        self.assertEqual(quote['text'], '示例 <msg>\n第二行')
        self.assertEqual(quote['target'], message_reference('account:room', '9212345678901234567'))
        self.assertNotEqual(quote['target'], message_reference('account:other', '9212345678901234567'))
        self.assertNotEqual(quote['target'], message_reference('account:room', '9212345678901234568'))

    def test_quoted_cards_and_nested_replies_use_display_fields_only(self):
        for inner, kind, text in [
            ('<msg><appmsg><type>6</type><title>计划表.xlsx</title><aeskey>transport-secret</aeskey></appmsg></msg>', 'file', '文件 · 计划表.xlsx'),
            ('<appmsg><type>57</type><title>先前的回复</title><refermsg><content>更早的消息</content></refermsg></appmsg>', 'text', '先前的回复'),
            ('<appmsg><type>5</type><mmreader><category><item><title>文章标题</title></item></category></mmreader></appmsg>', 'link', '链接 · 文章标题'),
            ('<!DOCTYPE msg [<!ENTITY a "transport-secret">]><msg>&a;</msg>', 'card', '卡片消息'),
        ]:
            with self.subTest(kind=kind, text=text):
                body = f'<msg><appmsg><type>57</type><title>看一下</title><refermsg><type>49</type><content><![CDATA[{inner}]]></content></refermsg></appmsg></msg>'
                result = render_message({'local_type': 244813135921, 'content': body})
                self.assertEqual(result, {'Content': '看一下', 'Quote': {'sender': '', 'kind': kind, 'text': text}, 'System': False})

    def test_media_and_missing_quotes_have_readable_placeholders(self):
        for code, label in [('3', '图片'), ('47', '表情包'), ('43', '视频'), ('34', '语音'), ('1', '原消息内容不可用')]:
            content = '<content/>' if code == '1' else '<content><![CDATA[<msg aeskey="secret"/>]]></content>'
            row = {'local_type': 244813135921, 'content': f'<appmsg><type>57</type><refermsg><type>{code}</type><svrid>0</svrid>{content}</refermsg></appmsg>'}
            result = render_message(row, scope='account:room')
            self.assertEqual(result['Content'], '')
            self.assertEqual(result['Quote']['text'], label)
            self.assertNotIn('target', result['Quote'])
            self.assertNotIn('secret', str(result))
        result = render_message({'local_type': 244813135921, 'content': '<appmsg><type>57</type></appmsg>'})
        self.assertEqual(result['Quote']['kind'], 'unavailable')

    def test_forwarded_record_exposes_display_fields_only(self):
        body='''<appmsg><type>19</type><title>群聊记录</title><recorditem><![CDATA[
          <recordinfo><datalist><dataitem datatype="1"><datadesc>消息正文</datadesc><sourcename>小陈</sourcename></dataitem>
          <dataitem datatype="2"><cdn_dataurl>transport-secret</cdn_dataurl></dataitem></datalist></recordinfo>]]></recorditem></appmsg>'''
        result=render_message({'local_type':81604378673,'content':body})
        self.assertEqual(result['Content'],'群聊记录')
        self.assertEqual(result['ChatRecord']['entries'][0]['text'],'消息正文')
        self.assertEqual(result['ChatRecord']['entries'][0]['sender'],'小陈')
        self.assertEqual(result['ChatRecord']['entries'][1]['kind'],'图片')
        self.assertNotIn('transport-secret',str(result))


if __name__ == '__main__':
    unittest.main()
