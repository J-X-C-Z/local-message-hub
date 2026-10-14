"""PyInstaller entrypoint for the standalone Local Message Hub MCP server."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        storage = local_app_data / "LocalMessageHub"
        storage.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("LMH_STORAGE_DIR", str(storage))
        ffmpeg = Path(sys._MEIPASS) / "media" / "ffmpeg.exe"
        if ffmpeg.is_file():
            os.environ.setdefault("LMH_FFMPEG_PATH", str(ffmpeg))
    from hub.mcp_server import main as mcp_main

    mcp_main()


if __name__ == "__main__":
    main()
