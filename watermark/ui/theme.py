"""Colour palettes and ttk styling.

Tk's stock widgets look dated on every platform.  Restyling the ``clam`` theme
gives one consistent, modern appearance on Windows, macOS and Linux, and makes
a dark mode possible at all.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Dict


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    panel: str
    field: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    danger: str
    success: str
    #: Checkerboard squares behind transparent pixels.
    checker_a: str
    checker_b: str


DARK = Palette(
    name="dark",
    bg="#1b1d21",
    panel="#23262b",
    field="#2b2f36",
    border="#3a3f47",
    text="#e6e8ec",
    muted="#98a1ad",
    accent="#4c8dff",
    accent_text="#ffffff",
    danger="#ff6b6b",
    success="#4ade80",
    checker_a="#2a2d33",
    checker_b="#33373e",
)

LIGHT = Palette(
    name="light",
    bg="#eef0f3",
    panel="#ffffff",
    field="#ffffff",
    border="#d3d7de",
    text="#16181d",
    muted="#5c6570",
    accent="#2563eb",
    accent_text="#ffffff",
    danger="#dc2626",
    success="#16a34a",
    checker_a="#e9ebef",
    checker_b="#f7f8fa",
)

PALETTES: Dict[str, Palette] = {"dark": DARK, "light": LIGHT}


def apply_theme(root: tk.Misc, name: str = "dark") -> Palette:
    """Restyle every ttk widget class and return the active palette."""
    palette = PALETTES.get(name, DARK)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - exotic Tk builds
        pass

    root.configure(background=palette.bg)

    style.configure(".", background=palette.bg, foreground=palette.text,
                    fieldbackground=palette.field, bordercolor=palette.border,
                    lightcolor=palette.panel, darkcolor=palette.panel,
                    troughcolor=palette.field, focuscolor=palette.accent)

    style.configure("TFrame", background=palette.bg)
    style.configure("Panel.TFrame", background=palette.panel)
    style.configure("Card.TFrame", background=palette.panel, relief="flat")

    style.configure("TLabel", background=palette.bg, foreground=palette.text)
    style.configure("Panel.TLabel", background=palette.panel, foreground=palette.text)
    style.configure("Muted.TLabel", background=palette.bg, foreground=palette.muted)
    style.configure("PanelMuted.TLabel", background=palette.panel, foreground=palette.muted)
    style.configure("Heading.TLabel", background=palette.panel, foreground=palette.text,
                    font=("TkDefaultFont", 10, "bold"))
    style.configure("Title.TLabel", background=palette.bg, foreground=palette.text,
                    font=("TkDefaultFont", 14, "bold"))
    style.configure("Status.TLabel", background=palette.panel, foreground=palette.muted)
    style.configure("Danger.TLabel", background=palette.panel, foreground=palette.danger)

    style.configure("TButton", background=palette.field, foreground=palette.text,
                    bordercolor=palette.border, focusthickness=1, padding=(10, 5),
                    relief="flat")
    style.map("TButton",
              background=[("active", palette.border), ("disabled", palette.panel)],
              foreground=[("disabled", palette.muted)])

    style.configure("Accent.TButton", background=palette.accent,
                    foreground=palette.accent_text, padding=(14, 6))
    style.map("Accent.TButton",
              background=[("active", palette.accent), ("disabled", palette.border)],
              foreground=[("disabled", palette.muted)])

    style.configure("Toolbar.TButton", padding=(9, 5))
    style.configure("Link.TButton", background=palette.panel, foreground=palette.accent,
                    relief="flat", padding=(2, 1))

    style.configure("TCheckbutton", background=palette.panel, foreground=palette.text,
                    indicatorcolor=palette.field, focuscolor=palette.panel)
    style.map("TCheckbutton",
              background=[("active", palette.panel)],
              indicatorcolor=[("selected", palette.accent)])

    style.configure("TRadiobutton", background=palette.panel, foreground=palette.text,
                    indicatorcolor=palette.field, focuscolor=palette.panel)
    style.map("TRadiobutton",
              background=[("active", palette.panel)],
              indicatorcolor=[("selected", palette.accent)])

    style.configure("TEntry", fieldbackground=palette.field, foreground=palette.text,
                    bordercolor=palette.border, insertcolor=palette.text, padding=4)
    style.configure("TSpinbox", fieldbackground=palette.field, foreground=palette.text,
                    bordercolor=palette.border, arrowcolor=palette.text, padding=3)
    style.configure("TCombobox", fieldbackground=palette.field, background=palette.field,
                    foreground=palette.text, arrowcolor=palette.text, padding=3)
    style.map("TCombobox", fieldbackground=[("readonly", palette.field)],
              foreground=[("readonly", palette.text)])
    root.option_add("*TCombobox*Listbox.background", palette.field)
    root.option_add("*TCombobox*Listbox.foreground", palette.text)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", palette.accent_text)

    style.configure("TScale", background=palette.panel, troughcolor=palette.field)
    style.configure("Horizontal.TScale", background=palette.panel)

    style.configure("TNotebook", background=palette.bg, borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=palette.bg, foreground=palette.muted,
                    padding=(9, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", palette.panel)],
              foreground=[("selected", palette.text)])

    style.configure("TProgressbar", background=palette.accent, troughcolor=palette.field,
                    bordercolor=palette.border, lightcolor=palette.accent,
                    darkcolor=palette.accent)

    style.configure("Treeview", background=palette.field, fieldbackground=palette.field,
                    foreground=palette.text, bordercolor=palette.border, rowheight=22)
    style.configure("Treeview.Heading", background=palette.panel, foreground=palette.muted,
                    relief="flat")
    style.map("Treeview", background=[("selected", palette.accent)],
              foreground=[("selected", palette.accent_text)])

    style.configure("TSeparator", background=palette.border)
    style.configure("TLabelframe", background=palette.panel, foreground=palette.muted,
                    bordercolor=palette.border)
    style.configure("TLabelframe.Label", background=palette.panel, foreground=palette.muted)
    return palette
