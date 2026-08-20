"""The watermark rendering pipeline.

Rendering is **non-destructive and resolution-independent**: every size in
:mod:`watermark.core.models` is a percentage of the canvas, so the same
settings produce a visually identical result on a 600px preview and on the
6000px original.  That property is what lets the GUI show a live preview
without re-rendering full-resolution pixels on every keystroke.

Layer order, bottom to top::

    base image → tiled pattern → logo watermark → text watermark
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from PIL import Image, ImageColor, ImageDraw, ImageFilter

from . import fonts, tokens
from .imageio import SourceImage, load_overlay
from .models import (
    ImageWatermark,
    JobSettings,
    Placement,
    TextWatermark,
    TileSource,
    TileWatermark,
)
from .transforms import apply_transform

#: Upper bound on stamps drawn for a tiled watermark.  Protects against a user
#: dragging spacing to zero on a 50MP image and freezing the app.
MAX_TILE_STAMPS = 20_000


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _rgba(color: str, opacity: int) -> Tuple[int, int, int, int]:
    """Parse a colour string and attach an alpha from a 0-100 opacity."""
    try:
        rgb = ImageColor.getrgb(color or "#FFFFFF")[:3]
    except ValueError:
        rgb = (255, 255, 255)
    alpha = int(round(max(0, min(100, opacity)) / 100.0 * 255))
    return (rgb[0], rgb[1], rgb[2], alpha)


def _apply_opacity(layer: Image.Image, opacity: int) -> Image.Image:
    """Scale a layer's alpha channel by ``opacity`` (0-100)."""
    opacity = max(0, min(100, int(opacity)))
    if opacity >= 100:
        return layer
    if opacity <= 0:
        return Image.new("RGBA", layer.size, (0, 0, 0, 0))
    alpha = layer.getchannel("A").point(lambda value: int(value * opacity / 100))
    result = layer.copy()
    result.putalpha(alpha)
    return result


def reference_length(size: Tuple[int, int]) -> float:
    """The length percentages are measured against.

    The geometric mean of width and height keeps a watermark visually the same
    weight on a square portrait and on a wide panorama, where using width alone
    makes one enormous and the other invisible.
    """
    width, height = size
    return math.sqrt(max(1, width) * max(1, height))


def _rotate(layer: Image.Image, angle: float) -> Image.Image:
    if not angle % 360:
        return layer
    return layer.rotate(
        angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0)
    )


# --------------------------------------------------------------------------- #
# Text layer
# --------------------------------------------------------------------------- #


@dataclass
class RenderedLayer:
    """A watermark stamp plus the placement that positions it."""

    image: Image.Image
    placement: Placement

    @property
    def size(self) -> Tuple[int, int]:
        return self.image.size


def build_text_layer(
    settings: TextWatermark,
    canvas_size: Tuple[int, int],
    context: Optional[Dict[str, str]] = None,
    apply_rotation: bool = True,
    opacity_override: Optional[int] = None,
    ignore_enabled: bool = False,
) -> Optional[Image.Image]:
    """Rasterise the text watermark onto a transparent, tightly cropped layer.

    Returns ``None`` when there is nothing to draw, so callers can skip the
    layer entirely rather than compositing an empty image.  ``ignore_enabled``
    lets the tiled pattern reuse the text's appearance while the single corner
    stamp is switched off.
    """
    text = tokens.expand(settings.text, context or {})
    if (not settings.enabled and not ignore_enabled) or not text.strip():
        return None

    reference = reference_length(canvas_size)
    font_size = max(6, int(round(settings.size_percent / 100.0 * reference)))
    font = fonts.load_font(settings.font, font_size)
    stroke_width = int(round(font_size * max(0.0, settings.outline_width_percent) / 100.0))
    line_spacing = int(round(font_size * (settings.line_spacing - 1.0)))

    # Measure on a throwaway canvas so we know exactly how much room to reserve.
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.multiline_textbbox(
        (0, 0), text, font=font, spacing=line_spacing, stroke_width=stroke_width
    )
    text_w, text_h = max(1, int(right - left)), max(1, int(bottom - top))

    backdrop_pad = 0
    if settings.backdrop.enabled:
        backdrop_pad = int(round(font_size * settings.backdrop.padding_percent / 100.0))

    shadow = settings.shadow
    shadow_pad = 0
    if shadow.enabled:
        blur = max(0.0, shadow.blur_radius)
        shadow_pad = int(math.ceil(blur * 3 + max(abs(shadow.offset_x), abs(shadow.offset_y))))

    pad = backdrop_pad + shadow_pad + 2
    layer = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    origin = (pad - left, pad - top)

    # 1. Backdrop plate, so light text stays legible over a busy photo.
    if settings.backdrop.enabled:
        plate = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        radius = int(round(
            (text_h + backdrop_pad * 2) * settings.backdrop.corner_radius_percent / 100.0
        ))
        box = (
            pad - backdrop_pad,
            pad - backdrop_pad,
            pad + text_w + backdrop_pad,
            pad + text_h + backdrop_pad,
        )
        ImageDraw.Draw(plate).rounded_rectangle(
            box,
            radius=max(0, min(radius, (text_h + backdrop_pad * 2) // 2)),
            fill=_rgba(settings.backdrop.color, settings.backdrop.opacity),
        )
        layer = Image.alpha_composite(layer, plate)

    # 2. Drop shadow, drawn as blurred text beneath the real text.
    if shadow.enabled:
        shadow_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow_layer).multiline_text(
            (origin[0] + shadow.offset_x, origin[1] + shadow.offset_y),
            text,
            font=font,
            fill=_rgba(shadow.color, shadow.opacity),
            spacing=line_spacing,
            stroke_width=stroke_width,
            stroke_fill=_rgba(shadow.color, shadow.opacity),
        )
        if shadow.blur_radius > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow.blur_radius))
        layer = Image.alpha_composite(layer, shadow_layer)

    # 3. The text itself.  Pillow's stroke_width draws a proper outline in one
    #    pass — the original code emulated this with 25 offset draws.
    text_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(text_layer).multiline_text(
        origin,
        text,
        font=font,
        fill=_rgba(settings.color, 100),
        spacing=line_spacing,
        stroke_width=stroke_width,
        stroke_fill=_rgba(settings.outline_color, settings.outline_opacity)
        if stroke_width
        else None,
    )
    layer = Image.alpha_composite(layer, text_layer)

    layer = _apply_opacity(
        layer, settings.opacity if opacity_override is None else opacity_override
    )
    if apply_rotation:
        layer = _rotate(layer, settings.rotation)
    return layer


# --------------------------------------------------------------------------- #
# Image layer
# --------------------------------------------------------------------------- #


def build_image_layer(
    settings: ImageWatermark,
    canvas_size: Tuple[int, int],
    apply_rotation: bool = True,
    overlay: Optional[Image.Image] = None,
    opacity_override: Optional[int] = None,
    ignore_enabled: bool = False,
) -> Optional[Image.Image]:
    """Scale, tint and rotate the logo watermark.

    ``overlay`` lets a caller pass an already-decoded logo so batch runs decode
    it once instead of once per photo.  ``ignore_enabled`` is used by the tiled
    pattern, which may repeat the logo without also stamping it in a corner.
    """
    if (not settings.enabled and not ignore_enabled) or (overlay is None and not settings.path):
        return None

    logo = overlay if overlay is not None else load_overlay(settings.path)
    canvas_w, canvas_h = canvas_size
    target_w = max(1, int(round(canvas_w * max(0.5, settings.scale_percent) / 100.0)))
    ratio = target_w / logo.width
    target_h = max(1, int(round(logo.height * ratio)))
    layer = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

    if settings.grayscale:
        alpha = layer.getchannel("A")
        layer = layer.convert("L").convert("RGBA")
        layer.putalpha(alpha)

    layer = _apply_opacity(
        layer, settings.opacity if opacity_override is None else opacity_override
    )
    if apply_rotation:
        layer = _rotate(layer, settings.rotation)
    return layer


# --------------------------------------------------------------------------- #
# Tiling
# --------------------------------------------------------------------------- #


def build_tile_layer(
    stamp: Image.Image,
    canvas_size: Tuple[int, int],
    settings: TileWatermark,
) -> Image.Image:
    """Repeat ``stamp`` across a full-canvas transparent layer.

    Rows are built once and reused, so covering a large canvas costs
    ``columns + rows`` pastes rather than ``columns × rows``.
    """
    canvas_w, canvas_h = canvas_size
    rotated = _rotate(stamp, settings.angle)
    # Callers hand over a fully opaque stamp, so the pattern's strength is
    # exactly what the tile opacity slider says.  Multiplying by the layer's own
    # opacity as well would make the slider behave differently depending on a
    # setting on another tab.
    rotated = _apply_opacity(rotated, settings.opacity)

    gap_x = canvas_w * max(0.0, settings.spacing_x_percent) / 100.0
    gap_y = canvas_h * max(0.0, settings.spacing_y_percent) / 100.0
    step_x = max(1, int(round(rotated.width + gap_x)))
    step_y = max(1, int(round(rotated.height + gap_y)))

    columns = canvas_w // step_x + 2
    rows = canvas_h // step_y + 2
    if columns * rows > MAX_TILE_STAMPS:
        # Widen the spacing just enough to stay under the budget instead of
        # refusing to render.
        factor = math.sqrt(columns * rows / MAX_TILE_STAMPS)
        step_x = max(1, int(step_x * factor))
        step_y = max(1, int(step_y * factor))
        columns = canvas_w // step_x + 2
        rows = canvas_h // step_y + 2

    # Rows are one step wider than the canvas on each side and are later pasted
    # at x = -step_x, so stamp coordinates inside a row are never negative.
    row_width = canvas_w + 2 * step_x

    def make_row(offset: int) -> Image.Image:
        row = Image.new("RGBA", (row_width, rotated.height), (0, 0, 0, 0))
        for x in range(offset, row_width, step_x):
            row.alpha_composite(rotated, (x, 0))
        return row

    plain = make_row(0)
    staggered = make_row(step_x // 2) if settings.stagger else plain

    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    for index in range(-1, rows + 1):
        row = staggered if (settings.stagger and index % 2) else plain
        layer.paste(row, (-step_x, index * step_y), row)
    return layer


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def render(
    source: SourceImage,
    settings: JobSettings,
    context: Optional[Dict[str, str]] = None,
    preview_max_dimension: Optional[int] = None,
    overlay: Optional[Image.Image] = None,
) -> Image.Image:
    """Produce the watermarked image.

    ``preview_max_dimension`` renders onto a downscaled canvas for a fast live
    preview; because every dimension is a percentage the result is a faithful
    miniature of the full-resolution output.
    """
    base = apply_transform(source.image, settings.transform)

    if preview_max_dimension:
        longest = max(base.size)
        if longest > preview_max_dimension:
            factor = preview_max_dimension / longest
            base = base.resize(
                (max(1, round(base.width * factor)), max(1, round(base.height * factor))),
                Image.Resampling.BILINEAR,
            )

    canvas = base.convert("RGBA")
    canvas_size = canvas.size
    context = dict(context or {})
    context.setdefault("width", str(canvas_size[0]))
    context.setdefault("height", str(canvas_size[1]))

    text_layer = build_text_layer(settings.text, canvas_size, context)
    image_layer = build_image_layer(settings.image, canvas_size, overlay=overlay)

    # Tiled pattern sits underneath the single stamps.
    if settings.tile.enabled:
        # A pattern can be used on its own: the tile builds its stamp from the
        # text/logo settings whether or not that single stamp is also drawn.
        if settings.tile.source is TileSource.IMAGE:
            stamp = build_image_layer(
                settings.image, canvas_size, apply_rotation=False, overlay=overlay,
                opacity_override=100, ignore_enabled=True,
            )
        else:
            stamp = build_text_layer(
                settings.text, canvas_size, context, apply_rotation=False,
                opacity_override=100, ignore_enabled=True,
            )
        if stamp is not None and stamp.width and stamp.height:
            canvas = Image.alpha_composite(
                canvas, build_tile_layer(stamp, canvas_size, settings.tile)
            )

    for layer, placement in (
        (image_layer, settings.image.placement),
        (text_layer, settings.text.placement),
    ):
        if layer is None:
            continue
        position = placement.resolve(canvas_size, layer.size)
        stamped = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        stamped.paste(layer, position, layer)
        canvas = Image.alpha_composite(canvas, stamped)

    return canvas


def layer_geometry(
    source: SourceImage,
    settings: JobSettings,
    context: Optional[Dict[str, str]] = None,
    canvas_size: Optional[Tuple[int, int]] = None,
) -> Dict[str, Tuple[int, int, int, int]]:
    """Bounding boxes of the placed layers, keyed ``"text"`` / ``"image"``.

    The preview canvas uses this for hit-testing so the user can grab a
    watermark and drag it.
    """
    if canvas_size is None:
        canvas_size = apply_transform(source.image, settings.transform).size

    boxes: Dict[str, Tuple[int, int, int, int]] = {}
    for key, layer, placement in (
        ("image", build_image_layer(settings.image, canvas_size), settings.image.placement),
        ("text", build_text_layer(settings.text, canvas_size, context), settings.text.placement),
    ):
        if layer is None:
            continue
        x, y = placement.resolve(canvas_size, layer.size)
        boxes[key] = (x, y, x + layer.width, y + layer.height)
    return boxes
