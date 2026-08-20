"""Serializable settings objects that describe a whole watermarking job.

Everything the user can tweak lives in one of these dataclasses.  They are
plain data — no Pillow objects, no Tk variables — so they can be round-tripped
to JSON for presets, diffed for undo/redo, and passed to worker threads.

The rendering pipeline reads them; it never writes to them.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class Anchor(str, Enum):
    """One of the nine positions a watermark can snap to."""

    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    MIDDLE_LEFT = "middle-left"
    CENTER = "center"
    MIDDLE_RIGHT = "middle-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"

    @property
    def fractions(self) -> Tuple[float, float]:
        """Return ``(fx, fy)`` in ``0.0 .. 1.0`` for this anchor."""
        col = {"left": 0.0, "center": 0.5, "right": 1.0}
        row = {"top": 0.0, "middle": 0.5, "bottom": 1.0}
        if self is Anchor.CENTER:
            return 0.5, 0.5
        vertical, _, horizontal = self.value.partition("-")
        return col[horizontal], row[vertical]


class ImageFormat(str, Enum):
    """Output container.  ``KEEP`` reuses the source file's format."""

    KEEP = "keep"
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    AVIF = "avif"
    TIFF = "tiff"

    @property
    def extension(self) -> str:
        return {"jpeg": ".jpg"}.get(self.value, "." + self.value)

    @property
    def supports_alpha(self) -> bool:
        return self in (ImageFormat.PNG, ImageFormat.WEBP, ImageFormat.AVIF, ImageFormat.TIFF)

    @property
    def is_lossy(self) -> bool:
        return self in (ImageFormat.JPEG, ImageFormat.WEBP, ImageFormat.AVIF)


class ResizeMode(str, Enum):
    """How the exported image should be scaled."""

    NONE = "none"
    #: Shrink so neither side exceeds ``max_dimension`` (never enlarges).
    FIT_WITHIN = "fit-within"
    #: Scale by ``scale_percent``.
    PERCENT = "percent"
    #: Force exact pixel dimensions, preserving aspect by letterboxing crop.
    EXACT = "exact"


class ConflictPolicy(str, Enum):
    """What a batch run does when the output file already exists."""

    RENAME = "rename"
    OVERWRITE = "overwrite"
    SKIP = "skip"


class TileSource(str, Enum):
    """Which layer the repeating pattern is built from."""

    TEXT = "text"
    IMAGE = "image"


# --------------------------------------------------------------------------- #
# Placement
# --------------------------------------------------------------------------- #


@dataclass
class Placement:
    """Where a watermark sits on the canvas.

    Position is stored resolution-independently so one preset looks the same on
    a 400px thumbnail and a 6000px original: the margin is a percentage of the
    canvas's shorter side and the free-drag offsets are percentages of width
    and height.
    """

    anchor: Anchor = Anchor.BOTTOM_RIGHT
    margin_percent: float = 3.0
    offset_x_percent: float = 0.0
    offset_y_percent: float = 0.0

    def resolve(
        self, canvas_size: Tuple[int, int], item_size: Tuple[int, int]
    ) -> Tuple[int, int]:
        """Return the top-left pixel at which to paste an item of ``item_size``."""
        canvas_w, canvas_h = canvas_size
        item_w, item_h = item_size
        margin = self.margin_percent / 100.0 * min(canvas_w, canvas_h)
        fx, fy = self.anchor.fractions

        # Available travel between the two margins; may go negative when the
        # watermark is larger than the canvas, which simply centres the overflow.
        span_x = canvas_w - 2 * margin - item_w
        span_y = canvas_h - 2 * margin - item_h
        x = margin + fx * span_x + self.offset_x_percent / 100.0 * canvas_w
        y = margin + fy * span_y + self.offset_y_percent / 100.0 * canvas_h
        return int(round(x)), int(round(y))

    def with_position(
        self,
        top_left: Tuple[float, float],
        canvas_size: Tuple[int, int],
        item_size: Tuple[int, int],
    ) -> "Placement":
        """Return a copy whose offsets place the item at ``top_left``.

        This is the inverse of :meth:`resolve` and is what the preview canvas
        uses when the user drags a watermark around.
        """
        base = dataclasses.replace(self, offset_x_percent=0.0, offset_y_percent=0.0)
        base_x, base_y = base.resolve(canvas_size, item_size)
        canvas_w, canvas_h = canvas_size
        return dataclasses.replace(
            self,
            offset_x_percent=(top_left[0] - base_x) / canvas_w * 100.0,
            offset_y_percent=(top_left[1] - base_y) / canvas_h * 100.0,
        )


# --------------------------------------------------------------------------- #
# Watermark layers
# --------------------------------------------------------------------------- #


@dataclass
class ShadowSettings:
    """Drop shadow drawn behind watermark text."""

    enabled: bool = False
    color: str = "#000000"
    opacity: int = 60
    offset_x: float = 2.0
    offset_y: float = 2.0
    blur_radius: float = 3.0


@dataclass
class BackdropSettings:
    """Rounded plate drawn behind watermark text, for busy photos."""

    enabled: bool = False
    color: str = "#000000"
    opacity: int = 35
    padding_percent: float = 25.0
    corner_radius_percent: float = 20.0


@dataclass
class TextWatermark:
    """A single text watermark.

    ``text`` may contain tokens such as ``{filename}`` or ``{date}``; see
    :func:`watermark.core.tokens.expand`.
    """

    enabled: bool = True
    text: str = "© {year} Your Name"
    font: str = "Tektur SemiCondensed"
    size_percent: float = 5.0
    color: str = "#FFFFFF"
    opacity: int = 85
    rotation: float = 0.0
    line_spacing: float = 1.15
    outline_width_percent: float = 6.0
    outline_color: str = "#000000"
    outline_opacity: int = 70
    shadow: ShadowSettings = field(default_factory=ShadowSettings)
    backdrop: BackdropSettings = field(default_factory=BackdropSettings)
    placement: Placement = field(default_factory=Placement)


@dataclass
class ImageWatermark:
    """A logo watermark loaded from disk."""

    enabled: bool = False
    path: str = ""
    scale_percent: float = 20.0
    opacity: int = 85
    rotation: float = 0.0
    grayscale: bool = False
    placement: Placement = field(default_factory=Placement)


@dataclass
class TileWatermark:
    """A repeating watermark covering the whole canvas.

    Reuses the text or image layer's appearance, so the user configures the
    look once and chooses whether it is stamped in a corner or tiled.
    """

    enabled: bool = False
    source: TileSource = TileSource.TEXT
    angle: float = 30.0
    spacing_x_percent: float = 12.0
    spacing_y_percent: float = 12.0
    opacity: int = 25
    stagger: bool = True


# --------------------------------------------------------------------------- #
# Geometry / export
# --------------------------------------------------------------------------- #


@dataclass
class TransformSettings:
    """Geometry applied to the source before any watermark is drawn."""

    rotation: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    #: Crop as fractions of each edge, ``(left, top, right, bottom)`` in 0..1.
    crop: Optional[List[float]] = None

    def is_identity(self) -> bool:
        """True when this transform would leave the image untouched."""
        return (
            self.rotation % 360 == 0
            and not self.flip_horizontal
            and not self.flip_vertical
            and not any(value > 1e-6 for value in (self.crop or ()))
        )


@dataclass
class ExportSettings:
    """Everything about how the finished image is encoded and written."""

    image_format: ImageFormat = ImageFormat.KEEP
    quality: int = 88
    lossless: bool = False
    optimize: bool = True
    progressive: bool = True
    #: JPEG chroma subsampling: 0 = 4:4:4 (best), 2 = 4:2:0 (smallest), -1 = auto.
    subsampling: int = -1
    png_compress_level: int = 7
    #: Quantize PNG output to a palette of N colours; ``None`` keeps truecolour.
    png_palette_colors: Optional[int] = None

    resize_mode: ResizeMode = ResizeMode.NONE
    max_dimension: int = 2048
    scale_percent: float = 100.0
    exact_width: int = 1920
    exact_height: int = 1080

    #: Shrink until the encoded file fits this budget, in kilobytes.
    target_size_kb: Optional[int] = None

    strip_metadata: bool = True
    keep_icc_profile: bool = True
    #: Colour painted behind transparent pixels when exporting to JPEG.
    matte_color: str = "#FFFFFF"
    dpi: Optional[int] = None


@dataclass
class OutputSettings:
    """Where batch results are written and what they are called."""

    directory: str = ""
    #: Supports the same tokens as watermark text, plus ``{index}``.
    filename_pattern: str = "{name}_wm{ext}"
    conflict: ConflictPolicy = ConflictPolicy.RENAME


@dataclass
class JobSettings:
    """The complete description of a watermarking job.

    A preset is exactly this object serialized to JSON.
    """

    name: str = "Untitled"
    transform: TransformSettings = field(default_factory=TransformSettings)
    text: TextWatermark = field(default_factory=TextWatermark)
    image: ImageWatermark = field(default_factory=ImageWatermark)
    tile: TileWatermark = field(default_factory=TileWatermark)
    export: ExportSettings = field(default_factory=ExportSettings)
    output: OutputSettings = field(default_factory=OutputSettings)

    def copy(self) -> "JobSettings":
        """Deep copy — used for undo snapshots."""
        return from_dict(JobSettings, to_dict(self))

    def to_json_dict(self) -> Dict[str, Any]:
        return to_dict(self)

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> "JobSettings":
        return from_dict(cls, data)


# --------------------------------------------------------------------------- #
# Generic dataclass <-> dict conversion
# --------------------------------------------------------------------------- #


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses, enums and containers to JSON types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {key: to_dict(value) for key, value in obj.items()}
    return obj


def _coerce(annotation: Any, value: Any) -> Any:
    """Convert one JSON value to the type named by ``annotation``."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Union:  # Optional[X] and friends
        if value is None:
            return None
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _coerce(candidate, value)
            except (TypeError, ValueError):
                continue
        return value

    if origin in (list, List):
        item_type = args[0] if args else Any
        return [_coerce(item_type, item) for item in value or []]

    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return annotation(value)
        if dataclasses.is_dataclass(annotation):
            return from_dict(annotation, value or {})
        if annotation is bool:
            return bool(value)
        if annotation is int and value is not None:
            return int(value)
        if annotation is float and value is not None:
            return float(value)
        if annotation is str and value is not None:
            return str(value)
    return value


def from_dict(cls: Any, data: Any) -> Any:
    """Rebuild a dataclass from ``data``, ignoring unknown keys.

    Unknown keys are dropped and missing keys fall back to field defaults, so a
    preset written by an older or newer version of the app still loads.
    """
    if not isinstance(data, dict):
        raise TypeError(f"expected a mapping to build {cls.__name__}, got {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        try:
            kwargs[f.name] = _coerce(hints.get(f.name, Any), data[f.name])
        except (TypeError, ValueError, KeyError):
            # A single corrupt value must not sink the whole preset.
            continue
    return cls(**kwargs)
