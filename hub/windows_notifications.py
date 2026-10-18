"""Submit privacy-minimal notifications to the Windows notification center."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _valid_open_url(value: str) -> bool:
    import re
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})/#notification=[A-Za-z0-9_-]{32}', value)
    return bool(match and 1 <= int(match.group(1)) <= 65535)


def show_notification(message: str, open_url: str | None = None) -> None:
    if os.name != 'nt':
        raise RuntimeError('Windows 桌面通知仅支持 Windows 本机 Hub')
    script = (Path(sys._MEIPASS) / 'hub/windows_toast.ps1'
              if getattr(sys, 'frozen', False) else Path(__file__).with_name('windows_toast.ps1'))
    args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-WindowStyle', 'Hidden',
            '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Title', '本地消息整合中枢',
            '-Message', message]
    if open_url is not None:
        if not _valid_open_url(open_url):
            raise RuntimeError('Windows 通知目标无效')
        args.extend(['-OpenUrl', open_url])
    result = subprocess.run(
        args,
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode or result.stdout.strip() != 'submitted':
        raise RuntimeError('Windows 未接受桌面通知')
