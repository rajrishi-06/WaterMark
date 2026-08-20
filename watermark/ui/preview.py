"""The live preview canvas.

Replaces the original app's throwaway "Preview" pop-up.  This canvas shows the
result continuously, draws transparency on a checkerboard, supports zoom and
pan, and — the part that makes positioning actually usable — lets the user grab
a watermark and drag it wherever they want.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageTk

from .theme import Palette

#: Layers are hit-tested in this order, so the text on top wins a tie.
_HIT_ORDER = ("text", "image")
_CHECKER_SIZE = 12

#: Half-width of a crop grab handle, in screen pixels.
_HANDLE = 5
#: Handles, as the (x, y) fractions of the crop rectangle they sit on.
_CROP_HANDLES = {
    "nw": (0.0, 0.0), "n": (0.5, 0.0), "ne": (1.0, 0.0),
    "w": (0.0, 0.5), "e": (1.0, 0.5),
    "sw": (0.0, 1.0), "s": (0.5, 1.0), "se": (1.0, 1.0),
}
_HANDLE_CURSORS = {
    "nw": "top_left_corner", "n": "top_side", "ne": "top_right_corner",
    "w": "left_side", "e": "right_side",
    "sw": "bottom_left_corner", "s": "bottom_side", "se": "bottom_right_corner",
}
#: Smallest crop, as a fraction of each axis.
_MIN_CROP = 0.02


class PreviewCanvas(tk.Canvas):
    """Displays the rendered image and turns mouse gestures into placement edits."""

    def __init__(
        self,
        parent: tk.Misc,
        palette: Palette,
        on_layer_moved: Optional[Callable[[str, Tuple[float, float]], None]] = None,
        on_open_requested: Optional[Callable[[], None]] = None,
        on_zoom_changed: Optional[Callable[[float], None]] = None,
        on_crop_changed: Optional[Callable[[Tuple[float, float, float, float]], None]] = None,
    ) -> None:
        super().__init__(parent, background=palette.bg, highlightthickness=0, bd=0)
        self.palette = palette
        self.on_layer_moved = on_layer_moved
        self.on_open_requested = on_open_requested
        self.on_zoom_changed = on_zoom_changed
        self.on_crop_changed = on_crop_changed

        self._source: Optional[Image.Image] = None
        #: The untouched original, shown while comparing.
        self._original: Optional[Image.Image] = None
        self.comparing = False
        self._geometry: Dict[str, Tuple[int, int, int, int]] = {}
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._checker: Optional[Image.Image] = None

        self.zoom = 1.0          # 1.0 == fit to window
        self.fit_scale = 1.0     # image pixels -> canvas pixels at zoom 1.0
        self._origin = (0, 0)    # top-left of the drawn image, in canvas pixels
        self._pan = (0, 0)

        self._drag_layer: Optional[str] = None
        self._drag_grab = (0.0, 0.0)
        self._panning: Optional[Tuple[int, int]] = None
        self._placeholder = True

        #: While cropping, the canvas shows the *uncropped* frame with a
        #: draggable rectangle over it — the same way every photo editor does.
        self.crop_mode = False
        self.crop_rect: Optional[List[float]] = None   # in preview-image pixels
        self._crop_grab: Optional[str] = None
        self._crop_origin = (0.0, 0.0)
        self._crop_start: Optional[List[float]] = None

        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_hover)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)

    # -- content ----------------------------------------------------------- #

    def set_image(
        self,
        image: Optional[Image.Image],
        geometry: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    ) -> None:
        """Show ``image`` (already rendered) and remember layer boxes for hit-testing."""
        resized = (
            image is not None and self._source is not None
            and image.size != self._source.size
        )
        self._source = image
        self._geometry = geometry or {}
        self._placeholder = image is None
        if self.crop_mode and (self.crop_rect is None or resized):
            # A rotation changed the frame; start the selection over rather than
            # leaving a rectangle that refers to the old geometry.
            self.reset_crop_rect()
        self.redraw()

    def set_original(self, image: Optional[Image.Image]) -> None:
        """Supply the untouched frame used by compare mode."""
        self._original = image
        if self.comparing:
            self.redraw()

    def set_comparing(self, comparing: bool) -> bool:
        """Show the original instead of the result.  Returns whether it took."""
        comparing = bool(comparing) and self._original is not None
        if comparing == self.comparing:
            return self.comparing
        self.comparing = comparing
        self.redraw()
        return self.comparing

    def clear(self) -> None:
        self._original = None
        self.comparing = False
        self.set_image(None)

    def set_crop_mode(
        self, enabled: bool, rect: Optional[Sequence[float]] = None
    ) -> None:
        """Turn the crop overlay on or off.

        ``rect`` is in preview-image pixels; omitting it selects the whole frame.
        """
        self.crop_mode = enabled
        if enabled:
            if rect is not None:
                self.crop_rect = [float(value) for value in rect]
            elif self._source is not None:
                self.crop_rect = [0.0, 0.0, float(self._source.width),
                                  float(self._source.height)]
        else:
            self._crop_grab = None
        self.configure(cursor="")
        self.redraw()

    def reset_crop_rect(self) -> None:
        """Select the whole frame again."""
        if self._source is not None:
            self.crop_rect = [0.0, 0.0, float(self._source.width),
                              float(self._source.height)]
            self.redraw()
            self._report_crop()

    def _report_crop(self) -> None:
        if self.on_crop_changed and self.crop_rect and self._source is not None:
            self.on_crop_changed(tuple(self.crop_rect))  # type: ignore[arg-type]

    def reset_view(self) -> None:
        self.zoom = 1.0
        self._pan = (0, 0)
        self.redraw()
        if self.on_zoom_changed:
            self.on_zoom_changed(self.effective_scale)

    @property
    def effective_scale(self) -> float:
        return self.fit_scale * self.zoom

    @property
    def image_size(self) -> Optional[Tuple[int, int]]:
        """Size of the frame currently displayed, in preview pixels."""
        return self._source.size if self._source is not None else None

    def layer_box(self, key: str) -> Optional[Tuple[int, int, int, int]]:
        """Bounding box of a placed layer, in preview pixels."""
        return self._geometry.get(key)

    # -- painting ---------------------------------------------------------- #

    def _checkerboard(self, size: Tuple[int, int]) -> Image.Image:
        """A tiled light/dark grid, so transparent areas read as transparent."""
        if self._checker is None:
            tile = Image.new("RGB", (_CHECKER_SIZE * 2, _CHECKER_SIZE * 2), self.palette.checker_a)
            square = Image.new("RGB", (_CHECKER_SIZE, _CHECKER_SIZE), self.palette.checker_b)
            tile.paste(square, (0, 0))
            tile.paste(square, (_CHECKER_SIZE, _CHECKER_SIZE))
            self._checker = tile
        board = Image.new("RGB", size)
        tile = self._checker
        for y in range(0, size[1], tile.height):
            for x in range(0, size[0], tile.width):
                board.paste(tile, (x, y))
        return board

    def redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())

        if self._source is None:
            self._draw_placeholder(width, height)
            return

        frame = self._source
        if self.comparing and self._original is not None:
            frame = self._original

        image_w, image_h = self._source.size
        self.fit_scale = min(width / image_w, height / image_h, 1.0) or 1.0
        scale = max(0.01, self.fit_scale * self.zoom)
        draw_w = max(1, int(image_w * scale))
        draw_h = max(1, int(image_h * scale))

        display = frame.resize((draw_w, draw_h), Image.Resampling.BILINEAR)
        if display.mode == "RGBA":
            board = self._checkerboard((draw_w, draw_h))
            board.paste(display, (0, 0), display)
            display = board
        else:
            display = display.convert("RGB")

        x = (width - draw_w) // 2 + self._pan[0]
        y = (height - draw_h) // 2 + self._pan[1]
        self._origin = (x, y)

        self._photo = ImageTk.PhotoImage(display)
        self.create_image(x, y, image=self._photo, anchor="nw")
        self.create_rectangle(
            x, y, x + draw_w, y + draw_h, outline=self.palette.border, width=1
        )
        if self.comparing:
            self._draw_badge(x + 10, y + 10, "BEFORE — no watermark")
        if self.crop_mode:
            self._draw_crop_overlay(x, y, draw_w, draw_h)
        elif self._drag_layer:
            self._outline_layer(self._drag_layer)

    def _draw_placeholder(self, width: int, height: int) -> None:
        """The empty state: an inviting drop target rather than a blank panel."""
        box = (width * 0.12, height * 0.16, width * 0.88, height * 0.84)
        self.create_rectangle(*box, outline=self.palette.border, dash=(6, 5), width=2)
        self.create_text(
            width / 2, height / 2 - 22,
            text="Drop an image here",
            fill=self.palette.text, font=("TkDefaultFont", 17, "bold"),
        )
        self.create_text(
            width / 2, height / 2 + 8,
            text="or double-click to browse  ·  JPEG, PNG, WebP, TIFF, AVIF",
            fill=self.palette.muted, font=("TkDefaultFont", 10),
        )
        self.create_text(
            width / 2, height / 2 + 34,
            text="Add a whole folder with Batch (Ctrl+B)",
            fill=self.palette.muted, font=("TkDefaultFont", 9),
        )

    def _draw_badge(self, x: int, y: int, text: str) -> None:
        """A small label over the image, so a screenshot is never ambiguous."""
        label = self.create_text(x + 9, y + 6, text=text, anchor="nw",
                                 fill=self.palette.accent_text,
                                 font=("TkDefaultFont", 10, "bold"))
        bounds = self.bbox(label)
        if bounds:
            plate = self.create_rectangle(
                bounds[0] - 9, bounds[1] - 5, bounds[2] + 9, bounds[3] + 5,
                fill=self.palette.accent, outline="",
            )
            self.tag_lower(plate, label)

    def _draw_crop_overlay(self, x: int, y: int, draw_w: int, draw_h: int) -> None:
        """Dim everything outside the selection and draw its handles."""
        if self.crop_rect is None:
            return
        scale = self.effective_scale
        left, top, right, bottom = (value * scale for value in self.crop_rect)
        left, right = x + left, x + right
        top, bottom = y + top, y + bottom

        # Four shaded bands rather than one translucent fill: the Tk canvas has
        # no alpha, so a stippled overlay is the way to dim an area.
        for band in (
            (x, y, x + draw_w, top),
            (x, bottom, x + draw_w, y + draw_h),
            (x, top, left, bottom),
            (right, top, x + draw_w, bottom),
        ):
            if band[2] > band[0] and band[3] > band[1]:
                self.create_rectangle(*band, fill=self.palette.bg, stipple="gray75",
                                      outline="", width=0)

        self.create_rectangle(left, top, right, bottom,
                              outline=self.palette.accent, width=2)
        # Thirds guides, the standard framing aid.
        for index in (1, 2):
            offset_x = left + (right - left) * index / 3
            offset_y = top + (bottom - top) * index / 3
            self.create_line(offset_x, top, offset_x, bottom,
                             fill=self.palette.accent, width=1, stipple="gray25")
            self.create_line(left, offset_y, right, offset_y,
                             fill=self.palette.accent, width=1, stipple="gray25")

        for fx, fy in _CROP_HANDLES.values():
            handle_x = left + (right - left) * fx
            handle_y = top + (bottom - top) * fy
            self.create_rectangle(
                handle_x - _HANDLE, handle_y - _HANDLE,
                handle_x + _HANDLE, handle_y + _HANDLE,
                fill=self.palette.accent, outline=self.palette.accent_text, width=1,
            )

    def _crop_handle_at(self, x: float, y: float) -> Optional[str]:
        """Which crop handle (or ``"move"``) is under the pointer."""
        if not self.crop_mode or self.crop_rect is None:
            return None
        scale = self.effective_scale
        left, top, right, bottom = (value * scale for value in self.crop_rect)
        left, right = self._origin[0] + left, self._origin[0] + right
        top, bottom = self._origin[1] + top, self._origin[1] + bottom

        for name, (fx, fy) in _CROP_HANDLES.items():
            handle_x = left + (right - left) * fx
            handle_y = top + (bottom - top) * fy
            if abs(x - handle_x) <= _HANDLE + 2 and abs(y - handle_y) <= _HANDLE + 2:
                return name
        if left <= x <= right and top <= y <= bottom:
            return "move"
        return None

    def _resize_crop(self, handle: str, image_x: float, image_y: float) -> None:
        """Apply a drag to the crop rectangle, clamped to the frame."""
        if self.crop_rect is None or self._source is None or self._crop_start is None:
            return
        width, height = self._source.size
        min_w, min_h = width * _MIN_CROP, height * _MIN_CROP
        left, top, right, bottom = self.crop_rect

        if handle == "move":
            start_left, start_top, start_right, start_bottom = self._crop_start
            delta_x = image_x - self._crop_origin[0]
            delta_y = image_y - self._crop_origin[1]
            span_x, span_y = start_right - start_left, start_bottom - start_top
            left = min(max(0.0, start_left + delta_x), width - span_x)
            top = min(max(0.0, start_top + delta_y), height - span_y)
            right, bottom = left + span_x, top + span_y
        else:
            if "w" in handle:
                left = min(max(0.0, image_x), right - min_w)
            if "e" in handle:
                right = max(min(float(width), image_x), left + min_w)
            if "n" in handle:
                top = min(max(0.0, image_y), bottom - min_h)
            if "s" in handle:
                bottom = max(min(float(height), image_y), top + min_h)

        self.crop_rect = [left, top, right, bottom]
        self.redraw()
        self._report_crop()

    def _outline_layer(self, key: str) -> None:
        box = self._geometry.get(key)
        if not box:
            return
        scale = self.effective_scale
        x0, y0, x1, y1 = box
        self.create_rectangle(
            self._origin[0] + x0 * scale, self._origin[1] + y0 * scale,
            self._origin[0] + x1 * scale, self._origin[1] + y1 * scale,
            outline=self.palette.accent, width=2, dash=(4, 3),
        )

    # -- coordinate helpers ------------------------------------------------- #

    def to_image_coords(self, x: float, y: float) -> Tuple[float, float]:
        scale = max(0.0001, self.effective_scale)
        return (x - self._origin[0]) / scale, (y - self._origin[1]) / scale

    def _layer_at(self, x: float, y: float) -> Optional[str]:
        image_x, image_y = self.to_image_coords(x, y)
        for key in _HIT_ORDER:
            box = self._geometry.get(key)
            if box and box[0] <= image_x <= box[2] and box[1] <= image_y <= box[3]:
                return key
        return None

    # -- events ------------------------------------------------------------- #

    def _on_press(self, event: tk.Event) -> None:
        if self._source is None:
            return
        self.focus_set()

        if self.crop_mode:
            handle = self._crop_handle_at(event.x, event.y)
            if handle:
                self._crop_grab = handle
                self._crop_origin = self.to_image_coords(event.x, event.y)
                self._crop_start = list(self.crop_rect or [])
            return

        layer = self._layer_at(event.x, event.y)
        if layer:
            box = self._geometry[layer]
            image_x, image_y = self.to_image_coords(event.x, event.y)
            self._drag_layer = layer
            self._drag_grab = (image_x - box[0], image_y - box[1])
            self.configure(cursor="fleur")
        else:
            self._panning = (event.x - self._pan[0], event.y - self._pan[1])
            self.configure(cursor="hand2")

    def _on_motion(self, event: tk.Event) -> None:
        if self.crop_mode:
            if self._crop_grab:
                image_x, image_y = self.to_image_coords(event.x, event.y)
                self._resize_crop(self._crop_grab, image_x, image_y)
            return
        if self._drag_layer and self.on_layer_moved:
            image_x, image_y = self.to_image_coords(event.x, event.y)
            self.on_layer_moved(
                self._drag_layer,
                (image_x - self._drag_grab[0], image_y - self._drag_grab[1]),
            )
        elif self._panning is not None:
            self._pan = (event.x - self._panning[0], event.y - self._panning[1])
            self.redraw()

    def _on_release(self, _event: tk.Event) -> None:
        if self.crop_mode:
            self._crop_grab = None
            self._crop_start = None
            return
        self._drag_layer = None
        self._panning = None
        self.configure(cursor="")
        self.redraw()

    def _on_hover(self, event: tk.Event) -> None:
        if self._source is None:
            return
        if self.crop_mode:
            handle = self._crop_handle_at(event.x, event.y)
            self.configure(cursor=_HANDLE_CURSORS.get(handle or "", "fleur" if handle else ""))
            return
        self.configure(cursor="fleur" if self._layer_at(event.x, event.y) else "")

    def _on_double_click(self, _event: tk.Event) -> None:
        if self._source is None and self.on_open_requested:
            self.on_open_requested()
        else:
            self.reset_view()

    def _on_wheel(self, event: tk.Event) -> None:
        if self._source is None:
            return
        if event.num == 4 or getattr(event, "delta", 0) > 0:
            self.zoom = min(8.0, self.zoom * 1.15)
        else:
            self.zoom = max(0.1, self.zoom / 1.15)
        if abs(self.zoom - 1.0) < 0.03:
            self.zoom = 1.0
            self._pan = (0, 0)
        self.redraw()
        if self.on_zoom_changed:
            self.on_zoom_changed(self.effective_scale)
