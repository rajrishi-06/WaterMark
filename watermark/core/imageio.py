"""Reading and writing image files safely.

The original app called ``Image.open`` directly and printed failures to a
console nobody sees.  This module centralises the three things that actually
bite in production:

* **EXIF orientation** — phone photos carry a rotation flag.  Ignoring it is
  why a watermark lands on its side.
* **Decompression bombs** — a 40 000 × 40 000 PNG will freeze a desktop app
  before it ever draws.  Oversized files are rejected with a clear message.
* **Typed errors** — callers get :class:`WatermarkError` subclasses instead of
  raw ``OSError``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import ImageTooLargeError, UnsupportedImageError

#: Extensions offered in file dialogs and scanned during batch runs.
SUPPORTED_EXTENSIONS: Tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".jpe", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".avif", ".heic", ".heif", ".ppm", ".ico",
)

#: Refuse anything above this many megapixels.  Generous for real photography
#: (a 100MP medium-format frame still loads) but stops obvious bombs.
MAX_MEGAPIXELS = 250.0


@dataclass
class SourceImage:
    """A decoded image plus the provenance the exporter needs."""

    image: Image.Image
    path: Optional[str] = None
    source_format: Optional[str] = None
    file_size: int = 0
    exif: Optional[bytes] = None
    icc_profile: Optional[bytes] = None
    dpi: Optional[Tuple[float, float]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def size(self) -> Tuple[int, int]:
        return self.image.size

    @property
    def megapixels(self) -> float:
        width, height = self.image.size
        return width * height / 1_000_000.0

    def describe(self) -> Dict[str, Any]:
        """Facts about the file, for the status bar and ``watermark info``."""
        width, height = self.image.size
        return {
            "path": self.path,
            "format": self.source_format,
            "width": width,
            "height": height,
            "megapixels": round(self.megapixels, 2),
            "mode": self.image.mode,
            "file_size": self.file_size,
            "has_alpha": has_alpha(self.image),
            "has_exif": bool(self.exif),
            "has_icc": bool(self.icc_profile),
        }


def has_alpha(image: Image.Image) -> bool:
    """True when the image carries per-pixel transparency."""
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS


def iter_image_files(directory: str, recursive: bool = False) -> Iterator[str]:
    """Yield supported image paths under ``directory``, sorted for stable batches."""
    if recursive:
        for root, dirs, files in os.walk(directory):
            dirs.sort()
            for filename in sorted(files):
                full = os.path.join(root, filename)
                if is_supported(full):
                    yield full
    else:
        try:
            entries = sorted(os.listdir(directory))
        except OSError as exc:
            raise UnsupportedImageError(f"Cannot read folder: {exc}") from exc
        for filename in entries:
            full = os.path.join(directory, filename)
            if os.path.isfile(full) and is_supported(full):
                yield full


def load_image(path: str, max_megapixels: float = MAX_MEGAPIXELS) -> SourceImage:
    """Decode ``path`` into a :class:`SourceImage`.

    The image is normalised to RGBA or RGB and EXIF-rotated, so every consumer
    downstream can assume upright pixels in a predictable mode.
    """
    if not os.path.isfile(path):
        raise UnsupportedImageError(f"File not found: {path}")

    try:
        handle = Image.open(path)
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError(
            f"{os.path.basename(path)} is not an image file Pillow can read."
        ) from exc
    except OSError as exc:
        raise UnsupportedImageError(f"Could not open {os.path.basename(path)}: {exc}") from exc

    warnings: List[str] = []
    with handle:
        width, height = handle.size
        megapixels = width * height / 1_000_000.0
        if megapixels > max_megapixels:
            raise ImageTooLargeError(
                f"{os.path.basename(path)} is {megapixels:.0f} megapixels "
                f"({width}×{height}); the limit is {max_megapixels:.0f}."
            )

        source_format = handle.format
        exif = handle.info.get("exif")
        icc_profile = handle.info.get("icc_profile")
        dpi = handle.info.get("dpi")
        if getattr(handle, "n_frames", 1) > 1:
            warnings.append(
                f"{os.path.basename(path)} has {handle.n_frames} frames; "
                "only the first is processed."
            )

        try:
            image = handle.convert("RGBA" if has_alpha(handle) else "RGB")
        except OSError as exc:
            raise UnsupportedImageError(
                f"{os.path.basename(path)} is truncated or corrupt: {exc}"
            ) from exc

    # Apply the EXIF orientation flag, then drop it so downstream writers do
    # not rotate the already-rotated pixels a second time.
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:  # pragma: no cover - malformed EXIF
        warnings.append("EXIF orientation tag was unreadable and has been ignored.")

    try:
        file_size = os.path.getsize(path)
    except OSError:
        file_size = 0

    return SourceImage(
        image=image,
        path=path,
        source_format=source_format,
        file_size=file_size,
        exif=exif,
        icc_profile=icc_profile,
        dpi=dpi if isinstance(dpi, tuple) else None,
        warnings=warnings,
    )


def load_overlay(path: str) -> Image.Image:
    """Load a logo/stamp image as RGBA, ready to composite."""
    if not os.path.isfile(path):
        raise UnsupportedImageError(f"Logo file not found: {path}")
    try:
        with Image.open(path) as handle:
            width, height = handle.size
            if width * height / 1_000_000.0 > MAX_MEGAPIXELS:
                raise ImageTooLargeError(f"Logo {os.path.basename(path)} is too large.")
            overlay = handle.convert("RGBA")
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError(
            f"{os.path.basename(path)} is not a readable image."
        ) from exc
    except OSError as exc:
        raise UnsupportedImageError(f"Could not open logo: {exc}") from exc
    return ImageOps.exif_transpose(overlay)
