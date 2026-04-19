# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Top-level entry point for the Sethlans tray helper.

Installs SIGINT/SIGTERM handlers and delegates to ``shared.tray.app.main``.
Kept intentionally tiny so the frozen bundle has a single, unambiguous
entry point for PyInstaller.
"""

from __future__ import annotations

import logging
import signal
import sys


def _shutdown(signum, frame):  # pragma: no cover - signal path
    del frame
    logging.getLogger(__name__).info(
        "Tray received signal %s, shutting down.", signum,
    )
    sys.exit(0)


def main() -> int:
    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):  # pragma: no cover - win
        pass
    try:
        from shared.tray import app
        app.main()
        return 0
    except Exception:  # pragma: no cover - top-level guard
        logging.getLogger(__name__).exception("Tray crashed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
