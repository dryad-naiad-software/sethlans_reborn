# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Subprocess lifecycle management for E2E tests.

Provides functions to start/stop the Django manager and worker agent
as isolated subprocesses with per-test-class environments.
"""

import logging
import os
import platform
import subprocess
import sys
import time

import psutil

# Re-export env helpers so existing imports keep working.
from tests.e2e.env_config import (  # noqa: F401
    REPO_ROOT,
    MANAGE_PY,
    WORKER_ENTRY,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    ADMIN_EMAIL,
    find_free_port,
    build_manager_env,
    build_worker_env,
    generate_secrets,
)

logger = logging.getLogger(__name__)

# Timeout for process tree cleanup (matches worker graceful shutdown).
_SHUTDOWN_TIMEOUT = 30


def run_management_command(env, *args):
    """Run a Django management command and return the result."""
    cmd = [sys.executable, MANAGE_PY] + list(args)
    result = subprocess.run(
        cmd, env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error(
            "Management command %s failed:\nSTDOUT: %s\nSTDERR: %s",
            args, result.stdout, result.stderr,
        )
    return result


def setup_database(env):
    """Run migrate and createsuperuser for a fresh test database."""
    result = run_management_command(env, "migrate", "--run-syncdb")
    if result.returncode != 0:
        raise RuntimeError(
            f"migrate failed: {result.stderr}"
        )
    result = run_management_command(
        env, "createsuperuser", "--noinput",
    )
    if result.returncode != 0:
        # May fail if user already exists — that's OK.
        if "already taken" not in result.stderr.lower():
            logger.warning(
                "createsuperuser returned non-zero: %s", result.stderr
            )


def _open_log_files(prefix):
    """Open temp files for capturing subprocess stdout and stderr.

    Using files instead of PIPE avoids a deadlock on Windows where a
    full pipe buffer (4 KB) blocks the subprocess when nobody is
    reading from it.  The files are read back during teardown.

    Returns:
        tuple: (stdout_file, stderr_file) — open file objects.
    """
    import tempfile
    stdout_f = tempfile.NamedTemporaryFile(
        mode="w", prefix=f"e2e_{prefix}_out_",
        suffix=".log", delete=False,
    )
    stderr_f = tempfile.NamedTemporaryFile(
        mode="w", prefix=f"e2e_{prefix}_err_",
        suffix=".log", delete=False,
    )
    return stdout_f, stderr_f


def start_manager(env, port):
    """
    Start the Django development server as a subprocess.

    Returns:
        subprocess.Popen: The manager process.
    """
    cmd = [
        sys.executable, "-u", MANAGE_PY, "runserver",
        f"127.0.0.1:{port}", "--noreload",
    ]
    stdout_f, stderr_f = _open_log_files("manager")
    # Force unbuffered output so log files are readable in real time.
    unbuffered_env = dict(env, PYTHONUNBUFFERED="1")
    popen_kwargs = {
        "env": unbuffered_env,
        "stdout": stdout_f,
        "stderr": stderr_f,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)
    proc._log_files = (stdout_f, stderr_f)
    logger.info("Started manager (PID %d) on port %d", proc.pid, port)
    return proc


def start_worker(env):
    """
    Start the worker agent as a subprocess.

    Returns:
        subprocess.Popen: The worker process.
    """
    cmd = [sys.executable, "-u", WORKER_ENTRY, "--loglevel", "DEBUG"]
    stdout_f, stderr_f = _open_log_files("worker")
    unbuffered_env = dict(env, PYTHONUNBUFFERED="1")
    popen_kwargs = {
        "env": unbuffered_env,
        "stdout": stdout_f,
        "stderr": stderr_f,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)
    proc._log_files = (stdout_f, stderr_f)
    logger.info("Started worker (PID %d)", proc.pid)
    return proc


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


def _close_and_read(file_obj):
    """Close a temp log file, read its contents, and delete it."""
    path = file_obj.name
    try:
        file_obj.close()
    except Exception:
        pass
    try:
        with open(path, "r", errors="replace") as fh:
            content = fh.read()
    except Exception:
        content = ""
    try:
        os.remove(path)
    except OSError:
        pass
    return content


def _read_log_files(proc):
    """Read and clean up the temp log files attached to a process."""
    log_files = getattr(proc, '_log_files', None)
    if not log_files:
        return "", ""
    stdout_f, stderr_f = log_files
    return _close_and_read(stdout_f), _close_and_read(stderr_f)


def _peek_log_files(proc):
    """Read log files without closing them (safe while process runs)."""
    log_files = getattr(proc, '_log_files', None)
    if not log_files:
        return "", ""
    stdout_f, stderr_f = log_files
    contents = []
    for f in (stdout_f, stderr_f):
        try:
            # Flush parent's write buffer, then read from disk.
            f.flush()
            with open(f.name, "r", errors="replace") as fh:
                contents.append(fh.read())
        except Exception:
            contents.append("")
    return contents[0], contents[1]


def kill_process_tree(proc):
    """
    Kill a process and all its children using psutil.

    Sends SIGTERM first, waits up to _SHUTDOWN_TIMEOUT seconds,
    then SIGKILL any survivors.

    Returns:
        tuple: (stdout, stderr) captured from the process.
    """
    if proc is None or proc.poll() is not None:
        if proc is not None:
            return _read_log_files(proc)
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
    return _read_log_files(proc)


def wait_for_manager(base_url, timeout=60, proc=None):
    """Poll the manager until it responds to HTTP requests.

    Args:
        base_url: The manager's HTTP base URL.
        timeout: Maximum seconds to wait.
        proc: The manager subprocess.Popen — if supplied, we check
              whether the process has crashed on each poll iteration
              and surface its stderr/stdout in the error message.
    """
    import requests
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Fail fast if the manager process has exited.
        if proc is not None and proc.poll() is not None:
            stdout, stderr = _read_log_files(proc)
            raise RuntimeError(
                f"Manager process (PID {proc.pid}) exited with "
                f"code {proc.returncode} before becoming ready.\n"
                f"--- STDOUT ---\n{stdout[-2000:]}\n"
                f"--- STDERR ---\n{stderr[-2000:]}"
            )
        try:
            resp = requests.get(
                f"{base_url}/api/auth/csrf/", timeout=3,
            )
            if resp.status_code == 200:
                logger.info("Manager is ready at %s", base_url)
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)

    # Timeout reached — collect whatever output we can.
    detail = ""
    if proc is not None:
        alive = proc.poll() is None
        stdout, stderr = _peek_log_files(proc)
        detail = (
            f"\nProcess alive: {alive}, returncode: {proc.returncode}"
            f"\n--- STDOUT (last 2000 chars) ---\n{stdout[-2000:]}"
            f"\n--- STDERR (last 2000 chars) ---\n{stderr[-2000:]}"
        )
    raise TimeoutError(
        f"Manager at {base_url} did not become ready "
        f"within {timeout}s{detail}"
    )


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
