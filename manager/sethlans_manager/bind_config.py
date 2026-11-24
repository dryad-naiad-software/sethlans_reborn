# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The manager's public host and port: env var > settings file > default.

One resolver for everything that needs the manager's own address: the
start (``run_manager.get_manager_bind``), the local worker's
auto-enrollment URL and the admin summary (issue #313). Django-free.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from shared.settings_file import read_settings

HOST_ENV = "SETHLANS_MANAGER_HOST"
PORT_ENV = "SETHLANS_MANAGER_PORT"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8080"


def get_manager_bind(ini_path: Optional[Path] = None) -> Tuple[str, str]:
    """Return ``(host, port)`` as strings.

    1. ``SETHLANS_MANAGER_HOST`` / ``SETHLANS_MANAGER_PORT`` (an empty
       value counts as unset);
    2. ``[server] host`` / ``port`` of the settings file (*ini_path*,
       default :func:`shared.settings_file.settings_path`);
    3. ``0.0.0.0`` / ``8080``.

    An unreadable settings file prints a warning and falls through to the
    defaults, as the start always did.
    """
    host = os.getenv(HOST_ENV)
    port = os.getenv(PORT_ENV)
    if not host or not port:
        try:
            config = read_settings(ini_path)
            if not host:
                host = config.get("server", "host", fallback=None)
            if not port:
                port = config.get("server", "port", fallback=None)
        except Exception as exc:  # noqa: BLE001 - never block the start on a bad file
            print(
                f"[WARNING] Could not read the settings file: {exc}",
                file=sys.stderr,
            )
    return (host or DEFAULT_HOST, port or DEFAULT_PORT)
