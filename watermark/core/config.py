"""Persisted user preferences.

Remembers the things people resent re-entering: recent files, the last output
folder, window size, theme, and the settings they were last using.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .logs import get_logger
from .models import JobSettings, from_dict, to_dict
from .paths import config_dir, ensure_dir

_LOG = get_logger(__name__)
CONFIG_FILENAME = "settings.json"
MAX_RECENT = 12


@dataclass
class AppConfig:
    """Everything the app remembers between launches."""

    theme: str = "dark"
    recent_files: List[str] = field(default_factory=list)
    last_open_dir: str = ""
    last_output_dir: str = ""
    last_logo_path: str = ""
    window_geometry: str = ""
    #: Longest preview edge in pixels — lower is faster on old machines.
    preview_resolution: int = 1100
    remember_settings: bool = True
    last_settings: Optional[Dict[str, Any]] = None
    batch_workers: int = 4

    # -- recent files ----------------------------------------------------- #

    def remember_file(self, path: str) -> None:
        """Move ``path`` to the front of the recent list, de-duplicated."""
        if not path:
            return
        path = os.path.abspath(path)
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        del self.recent_files[MAX_RECENT:]
        self.last_open_dir = os.path.dirname(path)

    def existing_recent_files(self) -> List[str]:
        """Recent entries that are still on disk."""
        return [path for path in self.recent_files if os.path.isfile(path)]

    # -- job settings ------------------------------------------------------ #

    def store_settings(self, settings: JobSettings) -> None:
        self.last_settings = to_dict(settings) if self.remember_settings else None

    def restore_settings(self) -> JobSettings:
        if self.remember_settings and self.last_settings:
            try:
                return JobSettings.from_json_dict(self.last_settings)
            except Exception:
                _LOG.warning("Stored settings were unreadable; using defaults", exc_info=True)
        return JobSettings()


def config_path() -> str:
    return os.path.join(config_dir(), CONFIG_FILENAME)


def load_config() -> AppConfig:
    """Read the config file, falling back to defaults on any problem."""
    path = config_path()
    if not os.path.isfile(path):
        return AppConfig()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return from_dict(AppConfig, json.load(handle))
    except (OSError, ValueError, TypeError):
        _LOG.warning("Could not read %s; starting with defaults", path, exc_info=True)
        return AppConfig()


def save_config(config: AppConfig) -> bool:
    """Write the config atomically.  Returns ``False`` if it could not be saved."""
    path = config_path()
    try:
        ensure_dir(os.path.dirname(path))
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(to_dict(config), handle, indent=2)
        os.replace(temporary, path)
        return True
    except OSError:
        _LOG.warning("Could not save settings to %s", path, exc_info=True)
        return False
