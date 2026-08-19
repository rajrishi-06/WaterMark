"""``python -m watermark`` — launches the GUI, or the CLI when given a command."""

from __future__ import annotations

import sys

_CLI_COMMANDS = {"apply", "batch", "compress", "info", "presets"}


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in _CLI_COMMANDS:
        from .cli import main as cli_main

        return cli_main(argv)
    from .ui import main as ui_main

    return ui_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
