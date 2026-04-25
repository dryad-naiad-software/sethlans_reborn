# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Process lifecycle helpers for E2E tests.

Shutdown of subprocess trees spawned by :mod:`tests.e2e.manager_process`
and :mod:`tests.e2e.worker_process`. Split out of ``process_manager`` so
the spawn-side modules and the teardown-side helpers can each stay
under the project's 300-line cap.
"""

import logging

import psutil

from tests.e2e.caddy_process import stop_caddy
from tests.e2e.log_capture import read_log_files

logger = logging.getLogger(__name__)

# Timeout for process tree cleanup (matches worker graceful shutdown).
_SHUTDOWN_TIMEOUT = 30


def _terminate_process_list(processes):
    """Send SIGTERM to a list of psutil.Process objects."""
    for p in processes:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass


def _kill_survivors(alive, pid):
    """Force-kill processes that survived SIGTERM."""
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    if alive:
        logger.warning(
            "Force-killed %d process(es) in tree of PID %d",
            len(alive), pid,
        )


def kill_process_tree(proc):
    """
    Kill a process and all its children using psutil.

    Sends SIGTERM first, waits up to _SHUTDOWN_TIMEOUT seconds,
    then SIGKILL any survivors.

    If the process was started by :func:`start_manager`, any Caddy
    subprocess attached as ``proc._caddy_proc`` is terminated first so
    the TLS front door closes before Waitress drains.

    Returns:
        tuple: (stdout, stderr) captured from the process.
    """
    caddy_proc = getattr(proc, "_caddy_proc", None) if proc else None
    if caddy_proc is not None:
        stop_caddy(caddy_proc)
        # Read and discard Caddy's log files so the tempfiles are
        # cleaned up; tests never consume Caddy's logs directly.
        read_log_files(caddy_proc)

    if proc is None or proc.poll() is not None:
        if proc is not None:
            return read_log_files(proc)
        return "", ""

    pid = proc.pid
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        _terminate_process_list(children + [parent])

        # Wait for graceful shutdown.
        _gone, alive = psutil.wait_procs(
            children + [parent], timeout=_SHUTDOWN_TIMEOUT,
        )
        _kill_survivors(alive, pid)
    except psutil.NoSuchProcess:
        logger.debug("Process %d already exited.", pid)

    proc.wait(timeout=5)
    return read_log_files(proc)
