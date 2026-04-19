# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Launcher + tray shared logging bootstrap.

Alpha-default level is DEBUG.  A rotating file handler writes to
``<shared_data_dir>/logs/<component>.log`` (e.g. ``launcher.log``,
``tray.log``).  Console stream handler is retained for foreground
interactive use.

Users don't see these logs in normal operation; they live under the
per-user data dir and are read only when diagnosing issues.  Override
the level with ``SETHLANS_LOG_LEVEL=INFO|WARNING|...``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

_DEFAULT_LEVEL = os.getenv("SETHLANS_LOG_LEVEL", "DEBUG").upper()
_FORMAT = (
    "[%(asctime)s] [%(levelname)s] [%(name)s] "
    "[pid=%(process)d tid=%(thread)d] %(message)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure(
    component: str,
    data_dir: Optional[Path] = None,
    level: Optional[str] = None,
) -> None:
    """Install console + rotating-file handlers on the root logger.

    ``component`` is used as the log file base name (``launcher.log``,
    ``tray.log``).  ``data_dir`` should be the shared data dir; if
    omitted, the file handler is skipped and only the console handler
    is installed (useful for dev / unit tests).
    """
    root = logging.getLogger()
    root.setLevel(level or _DEFAULT_LEVEL)

    # Clear any existing handlers from an earlier ``basicConfig`` call
    # so we don't double-log.  Safe because this function owns the
    # root logger contract for this process.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(root.level)
    root.addHandler(console)

    if data_dir is not None:
        logs_dir = Path(data_dir) / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                logs_dir / f"{component}.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            file_handler.setLevel(root.level)
            root.addHandler(file_handler)
        except OSError as exc:
            root.warning(
                "Could not open %s log file: %s", component, exc,
            )
    root.debug(
        "Logging configured: component=%s level=%s data_dir=%s",
        component, root.level, data_dir,
    )
