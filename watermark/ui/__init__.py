"""Tkinter user interface.

Importing this package pulls in Tkinter; :mod:`watermark.core` never does.
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:
    """Launch the desktop application."""
    from .app import main as _main

    return _main(argv)
