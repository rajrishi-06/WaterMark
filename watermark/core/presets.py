"""Named, reusable job settings.

Presets are how a non-technical user gets a good result without touching
twenty sliders: pick "Confidential — tiled", drop in the photos, export.  The
built-ins ship with the app and are read-only; anything the user saves lands in
their data directory as plain JSON they can back up or share.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .errors import PresetError
from .logs import get_logger
from .models import (
    Anchor,
    ImageFormat,
    JobSettings,
    Placement,
    ResizeMode,
    TileSource,
)
from .paths import ensure_dir, preset_dir

_LOG = get_logger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 _.()-]+")


@dataclass(frozen=True)
class PresetInfo:
    """A preset the user can pick from a list."""

    name: str
    description: str
    builtin: bool
    path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Built-ins
# --------------------------------------------------------------------------- #


def _signature() -> JobSettings:
    settings = JobSettings(name="Signature")
    settings.text.text = "© {year} Your Name"
    settings.text.size_percent = 3.2
    settings.text.opacity = 80
    settings.text.placement = Placement(anchor=Anchor.BOTTOM_RIGHT, margin_percent=3.0)
    return settings


def _copyright_bar() -> JobSettings:
    settings = JobSettings(name="Copyright bar")
    settings.text.text = "© {year} Your Name  ·  {filename}"
    settings.text.size_percent = 2.6
    settings.text.opacity = 95
    settings.text.outline_width_percent = 0.0
    settings.text.backdrop.enabled = True
    settings.text.backdrop.opacity = 45
    settings.text.placement = Placement(anchor=Anchor.BOTTOM_CENTER, margin_percent=2.5)
    return settings


def _confidential() -> JobSettings:
    settings = JobSettings(name="Confidential — tiled")
    settings.text.text = "CONFIDENTIAL"
    settings.text.size_percent = 3.0
    settings.text.opacity = 100
    settings.text.outline_width_percent = 0.0
    settings.tile.enabled = True
    settings.tile.source = TileSource.TEXT
    settings.tile.angle = 30.0
    settings.tile.opacity = 18
    settings.tile.spacing_x_percent = 10.0
    settings.tile.spacing_y_percent = 14.0
    return settings


def _draft_proof() -> JobSettings:
    settings = JobSettings(name="Draft proof")
    settings.text.text = "PROOF"
    settings.text.size_percent = 18.0
    settings.text.opacity = 22
    settings.text.rotation = 30.0
    settings.text.outline_width_percent = 2.0
    settings.text.outline_opacity = 30
    settings.text.placement = Placement(anchor=Anchor.CENTER)
    return settings


def _logo_corner() -> JobSettings:
    settings = JobSettings(name="Logo corner")
    settings.text.enabled = False
    settings.image.enabled = True
    settings.image.scale_percent = 16.0
    settings.image.opacity = 90
    settings.image.placement = Placement(anchor=Anchor.BOTTOM_RIGHT, margin_percent=3.0)
    return settings


def _web_ready() -> JobSettings:
    settings = JobSettings(name="Web ready (1920px JPEG)")
    settings.text.enabled = False
    settings.export.image_format = ImageFormat.JPEG
    settings.export.quality = 82
    settings.export.resize_mode = ResizeMode.FIT_WITHIN
    settings.export.max_dimension = 1920
    settings.export.strip_metadata = True
    return settings


def _email_friendly() -> JobSettings:
    settings = JobSettings(name="Email friendly (500 KB)")
    settings.text.enabled = False
    settings.export.image_format = ImageFormat.JPEG
    settings.export.target_size_kb = 500
    settings.export.resize_mode = ResizeMode.FIT_WITHIN
    settings.export.max_dimension = 2400
    return settings


def _social_square() -> JobSettings:
    settings = JobSettings(name="Social square (1080)")
    settings.text.text = "@yourhandle"
    settings.text.size_percent = 2.8
    settings.text.opacity = 85
    settings.export.image_format = ImageFormat.JPEG
    settings.export.quality = 90
    settings.export.resize_mode = ResizeMode.EXACT
    settings.export.exact_width = 1080
    settings.export.exact_height = 1080
    return settings


def _privacy_scrub() -> JobSettings:
    settings = JobSettings(name="Privacy scrub")
    settings.text.enabled = False
    settings.export.strip_metadata = True
    settings.export.keep_icc_profile = False
    return settings


#: name -> (factory, description).  Order is the order shown in the UI.
BUILTIN_PRESETS: Dict[str, "tuple[Callable[[], JobSettings], str]"] = {
    "Signature": (_signature, "Small copyright line in the bottom-right corner."),
    "Copyright bar": (_copyright_bar, "Readable caption plate along the bottom edge."),
    "Confidential — tiled": (
        _confidential,
        "Diagonal repeating CONFIDENTIAL wash across the whole image.",
    ),
    "Draft proof": (_draft_proof, "Large faint PROOF stamp across the centre."),
    "Logo corner": (_logo_corner, "Your logo in the corner — pick the file, then apply."),
    "Web ready (1920px JPEG)": (
        _web_ready,
        "No watermark: shrink to 1920px and re-encode for the web.",
    ),
    "Email friendly (500 KB)": (
        _email_friendly,
        "No watermark: compress until the file fits under 500 KB.",
    ),
    "Social square (1080)": (_social_square, "Crop to a 1080×1080 square with a handle."),
    "Privacy scrub": (_privacy_scrub, "No watermark: strip EXIF, GPS and colour profiles."),
}


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def safe_filename(name: str) -> str:
    """Turn a preset name into a filesystem-safe basename."""
    cleaned = _SAFE_NAME.sub("_", name).strip() or "preset"
    return cleaned[:80] + ".json"


def list_presets() -> List[PresetInfo]:
    """Built-in presets first, then the user's own, alphabetically."""
    items = [
        PresetInfo(name=name, description=description, builtin=True)
        for name, (_factory, description) in BUILTIN_PRESETS.items()
    ]
    directory = preset_dir()
    if os.path.isdir(directory):
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(directory, filename)
            name = filename[:-5]
            description = "Saved preset"
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                name = data.get("name") or name
                description = data.get("description") or description
            except (OSError, ValueError):
                _LOG.warning("Ignoring unreadable preset %s", path)
                continue
            items.append(PresetInfo(name=name, description=description, builtin=False, path=path))
    return items


def load_preset(name: str) -> JobSettings:
    """Load a preset by name, checking built-ins first."""
    if name in BUILTIN_PRESETS:
        settings = BUILTIN_PRESETS[name][0]()
        settings.name = name
        return settings

    for info in list_presets():
        if not info.builtin and info.name == name and info.path:
            try:
                with open(info.path, "r", encoding="utf-8") as handle:
                    return JobSettings.from_json_dict(json.load(handle))
            except (OSError, ValueError, TypeError) as exc:
                raise PresetError(f"Preset '{name}' could not be read: {exc}") from exc
    raise PresetError(f"No preset named '{name}'.")


def save_preset(name: str, settings: JobSettings, description: str = "") -> str:
    """Write a user preset and return its path."""
    name = name.strip()
    if not name:
        raise PresetError("A preset needs a name.")
    if name in BUILTIN_PRESETS:
        raise PresetError(f"'{name}' is a built-in preset — choose a different name.")

    directory = ensure_dir(preset_dir())
    path = os.path.join(directory, safe_filename(name))
    payload = settings.to_json_dict()
    payload["name"] = name
    if description:
        payload["description"] = description
    try:
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError as exc:
        raise PresetError(f"Could not save preset: {exc}") from exc
    return path


def delete_preset(name: str) -> None:
    """Remove a user preset.  Built-ins cannot be deleted."""
    if name in BUILTIN_PRESETS:
        raise PresetError(f"'{name}' is built in and cannot be deleted.")
    for info in list_presets():
        if not info.builtin and info.name == name and info.path:
            try:
                os.remove(info.path)
            except OSError as exc:
                raise PresetError(f"Could not delete preset: {exc}") from exc
            return
    raise PresetError(f"No preset named '{name}'.")
