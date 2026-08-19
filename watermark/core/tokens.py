"""Token substitution for watermark text and output filenames.

Everyday users want ``© 2026 Studio`` or ``IMG_0042_wm.jpg`` without editing a
string per file, so both watermark text and batch filename patterns accept the
same small set of ``{token}`` placeholders.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Dict, Optional, Tuple

#: Human-readable catalogue, shown in the UI's token help popover.
TOKEN_HELP: Dict[str, str] = {
    "{filename}": "Source file name with extension (photo.jpg)",
    "{name}": "Source file name without extension (photo)",
    "{ext}": "Source extension including the dot (.jpg)",
    "{width}": "Image width in pixels after transforms",
    "{height}": "Image height in pixels after transforms",
    "{date}": "Today's date (2026-08-19)",
    "{time}": "Current time (14-05-32)",
    "{datetime}": "Date and time (2026-08-19_14-05-32)",
    "{year}": "Current year (2026)",
    "{index}": "1-based position in a batch run",
    "{count}": "Total number of files in a batch run",
}

_TOKEN_RE = re.compile(r"\{([a-z_]+)\}")


def build_context(
    source_path: Optional[str] = None,
    size: Optional[Tuple[int, int]] = None,
    index: int = 1,
    count: int = 1,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, str]:
    """Assemble the substitution table for one image."""
    now = now or _dt.datetime.now()
    base = os.path.basename(source_path) if source_path else ""
    stem, ext = os.path.splitext(base)
    width, height = size or (0, 0)
    return {
        "filename": base,
        "name": stem,
        "ext": ext,
        "width": str(width),
        "height": str(height),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H-%M-%S"),
        "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
        "year": now.strftime("%Y"),
        "index": str(index),
        "count": str(count),
    }


def expand(template: str, context: Dict[str, str]) -> str:
    """Replace every known ``{token}``; unknown ones are left verbatim.

    Leaving unknown tokens alone means a watermark reading ``{Confidential}``
    survives untouched instead of raising or vanishing.
    """
    if not template:
        return ""
    return _TOKEN_RE.sub(lambda m: context.get(m.group(1), m.group(0)), template)
