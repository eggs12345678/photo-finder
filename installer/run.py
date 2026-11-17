"""Launcher for the installed Photo Finder app.

The installer puts this next to ``photofinder/`` and starts it with the bundled
``pythonw.exe``. It points the library at ``%LOCALAPPDATA%\\PhotoFinder\\data``
so the index survives upgrades and uninstalls.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def configure() -> None:
    if not os.environ.get("PHOTOFINDER_DATA"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        os.environ["PHOTOFINDER_DATA"] = str(Path(base) / "PhotoFinder" / "data")


def main() -> int:
    configure()
    if sys.stdout is None or sys.stderr is None:
        # pythonw launches with no console streams; keep library code quiet.
        devnull = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or devnull
        sys.stderr = sys.stderr or devnull
    from photofinder.cli import main as cli_main

    return int(cli_main())


if __name__ == "__main__":
    raise SystemExit(main())
