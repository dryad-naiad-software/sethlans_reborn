# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Restart-request orchestration for the launcher.

Watches ``<data_dir>/.restart_requested`` while the manager subprocess
is alive.  When the sentinel appears, the launcher:

1. Deletes ``.restart_requested`` FIRST (prevents restart loop on
   spurious respawn — C4 / C10).
2. Sends ``SIGTERM`` to the manager subprocess.
3. Waits up to 15 seconds for graceful shutdown.
4. If still alive, escalates to ``SIGKILL``.
5. Removes the ``[setup]`` section from ``manager.ini`` (atomic).
6. Purges ``setup_phase=True`` Django sessions (FR-18a / S6).
7. Respawns the manager via the caller-supplied ``respawn`` callable.

Contract with manager (documented, not enforced here): the post-restart
manager MUST also delete ``.restart_requested`` on boot
(belt-and-suspenders, FR-14a / C10).  That is backend's responsibility.

Stdlib only.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from launcher.session_purge import purge_setup_phase_sessions
from launcher.setup_helpers import remove_setup_section

logger = logging.getLogger(__name__)

RESTART_MARKER = ".restart_requested"
TERMINATE_TIMEOUT_SECONDS = 15.0
DEFAULT_POLL_INTERVAL = 2.0


def poll_for_restart_request(
    data_dir: Path, interval: float = DEFAULT_POLL_INTERVAL,
) -> bool:
    """Single-shot check for the restart sentinel.

    Returns ``True`` if ``<data_dir>/.restart_requested`` exists.  The
    ``interval`` argument is retained for API compatibility with the
    caller (who controls sleep cadence) — it is not used here.
    """
    del interval  # caller controls loop cadence
    return (data_dir / RESTART_MARKER).exists()


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
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.error("Manager failed to die after SIGKILL")


def handle_restart_request(
    proc: subprocess.Popen,
    data_dir: Path,
    respawn: Callable[[], subprocess.Popen],
) -> Optional[subprocess.Popen]:
    """Run the full restart sequence; return the new manager proc.

    Steps are ordered per spec §6 technical approach.  ``respawn``
    must be idempotent and return a running ``Popen`` for the new
    manager instance.
    """
    # Step 1: delete the marker FIRST (prevents restart loop).
    _delete_marker(data_dir)

    # Step 2-4: terminate / wait / kill.
    _terminate_and_wait(proc)

    # Step 5: atomically drop the [setup] section.
    manager_data = data_dir / "manager"
    try:
        remove_setup_section(manager_data)
    except OSError as exc:
        logger.warning(
            "Could not remove [setup] from manager.ini: %s", exc,
        )

    # Step 6: purge setup-phase sessions.
    try:
        purged = purge_setup_phase_sessions(data_dir)
        if purged:
            logger.info(
                "Purged %d setup-phase session(s)", purged,
            )
    except Exception as exc:
        logger.warning("Session purge raised: %s", exc)

    # Step 7: respawn.
    try:
        new_proc = respawn()
    except Exception as exc:
        logger.error("Failed to respawn manager: %s", exc)
        return None
    return new_proc


def watch_and_restart(
    proc: subprocess.Popen,
    data_dir: Path,
    respawn: Callable[[], subprocess.Popen],
    interval: float = DEFAULT_POLL_INTERVAL,
) -> int:
    """Watch loop: poll marker, restart on sentinel, exit on proc death.

    Returns the final exit code of the last manager process (or 0).
    """
    current = proc
    while True:
        if current.poll() is not None:
            return current.returncode or 0
        if poll_for_restart_request(data_dir):
            logger.info("Restart requested; orchestrating handoff")
            new_proc = handle_restart_request(
                current, data_dir, respawn,
            )
            if new_proc is None:
                return 1
            current = new_proc
            continue
        time.sleep(interval)
