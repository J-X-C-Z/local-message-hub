"""PyInstaller entrypoint for the Local Message Hub portable Windows build."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import webbrowser

from start_hub import _running_hub_url


def _configure_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    storage = local_app_data / "LocalMessageHub"
    storage.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LMH_STORAGE_DIR", str(storage))
    os.environ.setdefault("WECOM_LOCAL_DIR", str(storage / "integrations" / "wecom"))
    os.environ.setdefault("QCE_SECURITY_FILE", str(storage / "qq" / "security.json"))
    ffmpeg = Path(sys._MEIPASS) / "media" / "ffmpeg.exe"
    if ffmpeg.is_file():
        os.environ.setdefault("LMH_FFMPEG_PATH", str(ffmpeg))

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
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--storage", default=os.environ.get(
        "LMH_STORAGE_DIR", str(Path(__file__).resolve().parents[1] / ".local")))
    storage = Path(parser.parse_known_args()[0].storage)
    running_url = _running_hub_url(storage / "server.json")
    if running_url:
        if open_browser:
            webbrowser.open(running_url)
        return

    if open_browser:
        def open_when_ready() -> None:
            state_path = storage / "server.json"
            for _ in range(120):
                url = _running_hub_url(state_path)
                if url:
                    webbrowser.open(url)
                    return
                time.sleep(0.25)

        threading.Thread(target=open_when_ready, daemon=True).start()
    from hub.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
