"""Current-user Windows login startup for background message indexing."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "LocalMessageHubAutoIndex"


def _supported():
    return os.name == "nt"


def startup_command(root):
    """Return the command Windows should run at login without opening the UI."""
    root = Path(root).resolve()
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        arguments = [str(executable), "--background"]
    else:
        pythonw = root / "integrations/wechat/runtime/venv/Scripts/pythonw.exe"
        executable = pythonw if pythonw.is_file() else Path(sys.executable).resolve()
        arguments = [str(executable), str(root / "start_hub.py"), "--background"]
    return subprocess.list2cmdline(arguments)


def _registered_command():
    if not _supported():
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, value_type = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    return value if value_type == winreg.REG_SZ and isinstance(value, str) else None


def status(root):
    command = startup_command(root)
    return {
        "supported": _supported(),
        "installed": _registered_command() == command,
        "command": command,
    }


def set_enabled(root, enabled):
    """Install or remove the current user's login entry and return its status."""
    if not _supported():
        raise RuntimeError("当前系统不支持 Windows 登录启动")
    if not isinstance(enabled, bool):
        raise ValueError("自动索引启动开关无效")

    import winreg

    if enabled:
        with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, startup_command(root))
    else:
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                    winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
    return status(root)
