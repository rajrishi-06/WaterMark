"""Image logic with no GUI dependency.

Everything here is importable on a headless machine: it is what the CLI, the
batch runner and the test-suite drive, and what the Tkinter layer sits on top
of.  Nothing in this package may import :mod:`tkinter`.
"""

from __future__ import annotations

from .errors import (
    ExportError,
    FontError,
    ImageTooLargeError,
    PresetError,
    UnsupportedImageError,
    WatermarkError,
)

__all__ = [
    "WatermarkError",
    "UnsupportedImageError",
    "ImageTooLargeError",
    "FontError",
    "ExportError",
    "PresetError",
]
