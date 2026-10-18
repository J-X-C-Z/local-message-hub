import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from hub.windows_notifications import show_notification


@unittest.skipUnless(os.name == 'nt', 'Windows notification transport')
class WindowsNotificationTest(unittest.TestCase):
    def test_transport_passes_only_validated_loopback_activation(self):
        target = 'http://127.0.0.1:8772/#notification=' + 'a' * 32
        with patch('hub.windows_notifications.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, 'submitted\n', '')
            show_notification('QQ received a message', target)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index('-OpenUrl') + 1], target)
            self.assertEqual(args[args.index('-Message') + 1], 'QQ received a message')
            self.assertEqual(run.call_args.kwargs['timeout'], 15)
            run.reset_mock()
            with self.assertRaisesRegex(RuntimeError, '通知目标无效'):
                show_notification('test', 'https://example.com/#notification=' + 'a' * 32)
            run.assert_not_called()

    def test_toast_xml_body_and_button_open_same_opaque_target_without_showing(self):
        script_path = str(Path(__file__).with_name('windows_toast.ps1')).replace("'", "''")
        # Execute only the XML builder extracted by the PowerShell parser: no registration or Show.
        command = r"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('__SCRIPT__', [ref]$null, [ref]$null)
$function = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-LmhToastXml'}, $true)
Invoke-Expression $function.Extent.Text
$url = 'http://127.0.0.1:8772/#notification=' + ('a' * 32)
$invalid = @(
    'https://example.com/#notification=' + ('a' * 32)
    'http://127.0.0.1:0/#notification=' + ('a' * 32)
    'http://127.0.0.1:65536/#notification=' + ('a' * 32)
    'http://127.0.0.1:8772/#NOTIFICATION=' + ('a' * 32)
    'http://127.0.0.1:8772/#notification=' + ('a' * 32) + '&account=private'
    'http://127.0.0.1:8772/#notification=' + ('a' * 32) + "`n"
)
$rejected = @($invalid | ForEach-Object {
    try { Get-LmhToastXml -Title 'test' -Message 'test' -OpenUrl $_ | Out-Null; $false }
    catch { $true }
})
@{
    target = Get-LmhToastXml -Title 'Hub & <test>' -Message 'Body-free <notice>' -OpenUrl $url
    plain = Get-LmhToastXml -Title 'Hub' -Message 'Fixed test'
    rejected = $rejected
} | ConvertTo-Json -Compress
""".replace('__SCRIPT__', script_path)
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=15, check=True)
        output = json.loads(result.stdout)
        root = ET.fromstring(output['target'])
        expected_url = 'http://127.0.0.1:8772/#notification=' + 'a' * 32
        self.assertEqual(root.attrib, {'activationType': 'protocol', 'launch': expected_url})
        button = root.find('actions/action')
        self.assertEqual(button.get('content'), '\u6253\u5f00\u4f1a\u8bdd')
        self.assertEqual(button.get('activationType'), 'protocol')
        self.assertEqual(button.get('arguments'), expected_url)
        self.assertEqual([node.text for node in root.findall('visual/binding/text')],
                         ['Hub & <test>', 'Body-free <notice>'])
        plain = ET.fromstring(output['plain'])
        self.assertEqual(plain.attrib, {})
        self.assertIsNone(plain.find('actions'))
        self.assertEqual(output['rejected'], [True] * 6)


if __name__ == '__main__':
    unittest.main()
