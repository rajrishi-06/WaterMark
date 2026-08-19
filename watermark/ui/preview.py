"""The live preview canvas.

Replaces the original app's throwaway "Preview" pop-up.  This canvas shows the
result continuously, draws transparency on a checkerboard, supports zoom and
pan, and — the part that makes positioning actually usable — lets the user grab
a watermark and drag it wherever they want.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Dict, Optional, Tuple

from PIL import Image, ImageTk

from .theme import Palette

#: Layers are hit-tested in this order, so the text on top wins a tie.
_HIT_ORDER = ("text", "image")
_CHECKER_SIZE = 12


class PreviewCanvas(tk.Canvas):
    """Displays the rendered image and turns mouse gestures into placement edits."""

    def __init__(
        self,
        parent: tk.Misc,
        palette: Palette,
        on_layer_moved: Optional[Callable[[str, Tuple[float, float]], None]] = None,
        on_open_requested: Optional[Callable[[], None]] = None,
        on_zoom_changed: Optional[Callable[[float], None]] = None,
    ) -> None:
        super().__init__(parent, background=palette.bg, highlightthickness=0, bd=0)
        self.palette = palette
        self.on_layer_moved = on_layer_moved
        self.on_open_requested = on_open_requested
        self.on_zoom_changed = on_zoom_changed

        self._source: Optional[Image.Image] = None
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
        self._source = image
        self._geometry = geometry or {}
        self._placeholder = image is None
        self.redraw()

    def clear(self) -> None:
        self.set_image(None)

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

        image_w, image_h = self._source.size
        self.fit_scale = min(width / image_w, height / image_h, 1.0) or 1.0
        scale = max(0.01, self.fit_scale * self.zoom)
        draw_w = max(1, int(image_w * scale))
        draw_h = max(1, int(image_h * scale))

        display = self._source.resize((draw_w, draw_h), Image.Resampling.BILINEAR)
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
        if self._drag_layer:
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
        self._drag_layer = None
        self._panning = None
        self.configure(cursor="")
        self.redraw()

    def _on_hover(self, event: tk.Event) -> None:
        if self._source is None:
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
