"""Per-user application directories.

Follows each platform's convention so the app leaves nothing behind in the
user's home directory or next to the executable.
"""

from __future__ import annotations

import os
import sys

APP_DIRNAME = "WaterMark"


def _base_dir(kind: str) -> str:
    home = os.path.expanduser("~")
    if os.name == "nt":
        root = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(root, APP_DIRNAME, kind)
    if sys.platform == "darwin":
        root = os.path.join(home, "Library", "Application Support")
        if kind == "logs":
            return os.path.join(home, "Library", "Logs", APP_DIRNAME)
        return os.path.join(root, APP_DIRNAME)
    xdg = {
        "config": os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"),
        "data": os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"),
        "logs": os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state"),
    }[kind if kind in ("config", "data", "logs") else "config"]
    return os.path.join(xdg, APP_DIRNAME.lower())


def config_dir() -> str:
    """Directory holding ``settings.json``."""
    return _override("WATERMARK_CONFIG_DIR", _base_dir("config"))


def data_dir() -> str:
    """Directory holding user presets."""
    return _override("WATERMARK_DATA_DIR", _base_dir("data"))


def log_dir() -> str:
    """Directory holding rotating log files."""
    return _override("WATERMARK_LOG_DIR", _base_dir("logs"))


def preset_dir() -> str:
    return os.path.join(data_dir(), "presets")


def _override(variable: str, default: str) -> str:
    """Environment overrides keep tests (and portable installs) off real dirs."""
    return os.environ.get(variable) or default


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path
