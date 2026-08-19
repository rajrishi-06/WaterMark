"""Geometry applied to the source image before watermarking."""

from __future__ import annotations

from typing import Tuple

from PIL import Image

from .models import ResizeMode, TransformSettings


def apply_transform(image: Image.Image, settings: TransformSettings) -> Image.Image:
    """Crop, flip and rotate ``image`` according to ``settings``.

    Order matters: crop first (so the user's crop refers to the original
    framing), then flips, then rotation with ``expand`` so nothing is clipped.
    """
    if settings.is_identity():
        return image

    result = image
    if settings.crop:
        left, top, right, bottom = (max(0.0, min(1.0, v)) for v in settings.crop)
        width, height = result.size
        box = (
            int(left * width),
            int(top * height),
            int(width - right * width),
            int(height - bottom * height),
        )
        # A degenerate crop would raise; ignore it rather than kill the render.
        if box[2] - box[0] >= 1 and box[3] - box[1] >= 1:
            result = result.crop(box)

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
