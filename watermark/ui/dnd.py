"""Optional drag-and-drop support.

``tkinterdnd2`` is a native extension that is awkward to install and missing on
plenty of systems.  The original app made it a hard import, so a failed install
meant no app at all.  Here it is strictly an enhancement: without it every
feature still works through the file dialogs.
"""

from __future__ import annotations

import re
import tkinter as tk
from typing import Callable, List, Optional

try:  # pragma: no cover - depends on the local install
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    AVAILABLE = True
except Exception:  # pragma: no cover
    DND_FILES = None  # type: ignore[assignment]
    TkinterDnD = None  # type: ignore[assignment]
    AVAILABLE = False

#: Tk hands over multiple paths as ``{/a/b c.png} /d/e.png``.
_BRACED = re.compile(r"\{([^}]*)\}|(\S+)")


def create_root() -> tk.Tk:
    """A root window with drag-and-drop when available, plain Tk otherwise."""
    if AVAILABLE:
        try:
            return TkinterDnD.Tk()  # type: ignore[no-any-return]
        except Exception:  # pragma: no cover - broken tkdnd install
            pass
    return tk.Tk()


def parse_drop_paths(data: str) -> List[str]:
    """Split a Tk drop payload into individual file paths."""
    paths: List[str] = []
    for braced, bare in _BRACED.findall(data or ""):
        candidate = (braced or bare).strip()
        if candidate:
            paths.append(candidate)
    return paths


def register(widget: tk.Misc, on_drop: Callable[[List[str]], None]) -> bool:
    """Make ``widget`` accept dropped files.  Returns whether it took effect."""
    if not AVAILABLE:
        return False
    try:
        widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
        widget.dnd_bind(  # type: ignore[attr-defined]
            "<<Drop>>", lambda event: on_drop(parse_drop_paths(event.data))
        )
        return True
    except Exception:  # pragma: no cover - tkdnd present but unusable
        return False


def status_note() -> Optional[str]:
    """A hint for the UI when drag-and-drop is unavailable."""
    if AVAILABLE:
        return None
    return "Drag & drop is off (install tkinterdnd2 to enable it) — use Open instead."
