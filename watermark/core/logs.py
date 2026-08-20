"""Application logging.

The original app reported every failure with ``print()``, which is invisible
when the GUI is launched from a desktop icon.  Here everything lands in a
rotating file the user can attach to a bug report.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .paths import ensure_dir, log_dir

_CONFIGURED = False
LOGGER_NAME = "watermark"


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def configure(verbose: bool = False, to_file: bool = True) -> logging.Logger:
    """Install handlers once; repeated calls only adjust the level."""
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    if _CONFIGURED:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    logger.addHandler(console)

    if to_file:
        try:
            path = os.path.join(ensure_dir(log_dir()), "watermark.log")
            handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.setLevel(logging.DEBUG)
            logger.addHandler(handler)
        except OSError:
            # A read-only home directory must not stop the app from starting.
            logger.debug("File logging unavailable", exc_info=True)

    logger.propagate = False
    _CONFIGURED = True
    return logger
