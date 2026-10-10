from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
_DEFAULT_LOCAL_DIR = (Path(os.environ["LMH_STORAGE_DIR"]) / "integrations" / "wecom"
                      if os.environ.get("LMH_STORAGE_DIR") else PACKAGE_DIR / ".local")
LOCAL_DIR = Path(os.environ.get("WECOM_LOCAL_DIR", _DEFAULT_LOCAL_DIR))
CONFIG_PATH = LOCAL_DIR / "wecom.json"
CONFIG_LOCK = threading.RLock()


def configure(data_dir: str = "", key: str = "", user_slot_id: str = "") -> dict[str, str]:
    """Persist optional UI updates without returning or logging secret values."""
    with CONFIG_LOCK:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            config = {}

        if data_dir.strip():
            candidate = Path(data_dir.strip()).expanduser()
            if not candidate.is_absolute() or not candidate.is_dir():
                raise ValueError("data_dir must be an existing absolute directory")
            config["database_root"] = str(candidate.resolve())

        if key.strip():
            candidate_key = key.strip()
            try:
                raw_key = bytes.fromhex(candidate_key)
            except ValueError as exc:
                raise ValueError("key must be 32 hexadecimal characters") from exc
            if len(candidate_key) != 32 or len(raw_key) != 16:
                raise ValueError("key must be 32 hexadecimal characters")
            config["key_hex"] = candidate_key.lower()

        if user_slot_id.strip():
            slot = user_slot_id.strip()
            if slot in {".", ".."} or "/" in slot or "\\" in slot:
                raise ValueError("账号槽位标识不能包含路径分隔符")
            config["user_slot_id"] = slot

        if not config.get("database_root") or not config.get("key_hex") or not config.get("user_slot_id"):
            raise ValueError("请填写本机数据库目录、数据库密钥和账号槽位标识")

        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=LOCAL_DIR, delete=False
            ) as f:
                temp_name = f.name
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, CONFIG_PATH)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
    return {"configured": "true"}


def local_data_dir() -> str:
    path = LOCAL_DIR / "data"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def load_local_config() -> dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "WeCom local configuration is missing; configure integrations/wecom/.local/wecom.json"
        ) from exc

    overrides = {
        "key_hex": "WECOM_KEY_HEX",
        "database_root": "WECOM_DATABASE_ROOT",
        "user_slot_id": "WECOM_USER_SLOT_ID",
        "decrypt_reference_path": "WECOM_DECRYPT_REFERENCE_PATH",
    }
    for key, env_name in overrides.items():
        if os.environ.get(env_name):
            config[key] = os.environ[env_name]

    paths = config.setdefault("extract_paths", {})
    for key in ("root", "messages", "sessions", "users"):
        env_name = "WECOM_EXTRACT_" + key.upper()
        if os.environ.get(env_name):
            paths[key] = os.environ[env_name]

    missing = [key for key in ("key_hex", "database_root", "user_slot_id") if not config.get(key)]
    if missing:
        raise RuntimeError("WeCom local configuration is incomplete")
    if len(config["key_hex"]) != 32:
        raise RuntimeError("WECOM_KEY_HEX must encode a 16-byte key")
    return config

