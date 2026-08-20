"""WaterMark — a batch image watermarking and compression toolkit.

The package is split into two halves:

* :mod:`watermark.core` — pure image logic with no GUI dependency.  It can be
  imported on a headless machine and is what the CLI and the test-suite drive.
* :mod:`watermark.ui` — the Tkinter desktop application built on top of it.

Importing this module never pulls in Tkinter.
"""

__all__ = ["__version__", "APP_NAME"]

__version__ = "2.1.0"
APP_NAME = "WaterMark"
