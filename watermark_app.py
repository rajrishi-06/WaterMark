#!/usr/bin/env python3
"""Legacy launcher — kept so `python watermark_app.py` still starts the app.

The application now lives in the :mod:`watermark` package.  Prefer::

    watermark-gui          # after `pip install .`
    python -m watermark    # from a checkout
"""

from __future__ import annotations

import os
import sys

# Allow running straight from a source checkout without installing first.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    # watermark.ui imports Tkinter lazily, so the failure surfaces when the app
    # is launched, not when the module is imported — both must be guarded.
    try:
        from watermark.ui import main as run_app

        return run_app(sys.argv[1:])
    except ImportError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        print(f"WaterMark could not start: {exc}\n", file=sys.stderr)
        if "PIL" in missing or "Pillow" in missing:
            print("Pillow is missing. Install the dependencies with:\n"
                  "    pip install -r requirements.txt", file=sys.stderr)
        elif "tkinter" in missing:
            print("Tkinter is missing.\n"
                  "    Debian/Ubuntu:  sudo apt install python3-tk\n"
                  "    Fedora:         sudo dnf install python3-tkinter\n"
                  "    macOS/Windows:  install Python from python.org, which bundles it.",
                  file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
