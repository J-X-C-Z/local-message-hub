import threading
import unittest
from unittest.mock import patch

from hub.account_safety import AccountSafety


class AccountSafetyTest(unittest.TestCase):
    def test_concurrent_first_setup_has_one_winner_and_one_valid_session(self):
        safety = AccountSafety({}, lambda _: None)
        start = threading.Barrier(3)
        results = {}
        phrases = {
            'first': 'a sufficiently long first passphrase',
            'second': 'a sufficiently long second passphrase',
        }

        def setup(label):
            start.wait()
            results[label] = safety.setup(phrases[label])

        threads = [threading.Thread(target=setup, args=(label,)) for label in phrases]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        winners = [label for label, token in results.items() if token]
        self.assertEqual(len(winners), 1)
        winner = winners[0]
        loser = next(label for label in phrases if label != winner)
        self.assertTrue(safety.verify(phrases[winner]))
        self.assertFalse(safety.verify(phrases[loser]))
        self.assertTrue(safety.authenticated(results[winner]))
        self.assertIsNone(results[loser])

    def test_hashes_passphrase_expires_idle_session_and_locks_all_sessions(self):
        settings = {}
        persisted = []
        safety = AccountSafety(settings, lambda value: persisted.append(dict(value)), idle_seconds=10)
        safety.set_passphrase('a sufficiently long local passphrase')
        record = settings['account_lock']
        self.assertNotIn('a sufficiently long local passphrase', str(record))
        self.assertEqual(record['iterations'], 310_000)
        self.assertEqual(len(persisted), 1)
        self.assertFalse(safety.verify('incorrect local passphrase'))
        self.assertTrue(safety.verify('a sufficiently long local passphrase'))
        clock = [100.0]
        with patch('hub.account_safety.time.monotonic', side_effect=lambda: clock[0]):
            token, error = safety.unlock('a sufficiently long local passphrase')
            self.assertEqual(error, '')
            self.assertTrue(safety.authenticated(token))
            clock[0] += 11
            self.assertFalse(safety.authenticated(token))
            token, _ = safety.unlock('a sufficiently long local passphrase')
        safety.lock()
        self.assertFalse(safety.authenticated(token))
        with self.assertRaisesRegex(ValueError, '当前口令不正确'):
            safety.disable('incorrect local passphrase')
        safety.disable('a sufficiently long local passphrase')
        self.assertFalse(safety.configured)
        self.assertEqual(len(persisted), 2)

    def test_unlock_rate_limit_clears_after_cooldown(self):
        safety = AccountSafety({'account_lock': {'salt': '00', 'digest': '00'}}, lambda _: None)
        clock = [100.0]
        with patch('hub.account_safety.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(safety, 'verify', return_value=False):
            for _ in range(5):
                self.assertEqual(safety.unlock('wrong passphrase')[1], 'invalid_passphrase')
            self.assertEqual(safety.unlock('correct passphrase')[1], 'rate_limited')
            clock[0] += 61
            with patch.object(safety, 'verify', return_value=True):
                token, error = safety.unlock('correct passphrase')
            self.assertEqual(error, '')
            self.assertTrue(safety.authenticated(token))

    def test_unlock_reaps_expired_sessions_and_caps_in_memory_sessions(self):
        safety = AccountSafety({'account_lock': {'salt': '00', 'digest': '00'}}, lambda _: None)
        clock = [100.0]
        with patch('hub.account_safety.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(safety, 'verify', return_value=True):
            tokens = []
            for index in range(70):
                clock[0] += 1
                token, error = safety.unlock('a sufficiently long local passphrase')
                self.assertEqual(error, '')
                tokens.append(token)
                self.assertLessEqual(len(safety._sessions), 64)

            self.assertFalse(safety.authenticated(tokens[0]))
            self.assertTrue(safety.authenticated(tokens[-1]))
            clock[0] += safety.idle_seconds + 1
            token, error = safety.unlock('a sufficiently long local passphrase')
            self.assertEqual(error, '')
            self.assertEqual(len(safety._sessions), 1)
            self.assertTrue(safety.authenticated(token))

    def test_concurrent_passphrase_change_cannot_be_removed_by_disable(self):
        safety = AccountSafety({}, lambda _: None)
        old_passphrase = 'a sufficiently long local passphrase'
        new_passphrase = 'another sufficiently long local passphrase'
        safety.set_passphrase(old_passphrase)
        checked, release = threading.Event(), threading.Event()
        updated = threading.Event()
        original_verify = safety.verify

        def pause_after_verify(value):
            valid = original_verify(value)
            checked.set()
            self.assertTrue(release.wait(2))
            return valid

        def update_passphrase():
            try:
                safety.set_passphrase(new_passphrase)
            finally:
                updated.set()

        with patch.object(safety, 'verify', side_effect=pause_after_verify):
            disable = threading.Thread(target=safety.disable, args=(old_passphrase,))
            disable.start()
            self.assertTrue(checked.wait(2))
            update = threading.Thread(target=update_passphrase)
            update.start()
            updated_before_release = updated.wait(0.5)
            release.set()
            disable.join(2)
            update.join(2)

        self.assertFalse(updated_before_release)
        self.assertFalse(disable.is_alive())
        self.assertFalse(update.is_alive())
        self.assertTrue(safety.configured)
        self.assertTrue(safety.verify(new_passphrase))


if __name__ == '__main__':
    unittest.main()
