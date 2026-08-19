"""Font discovery and loading.

The app ships two display faces but everyday users expect their own installed
fonts too, so this module indexes the platform's font directories once and
resolves a saved preset's font *by family name* — a preset made on a Mac still
opens on Windows, falling back to a bundled face when the family is missing.
"""

from __future__ import annotations

import functools
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from PIL import ImageFont

from .errors import FontError

_EXTENSIONS = (".ttf", ".otf", ".ttc")


def bundled_dirs() -> List[str]:
    """Every place the shipped fonts might live.

    Covers an installed wheel, a source checkout, a PyInstaller one-file bundle
    (which unpacks to ``sys._MEIPASS``), and the pre-2.0 layout that kept the
    fonts in a top-level ``fonts/`` folder.
    """
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(package_root, "assets", "fonts"),
        os.path.join(os.path.dirname(package_root), "fonts"),
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, os.path.join(meipass, "fonts"))
        candidates.insert(0, os.path.join(meipass, "watermark", "assets", "fonts"))
    return [path for path in candidates if os.path.isdir(path)]


#: First existing bundled-font directory, or "" when none is present.
BUNDLED_DIR = next(iter(bundled_dirs()), "")

#: Families we prefer when a preset asks for something unavailable.
_FALLBACK_ORDER = ("Tektur SemiCondensed", "DejaVu Sans", "Arial", "Helvetica")


@dataclass(frozen=True)
class FontEntry:
    """One resolvable font face."""

    family: str
    style: str
    path: str
    bundled: bool = False

    @property
    def display_name(self) -> str:
        if self.style and self.style.lower() not in ("regular", "book"):
            return f"{self.family} {self.style}"
        return self.family


def _system_font_dirs() -> List[str]:
    """Font directories for the current platform, most specific first."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return [os.path.join(home, "Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts"]
    if os.name == "nt":
        return [
            os.path.join(os.environ.get("LOCALAPPDATA", home), "Microsoft/Windows/Fonts"),
            os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"),
        ]
    return [
        os.path.join(home, ".fonts"),
        os.path.join(home, ".local/share/fonts"),
        "/usr/share/fonts",
        "/usr/local/share/fonts",
    ]


def _probe(path: str, bundled: bool) -> Optional[FontEntry]:
    """Read a font's family/style, returning ``None`` if it is unreadable."""
    try:
        font = ImageFont.truetype(path, 12)
        family, style = font.getname()
    except Exception:
        return None
    if not family:
        return None
    return FontEntry(family=family, style=style or "Regular", path=path, bundled=bundled)


@functools.lru_cache(maxsize=1)
def available_fonts() -> List[FontEntry]:
    """Every usable font, bundled first then system, sorted by display name.

    Scanning is capped so a machine with thousands of faces cannot stall
    startup; the result is cached for the process lifetime.
    """
    seen: Dict[str, FontEntry] = {}

    def scan(directory: str, bundled: bool, budget: int) -> int:
        if not os.path.isdir(directory):
            return budget
        for root, _dirs, files in os.walk(directory):
            for filename in sorted(files):
                if budget <= 0:
                    return 0
                if not filename.lower().endswith(_EXTENSIONS):
                    continue
                entry = _probe(os.path.join(root, filename), bundled)
                budget -= 1
                if entry and entry.display_name not in seen:
                    seen[entry.display_name] = entry
        return budget

    for directory in bundled_dirs():  # bundled faces get their own small budget
        scan(directory, True, 64)
    budget = 2000
    for directory in _system_font_dirs():
        budget = scan(directory, False, budget)

    bundled = sorted((e for e in seen.values() if e.bundled), key=lambda e: e.display_name)
    system = sorted((e for e in seen.values() if not e.bundled), key=lambda e: e.display_name)
    return bundled + system


def find_font(name: str) -> Optional[FontEntry]:
    """Look up a font by display name, family name, or file path."""
    if not name:
        return None
    if os.path.isfile(name):
        return _probe(name, bundled=False)
    lowered = name.strip().lower()
    entries = available_fonts()
    for entry in entries:
        if entry.display_name.lower() == lowered:
            return entry
    for entry in entries:
        if entry.family.lower() == lowered:
            return entry
    return None


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load ``name`` at ``size`` px, falling back rather than failing.

    A watermark with the wrong typeface still beats a crash mid-batch, so this
    degrades through the fallback list and finally to Pillow's built-in bitmap
    font.
    """
    size = max(1, int(size))
    candidates = [name] + [f for f in _FALLBACK_ORDER if f != name]
    for candidate in candidates:
        entry = find_font(candidate)
        if entry is None:
            continue
        try:
            return ImageFont.truetype(entry.path, size)
        except Exception:
            continue

    for entry in available_fonts():
        try:
            return ImageFont.truetype(entry.path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 9.2 has no size argument
        return ImageFont.load_default()
    except Exception as exc:  # pragma: no cover - defensive
        raise FontError(f"No usable font found: {exc}") from exc
