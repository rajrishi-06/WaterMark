"""The WaterMark desktop application.

One window, live preview, everything reachable without a chain of pop-ups.

The original app asked a question, opened a window, asked another question and
finally showed a result you could not adjust.  Here the image is always on
screen, every control updates it immediately, and the same settings can be
applied to a whole folder from the Batch dialog.
"""

from __future__ import annotations

import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import APP_NAME
from ..core import config as config_module
from ..core import export, fonts, imageio, logs, presets
from ..core.errors import WatermarkError
from ..core.models import (
    Anchor,
    ImageFormat,
    JobSettings,
    Placement,
    ResizeMode,
    TileSource,
)
from ..core.tokens import TOKEN_HELP
from . import dialogs, dnd
from .engine import PreviewEngine, run_async
from .preview import PreviewCanvas
from .theme import apply_theme
from .widgets import AnchorPad, ColorRow, LabeledCombo, ScrollableFrame, Section, SliderRow, tooltip

_LOG = logs.get_logger(__name__)

FORMAT_LABELS: Dict[ImageFormat, str] = {
    ImageFormat.KEEP: "Same as original",
    ImageFormat.PNG: "PNG — lossless",
    ImageFormat.JPEG: "JPEG — photos",
    ImageFormat.WEBP: "WebP — smaller files",
    ImageFormat.AVIF: "AVIF — smallest files",
    ImageFormat.TIFF: "TIFF — archival",
}

RESIZE_LABELS: Dict[ResizeMode, str] = {
    ResizeMode.NONE: "Keep original size",
    ResizeMode.FIT_WITHIN: "Fit within a maximum",
    ResizeMode.PERCENT: "Scale by percentage",
    ResizeMode.EXACT: "Exact size (crop to fill)",
}

TILE_SOURCE_LABELS: Dict[TileSource, str] = {
    TileSource.TEXT: "Repeat the text",
    TileSource.IMAGE: "Repeat the logo",
}

_IMAGE_FILETYPES = [
    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif"),
    ("All files", "*.*"),
]
_MAX_UNDO = 60


class WatermarkApp:
    """Owns the window, the settings and the render loop."""

    def __init__(self, initial_paths: Optional[List[str]] = None) -> None:
        self.config = config_module.load_config()
        self.settings: JobSettings = self.config.restore_settings()
        self.source: Optional[imageio.SourceImage] = None
        self.last_result_size: Tuple[int, int] = (0, 0)
        self.busy = False

        self.undo_stack: List[Dict[str, Any]] = []
        self.redo_stack: List[Dict[str, Any]] = []

        self.root = dnd.create_root()
        self.root.title(APP_NAME)
        self.root.minsize(940, 620)
        if self.config.window_geometry:
            try:
                self.root.geometry(self.config.window_geometry)
            except tk.TclError:
                pass
        else:
            self.root.geometry("1280x800")

        self.palette = apply_theme(self.root, self.config.theme)
        self.engine = PreviewEngine()
        self.callbacks: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self.vars: Dict[str, tk.Variable] = {}
        self.refreshables: List[Any] = []
        self._syncing = False
        self._render_job: Optional[str] = None

        self._build()
        self._sync_from_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(40, self._pump)

        if initial_paths:
            self.open_paths(initial_paths)
        else:
            self._update_status()

    # ------------------------------------------------------------------ #
    # Settings binding
    # ------------------------------------------------------------------ #

    def _get(self, path: str) -> Any:
        target: Any = self.settings
        for part in path.split("."):
            target = getattr(target, part)
        return target

    def _set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        target: Any = self.settings
        for part in parts[:-1]:
            target = getattr(target, part)
        setattr(target, parts[-1], value)

    def var(
        self,
        path: str,
        kind: str = "double",
        transform: Optional[Callable[[Any], Any]] = None,
        commit: bool = False,
    ) -> tk.Variable:
        """Create a Tk variable bound to a dotted settings path.

        Writes flow variable → settings → re-render.  ``_syncing`` guards the
        reverse direction so loading a preset does not fire sixty renders.
        """
        current = self._get(path)
        factory = {
            "double": tk.DoubleVar,
            "int": tk.IntVar,
            "bool": tk.BooleanVar,
            "string": tk.StringVar,
        }[kind]
        initial = current.value if hasattr(current, "value") else current
        variable = factory(self.root, value=initial)

        def on_write(*_args: Any) -> None:
            if self._syncing:
                return
            try:
                value = variable.get()
            except tk.TclError:
                return  # half-typed number in a spinbox
            self._set(path, transform(value) if transform else value)
            if commit:
                self.push_undo()
            self.schedule_render()

        variable.trace_add("write", on_write)
        self.vars[path] = variable
        return variable

    def _sync_from_settings(self) -> None:
        """Push the settings object back into every bound widget."""
        self._syncing = True
        try:
            for path, variable in self.vars.items():
                value = self._get(path)
                if hasattr(value, "value"):
                    value = value.value
                if value is None:
                    value = "" if isinstance(variable, tk.StringVar) else 0
                try:
                    variable.set(value)
                except tk.TclError:
                    continue
            if hasattr(self, "text_box"):
                self.text_box.delete("1.0", "end")
                self.text_box.insert("1.0", self.settings.text.text)
            self.target_size_enabled.set(self.settings.export.target_size_kb is not None)
            self.target_size_value.set(self.settings.export.target_size_kb or 500)
            self.palette_enabled.set(self.settings.export.png_palette_colors is not None)
            self.palette_value.set(self.settings.export.png_palette_colors or 128)
        finally:
            self._syncing = False
        for widget in self.refreshables:
            widget.refresh()
        self._update_export_state()
        self.schedule_render()

    # ------------------------------------------------------------------ #
    # Undo / redo
    # ------------------------------------------------------------------ #

    def push_undo(self) -> None:
        """Snapshot the settings, skipping no-op repeats."""
        snapshot = self.settings.to_json_dict()
        if self.undo_stack and self.undo_stack[-1] == snapshot:
            return
        self.undo_stack.append(snapshot)
        del self.undo_stack[:-_MAX_UNDO]
        self.redo_stack.clear()
        self._update_history_buttons()

    def undo(self, _event: Optional[tk.Event] = None) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append(self.settings.to_json_dict())
        self.settings = JobSettings.from_json_dict(self.undo_stack.pop())
        self._sync_from_settings()
        self._update_history_buttons()

    def redo(self, _event: Optional[tk.Event] = None) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append(self.settings.to_json_dict())
        self.settings = JobSettings.from_json_dict(self.redo_stack.pop())
        self._sync_from_settings()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        if hasattr(self, "undo_button"):
            self.undo_button.configure(state="normal" if self.undo_stack else "disabled")
            self.redo_button.configure(state="normal" if self.redo_stack else "disabled")

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        self._build_menu()

        self.toolbar = ttk.Frame(self.root, padding=(12, 8))
        self.toolbar.pack(side="top", fill="x")
        self._build_toolbar(self.toolbar)

        ttk.Separator(self.root, orient="horizontal").pack(fill="x")

        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        self.canvas = PreviewCanvas(
            body,
            self.palette,
            on_layer_moved=self._on_layer_moved,
            on_open_requested=self.open_image,
            on_zoom_changed=self._on_zoom_changed,
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        dnd.register(self.canvas, self._on_drop)

        self.panel = ttk.Frame(body, style="Panel.TFrame", width=372)
        self.panel.pack(side="right", fill="y")
        self.panel.pack_propagate(False)
        self._build_panel(self.panel)

        self.statusbar = ttk.Frame(self.root, style="Panel.TFrame", padding=(12, 6))
        self.statusbar.pack(side="bottom", fill="x")
        self._build_statusbar(self.statusbar)

        # Menus are populated last: they reference toolbar widgets that only
        # exist once the toolbar has been built.
        self._refresh_recent_menu()
        self._refresh_preset_menu()
        self._bind_shortcuts()

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        colors = dict(
            background=self.palette.panel, foreground=self.palette.text,
            activebackground=self.palette.accent, activeforeground=self.palette.accent_text,
            borderwidth=0,
        )

        file_menu = tk.Menu(menubar, tearoff=0, **colors)
        file_menu.add_command(label="Open image…", accelerator="Ctrl+O", command=self.open_image)
        self.recent_menu = tk.Menu(file_menu, tearoff=0, **colors)
        file_menu.add_cascade(label="Open recent", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Export as…", accelerator="Ctrl+S", command=self.save_as)
        file_menu.add_command(label="Batch process…", accelerator="Ctrl+B", command=self.open_batch)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, **colors)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Reset all settings", accelerator="Ctrl+R", command=self.reset)
        edit_menu.add_command(label="Preferences…", command=self.open_preferences)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0, **colors)
        view_menu.add_command(label="Fit to window", accelerator="Ctrl+0",
                              command=lambda: self.canvas.reset_view())
        view_menu.add_command(label="Zoom in", accelerator="Ctrl++",
                              command=lambda: self._zoom(1.2))
        view_menu.add_command(label="Zoom out", accelerator="Ctrl+-",
                              command=lambda: self._zoom(1 / 1.2))
        view_menu.add_separator()
        view_menu.add_command(label="Toggle dark / light", accelerator="Ctrl+D",
                              command=self.toggle_theme)
        menubar.add_cascade(label="View", menu=view_menu)

        self.preset_menu = tk.Menu(menubar, tearoff=0, **colors)
        menubar.add_cascade(label="Presets", menu=self.preset_menu)

        help_menu = tk.Menu(menubar, tearoff=0, **colors)
        help_menu.add_command(label="Keyboard shortcuts",
                              command=lambda: dialogs.show_shortcuts(self.root, self.palette))
        help_menu.add_command(label="Text tokens", command=self._show_tokens)
        help_menu.add_separator()
        help_menu.add_command(label=f"About {APP_NAME}",
                              command=lambda: dialogs.show_about(self.root, self.palette))
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.configure(menu=menubar)

    def _build_toolbar(self, parent: ttk.Frame) -> None:
        ttk.Button(parent, text="Open", style="Toolbar.TButton",
                   command=self.open_image).pack(side="left")
        ttk.Button(parent, text="Batch…", style="Toolbar.TButton",
                   command=self.open_batch).pack(side="left", padx=(6, 0))

        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=10)

        self.undo_button = ttk.Button(parent, text="↶", width=3, style="Toolbar.TButton",
                                      command=self.undo, state="disabled")
        self.undo_button.pack(side="left")
        self.redo_button = ttk.Button(parent, text="↷", width=3, style="Toolbar.TButton",
                                      command=self.redo, state="disabled")
        self.redo_button.pack(side="left", padx=(4, 0))
        ttk.Button(parent, text="Reset", style="Toolbar.TButton",
                   command=self.reset).pack(side="left", padx=(6, 0))

        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(parent, text="Preset", style="Muted.TLabel").pack(side="left", padx=(0, 5))
        self.preset_var = tk.StringVar(value="")
        self.preset_combo = ttk.Combobox(
            parent, textvariable=self.preset_var, state="readonly", width=24,
            values=[info.name for info in presets.list_presets()],
        )
        self.preset_combo.pack(side="left")
        self.preset_combo.bind("<<ComboboxSelected>>",
                               lambda _e: self.apply_preset(self.preset_var.get()))
        ttk.Button(parent, text="Save…", style="Toolbar.TButton",
                   command=self.save_preset).pack(side="left", padx=(6, 0))

        self.export_button = ttk.Button(parent, text="Export as…", style="Accent.TButton",
                                        command=self.save_as)
        self.export_button.pack(side="right")

    def _build_statusbar(self, parent: ttk.Frame) -> None:
        self.status_var = tk.StringVar(value="Open an image to begin.")
        self.hint_var = tk.StringVar(value=dnd.status_note() or "")
        ttk.Label(parent, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(parent, mode="indeterminate", length=110)
        ttk.Label(parent, textvariable=self.hint_var, style="Status.TLabel").pack(side="right")

    # ------------------------------------------------------------------ #
    # Control panel
    # ------------------------------------------------------------------ #

    def _build_panel(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)

        for title, builder in (
            ("Text", self._build_text_tab),
            ("Logo", self._build_logo_tab),
            ("Pattern", self._build_pattern_tab),
            ("Adjust", self._build_adjust_tab),
            ("Export", self._build_export_tab),
        ):
            holder = ttk.Frame(notebook, style="Panel.TFrame")
            notebook.add(holder, text=title)
            # Each tab scrolls on its own so the tab strip stays put and long
            # panels stay reachable on a small laptop screen.
            scroller = ScrollableFrame(holder, self.palette, width=352)
            scroller.pack(fill="both", expand=True)
            builder(scroller.interior)

    def _slider(self, parent: tk.Misc, label: str, path: str, minimum: float, maximum: float,
                step: float = 1.0, suffix: str = "", decimals: int = 0) -> SliderRow:
        row = SliderRow(
            parent, label, self.var(path), minimum, maximum, step, suffix,
            on_change=self.schedule_render, on_commit=self.push_undo, decimals=decimals,
        )
        row.pack(fill="x")
        self.refreshables.append(row)
        return row

    def _check(self, parent: tk.Misc, label: str, path: str,
               command: Optional[Callable[[], None]] = None) -> ttk.Checkbutton:
        variable = self.var(path, "bool", commit=True)

        def on_toggle() -> None:
            if command:
                command()

        widget = ttk.Checkbutton(parent, text=label, variable=variable, command=on_toggle)
        widget.pack(anchor="w", pady=2)
        return widget

    def _color(self, parent: tk.Misc, label: str, path: str) -> ColorRow:
        row = ColorRow(parent, label, self.var(path, "string"), self.palette,
                       on_change=self.push_undo)
        row.pack(fill="x", pady=3)
        self.refreshables.append(row)
        return row

    def _enum_combo(self, parent: tk.Misc, label: str, path: str, labels: Dict[Any, str],
                    on_change: Optional[Callable[[], None]] = None) -> LabeledCombo:
        reverse = {text: member for member, text in labels.items()}
        display = tk.StringVar(self.root, value=labels[self._get(path)])

        def commit() -> None:
            if self._syncing:
                return
            self._set(path, reverse[display.get()])
            self.push_undo()
            if on_change:
                on_change()
            self.schedule_render()

        combo = LabeledCombo(parent, label, display, list(labels.values()),
                             on_change=commit, width=22)
        combo.pack(fill="x", pady=(2, 4))

        class _Proxy:
            """Keeps the combobox in step with programmatic settings changes."""

            def __init__(self, app: "WatermarkApp") -> None:
                self.app = app

            def refresh(self) -> None:
                value = self.app._get(path)
                if value in labels:
                    display.set(labels[value])

        self.refreshables.append(_Proxy(self))
        return combo

    def _placement_controls(self, parent: tk.Misc, prefix: str) -> None:
        """The nine-way anchor pad plus margin, shared by the text and logo tabs."""
        holder = ttk.Frame(parent, style="Panel.TFrame")
        holder.pack(fill="x", pady=(4, 0))
        ttk.Label(holder, text="Position", style="PanelMuted.TLabel").pack(anchor="w")

        row = ttk.Frame(holder, style="Panel.TFrame")
        row.pack(fill="x", pady=(2, 0))

        anchor_var = self.var(f"{prefix}.placement.anchor", "string",
                              transform=Anchor, commit=True)
        pad = AnchorPad(row, anchor_var, self.palette, on_change=self._reset_offsets(prefix))
        pad.pack(side="left")
        self.refreshables.append(pad)

        side = ttk.Frame(row, style="Panel.TFrame")
        side.pack(side="left", fill="x", expand=True, padx=(10, 0))
        ttk.Label(side, text="Or drag it right on\nthe preview.",
                  style="PanelMuted.TLabel", justify="left", wraplength=150).pack(anchor="w")
        ttk.Button(side, text="Re-centre", style="Toolbar.TButton",
                   command=self._reset_offsets(prefix)).pack(anchor="w", pady=(6, 0))

        self._slider(holder, "Margin", f"{prefix}.placement.margin_percent", 0, 25, 0.5,
                     "%", decimals=1)

    def _reset_offsets(self, prefix: str) -> Callable[[], None]:
        def reset() -> None:
            placement: Placement = self._get(f"{prefix}.placement")
            placement.offset_x_percent = 0.0
            placement.offset_y_percent = 0.0
            self.push_undo()
            self.schedule_render()

        return reset

    # -- tabs --------------------------------------------------------------- #

    def _build_text_tab(self, parent: ttk.Frame) -> None:
        enabled = self.var("text.enabled", "bool", commit=True)
        ttk.Checkbutton(parent, text="Add a text watermark", variable=enabled).pack(
            anchor="w", pady=(0, 6)
        )

        ttk.Label(parent, text="Text", style="PanelMuted.TLabel").pack(anchor="w")
        self.text_box = tk.Text(
            parent, height=3, wrap="word", background=self.palette.field,
            foreground=self.palette.text, insertbackground=self.palette.text,
            relief="flat", highlightthickness=1, highlightbackground=self.palette.border,
            padx=6, pady=5,
        )
        self.text_box.pack(fill="x", pady=(2, 2))
        self.text_box.insert("1.0", self.settings.text.text)
        self.text_box.bind("<KeyRelease>", self._on_text_changed)
        self.text_box.bind("<FocusOut>", lambda _e: self.push_undo())

        token_row = ttk.Frame(parent, style="Panel.TFrame")
        token_row.pack(fill="x", pady=(0, 6))
        hint = ttk.Label(token_row, text="Tokens: {year} {filename} {date} …",
                         style="PanelMuted.TLabel", cursor="hand2")
        hint.pack(side="left")
        hint.bind("<Button-1>", lambda _e: self._show_tokens())
        tooltip(hint, "Click to see every token you can put in the text.", self.palette)

        self.font_combo = LabeledCombo(
            parent, "Font", self.var("text.font", "string"),
            [self.settings.text.font], on_change=self.push_undo, width=22,
        )
        self.font_combo.pack(fill="x")
        self._load_fonts_async()

        self._slider(parent, "Size", "text.size_percent", 0.5, 40, 0.1, "%", decimals=1)
        self._slider(parent, "Opacity", "text.opacity", 0, 100, 1, "%")
        self._slider(parent, "Rotation", "text.rotation", -180, 180, 1, "°")
        self._color(parent, "Colour", "text.color")

        outline = Section(parent, "Outline", self.palette, expanded=False)
        outline.pack(fill="x", pady=(8, 0))
        self._slider(outline.body, "Thickness", "text.outline_width_percent", 0, 25, 0.5,
                     "%", decimals=1)
        self._slider(outline.body, "Opacity", "text.outline_opacity", 0, 100, 1, "%")
        self._color(outline.body, "Colour", "text.outline_color")

        shadow = Section(parent, "Drop shadow", self.palette, expanded=False,
                         toggle_variable=self.var("text.shadow.enabled", "bool", commit=True))
        shadow.pack(fill="x")
        self._slider(shadow.body, "Opacity", "text.shadow.opacity", 0, 100, 1, "%")
        self._slider(shadow.body, "Offset X", "text.shadow.offset_x", -30, 30, 1)
        self._slider(shadow.body, "Offset Y", "text.shadow.offset_y", -30, 30, 1)
        self._slider(shadow.body, "Blur", "text.shadow.blur_radius", 0, 25, 0.5, decimals=1)
        self._color(shadow.body, "Colour", "text.shadow.color")

        backdrop = Section(parent, "Backdrop plate", self.palette, expanded=False,
                           toggle_variable=self.var("text.backdrop.enabled", "bool", commit=True))
        backdrop.pack(fill="x")
        self._slider(backdrop.body, "Opacity", "text.backdrop.opacity", 0, 100, 1, "%")
        self._slider(backdrop.body, "Padding", "text.backdrop.padding_percent", 0, 120, 1, "%")
        self._slider(backdrop.body, "Corner radius", "text.backdrop.corner_radius_percent",
                     0, 100, 1, "%")
        self._color(backdrop.body, "Colour", "text.backdrop.color")

        self._slider(parent, "Line spacing", "text.line_spacing", 0.6, 3.0, 0.05, "×", decimals=2)
        self._placement_controls(parent, "text")

    def _build_logo_tab(self, parent: ttk.Frame) -> None:
        enabled = self.var("image.enabled", "bool", commit=True)
        ttk.Checkbutton(parent, text="Add a logo watermark", variable=enabled).pack(
            anchor="w", pady=(0, 6)
        )

        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, 6))
        self.logo_label = ttk.Label(row, text="No logo chosen", style="PanelMuted.TLabel",
                                    wraplength=200)
        self.logo_label.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose…", command=self.choose_logo).pack(side="right")

        ttk.Label(parent, text="PNG with transparency works best.",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 6))

        self._slider(parent, "Size", "image.scale_percent", 1, 100, 1, "% of width")
        self._slider(parent, "Opacity", "image.opacity", 0, 100, 1, "%")
        self._slider(parent, "Rotation", "image.rotation", -180, 180, 1, "°")
        self._check(parent, "Convert the logo to greyscale", "image.grayscale")
        self._placement_controls(parent, "image")

    def _build_pattern_tab(self, parent: ttk.Frame) -> None:
        enabled = self.var("tile.enabled", "bool", commit=True)
        ttk.Checkbutton(parent, text="Repeat the watermark across the image",
                        variable=enabled).pack(anchor="w", pady=(0, 6))
        ttk.Label(
            parent,
            text="A tiled wash is much harder to crop out than a single corner stamp.",
            style="PanelMuted.TLabel", wraplength=280, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._enum_combo(parent, "Pattern made from", "tile.source", TILE_SOURCE_LABELS)
        self._slider(parent, "Angle", "tile.angle", -90, 90, 1, "°")
        self._slider(parent, "Opacity", "tile.opacity", 0, 100, 1, "%")
        self._slider(parent, "Horizontal spacing", "tile.spacing_x_percent", 0, 60, 0.5,
                     "%", decimals=1)
        self._slider(parent, "Vertical spacing", "tile.spacing_y_percent", 0, 60, 0.5,
                     "%", decimals=1)
        self._check(parent, "Offset alternate rows", "tile.stagger")

    def _build_adjust_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Rotate", style="Heading.TLabel").pack(anchor="w")
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 8))
        for label, angle in (("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)):
            ttk.Button(row, text=label, width=5,
                       command=lambda value=angle: self._set_rotation(value)).pack(
                side="left", padx=(0, 4)
            )
        self._slider(parent, "Free angle", "transform.rotation", -180, 180, 1, "°")

        ttk.Label(parent, text="Flip", style="Heading.TLabel").pack(anchor="w", pady=(10, 2))
        self._check(parent, "Flip horizontally", "transform.flip_horizontal")
        self._check(parent, "Flip vertically", "transform.flip_vertical")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(
            parent,
            text="Rotation is applied before the watermark, so the watermark always "
                 "stays the right way up.",
            style="PanelMuted.TLabel", wraplength=280, justify="left",
        ).pack(anchor="w")

    def _build_export_tab(self, parent: ttk.Frame) -> None:
        available = {
            image_format: FORMAT_LABELS[image_format]
            for image_format in export.available_formats()
        }
        self._enum_combo(parent, "File format", "export.image_format", available,
                         on_change=self._update_export_state)

        self.quality_slider = self._slider(parent, "Quality", "export.quality", 1, 100, 1, "%")
        self.lossless_check = self._check(parent, "Lossless (WebP / AVIF)", "export.lossless",
                                          command=self._update_export_state)
        self.png_level_slider = self._slider(
            parent, "PNG compression effort", "export.png_compress_level", 0, 9, 1
        )

        palette_row = ttk.Frame(parent, style="Panel.TFrame")
        palette_row.pack(fill="x", pady=(6, 0))
        self.palette_enabled = tk.BooleanVar(self.root, value=False)
        self.palette_value = tk.IntVar(self.root, value=128)
        check = ttk.Checkbutton(
            palette_row, text="Reduce PNG to a colour palette", variable=self.palette_enabled,
            command=self._apply_palette_setting,
        )
        check.pack(side="left")
        spin = ttk.Spinbox(palette_row, from_=2, to=256, increment=2, width=5,
                           textvariable=self.palette_value, command=self._apply_palette_setting)
        spin.pack(side="right")
        spin.bind("<Return>", lambda _e: self._apply_palette_setting())
        tooltip(check, "Great for screenshots, logos and flat graphics; photos may band.",
                self.palette)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._enum_combo(parent, "Resize", "export.resize_mode", RESIZE_LABELS,
                         on_change=self._update_export_state)
        self.max_dimension_slider = self._slider(
            parent, "Longest side", "export.max_dimension", 120, 8000, 10, "px"
        )
        self.scale_slider = self._slider(parent, "Scale", "export.scale_percent", 5, 200, 1, "%")

        exact_row = ttk.Frame(parent, style="Panel.TFrame")
        exact_row.pack(fill="x", pady=(2, 0))
        ttk.Label(exact_row, text="Exact size", style="PanelMuted.TLabel").pack(side="left")
        ttk.Spinbox(exact_row, from_=1, to=20000, width=6,
                    textvariable=self.var("export.exact_width", "int")).pack(side="left", padx=4)
        ttk.Label(exact_row, text="×", style="PanelMuted.TLabel").pack(side="left")
        ttk.Spinbox(exact_row, from_=1, to=20000, width=6,
                    textvariable=self.var("export.exact_height", "int")).pack(side="left", padx=4)
        self.exact_row = exact_row

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        target_row = ttk.Frame(parent, style="Panel.TFrame")
        target_row.pack(fill="x")
        self.target_size_enabled = tk.BooleanVar(self.root, value=False)
        self.target_size_value = tk.IntVar(self.root, value=500)
        target_check = ttk.Checkbutton(
            target_row, text="Fit under a file size", variable=self.target_size_enabled,
            command=self._apply_target_size,
        )
        target_check.pack(side="left")
        target_spin = ttk.Spinbox(target_row, from_=10, to=50000, increment=50, width=7,
                                  textvariable=self.target_size_value,
                                  command=self._apply_target_size)
        target_spin.pack(side="right")
        target_spin.bind("<Return>", lambda _e: self._apply_target_size())
        ttk.Label(target_row, text="KB", style="PanelMuted.TLabel").pack(side="right", padx=3)
        tooltip(
            target_check,
            "Lowers quality — and then the dimensions if it has to — until the file "
            "fits. Useful for upload limits and email attachments.",
            self.palette,
        )

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        privacy = Section(parent, "Metadata & privacy", self.palette, expanded=True)
        privacy.pack(fill="x")
        self._check(privacy.body, "Strip EXIF, GPS and camera data", "export.strip_metadata")
        self._check(privacy.body, "Keep the colour profile (ICC)", "export.keep_icc_profile")

        advanced = Section(parent, "Advanced encoding", self.palette, expanded=False)
        advanced.pack(fill="x")
        self._check(advanced.body, "Optimise (slower, smaller)", "export.optimize")
        self._check(advanced.body, "Progressive JPEG", "export.progressive")
        self._enum_combo(
            advanced.body, "JPEG chroma", "export.subsampling",
            {-1: "Automatic", 0: "4:4:4 — sharpest colour", 1: "4:2:2", 2: "4:2:0 — smallest"},
        )
        self._color(advanced.body, "Background behind transparency", "export.matte_color")

    # ------------------------------------------------------------------ #
    # Panel behaviour
    # ------------------------------------------------------------------ #

    def _set_rotation(self, angle: float) -> None:
        self.settings.transform.rotation = angle
        self._sync_from_settings()
        self.push_undo()

    def _on_text_changed(self, _event: tk.Event) -> None:
        if self._syncing:
            return
        self.settings.text.text = self.text_box.get("1.0", "end-1c")
        self.schedule_render()

    def _apply_target_size(self) -> None:
        if self._syncing:
            return
        try:
            value = int(self.target_size_value.get())
        except (tk.TclError, ValueError):
            value = 500
        self.settings.export.target_size_kb = value if self.target_size_enabled.get() else None
        self.push_undo()
        self.schedule_render()

    def _apply_palette_setting(self) -> None:
        if self._syncing:
            return
        try:
            value = int(self.palette_value.get())
        except (tk.TclError, ValueError):
            value = 128
        self.settings.export.png_palette_colors = (
            value if self.palette_enabled.get() else None
        )
        self.push_undo()
        self.schedule_render()

    def _update_export_state(self) -> None:
        """Grey out controls that do not apply to the chosen format or resize mode."""
        image_format = export.resolve_format(self.settings.export, self.source)
        lossy = image_format.is_lossy and not (
            self.settings.export.lossless and image_format is not ImageFormat.JPEG
        )
        self._set_enabled(self.quality_slider, lossy)
        self._set_enabled(self.lossless_check,
                          image_format in (ImageFormat.WEBP, ImageFormat.AVIF))
        self._set_enabled(self.png_level_slider, image_format is ImageFormat.PNG)

        mode = self.settings.export.resize_mode
        self._set_enabled(self.max_dimension_slider, mode is ResizeMode.FIT_WITHIN)
        self._set_enabled(self.scale_slider, mode is ResizeMode.PERCENT)
        self._set_enabled(self.exact_row, mode is ResizeMode.EXACT)

    @staticmethod
    def _set_enabled(widget: tk.Misc, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for child in [widget] + list(widget.winfo_children()):
            try:
                child.configure(state=state)  # type: ignore[call-arg]
            except tk.TclError:
                continue

    def _load_fonts_async(self) -> None:
        """Index fonts off-thread — a machine with 800 faces would stall startup."""

        def work() -> List[str]:
            return [entry.display_name for entry in fonts.available_fonts()]

        run_async(
            work,
            lambda names: self.font_combo.set_values(names),
            lambda exc: _LOG.warning("Font scan failed: %s", exc),
            self.callbacks,
        )

    def _show_tokens(self) -> None:
        body = "\n".join(f"{token:<12} {text}" for token, text in TOKEN_HELP.items())
        messagebox.showinfo(
            "Text tokens",
            "Put any of these in your watermark text or batch filename pattern:\n\n" + body,
            parent=self.root,
        )

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    def schedule_render(self, delay: int = 90) -> None:
        """Debounce renders so dragging a slider does not queue dozens of frames."""
        if self.source is None:
            self._update_status()
            return
        if self._render_job is not None:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(delay, self._request_render)

    def _request_render(self) -> None:
        self._render_job = None
        if self.source is None:
            return
        self.engine.request(self.source, self.settings, self.config.preview_resolution)

    def _pump(self) -> None:
        """Tk-thread loop: deliver preview frames and worker callbacks."""
        result = self.engine.drain()
        if result is not None:
            if result.error:
                self.status_var.set(f"Preview failed: {result.error}")
            else:
                self.canvas.set_image(result.image, result.geometry)
                self.last_result_size = result.output_size
                self._update_status(result)
                if result.warning:
                    self.hint_var.set(f"Logo unavailable — {result.warning}")

        while True:
            try:
                self.callbacks.get_nowait()()
            except queue.Empty:
                break
            except Exception:  # pragma: no cover - keep the loop alive
                _LOG.exception("A UI callback failed")
        self.root.after(40, self._pump)

    def _update_status(self, result: Optional[Any] = None) -> None:
        if self.source is None:
            self.status_var.set("Open an image to begin — or drop one on the window.")
            return

        width, height = self.source.size
        parts = [f"{width} × {height}", export.human_size(self.source.file_size)]
        if result is not None:
            image_format = (result.output_format or ImageFormat.PNG).value.upper()
            out_w, out_h = result.output_size
            piece = f"→  {image_format} · {out_w} × {out_h}"
            if result.estimated_bytes:
                piece += f" · ≈ {export.human_size(result.estimated_bytes)}"
                if self.source.file_size:
                    change = (result.estimated_bytes - self.source.file_size) / self.source.file_size
                    piece += f" ({change * 100:+.0f}%)"
            parts.append(piece)
        self.status_var.set("   ·   ".join(parts))

        warnings = list(self.source.warnings)
        note = dnd.status_note()
        if note:
            warnings.append(note)
        self.hint_var.set(warnings[0] if warnings else "")

    def _on_layer_moved(self, key: str, top_left: Tuple[float, float]) -> None:
        """The preview canvas reports a drag; convert it into placement offsets."""
        box = self.canvas.layer_box(key)
        canvas_size = self.canvas.image_size
        if not box or canvas_size is None or self.source is None:
            return
        item_size = (box[2] - box[0], box[3] - box[1])
        prefix = "text" if key == "text" else "image"
        placement: Placement = self._get(f"{prefix}.placement")
        updated = placement.with_position(top_left, canvas_size, item_size)
        placement.offset_x_percent = updated.offset_x_percent
        placement.offset_y_percent = updated.offset_y_percent
        self.schedule_render(delay=16)

    def _on_zoom_changed(self, scale: float) -> None:
        if self.source is not None:
            self.hint_var.set(f"Zoom {scale * 100:.0f}%")

    def _zoom(self, factor: float) -> None:
        self.canvas.zoom = max(0.1, min(8.0, self.canvas.zoom * factor))
        self.canvas.redraw()

    # ------------------------------------------------------------------ #
    # File actions
    # ------------------------------------------------------------------ #

    def open_image(self, _event: Optional[tk.Event] = None) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Open an image", filetypes=_IMAGE_FILETYPES,
            initialdir=self.config.last_open_dir or os.path.expanduser("~"),
        )
        if path:
            self.load(path)

    def open_paths(self, paths: List[str]) -> None:
        """Open one path, or send several straight to the batch dialog."""
        images = [path for path in paths if imageio.is_supported(path) and os.path.isfile(path)]
        folders = [path for path in paths if os.path.isdir(path)]
        if folders or len(images) > 1:
            self.open_batch(initial_files=paths)
            if images:
                self.load(images[0])
            return
        if images:
            self.load(images[0])

    def load(self, path: str) -> None:
        try:
            source = imageio.load_image(path)
        except WatermarkError as exc:
            messagebox.showerror("Could not open the image", str(exc), parent=self.root)
            return

        if self.source is not None:
            self.source.image.close()
        self.source = source
        self.config.remember_file(path)
        self._refresh_recent_menu()
        self.root.title(f"{APP_NAME} — {os.path.basename(path)}")
        self.canvas.reset_view()
        self._update_export_state()
        self.schedule_render(delay=10)

    def _on_drop(self, paths: List[str]) -> None:
        if paths:
            self.open_paths(paths)

    def choose_logo(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose a logo",
            filetypes=[("Images", "*.png *.webp *.jpg *.jpeg *.gif *.bmp"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.config.last_logo_path) or os.path.expanduser("~"),
        )
        if not path:
            return
        self.settings.image.path = path
        self.settings.image.enabled = True
        self.config.last_logo_path = path
        self.logo_label.configure(text=os.path.basename(path))
        self._sync_from_settings()
        self.push_undo()

    def save_as(self, _event: Optional[tk.Event] = None) -> None:
        if self.source is None:
            messagebox.showinfo("Nothing to export", "Open an image first.", parent=self.root)
            return
        if self.busy:
            return

        image_format = export.resolve_format(self.settings.export, self.source)
        stem = os.path.splitext(os.path.basename(self.source.path or "image"))[0]
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export image",
            defaultextension=image_format.extension,
            initialfile=f"{stem}_wm{image_format.extension}",
            initialdir=self.config.last_output_dir or self.config.last_open_dir
            or os.path.expanduser("~"),
            filetypes=[(image_format.value.upper(), "*" + image_format.extension),
                       ("All files", "*.*")],
        )
        if not path:
            return

        self.config.last_output_dir = os.path.dirname(path)
        self._set_busy(True, "Exporting at full resolution…")
        source = self.source
        settings = self.settings.copy()

        def work() -> Any:
            from ..core.batch import process_image

            image, _context = process_image(source, settings)
            return export.save_image(image, path, settings.export, source), path

        run_async(work, self._on_export_done, self._on_export_error, self.callbacks)

    def _on_export_done(self, payload: Tuple[Any, str]) -> None:
        result, path = payload
        self._set_busy(False)
        message = (
            f"Saved {os.path.basename(path)}\n"
            f"{result.size[0]} × {result.size[1]} · {export.human_size(result.byte_count)}"
        )
        if self.source and self.source.file_size:
            change = (result.byte_count - self.source.file_size) / self.source.file_size * 100
            message += f" ({change:+.0f}% vs original)"
        if result.notes:
            message += "\n\n" + "\n".join(result.notes)
        self.status_var.set(message.replace("\n", "   ·   "))
        if messagebox.askyesno("Export complete", message + "\n\nOpen the folder?",
                               parent=self.root):
            dialogs.open_folder(os.path.dirname(path))

    def _on_export_error(self, exc: Exception) -> None:
        self._set_busy(False)
        messagebox.showerror("Export failed", str(exc), parent=self.root)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        self.export_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.status_var.set(message)
            self.progress.pack(side="right", padx=(10, 0))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def open_batch(self, _event: Optional[tk.Event] = None,
                   initial_files: Optional[List[str]] = None) -> None:
        files = initial_files
        if files is None and self.source and self.source.path:
            files = [self.source.path]
        dialogs.BatchDialog(self.root, self.palette, self.settings.copy(), self.config, files)

    # ------------------------------------------------------------------ #
    # Presets, preferences, theme
    # ------------------------------------------------------------------ #

    def _refresh_preset_menu(self) -> None:
        self.preset_menu.delete(0, "end")
        for info in presets.list_presets():
            self.preset_menu.add_command(
                label=info.name, command=lambda name=info.name: self.apply_preset(name)
            )
        self.preset_menu.add_separator()
        self.preset_menu.add_command(label="Save current settings…", command=self.save_preset)
        self.preset_menu.add_command(label="Delete a preset…", command=self.delete_preset)
        try:
            self.preset_combo.configure(values=[info.name for info in presets.list_presets()])
        except (AttributeError, tk.TclError):
            pass  # the toolbar has not been built yet, or is being replaced

    def apply_preset(self, name: str) -> None:
        if not name:
            return
        try:
            loaded = presets.load_preset(name)
        except WatermarkError as exc:
            messagebox.showerror("Preset problem", str(exc), parent=self.root)
            return

        self.push_undo()
        # A preset describes the look, not where your files go, so the output
        # folder the user already chose is preserved.
        loaded.output.directory = self.settings.output.directory
        if loaded.image.enabled and not loaded.image.path:
            loaded.image.path = self.settings.image.path or self.config.last_logo_path
        self.settings = loaded
        self._sync_from_settings()
        self._refresh_logo_label()
        self.status_var.set(f"Applied preset “{name}”.")

    def save_preset(self) -> None:
        name = dialogs.ask_preset_name(self.root, self.palette)
        if not name:
            return
        try:
            presets.save_preset(name, self.settings)
        except WatermarkError as exc:
            messagebox.showerror("Could not save the preset", str(exc), parent=self.root)
            return
        self._refresh_preset_menu()
        self.preset_var.set(name)
        self.status_var.set(f"Saved preset “{name}”.")

    def delete_preset(self) -> None:
        user_presets = [info.name for info in presets.list_presets() if not info.builtin]
        if not user_presets:
            messagebox.showinfo("No presets to delete",
                                "You have not saved any presets yet.", parent=self.root)
            return
        name = self.preset_var.get()
        if name not in user_presets:
            messagebox.showinfo(
                "Pick a preset",
                "Choose one of your own presets in the toolbar first, then delete it.",
                parent=self.root,
            )
            return
        if messagebox.askyesno("Delete preset", f"Delete “{name}”?", parent=self.root):
            try:
                presets.delete_preset(name)
            except WatermarkError as exc:
                messagebox.showerror("Could not delete", str(exc), parent=self.root)
                return
            self.preset_var.set("")
            self._refresh_preset_menu()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.delete(0, "end")
        recent = self.config.existing_recent_files()
        if not recent:
            self.recent_menu.add_command(label="(nothing yet)", state="disabled")
            return
        for path in recent:
            self.recent_menu.add_command(
                label=os.path.basename(path), command=lambda value=path: self.load(value)
            )
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear list", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.config.recent_files.clear()
        self._refresh_recent_menu()

    def _refresh_logo_label(self) -> None:
        if hasattr(self, "logo_label"):
            path = self.settings.image.path
            self.logo_label.configure(
                text=os.path.basename(path) if path else "No logo chosen"
            )

    def open_preferences(self) -> None:
        dialogs.PreferencesDialog(self.root, self.palette, self.config, self._apply_preferences)

    def _apply_preferences(self) -> None:
        config_module.save_config(self.config)
        if self.config.theme != self.palette.name:
            self._rebuild_ui()
        else:
            self.schedule_render()

    def toggle_theme(self, _event: Optional[tk.Event] = None) -> None:
        self.config.theme = "light" if self.config.theme == "dark" else "dark"
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """Rebuild every widget so a theme change applies everywhere."""
        zoom = self.canvas.zoom
        for child in self.root.winfo_children():
            if isinstance(child, tk.Menu):
                continue
            child.destroy()
        for attribute in ("preset_combo", "text_box", "logo_label", "undo_button",
                          "redo_button", "export_button", "canvas", "progress"):
            self.__dict__.pop(attribute, None)
        self.vars.clear()
        self.refreshables.clear()
        self.palette = apply_theme(self.root, self.config.theme)
        self._build()
        self._sync_from_settings()
        self._refresh_logo_label()
        self.canvas.zoom = zoom
        self._update_history_buttons()

    def reset(self, _event: Optional[tk.Event] = None) -> None:
        if not messagebox.askyesno(
            "Reset everything?",
            "This returns every watermark and export setting to its default.",
            parent=self.root,
        ):
            return
        self.push_undo()
        self.settings = JobSettings()
        self._sync_from_settings()
        self._refresh_logo_label()
        self.preset_var.set("")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _bind_shortcuts(self) -> None:
        bindings = {
            "<Control-o>": lambda e: self.open_image(),
            "<Control-s>": lambda e: self.save_as(),
            "<Control-b>": lambda e: self.open_batch(),
            "<Control-z>": lambda e: self.undo(),
            "<Control-y>": lambda e: self.redo(),
            "<Control-Shift-Z>": lambda e: self.redo(),
            "<Control-r>": lambda e: self.reset(),
            "<Control-d>": lambda e: self.toggle_theme(),
            "<Control-q>": lambda e: self.quit(),
            "<Control-Key-0>": lambda e: self.canvas.reset_view(),
            "<Control-plus>": lambda e: self._zoom(1.2),
            "<Control-equal>": lambda e: self._zoom(1.2),
            "<Control-minus>": lambda e: self._zoom(1 / 1.2),
        }
        for sequence, handler in bindings.items():
            self.root.bind_all(sequence, handler)
        dnd.register(self.root, self._on_drop)

    def quit(self, _event: Optional[tk.Event] = None) -> None:
        try:
            self.config.window_geometry = self.root.winfo_geometry()
            self.config.store_settings(self.settings)
            config_module.save_config(self.config)
        except Exception:  # pragma: no cover - never block shutdown
            _LOG.debug("Could not persist configuration", exc_info=True)
        self.engine.shutdown()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``watermark-gui`` and ``python -m watermark``."""
    import sys

    logs.configure()
    paths = [arg for arg in (argv if argv is not None else sys.argv[1:]) if not arg.startswith("-")]
    try:
        app = WatermarkApp(initial_paths=paths or None)
    except tk.TclError as exc:
        print(f"Could not start the interface: {exc}", file=sys.stderr)
        print("A desktop session with a display is required.", file=sys.stderr)
        return 1
    app.run()
    return 0
