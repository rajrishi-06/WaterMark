"""Modal dialogs: batch processing, preferences and help."""

from __future__ import annotations

import os
import queue
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from .. import __version__
from ..core import export, paths, presets
from ..core.batch import BatchReport, BatchRunner, collect_inputs
from ..core.config import AppConfig
from ..core.logs import get_logger
from ..core.models import ConflictPolicy, JobSettings
from ..core.tokens import TOKEN_HELP
from .theme import Palette
from .widgets import LabeledCombo, tooltip

_LOG = get_logger(__name__)

_IMAGE_FILETYPES = [
    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif"),
    ("All files", "*.*"),
]


class BatchDialog(tk.Toplevel):
    """Apply the current settings to many files at once."""

    def __init__(
        self,
        parent: tk.Misc,
        palette: Palette,
        settings: JobSettings,
        config: AppConfig,
        initial_files: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        self.title("Batch process")
        self.configure(background=palette.bg)
        self.palette = palette
        self.settings = settings
        self.config_obj = config
        self.transient(parent)
        self.minsize(720, 520)

        self.files: List[str] = []
        self.runner: Optional[BatchRunner] = None
        self._events: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._pump_job: Optional[str] = None

        self.output_var = tk.StringVar(value=settings.output.directory or config.last_output_dir)
        self.pattern_var = tk.StringVar(value=settings.output.filename_pattern)
        self.conflict_var = tk.StringVar(value=settings.output.conflict.value)
        self.status_var = tk.StringVar(value="No files added yet.")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build()
        if initial_files:
            self.add_paths(initial_files)
        self._pump_job = self.after(60, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- layout ------------------------------------------------------------ #

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Label(bar, text="Files to process", style="Title.TLabel").pack(side="left")
        ttk.Button(bar, text="Add files…", command=self._add_files).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="Add folder…", command=self._add_folder).pack(side="right")

        columns = ("file", "folder")
        self.tree = ttk.Treeview(outer, columns=columns, show="headings", height=10)
        self.tree.heading("file", text="File")
        self.tree.heading("folder", text="Folder")
        self.tree.column("file", width=260, anchor="w")
        self.tree.column("folder", width=380, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(8, 4))
        self.tree.bind("<Delete>", lambda _e: self._remove_selected())

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 10))
        ttk.Button(row, text="Remove selected", command=self._remove_selected).pack(side="left")
        ttk.Button(row, text="Clear", command=self._clear).pack(side="left", padx=6)
        self.count_label = ttk.Label(row, text="", style="Muted.TLabel")
        self.count_label.pack(side="right")

        options = ttk.LabelFrame(outer, text="Output", padding=10)
        options.pack(fill="x")

        ttk.Label(options, text="Folder", style="PanelMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=2
        )
        entry = ttk.Entry(options, textvariable=self.output_var)
        entry.grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(options, text="Browse…", command=self._pick_output).grid(row=0, column=2, pady=2)

        ttk.Label(options, text="Name pattern", style="PanelMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=2
        )
        pattern_entry = ttk.Entry(options, textvariable=self.pattern_var)
        pattern_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        tooltip(
            pattern_entry,
            "Tokens: " + ", ".join(sorted(TOKEN_HELP)) + ". The extension is corrected "
            "automatically when you convert format.",
            self.palette,
        )
        ttk.Button(options, text="Tokens…", command=self._show_tokens).grid(row=1, column=2, pady=2)

        combo = LabeledCombo(
            options, "If the file already exists",
            self.conflict_var, [policy.value for policy in ConflictPolicy], width=14,
        )
        combo.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        options.columnconfigure(1, weight=1)

        progress_row = ttk.Frame(outer)
        progress_row.pack(fill="x", pady=(12, 0))
        self.progress = ttk.Progressbar(progress_row, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x")
        ttk.Label(progress_row, textvariable=self.status_var, style="Muted.TLabel").pack(
            anchor="w", pady=(4, 0)
        )

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        self.close_button = ttk.Button(actions, text="Close", command=self._close)
        self.close_button.pack(side="right")
        self.start_button = ttk.Button(
            actions, text="Start", style="Accent.TButton", command=self._start
        )
        self.start_button.pack(side="right", padx=6)
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self._cancel, state="disabled"
        )
        self.cancel_button.pack(side="right")

    # -- file list ---------------------------------------------------------- #

    def add_paths(self, paths_in: List[str]) -> None:
        added = 0
        existing = {path.lower() for path in self.files}
        for path in collect_inputs(paths_in, recursive=True):
            if path.lower() in existing:
                continue
            self.files.append(path)
            existing.add(path.lower())
            self.tree.insert(
                "", "end", values=(os.path.basename(path), os.path.dirname(path))
            )
            added += 1
        self._refresh_count()
        if added:
            self.status_var.set(f"Added {added} file{'s' if added != 1 else ''}.")

    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(
            parent=self, title="Add images", filetypes=_IMAGE_FILETYPES,
            initialdir=self.config_obj.last_open_dir or os.path.expanduser("~"),
        )
        if chosen:
            self.add_paths(list(chosen))

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(
            parent=self, title="Add every image in a folder",
            initialdir=self.config_obj.last_open_dir or os.path.expanduser("~"),
        )
        if folder:
            self.add_paths([folder])

    def _remove_selected(self) -> None:
        for item in self.tree.selection():
            index = self.tree.index(item)
            self.tree.delete(item)
            if 0 <= index < len(self.files):
                del self.files[index]
        self._refresh_count()

    def _clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.files.clear()
        self._refresh_count()

    def _refresh_count(self) -> None:
        self.count_label.configure(text=f"{len(self.files)} file(s)")

    def _pick_output(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Where should results go?")
        if folder:
            self.output_var.set(folder)

    def _show_tokens(self) -> None:
        body = "\n".join(f"{token:<12} {help_text}" for token, help_text in TOKEN_HELP.items())
        messagebox.showinfo("Filename tokens", body, parent=self)

    # -- running ------------------------------------------------------------ #

    def _start(self) -> None:
        if not self.files:
            messagebox.showinfo("Nothing to do", "Add some files first.", parent=self)
            return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showwarning(
                "Choose an output folder",
                "Pick a folder for the results so your originals stay untouched.",
                parent=self,
            )
            return

        self.settings.output.directory = output
        self.settings.output.filename_pattern = self.pattern_var.get().strip() or "{name}_wm{ext}"
        self.settings.output.conflict = ConflictPolicy(self.conflict_var.get())
        self.config_obj.last_output_dir = output

        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.close_button.configure(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Working…")

        self.runner = BatchRunner(
            self.files,
            self.settings,
            workers=self.config_obj.batch_workers,
            on_progress=lambda done, total, path: self._events.put(
                lambda: self._on_progress(done, total, path)
            ),
            on_finish=lambda report: self._events.put(lambda: self._on_finish(report)),
        )
        self.runner.start()

    def _cancel(self) -> None:
        if self.runner:
            self.runner.cancel()
            self.status_var.set("Finishing the files already in flight…")

    def _on_progress(self, done: int, total: int, path: str) -> None:
        self.progress_var.set(done / max(1, total) * 100)
        self.status_var.set(f"{done}/{total} — {os.path.basename(path)}")

    def _on_finish(self, report: BatchReport) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.close_button.configure(state="normal")
        self.progress_var.set(100.0)
        self.status_var.set(report.summary())
        self.runner = None

        if report.failed:
            detail = "\n".join(
                f"• {os.path.basename(item.source)}: {item.message}" for item in report.failed[:12]
            )
            if len(report.failed) > 12:
                detail += f"\n… and {len(report.failed) - 12} more"
            messagebox.showwarning(
                "Some files could not be processed", f"{report.summary()}\n\n{detail}", parent=self
            )
        elif not report.cancelled:
            if messagebox.askyesno(
                "Batch complete",
                f"{report.summary()}.\n\nOpen the output folder?",
                parent=self,
            ):
                open_folder(self.settings.output.directory)

    def _pump(self) -> None:
        """Drain worker-thread callbacks on the Tk thread."""
        while True:
            try:
                self._events.get_nowait()()
            except queue.Empty:
                break
            except Exception:  # pragma: no cover - a callback must not kill the pump
                _LOG.exception("A batch callback failed")
                break
        self._pump_job = self.after(80, self._pump)

    def destroy(self) -> None:
        """Cancel the queued poll before tearing the window down.

        Without this the pending ``after`` fires against a destroyed widget and
        Tk prints ``invalid command name ..._pump`` to stderr.
        """
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except tk.TclError:  # pragma: no cover - already gone
                pass
            self._pump_job = None
        super().destroy()

    def _close(self) -> None:
        if self.runner and self.runner.running:
            if not messagebox.askyesno(
                "Stop the batch?", "A batch is still running. Stop it and close?", parent=self
            ):
                return
            self.runner.cancel()
        self.destroy()


class PreferencesDialog(tk.Toplevel):
    """App-wide settings that are not part of a job."""

    def __init__(
        self,
        parent: tk.Misc,
        palette: Palette,
        config: AppConfig,
        on_apply: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.title("Preferences")
        self.configure(background=palette.bg)
        self.transient(parent)
        self.resizable(False, False)
        self.config_obj = config
        self.on_apply = on_apply

        self.theme_var = tk.StringVar(value=config.theme)
        self.preview_var = tk.IntVar(value=config.preview_resolution)
        self.workers_var = tk.IntVar(value=config.batch_workers)
        self.remember_var = tk.BooleanVar(value=config.remember_settings)

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        LabeledCombo(body, "Theme", self.theme_var, ["dark", "light"], width=12).pack(
            anchor="w", fill="x"
        )

        ttk.Label(body, text="Preview quality (longest edge, px)", style="PanelMuted.TLabel").pack(
            anchor="w", pady=(10, 2)
        )
        preview_box = ttk.Spinbox(
            body, from_=400, to=2400, increment=100, textvariable=self.preview_var, width=8
        )
        preview_box.pack(anchor="w")
        tooltip(preview_box, "Lower values make the live preview faster on older machines.", palette)

        ttk.Label(body, text="Batch workers", style="PanelMuted.TLabel").pack(
            anchor="w", pady=(10, 2)
        )
        ttk.Spinbox(body, from_=1, to=8, textvariable=self.workers_var, width=8).pack(anchor="w")

        ttk.Checkbutton(
            body, text="Remember my settings between sessions", variable=self.remember_var
        ).pack(anchor="w", pady=(12, 0))

        locations = ttk.LabelFrame(body, text="Where files are kept", padding=8)
        locations.pack(fill="x", pady=(14, 0))
        for label, value in (
            ("Settings", paths.config_dir()),
            ("Presets", paths.preset_dir()),
            ("Logs", paths.log_dir()),
        ):
            row = ttk.Frame(locations)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label + ":", width=9, style="PanelMuted.TLabel").pack(side="left")
            ttk.Label(row, text=value, style="PanelMuted.TLabel").pack(side="left")
            ttk.Button(
                row, text="Open", width=6,
                command=lambda path=value: open_folder(path),
            ).pack(side="right")

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(16, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Apply", style="Accent.TButton", command=self._apply).pack(
            side="right", padx=6
        )

    def _apply(self) -> None:
        self.config_obj.theme = self.theme_var.get()
        self.config_obj.preview_resolution = max(400, min(2400, int(self.preview_var.get())))
        self.config_obj.batch_workers = max(1, min(8, int(self.workers_var.get())))
        self.config_obj.remember_settings = bool(self.remember_var.get())
        self.on_apply()
        self.destroy()


def ask_preset_name(parent: tk.Misc, palette: Palette) -> Optional[str]:
    """Prompt for a preset name, refusing built-in names."""
    dialog = tk.Toplevel(parent)
    dialog.title("Save preset")
    dialog.configure(background=palette.bg)
    dialog.transient(parent)
    dialog.resizable(False, False)
    dialog.grab_set()

    result: List[Optional[str]] = [None]
    body = ttk.Frame(dialog, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Name this preset", style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        body, text="It will appear in the Presets menu and can be reused on any image.",
        style="Muted.TLabel", wraplength=320,
    ).pack(anchor="w", pady=(2, 10))

    variable = tk.StringVar(value="My preset")
    entry = ttk.Entry(body, textvariable=variable, width=36)
    entry.pack(fill="x")
    entry.focus_set()
    entry.select_range(0, "end")
    error = ttk.Label(body, text="", style="Danger.TLabel")
    error.pack(anchor="w", pady=(4, 0))

    def confirm() -> None:
        name = variable.get().strip()
        if not name:
            error.configure(text="Please enter a name.")
            return
        if name in presets.BUILTIN_PRESETS:
            error.configure(text="That name belongs to a built-in preset.")
            return
        result[0] = name
        dialog.destroy()

    actions = ttk.Frame(body)
    actions.pack(fill="x", pady=(14, 0))
    ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(actions, text="Save", style="Accent.TButton", command=confirm).pack(
        side="right", padx=6
    )
    entry.bind("<Return>", lambda _e: confirm())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    parent.wait_window(dialog)
    return result[0]


SHORTCUTS = [
    ("Ctrl+O", "Open an image"),
    ("Ctrl+S", "Export the current image"),
    ("Ctrl+B", "Batch process a folder"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
    ("Ctrl+R", "Reset all settings"),
    ("Ctrl+0", "Fit the preview to the window"),
    ("Ctrl+ + / -", "Zoom in and out"),
    ("Ctrl+D", "Switch between dark and light"),
    ("Ctrl+Shift+C", "Compare without the watermark"),
    ("Hold \\", "Peek at the un-watermarked image"),
    ("Drag on preview", "Move the watermark"),
    ("Scroll on preview", "Zoom"),
]


def show_shortcuts(parent: tk.Misc, palette: Palette) -> None:
    window = tk.Toplevel(parent)
    window.title("Keyboard shortcuts")
    window.configure(background=palette.bg)
    window.transient(parent)
    window.resizable(False, False)
    body = ttk.Frame(window, padding=16)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Keyboard shortcuts", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
    for keys, description in SHORTCUTS:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=keys, width=18, style="Muted.TLabel").pack(side="left")
        ttk.Label(row, text=description).pack(side="left")
    ttk.Button(body, text="Close", command=window.destroy).pack(anchor="e", pady=(14, 0))


def show_about(parent: tk.Misc, palette: Palette) -> None:
    window = tk.Toplevel(parent)
    window.title("About WaterMark")
    window.configure(background=palette.bg)
    window.transient(parent)
    window.resizable(False, False)
    body = ttk.Frame(window, padding=20)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="WaterMark", style="Title.TLabel").pack(anchor="w")
    ttk.Label(body, text=f"Version {__version__}", style="Muted.TLabel").pack(anchor="w")
    ttk.Label(
        body,
        text=(
            "Watermark, resize and compress your photos — one at a time or a "
            "whole folder at once.\n\nEverything runs on your computer; no image "
            "ever leaves it."
        ),
        style="Muted.TLabel", wraplength=380, justify="left",
    ).pack(anchor="w", pady=(10, 0))
    ttk.Label(
        body, text=f"Formats available: {', '.join(f.value.upper() for f in export.available_formats() if f.value != 'keep')}",
        style="Muted.TLabel", wraplength=380,
    ).pack(anchor="w", pady=(10, 0))
    ttk.Button(body, text="Close", command=window.destroy).pack(anchor="e", pady=(16, 0))


def open_folder(path: str) -> None:
    """Reveal a folder in the platform's file manager."""
    if not path or not os.path.isdir(path):
        return
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open("file://" + os.path.abspath(path))
    except Exception:  # pragma: no cover - depends on the desktop environment
        pass
