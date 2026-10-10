"""PyInstaller entrypoint for the Local Message Hub portable Windows build."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import json
import threading
import time
from urllib.request import urlopen
import webbrowser


def _configure_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    storage = local_app_data / "LocalMessageHub"
    storage.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LMH_STORAGE_DIR", str(storage))
    os.environ.setdefault("WECOM_LOCAL_DIR", str(storage / "integrations" / "wecom"))
    os.environ.setdefault("QCE_SECURITY_FILE", str(storage / "qq" / "security.json"))

    packaged_qce = Path(sys._MEIPASS) / ".runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64"
    installed_qce = storage / "runtime/qq/v6.3.2/shell/NapCat-QCE-Windows-x64"
    if packaged_qce.is_dir() and not installed_qce.exists():
        installed_qce.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(packaged_qce, installed_qce)
    os.environ.setdefault("QCE_BUNDLE_DIR", str(installed_qce))


def main() -> None:
    _configure_runtime()
    open_browser = "--no-browser" not in sys.argv
    if not open_browser:
        sys.argv.remove("--no-browser")
    storage = Path(os.environ.get("LMH_STORAGE_DIR", Path(__file__).resolve().parents[1] / ".local"))

    if open_browser:
        def open_when_ready() -> None:
            state_path = storage / "server.json"
            for _ in range(120):
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    url = state.get("url")
                    if isinstance(url, str) and url.startswith("http://127.0.0.1:"):
                        with urlopen(url + "/api/sources", timeout=1):
                            webbrowser.open(url)
                            return
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)

        threading.Thread(target=open_when_ready, daemon=True).start()
    from hub.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
