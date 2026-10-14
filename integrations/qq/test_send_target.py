import unittest

from integrations.qq.send_target import (SendTargetError, is_uin, resolve_send_target)


class SendTargetTest(unittest.TestCase):
    def test_numeric_identifiers_pass_through_unchanged(self):
        for kind, value in (('private', '2747277822'), ('group', '1105185565')):
            with self.subTest(kind=kind, value=value):
                self.assertEqual(resolve_send_target(value, kind), {'conversation_id': value})

    def test_private_uid_is_mapped_through_the_uid_table(self):
        result = resolve_send_target('u_-PBswiplK-7J7bmaQLA-mA', 'private',
                             {'u_-PBswiplK-7J7bmaQLA-mA': '2747277822'})
        self.assertEqual(result, {'conversation_id': '2747277822'})

    def test_unknown_private_uid_is_refused_without_guessing(self):
        with self.assertRaises(SendTargetError) as failure:
            resolve_send_target('u_unknown0000000000000', 'private', {})
        self.assertEqual(failure.exception.code, 'send_target_private_unresolved')
        self.assertIn('QQ', str(failure.exception))

    def test_group_peer_must_already_be_a_group_code(self):
        with self.assertRaises(SendTargetError) as failure:
            resolve_send_target('u_group000000000000', 'group', {})
        self.assertEqual(failure.exception.code, 'send_target_group_unresolved')

    def test_mappings_that_are_not_numeric_are_rejected(self):
        for uins in ({'u_x': 'not-a-uin'}, {'u_x': '12'}, {'u_x': '27472778221' * 2},
                     {'u_x': None}, {'u_x': 2747277822}, 'not-a-mapping', None):
            with self.subTest(uins=uins):
                with self.assertRaises(SendTargetError):
                    resolve_send_target('u_x', 'private', uins)

    def test_missing_identity_and_unknown_kind_are_rejected(self):
        for identity, kind in (('', 'private'), (None, 'private'), (None, 'group'),
                               ('123456', 'channel'), ('1' * 21, 'private')):
            with self.subTest(identity=identity, kind=kind):
                with self.assertRaises(SendTargetError):
                    resolve_send_target(identity, kind)

    def test_is_uin_bounds_match_the_onebot_contract(self):
        for value in ('12345', '2747277822', '1' * 20):
            with self.subTest(value=value):
                self.assertTrue(is_uin(value))
        for value in ('', None, '1234', '1' * 21, 'u_abcd', ' 123456', '12345 ', '12345a'):
            with self.subTest(value=value):
                self.assertFalse(is_uin(value))


if __name__ == '__main__':
    unittest.main()
