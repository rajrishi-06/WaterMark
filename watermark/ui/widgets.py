"""Reusable Tk widgets.

Small, focused controls that keep :mod:`watermark.ui.app` readable: a slider
that also shows a typeable number, a colour swatch, a nine-way anchor pad, and
collapsible sections so the panel is not a wall of options.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, ttk
from typing import Callable, Iterable, List, Optional, Sequence

from .theme import Palette


class Section(ttk.Frame):
    """A titled, optionally collapsible group of controls."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        palette: Palette,
        collapsible: bool = True,
        expanded: bool = True,
        toggle_variable: Optional[tk.BooleanVar] = None,
        on_toggle: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", padding=(0, 0, 0, 8))
        self.palette = palette
        self._expanded = expanded
        self._collapsible = collapsible

        header = ttk.Frame(self, style="Panel.TFrame")
        header.pack(fill="x")

        if toggle_variable is not None:
            ttk.Checkbutton(
                header, variable=toggle_variable, command=on_toggle, takefocus=False
            ).pack(side="left", padx=(0, 4))

        self._chevron = ttk.Label(header, text="▾" if expanded else "▸", style="Heading.TLabel")
        if collapsible:
            self._chevron.pack(side="left", padx=(0, 4))
        self._title = ttk.Label(header, text=title, style="Heading.TLabel")
        self._title.pack(side="left")

        self.body = ttk.Frame(self, style="Panel.TFrame", padding=(6, 6, 0, 0))
        if expanded:
            self.body.pack(fill="x")

        if collapsible:
            for widget in (self._chevron, self._title, header):
                widget.bind("<Button-1>", lambda _event: self.toggle())
                widget.configure(cursor="hand2")

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._chevron.configure(text="▾" if self._expanded else "▸")
        if self._expanded:
            self.body.pack(fill="x")
        else:
            self.body.forget()

    def set_expanded(self, expanded: bool) -> None:
        if expanded != self._expanded:
            self.toggle()


class SliderRow(ttk.Frame):
    """A labelled slider paired with an editable numeric box.

    Sliders alone make precise values impossible; a number box alone makes
    exploration tedious.  Both, kept in sync, covers everyone.
    """

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.DoubleVar,
        minimum: float,
        maximum: float,
        step: float = 1.0,
        suffix: str = "",
        on_change: Optional[Callable[[], None]] = None,
        on_commit: Optional[Callable[[], None]] = None,
        decimals: int = 0,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = decimals
        self._on_change = on_change
        self._on_commit = on_commit
        self._syncing = False

        ttk.Label(self, text=label, style="PanelMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(4, 0)
        )
        self.entry_var = tk.StringVar(value=self._format(variable.get()))
        entry = ttk.Spinbox(
            self,
            from_=minimum,
            to=maximum,
            increment=step,
            textvariable=self.entry_var,
            width=7,
            justify="right",
            command=self._from_entry,
        )
        entry.grid(row=0, column=1, sticky="e", pady=(4, 0))
        entry.bind("<Return>", lambda _e: self._from_entry())
        entry.bind("<FocusOut>", lambda _e: self._from_entry())
        if suffix:
            ttk.Label(self, text=suffix, style="PanelMuted.TLabel").grid(
                row=0, column=2, sticky="w", padx=(3, 0), pady=(4, 0)
            )

        self.scale = ttk.Scale(
            self, from_=minimum, to=maximum, variable=variable,
            orient="horizontal", command=self._from_scale,
        )
        self.scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(1, 0))
        self.scale.bind("<ButtonRelease-1>", lambda _e: self._commit())
        self.columnconfigure(0, weight=1)

    def _format(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def _from_scale(self, _value: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.entry_var.set(self._format(self.variable.get()))
        self._syncing = False
        if self._on_change:
            self._on_change()

    def _from_entry(self) -> None:
        if self._syncing:
            return
        try:
            value = float(self.entry_var.get().replace(",", "."))
        except ValueError:
            value = self.variable.get()
        value = max(self.minimum, min(self.maximum, value))
        self._syncing = True
        self.variable.set(value)
        self.entry_var.set(self._format(value))
        self._syncing = False
        if self._on_change:
            self._on_change()
        self._commit()

    def _commit(self) -> None:
        if self._on_commit:
            self._on_commit()

    def refresh(self) -> None:
        """Pull a value that changed elsewhere (preset load, undo) into the box."""
        self._syncing = True
        self.entry_var.set(self._format(self.variable.get()))
        self._syncing = False


class ColorRow(ttk.Frame):
    """A label plus a clickable colour swatch."""

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        palette: Palette,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.variable = variable
        self.palette = palette
        self._on_change = on_change

        ttk.Label(self, text=label, style="PanelMuted.TLabel").pack(side="left")
        self.swatch = tk.Button(
            self, width=6, relief="flat", cursor="hand2", command=self._pick,
            highlightthickness=1, highlightbackground=palette.border, bd=0,
        )
        self.swatch.pack(side="right")
        self.refresh()

    def _pick(self) -> None:
        _rgb, hex_value = colorchooser.askcolor(
            color=self.variable.get(), title="Choose colour", parent=self.winfo_toplevel()
        )
        if hex_value:
            self.variable.set(hex_value)
            self.refresh()
            if self._on_change:
                self._on_change()

    def refresh(self) -> None:
        value = self.variable.get() or "#FFFFFF"
        try:
            self.swatch.configure(background=value, activebackground=value)
        except tk.TclError:
            self.swatch.configure(background="#FFFFFF")


class AnchorPad(ttk.Frame):
    """A 3×3 grid of position buttons, mirroring the nine anchors."""

    ORDER: Sequence[Sequence[str]] = (
        ("top-left", "top-center", "top-right"),
        ("middle-left", "center", "middle-right"),
        ("bottom-left", "bottom-center", "bottom-right"),
    )

    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        palette: Palette,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.variable = variable
        self.palette = palette
        self._on_change = on_change
        self._buttons: dict = {}

        for row, names in enumerate(self.ORDER):
            for column, name in enumerate(names):
                button = tk.Button(
                    self, text="", width=3, height=1, bd=0, relief="flat",
                    cursor="hand2", highlightthickness=1,
                    highlightbackground=palette.border,
                    command=lambda value=name: self._select(value),
                )
                button.grid(row=row, column=column, padx=1, pady=1)
                self._buttons[name] = button
        self.refresh()

    def _select(self, value: str) -> None:
        self.variable.set(value)
        self.refresh()
        if self._on_change:
            self._on_change()

    def refresh(self) -> None:
        current = self.variable.get()
        for name, button in self._buttons.items():
            selected = name == current
            button.configure(
                background=self.palette.accent if selected else self.palette.field,
                activebackground=self.palette.accent if selected else self.palette.border,
                text="●" if selected else "",
                foreground=self.palette.accent_text,
            )


class LabeledCombo(ttk.Frame):
    """A label above a read-only combobox."""

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        values: Iterable[str],
        on_change: Optional[Callable[[], None]] = None,
        width: int = 18,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame")
        ttk.Label(self, text=label, style="PanelMuted.TLabel").pack(anchor="w", pady=(4, 1))
        self.combo = ttk.Combobox(
            self, textvariable=variable, values=list(values), state="readonly", width=width
        )
        self.combo.pack(fill="x")
        if on_change:
            self.combo.bind("<<ComboboxSelected>>", lambda _e: on_change())

    def set_values(self, values: Iterable[str]) -> None:
        self.combo.configure(values=list(values))


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container that tracks its own width.

    Settings panels outgrow any window; without this the bottom controls become
    unreachable on a laptop screen.
    """

    def __init__(self, parent: tk.Misc, palette: Palette, width: int = 320) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.canvas = tk.Canvas(
            self, borderwidth=0, highlightthickness=0,
            background=palette.panel, width=width,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.interior = ttk.Frame(self.canvas, style="Panel.TFrame", padding=(12, 10))

        self._window = self.canvas.create_window((0, 0), window=self.interior, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.interior.bind("<Configure>", self._on_interior_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

    def _on_interior_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, active: bool) -> None:
        root = self.winfo_toplevel()
        if active:
            root.bind_all("<MouseWheel>", self._on_wheel)
            root.bind_all("<Button-4>", self._on_wheel)
            root.bind_all("<Button-5>", self._on_wheel)
        else:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                root.unbind_all(sequence)

    def _on_wheel(self, event: tk.Event) -> None:
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


def tooltip(widget: tk.Widget, text: str, palette: Palette) -> None:
    """Attach a lightweight hover tooltip.

    Used to explain the non-obvious options (tokens, subsampling, target size)
    without cluttering the panel with paragraphs.
    """
    state: List[Optional[tk.Toplevel]] = [None]

    def show(_event: tk.Event) -> None:
        if state[0] is not None:
            return
        window = tk.Toplevel(widget)
        window.wm_overrideredirect(True)
        window.configure(background=palette.border)
        label = tk.Label(
            window, text=text, justify="left", background=palette.panel,
            foreground=palette.text, padx=8, pady=5, wraplength=260, bd=0,
        )
        label.pack(padx=1, pady=1)
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 6
        window.wm_geometry(f"+{x}+{y}")
        state[0] = window

    def hide(_event: tk.Event) -> None:
        if state[0] is not None:
            state[0].destroy()
            state[0] = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")
