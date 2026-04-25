# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Worker subprocess lifecycle for E2E tests.

Spawns the worker agent and provides a readiness probe (heartbeat
list). Split out of ``process_manager`` so the file stays under the
project's 300-line cap.
"""

import logging
import platform
import subprocess
import sys
import time

from tests.e2e.env_config import WORKER_ENTRY
from tests.e2e.log_capture import open_log_files

logger = logging.getLogger(__name__)


def start_worker(env):
    """
    Start the worker agent as a subprocess.

    Stdin is redirected to a closed ``PIPE`` so the first-run wizard's
    ``sys.stdin.isatty()`` check returns ``False`` and the unattended
    path is taken.  On Windows ``subprocess.DEVNULL`` opens ``NUL``
    which is a character device — ``isatty()`` returns True for it.
    A closed pipe is the only reliable way to get ``isatty() == False``
    across all platforms.

    Returns:
        subprocess.Popen: The worker process.
    """
    cmd = [sys.executable, "-u", WORKER_ENTRY, "--loglevel", "DEBUG"]
    stdout_f, stderr_f = open_log_files("worker")
    unbuffered_env = dict(env, PYTHONUNBUFFERED="1")
    popen_kwargs = {
        "env": unbuffered_env,
        "stdin": subprocess.PIPE,
        "stdout": stdout_f,
        "stderr": stderr_f,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)
    proc.stdin.close()  # Immediate EOF -> isatty() returns False
    proc._log_files = (stdout_f, stderr_f)
    logger.info("Started worker (PID %d)", proc.pid)
    return proc


def wait_for_worker(session, base_url, timeout=180):
    """
    Poll the heartbeat list until a worker appears as active.

    Returns:
        dict: The first active worker record.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = session.get(
                f"{base_url}/api/heartbeat/", timeout=5,
            )
            if resp.status_code == 200:
                workers = resp.json()
                active = [
                    w for w in workers if w.get("is_active")
                ]
                if active:
                    logger.info(
                        "Worker enrolled: %s", active[0].get("hostname"),
                    )
                    return active[0]
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(
        f"No active worker appeared within {timeout}s"
    )
