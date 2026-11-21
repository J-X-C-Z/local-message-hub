import unittest
from message_render import render_message


class RenderTests(unittest.TestCase):
    def row(self, code=1002, raw=b'7881000000000001'):
        return {'content': raw.decode(), 'type_code': code, '_raw': raw,
                'conv_id': 'R:test', 'sender_id': '100', 'send_time': 100000}

    def test_confirmed_invitation_is_system_notice_with_names(self):
        result = render_message(self.row(), {'100': '邀请人', '7881000000000001': '新成员'},
            {('R:test', '7881000000000001'): {'join_time': 100000, 'inviter': '100'}})
        self.assertEqual(result, {'Content': '邀请人 邀请 新成员 加入群聊', 'System': True})

    def test_group_nickname_and_millisecond_times(self):
        row = self.row(); row['send_time'] = 3000000000000
        result = render_message(row, {}, {('R:test', '7881000000000001'):
            {'join_time': 3000000000000, 'name': '群昵称'}})
        self.assertEqual(result['Content'], '群昵称 加入群聊')

    def test_uncached_member_never_shows_internal_id_or_invents_invitation(self):
        result = render_message(self.row(), {'100': '邀请人'}, {})
        self.assertTrue(result['System'])
        self.assertNotIn('7881', result['Content'])
        self.assertNotIn('邀请人 邀请', result['Content'])

    def test_real_numeric_text_is_preserved(self):
        result = render_message(self.row(code=0), {}, {})
        self.assertEqual(result, {'Content': '7881000000000001', 'System': False})

    def test_stale_member_record_does_not_invent_join(self):
        result = render_message(self.row(), {'7881000000000001': '成员'},
            {('R:test', '7881000000000001'): {'join_time': 200000, 'inviter': '100'}})
        self.assertEqual(result['Content'], '群成员变动：成员')

    def test_readable_system_body_is_kept(self):
        result = render_message(self.row(raw='群成员信息更新'.encode()), {}, {})
        self.assertEqual(result, {'Content': '群成员信息更新', 'System': True})


if __name__ == '__main__':
    unittest.main()
