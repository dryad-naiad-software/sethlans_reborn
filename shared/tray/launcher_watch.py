# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Parent-launcher liveness watchdog (tray spec FR-19c).

If the launcher that spawned the tray disappears (clean exit OR
SIGKILL), the tray self-exits so it does not leak as an orphan.
PID-reuse is guarded against by pinning the process ``create_time``
recorded at startup.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover - optional at dev time
    psutil = None  # type: ignore[assignment]

_ENV_VAR = "SETHLANS_LAUNCHER_PID"

_launcher_pid: int = 0
_launcher_create_time: float | None = None
_initialized = False


def init() -> None:
    """Record the launcher PID + create_time at tray startup.

    Called once from ``shared.tray.app.main``.  A missing env var
    means the tray is running standalone (dev mode) and should never
    self-terminate.
    """
    global _launcher_pid, _launcher_create_time, _initialized
    _initialized = True
    raw = os.environ.get(_ENV_VAR, "0")
    try:
        _launcher_pid = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; ignoring", _ENV_VAR, raw)
        _launcher_pid = 0
    if not _launcher_pid or psutil is None:
        return
    try:
        if psutil.pid_exists(_launcher_pid):
            _launcher_create_time = psutil.Process(
                _launcher_pid,
            ).create_time()
    except (psutil.Error, OSError) as exc:
        logger.warning(
            "Could not snapshot launcher create_time: %s", exc,
        )


def is_launcher_alive() -> bool:
    """Return True if the launcher process still exists.

    When the env var is absent or psutil is unavailable this returns
    True (dev mode) so the tray does not self-terminate.
    """
    if not _initialized:
        init()
    if not _launcher_pid or psutil is None:
        return True
    if not psutil.pid_exists(_launcher_pid):
        return False
    try:
        proc = psutil.Process(_launcher_pid)
        if _launcher_create_time is None:
            return True
        return proc.create_time() == _launcher_create_time
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True


def launcher_pid() -> int:
    """Return the launcher PID recorded at init (0 if unknown)."""
    if not _initialized:
        init()
    return _launcher_pid
