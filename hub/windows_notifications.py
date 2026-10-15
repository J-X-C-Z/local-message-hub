"""Submit privacy-minimal notifications to the Windows notification center."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def show_notification(message: str) -> None:
    if os.name != 'nt':
        raise RuntimeError('Windows 桌面通知仅支持 Windows 本机 Hub')
    script = (Path(sys._MEIPASS) / 'hub/windows_toast.ps1'
              if getattr(sys, 'frozen', False) else Path(__file__).with_name('windows_toast.ps1'))
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-WindowStyle', 'Hidden',
         '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Title', '本地消息整合中枢',
         '-Message', message],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode or result.stdout.strip() != 'submitted':
        raise RuntimeError('Windows 未接受桌面通知')
