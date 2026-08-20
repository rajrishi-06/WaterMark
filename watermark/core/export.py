"""Encoding, compression and file writing.

This is the half of the app the original had none of.  It covers the things
people actually need after adding a watermark: shrink the file before emailing
it, convert a 12MB PNG screenshot to a 300KB WebP, hit a hard upload limit, and
strip the GPS coordinates out of a holiday photo before posting it.

Everything encodes through an in-memory buffer first, so the size shown in the
UI is the real byte count and a failed encode never leaves a truncated file on
disk.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageColor, features

from .errors import ExportError
from .imageio import SourceImage, has_alpha
from .models import ExportSettings, ImageFormat, ResizeMode
from .transforms import resize_to, scaled_size

#: Pillow's plugin name for each format we offer.
_PIL_FORMAT = {
    ImageFormat.PNG: "PNG",
    ImageFormat.JPEG: "JPEG",
    ImageFormat.WEBP: "WEBP",
    ImageFormat.AVIF: "AVIF",
    ImageFormat.TIFF: "TIFF",
}

_EXTENSION_FORMAT = {
    ".png": ImageFormat.PNG,
    ".jpg": ImageFormat.JPEG,
    ".jpeg": ImageFormat.JPEG,
    ".jpe": ImageFormat.JPEG,
    ".webp": ImageFormat.WEBP,
    ".avif": ImageFormat.AVIF,
    ".tif": ImageFormat.TIFF,
    ".tiff": ImageFormat.TIFF,
}


@dataclass
class EncodeResult:
    """The encoded bytes plus what it took to get there."""

    data: bytes
    image_format: ImageFormat
    size: Tuple[int, int]
    quality: Optional[int] = None
    scale: float = 1.0
    palette_colors: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    @property
    def byte_count(self) -> int:
        return len(self.data)


def available_formats() -> List[ImageFormat]:
    """Formats this Pillow build can actually write.

    AVIF and WebP depend on optional native libraries; offering a format that
    then fails at save time is worse than not offering it.
    """
    formats = [ImageFormat.KEEP, ImageFormat.PNG, ImageFormat.JPEG, ImageFormat.TIFF]
    if features.check("webp"):
        formats.insert(3, ImageFormat.WEBP)
    try:
        if features.check("avif"):
            formats.append(ImageFormat.AVIF)
    except ValueError:  # Pillow builds that predate the avif feature flag
        pass
    return formats


def format_for_path(path: str) -> Optional[ImageFormat]:
    """Infer the output format from a filename's extension."""
    return _EXTENSION_FORMAT.get(os.path.splitext(path)[1].lower())


def resolve_format(settings: ExportSettings, source: Optional[SourceImage] = None) -> ImageFormat:
    """Turn ``KEEP`` into a concrete format using the source file.

    Sources we cannot write back (HEIC, ICO, animated GIF …) fall back to PNG
    so "keep original format" never dead-ends.
    """
    if settings.image_format is not ImageFormat.KEEP:
        return settings.image_format
    if source is not None:
        if source.path:
            inferred = format_for_path(source.path)
            if inferred:
                return inferred
        name = (source.source_format or "").upper()
        for image_format, pil_name in _PIL_FORMAT.items():
            if pil_name == name:
                return image_format
    return ImageFormat.PNG


def flatten(image: Image.Image, matte_color: str = "#FFFFFF") -> Image.Image:
    """Composite transparency onto a solid colour, for formats without alpha.

    Without this, saving a watermarked RGBA image as JPEG raises ``OSError``
    mid-batch — the single most common crash in the original app.
    """
    if not has_alpha(image):
        return image.convert("RGB")
    try:
        rgb = ImageColor.getrgb(matte_color)[:3]
    except ValueError:
        rgb = (255, 255, 255)
    background = Image.new("RGB", image.size, rgb)
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    background.paste(rgba, (0, 0), rgba)
    return background


def prepare_image(
    image: Image.Image,
    settings: ExportSettings,
    image_format: ImageFormat,
    scale: float = 1.0,
) -> Image.Image:
    """Resize and colour-convert an image so ``image_format`` can encode it."""
    target = scaled_size(
        image.size,
        settings.resize_mode,
        settings.max_dimension,
        settings.scale_percent,
        (settings.exact_width, settings.exact_height),
    )
    if scale != 1.0:
        target = (max(1, round(target[0] * scale)), max(1, round(target[1] * scale)))
    if target != image.size:
        image = resize_to(image, target, cover=settings.resize_mode is ResizeMode.EXACT)

    if not image_format.supports_alpha:
        return flatten(image, settings.matte_color)
    if image.mode not in ("RGB", "RGBA", "L", "P"):
        return image.convert("RGBA" if has_alpha(image) else "RGB")
    return image


def _is_opaque(image: Image.Image) -> bool:
    """True when an image's alpha channel carries no actual transparency."""
    try:
        return image.getchannel("A").getextrema() == (255, 255)
    except (ValueError, KeyError):
        return True


def _save_kwargs(
    settings: ExportSettings,
    image_format: ImageFormat,
    source: Optional[SourceImage],
    quality: Optional[int],
) -> Dict[str, object]:
    """Build the per-format keyword arguments for ``Image.save``."""
    quality = settings.quality if quality is None else quality
    quality = max(1, min(100, int(quality)))
    kwargs: Dict[str, object] = {}

    if image_format is ImageFormat.JPEG:
        kwargs.update(
            quality=quality,
            optimize=settings.optimize,
            progressive=settings.progressive,
            # 4:4:4 keeps coloured text crisp; -1 lets the encoder decide.
            subsampling=settings.subsampling,
        )
    elif image_format is ImageFormat.PNG:
        kwargs.update(optimize=settings.optimize, compress_level=settings.png_compress_level)
    elif image_format is ImageFormat.WEBP:
        kwargs.update(quality=quality, lossless=settings.lossless, method=6)
    elif image_format is ImageFormat.AVIF:
        kwargs.update(quality=quality)
    elif image_format is ImageFormat.TIFF:
        kwargs.update(compression="tiff_lzw")

    if settings.dpi:
        kwargs["dpi"] = (settings.dpi, settings.dpi)
    elif source is not None and source.dpi:
        kwargs["dpi"] = source.dpi

    if not settings.strip_metadata and source is not None and source.exif:
        kwargs["exif"] = source.exif
    if settings.keep_icc_profile and source is not None and source.icc_profile:
        kwargs["icc_profile"] = source.icc_profile
    return kwargs


def _encode_prepared(
    prepared: Image.Image,
    settings: ExportSettings,
    image_format: ImageFormat,
    source: Optional[SourceImage],
    quality: Optional[int],
    palette_colors: Optional[int],
    scale: float,
) -> EncodeResult:
    """Encode an image that has already been resized and colour-converted."""
    colors = None
    if image_format is ImageFormat.PNG and palette_colors:
        colors = max(2, min(256, int(palette_colors)))
        # Quantizing a screenshot or logo to a palette routinely cuts a PNG by
        # 60-80% with no visible change; photographs will band, hence opt-in.
        #
        # Median cut picks a noticeably better palette -- gradients band badly
        # under fast octree -- but it rejects an alpha channel outright, and
        # every watermarked render carries one.  Most of those are fully
        # opaque, so drop the dead channel and keep the better method; only
        # genuinely transparent images fall back to fast octree.
        #
        # `dither` is deliberately not passed: Pillow ignores it unless a
        # reference palette is supplied.
        if has_alpha(prepared) and not _is_opaque(prepared):
            prepared = prepared.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
        else:
            prepared = prepared.convert("RGB").quantize(
                colors=colors, method=Image.Quantize.MEDIANCUT
            )

    buffer = io.BytesIO()
    kwargs = _save_kwargs(settings, image_format, source, quality)
    try:
        prepared.save(buffer, format=_PIL_FORMAT[image_format], **kwargs)
    except (OSError, ValueError, KeyError) as exc:
        raise ExportError(f"Could not encode {image_format.value.upper()}: {exc}") from exc

    return EncodeResult(
        data=buffer.getvalue(),
        image_format=image_format,
        size=prepared.size,
        quality=kwargs.get("quality"),  # type: ignore[arg-type]
        scale=scale,
        palette_colors=colors,
        notes=[],
    )


def encode(
    image: Image.Image,
    settings: ExportSettings,
    source: Optional[SourceImage] = None,
    image_format: Optional[ImageFormat] = None,
    quality: Optional[int] = None,
    scale: float = 1.0,
    palette_colors: Optional[int] = -1,
) -> EncodeResult:
    """Encode ``image`` to bytes without touching the filesystem."""
    image_format = image_format or resolve_format(settings, source)
    if image_format is ImageFormat.KEEP:
        image_format = ImageFormat.PNG
    colors = settings.png_palette_colors if palette_colors == -1 else palette_colors
    prepared = prepare_image(image, settings, image_format, scale)
    return _encode_prepared(prepared, settings, image_format, source, quality, colors, scale)


def compress_to_target(
    image: Image.Image,
    settings: ExportSettings,
    target_bytes: int,
    source: Optional[SourceImage] = None,
    min_quality: int = 25,
) -> EncodeResult:
    """Encode as close to ``target_bytes`` as possible without going over.

    Two stages, in the order that costs the least visible quality:

    1. Binary-search the encoder quality (lossy formats only).
    2. If the smallest acceptable quality is still too big, shrink the pixel
       dimensions in 12% steps and search again (up to ten steps, i.e. down to
       roughly a quarter of the original edge length).

    The best under-budget result wins; if nothing fits, the smallest attempt is
    returned with an explanatory note so the caller can warn instead of failing.
    """
    image_format = resolve_format(settings, source)
    if image_format is ImageFormat.KEEP:
        image_format = ImageFormat.PNG
    target_bytes = max(1024, int(target_bytes))

    best: Optional[EncodeResult] = None
    smallest: Optional[EncodeResult] = None

    # Resizing dominates the cost of a search, so each scale step is prepared
    # once and every quality probe re-encodes those same pixels.
    prepared_cache: Dict[float, Image.Image] = {}

    def attempt(quality: Optional[int], scale: float, colors: Optional[int] = -1) -> EncodeResult:
        nonlocal best, smallest
        if scale not in prepared_cache:
            prepared_cache[scale] = prepare_image(image, settings, image_format, scale)
        if colors == -1:
            colors = settings.png_palette_colors
        result = _encode_prepared(
            prepared_cache[scale], settings, image_format, source, quality, colors, scale
        )
        if smallest is None or result.byte_count < smallest.byte_count:
            smallest = result
        if result.byte_count <= target_bytes:
            if best is None or result.byte_count > best.byte_count:
                best = result
        return result

    scale = 1.0
    for _step in range(10):
        if image_format.is_lossy and not settings.lossless:
            low, high = min_quality, 95
            attempt(high, scale)
            if best is not None and best.scale == scale:
                break  # top quality already fits at this size
            for _ in range(7):
                if high - low <= 2:
                    break
                middle = (low + high) // 2
                if attempt(middle, scale).byte_count <= target_bytes:
                    low = middle
                else:
                    high = middle
            attempt(low, scale)
        else:
            attempt(None, scale)
            if image_format is ImageFormat.PNG and best is None:
                # Lossless formats have no quality dial, so trade colours next.
                for colors in (256, 128, 64, 32):
                    if attempt(None, scale, colors).byte_count <= target_bytes:
                        break

        if best is not None:
            break
        scale *= 0.88
        if min(image.size) * scale < 32:
            break

    if best is not None:
        if best.scale < 1.0:
            best.notes.append(
                f"Scaled to {best.size[0]}×{best.size[1]} to reach the size target."
            )
        return best

    assert smallest is not None
    smallest.notes.append(
        f"Could not reach {target_bytes // 1024} KB; smallest achievable was "
        f"{smallest.byte_count // 1024} KB."
    )
    return smallest


def render_to_bytes(
    image: Image.Image,
    settings: ExportSettings,
    source: Optional[SourceImage] = None,
) -> EncodeResult:
    """Encode honouring ``target_size_kb`` when one is set."""
    if settings.target_size_kb:
        return compress_to_target(image, settings, settings.target_size_kb * 1024, source)
    return encode(image, settings, source)


def save_image(
    image: Image.Image,
    path: str,
    settings: ExportSettings,
    source: Optional[SourceImage] = None,
) -> EncodeResult:
    """Encode and write to ``path``, creating parent folders as needed.

    The file is written to a temporary sibling and then moved into place, so an
    interrupted save cannot destroy an existing file.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ExportError(f"Cannot create folder {directory}: {exc}") from exc

    result = render_to_bytes(image, settings, source)
    temporary = f"{path}.part"
    try:
        with open(temporary, "wb") as handle:
            handle.write(result.data)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise ExportError(f"Could not write {os.path.basename(path)}: {exc}") from exc
    return result


def human_size(byte_count: int) -> str:
    """Format a byte count the way a file manager would."""
    value = float(byte_count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
