"""Geometry applied to the source image before watermarking."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PIL import Image

from .models import ResizeMode, TransformSettings

#: A crop keeps at least this fraction of each axis, so the user can never drag
#: the rectangle down to nothing and lose the image.
MIN_CROP_FRACTION = 0.02


def normalize_crop(crop: Optional[Sequence[float]]) -> Optional[List[float]]:
    """Clamp crop insets and collapse a no-op crop to ``None``.

    Insets are fractions taken off each edge — ``(left, top, right, bottom)``.
    Storing them as fractions rather than pixels keeps a crop meaningful when
    the same preset is applied to images of different sizes.
    """
    if not crop or len(crop) != 4:
        return None
    left, top, right, bottom = (max(0.0, min(1.0, float(value))) for value in crop)

    def fit(near: float, far: float) -> Tuple[float, float]:
        """Shrink opposing insets so at least MIN_CROP_FRACTION of the axis survives.

        Both are scaled by the same factor rather than each giving up half the
        excess: subtracting equally cannot work when one side is already at
        zero, which would leave the axis over budget.  Scaling also keeps the
        crop centred where the user put it.
        """
        limit = 1.0 - MIN_CROP_FRACTION
        total = near + far
        if total <= limit:
            return near, far
        factor = limit / total
        return near * factor, far * factor

    left, right = fit(left, right)
    top, bottom = fit(top, bottom)
    if max(left, top, right, bottom) < 1e-6:
        return None
    return [left, top, right, bottom]


def crop_box(
    crop: Optional[Sequence[float]], size: Tuple[int, int]
) -> Tuple[int, int, int, int]:
    """Convert crop insets into a pixel box ``(left, top, right, bottom)``."""
    width, height = size
    normalized = normalize_crop(crop)
    if normalized is None:
        return 0, 0, width, height
    left, top, right, bottom = normalized
    return (
        int(round(left * width)),
        int(round(top * height)),
        int(round(width - right * width)),
        int(round(height - bottom * height)),
    )


def crop_from_box(
    box: Sequence[float], size: Tuple[int, int]
) -> Optional[List[float]]:
    """Convert a pixel box back into crop insets.  Inverse of :func:`crop_box`."""
    width, height = size
    if width <= 0 or height <= 0:
        return None
    left, top, right, bottom = box
    return normalize_crop([
        left / width,
        top / height,
        (width - right) / width,
        (height - bottom) / height,
    ])


def apply_transform(image: Image.Image, settings: TransformSettings) -> Image.Image:
    """Flip, rotate and crop ``image`` according to ``settings``.

    Order matters, and crop comes **last**: you straighten a horizon and then
    trim, not the other way round.  It also means the crop rectangle the user
    drags on the preview is in the same coordinate space as what they see,
    including the empty corners an off-axis rotation leaves behind.
    """
    if settings.is_identity():
        return image

    result = image
    if settings.flip_horizontal:
        result = result.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if settings.flip_vertical:
        result = result.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    angle = settings.rotation % 360
    if angle:
        # Right angles are lossless and much faster than a resampled rotate.
        quarter = {90: Image.Transpose.ROTATE_90,
                   180: Image.Transpose.ROTATE_180,
                   270: Image.Transpose.ROTATE_270}
        if angle in quarter:
            result = result.transpose(quarter[angle])
        else:
            fill = (0, 0, 0, 0) if result.mode == "RGBA" else (255, 255, 255)
            result = result.rotate(
                angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=fill
            )

    if normalize_crop(settings.crop) is not None:
        box = crop_box(settings.crop, result.size)
        # A degenerate crop would raise; ignore it rather than kill the render.
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            result = result.crop(box)
    return result


def scaled_size(
    size: Tuple[int, int],
    mode: ResizeMode,
    max_dimension: int = 2048,
    scale_percent: float = 100.0,
    exact: Tuple[int, int] = (1920, 1080),
) -> Tuple[int, int]:
    """Compute the target size for a resize mode without touching pixels."""
    width, height = size
    if mode is ResizeMode.NONE or width <= 0 or height <= 0:
        return width, height
    if mode is ResizeMode.FIT_WITHIN:
        longest = max(width, height)
        if longest <= max_dimension:
            return width, height  # never upscale
        factor = max_dimension / longest
        return max(1, round(width * factor)), max(1, round(height * factor))
    if mode is ResizeMode.PERCENT:
        factor = max(0.01, scale_percent / 100.0)
        return max(1, round(width * factor)), max(1, round(height * factor))
    if mode is ResizeMode.EXACT:
        return max(1, int(exact[0])), max(1, int(exact[1]))
    return width, height


def resize_to(image: Image.Image, target: Tuple[int, int], cover: bool = False) -> Image.Image:
    """Resize to ``target``.

    With ``cover`` the aspect ratio is preserved by scaling up to fill and
    centre-cropping the overflow, which is what "exact size" should do to a
    photo rather than squashing it.
    """
    if image.size == tuple(target):
        return image
    if not cover:
        return image.resize(target, Image.Resampling.LANCZOS)

    target_w, target_h = target
    width, height = image.size
    factor = max(target_w / width, target_h / height)
    intermediate = (max(target_w, round(width * factor)), max(target_h, round(height * factor)))
    stretched = image.resize(intermediate, Image.Resampling.LANCZOS)
    left = (intermediate[0] - target_w) // 2
    top = (intermediate[1] - target_h) // 2
    return stretched.crop((left, top, left + target_w, top + target_h))
