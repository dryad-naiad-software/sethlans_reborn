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
from pathlib import Path

# In frozen mode PyInstaller handles sys.path; only add the project
# root when running from source (issue #178). Mirrors
# ``wizard/run_wizard.py`` and ``launcher/run_launcher.py``.
if not getattr(sys, "frozen", False):
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


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
