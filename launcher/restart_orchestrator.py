# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The tray's Restart for the manager, as the running loop does it.

:func:`handle_restart_request`:

1. deletes ``.restart_requested`` first (no restart loop on a spurious
   respawn);
2. sends ``SIGTERM`` to the manager;
3. waits up to 15 seconds for it to exit;
4. escalates to ``SIGKILL`` and waits again;
5. starts the new manager through the caller's ``respawn``, but only
   once the old one has exited: on Windows the new manager's
   ``manager-start.log`` cannot be replaced while the old one still
   holds it (``launcher.components.open_owner_only_log``).

It returns the new manager, or ``None`` with the reason logged when the
old one would not exit or the new one could not start.

Stdlib only.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

RESTART_MARKER = ".restart_requested"
TERMINATE_TIMEOUT_SECONDS = 15.0
KILL_WAIT_SECONDS = 5.0


def _delete_marker(data_dir: Path) -> None:
    """Delete ``.restart_requested`` if present (idempotent)."""
    marker = data_dir / RESTART_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not delete %s: %s", marker, exc)


def _terminate_and_wait(
    proc: subprocess.Popen,
    timeout: float = TERMINATE_TIMEOUT_SECONDS,
) -> None:
    """``terminate()``; escalate to ``kill()`` after ``timeout``."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError as exc:
        logger.warning("terminate() failed: %s", exc)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Manager did not exit within %ss; sending SIGKILL",
            timeout,
        )
        try:
            proc.kill()
        except OSError as exc:
            logger.warning("kill() failed: %s", exc)
        try:
            proc.wait(timeout=KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.error("Manager failed to die after SIGKILL")


def handle_restart_request(
    proc: subprocess.Popen,
    data_dir: Path,
    respawn: Callable[[], subprocess.Popen],
) -> Optional[subprocess.Popen]:
    """Run the restart sequence (module doc); return the new manager or ``None``."""
    _delete_marker(data_dir)
    _terminate_and_wait(proc)
    if proc.poll() is None:
        logger.error(
            "The manager (pid %s) did not exit; not starting a new one beside it", proc.pid,
        )
        return None
    try:
        return respawn()
    except PermissionError as exc:
        logger.error(
            "Could not start the manager: its start log is still held by another process (%s)", exc,
        )
    except Exception as exc:
        logger.error("Failed to respawn manager: %s", exc)
    return None
