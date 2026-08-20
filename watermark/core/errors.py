"""Exception hierarchy for the watermark core.

Every failure the core raises derives from :class:`WatermarkError` so callers
(the GUI, the CLI, batch workers) can present one friendly message instead of
guessing which of Pillow's many exception types leaked through.
"""

from __future__ import annotations


class WatermarkError(Exception):
    """Base class for every error raised by :mod:`watermark.core`."""

    #: Short, user-facing sentence.  Subclasses fill this in.
    def __str__(self) -> str:  # pragma: no cover - trivial
        return super().__str__() or self.__class__.__name__


class UnsupportedImageError(WatermarkError):
    """The file is not an image, or uses a codec Pillow cannot decode."""


class ImageTooLargeError(WatermarkError):
    """The image exceeds the configured pixel budget (decompression bomb guard)."""


class FontError(WatermarkError):
    """A font file could not be loaded."""


class ExportError(WatermarkError):
    """The processed image could not be encoded or written."""


class PresetError(WatermarkError):
    """A preset file is missing, unreadable or malformed."""
