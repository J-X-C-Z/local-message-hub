import json
import os
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import start_hub
from scripts import windows_portable


class HubStartupTests(unittest.TestCase):
    def test_reuses_running_unlocked_or_known_locked_hub(self):
        url = 'http://127.0.0.1:8765'
        statefile = SimpleNamespace(read_text=lambda **_kwargs: json.dumps({'url': url}))
        cases = [(200, {'sources': []}, url),
                 (423, {'code': 'account_locked'}, url),
                 (401, {'code': 'account_locked'}, url),
                 (401, {'error': 'Unauthorized'}, None),
                 (423, {'code': 'another_application'}, None),
                 (200, {'other_service': True}, None)]
        for status, body, expected in cases:
            with self.subTest(status=status, body=body):
                response = BytesIO(json.dumps(body).encode())
                if status == 200:
                    request = patch('start_hub.urlopen', return_value=response)
                else:
                    request = patch('start_hub.urlopen', side_effect=HTTPError(
                        url, status, 'Fixture response', {}, response))
                with request:
                    self.assertEqual(start_hub._running_hub_url(statefile), expected)

    def test_running_hub_probe_rejects_nonloopback_and_invalid_state(self):
        values = [None, {}, {'url': 'https://127.0.0.1:8765'},
                  {'url': 'http://example.invalid:8765'},
                  {'url': 'http://127.0.0.1:8765@other.invalid'},
                  {'url': 'http://127.0.0.1:8765/path'},
                  {'url': 'http://127.0.0.1:99999'}]
        for value in values:
            with self.subTest(value=value), patch('start_hub.urlopen') as request:
                statefile = SimpleNamespace(read_text=lambda **_kwargs: json.dumps(value))
                self.assertIsNone(start_hub._running_hub_url(statefile))
                request.assert_not_called()

    def test_source_launcher_opens_existing_hub_without_spawning(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(start_hub, 'LOCAL', Path(folder)), \
                patch.object(start_hub, '_running_hub_url', return_value='http://127.0.0.1:8765'), \
                patch.object(start_hub.webbrowser, 'open') as browser, \
                patch.object(start_hub.subprocess, 'Popen') as spawn, \
                patch.object(start_hub.subprocess, 'run') as install:
            start_hub.main()
            browser.assert_called_once_with('http://127.0.0.1:8765')
            spawn.assert_not_called()
            install.assert_not_called()

    def test_portable_launcher_reuses_hub_even_without_browser(self):
        for arguments, opens_browser in [(['LocalMessageHub'], True),
                                         (['LocalMessageHub', '--no-browser'], False)]:
            with self.subTest(arguments=arguments), \
                    patch.object(windows_portable, '_configure_runtime'), \
                    patch.object(windows_portable, '_running_hub_url', return_value='http://127.0.0.1:8765'), \
                    patch.object(windows_portable.sys, 'argv', arguments[:]), \
                    patch.object(windows_portable.webbrowser, 'open') as browser, \
                    patch('hub.server.main') as launch:
                windows_portable.main()
                launch.assert_not_called()
                self.assertEqual(browser.call_count, int(opens_browser))

    def test_launcher_pins_repository_managed_qce_runtime(self):
        with patch.dict(os.environ, {'QCE_BUNDLE_DIR': r'C:\stale\runtime'}):
            environment = start_hub._hub_environment()
            self.assertEqual(
                environment['QCE_BUNDLE_DIR'],
                str(start_hub.ROOT / '.runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64'),
            )
            self.assertEqual(os.environ['QCE_BUNDLE_DIR'], r'C:\stale\runtime')

    def test_portable_reuse_probe_honors_explicit_storage(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(windows_portable, '_configure_runtime'), \
                patch.object(windows_portable.sys, 'argv', ['LocalMessageHub', '--storage', folder, '--no-browser']), \
                patch.object(windows_portable, '_running_hub_url', return_value='http://127.0.0.1:8765') as probe, \
                patch('hub.server.main') as launch:
            windows_portable.main()
            probe.assert_called_once_with(Path(folder) / 'server.json')
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
