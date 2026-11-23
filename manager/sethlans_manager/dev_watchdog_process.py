# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Child process helpers for the dev watchdog (issue #293).

The dev child is ``run_manager.py`` without ``--dev``, which itself
spawns ``manage.py migrate`` and ``manage.py collectstatic``
subprocesses while it boots. A restart or a stop must reach that whole
tree, not only the direct child, or a half-finished ``migrate`` can
outlive the child and race the next one.

* **Windows**: the child starts in its own process group
  (``CREATE_NEW_PROCESS_GROUP``). ``CTRL_BREAK_EVENT`` sent to that
  group reaches the child and every grandchild in it, and the parent's
  own console Ctrl+C does not reach the child directly.
* **POSIX**: the child starts in its own session
  (``start_new_session=True``), so its pid is the process-group id and
  ``SIGTERM`` / ``SIGKILL`` go to the whole group with ``os.killpg``.
* **Kill fallback**: when the graceful signal does not stop the child
  in time, the group (POSIX) and every descendant (``psutil``) are
  killed, then the child itself.

Also holds the child command/env builders and the parent's signal
registration, split from ``dev_watchdog.py`` for the 300-line ceiling.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import psutil

logger = logging.getLogger(__name__)

# Signals that stop the parent (and, through it, the child tree).
# Missing names are skipped: SIGBREAK is Windows-only, SIGHUP POSIX-only.
PARENT_STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP")


def strip_dev_flag(argv: Sequence[str]) -> List[str]:
    """Return ``argv`` with the first ``--dev`` occurrence removed.

    The child is respawned via the production path; keeping ``--dev``
    in its argv would cause infinite recursion.
    """
    out: List[str] = []
    dropped = False
    for token in argv:
        if token == "--dev" and not dropped:
            dropped = True
            continue
        out.append(token)
    return out


def build_child_env(parent_env: Optional[dict] = None) -> dict:
    """Build the environment for the child process.

    Copies the parent env, preserves ``SETHLANS_DEV_MODE=1``, and
    strips ``SETHLANS_DEV_IS_PARENT`` so the child takes the
    production branch in ``run_manager.main()``.
    """
    src = dict(os.environ if parent_env is None else parent_env)
    src["SETHLANS_DEV_MODE"] = "1"
    src.pop("SETHLANS_DEV_IS_PARENT", None)
    return src


def build_child_command(
    server_script: Path, child_argv: Sequence[str],
) -> List[str]:
    """Return the command line for the dev child.

    ``server_script`` is ``run_manager.py``; ``child_argv`` already has
    ``--dev`` stripped. Running ``manage.py`` here instead would print
    Django's help and exit (issue #293).
    """
    return [sys.executable, str(server_script), *child_argv]


def popen_group_kwargs() -> Dict[str, object]:
    """Return the ``Popen`` keyword arguments that isolate the child.

    Windows gets ``CREATE_NEW_PROCESS_GROUP``; POSIX gets a new session
    so the child's pid is the process-group id for ``os.killpg``.
    """
    if sys.platform == "win32":
        return {
            "creationflags": getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
            ),
        }
    return {"start_new_session": True}


def send_stop_signal(proc: subprocess.Popen) -> None:
    """Ask the child's whole process group to stop.

    No-op when the child has already exited.
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError) as exc:
        logger.warning("dev watchdog: stop signal to child failed: %s", exc)


def _list_descendants(pid: int) -> list:
    """Return the ``psutil`` descendants of ``pid`` (empty if gone)."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


def _killpg_quietly(pgid: int, signum: int) -> None:
    """Send ``signum`` to process group ``pgid``; ignore a gone group."""
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, OSError):
        pass


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the child's group and every descendant, then the child.

    Descendants are listed first: once the child dies they are
    re-parented and no longer show up as its children.
    """
    descendants = _list_descendants(proc.pid)
    if sys.platform != "win32":
        _killpg_quietly(proc.pid, signal.SIGKILL)
    for child in descendants:
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except OSError as exc:
        logger.warning("dev watchdog: kill of child failed: %s", exc)


def install_parent_signal_handlers(shutdown_event, supervisor) -> None:
    """Forward the parent's stop signals to the child, trip shutdown.

    ``SIGBREAK`` (Windows Ctrl+Break, or ``CTRL_BREAK_EVENT`` from a
    supervising process) and ``SIGHUP`` (POSIX terminal closed) are
    handled too: the child runs in its own process group or session, so
    without this the parent would die without its cleanup and leave the
    child running.
    """

    def _handler(signum, _frame):  # pragma: no cover - signal path
        logger.info("dev watchdog: parent received signal %s", signum)
        shutdown_event.set()
        supervisor.stop()

    for name in PARENT_STOP_SIGNALS:
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _handler)
        except (ValueError, OSError):  # pragma: no cover
            pass
