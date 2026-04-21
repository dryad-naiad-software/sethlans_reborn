# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Subprocess lifecycle management for E2E tests.

Provides functions to start/stop the Django manager and worker agent
as isolated subprocesses with per-test-class environments.
"""

import logging
import platform
import subprocess
import sys
import time
from pathlib import Path

import psutil
import urllib3

# Re-export env helpers so existing imports keep working.
from tests.e2e.env_config import (  # noqa: F401
    REPO_ROOT,
    MANAGE_PY,
    RUN_MANAGER,
    WORKER_ENTRY,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    ADMIN_EMAIL,
    find_free_port,
    build_manager_env,
    build_worker_env,
    generate_secrets,
)
from tests.e2e.caddy_process import (
    start_caddy,
    stop_caddy,
    wait_for_waitress_plaintext,
    wait_for_caddy,
)
from tests.e2e.log_capture import (
    open_log_files,
    read_log_files,
    peek_log_files,
)

# Suppress InsecureRequestWarning globally for E2E tests since the
# manager serves a self-signed TLS certificate.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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


def _resolve_manager_data_dir(env):
    """Return ``(manager_data_dir, cert_path, key_path)`` for Caddy.

    Post-Waitress-migration the test harness must pass Caddy the same
    TLS cert Waitress uses. The cert lives at
    ``<SETHLANS_TLS_DATA_DIR>/cert.pem`` when that env var is set (the
    pattern the e2e harness uses for isolation). ``manager_data_dir``
    is the parent of that directory — the Caddyfile template validates
    that cert/key paths resolve inside it.
    """
    tls_data_dir = env.get("SETHLANS_TLS_DATA_DIR")
    if not tls_data_dir:
        raise RuntimeError(
            "Caddy+Waitress e2e harness requires "
            "SETHLANS_TLS_DATA_DIR to be set in the manager env dict "
            "(call build_manager_env with tls_data_dir=...). Without "
            "an explicit per-test TLS directory, the manager writes "
            "to manager/tls/ which Caddy cannot reach under its "
            "manager_data_dir containment check."
        )
    tls_dir = Path(tls_data_dir)
    tls_dir.mkdir(parents=True, exist_ok=True)
    manager_data_dir = tls_dir.parent
    manager_data_dir.mkdir(parents=True, exist_ok=True)
    return manager_data_dir, tls_dir / "cert.pem", tls_dir / "key.pem"


def start_manager(env, port):
    """Start Waitress + Caddy as the manager's serving path.

    ``port`` is the **public TLS port** — the address Caddy listens on
    and workers/tests connect to. Two additional loopback plaintext
    ports are allocated by this function for Waitress' public-origin
    and internal-origin listeners; a third loopback port is allocated
    for Caddy's plaintext tray-helper vhost. Those ports are injected
    into the manager subprocess via
    ``SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC`` /
    ``SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL``.

    Startup sequence:
      1. Spawn ``run_manager.py`` (Waitress).
      2. Wait for Waitress' public plaintext listener — this also
         confirms the self-signed TLS cert has been written to disk.
      3. Spawn Caddy pointed at the same cert/key.
      4. Wait for Caddy's HTTPS front to respond.

    Returns the manager ``subprocess.Popen``; the Caddy ``Popen`` is
    attached as ``proc._caddy_proc`` so ``kill_process_tree`` (below)
    can terminate it on teardown.
    """
    manager_data_dir, cert_path, key_path = _resolve_manager_data_dir(env)

    waitress_public_port = find_free_port()
    waitress_internal_port = find_free_port()
    loopback_plaintext_port = find_free_port()
    # Ensure all four ports are distinct — find_free_port() calls are
    # serialised, so clashes are vanishingly rare, but defend in depth.
    allocated = {
        port,
        waitress_public_port,
        waitress_internal_port,
        loopback_plaintext_port,
    }
    while len(allocated) != 4:
        waitress_public_port = find_free_port()
        waitress_internal_port = find_free_port()
        loopback_plaintext_port = find_free_port()
        allocated = {
            port,
            waitress_public_port,
            waitress_internal_port,
            loopback_plaintext_port,
        }

    # Augment the caller's env dict with the freshly allocated Waitress
    # ports. Kept here (rather than in build_manager_env) so env_config
    # stays dependency-free and the port allocation happens at the
    # moment of spawn.
    augmented = dict(
        env,
        PYTHONUNBUFFERED="1",
        SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC=str(waitress_public_port),
        SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL=str(
            waitress_internal_port,
        ),
    )

    cmd = [sys.executable, "-u", RUN_MANAGER]
    stdout_f, stderr_f = open_log_files("manager")
    popen_kwargs = {
        "env": augmented,
        "stdout": stdout_f,
        "stderr": stderr_f,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)
    proc._log_files = (stdout_f, stderr_f)
    proc._caddy_proc = None
    proc._waitress_public_port = waitress_public_port
    proc._waitress_internal_port = waitress_internal_port
    proc._loopback_plaintext_port = loopback_plaintext_port
    logger.info(
        "Started manager (PID %d): public TLS=%d, Waitress "
        "public=%d, Waitress internal=%d, Caddy loopback=%d",
        proc.pid, port, waitress_public_port,
        waitress_internal_port, loopback_plaintext_port,
    )

    # Block until Waitress is listening — also confirms the TLS cert
    # has been generated inside manager_data_dir so Caddy can read it.
    wait_for_waitress_plaintext(waitress_public_port, proc)

    try:
        caddy_proc = start_caddy(
            manager_data_dir=manager_data_dir,
            public_tls_port=port,
            waitress_public_port=waitress_public_port,
            waitress_internal_port=waitress_internal_port,
            loopback_plaintext_port=loopback_plaintext_port,
            cert_path=cert_path,
            key_path=key_path,
        )
    except Exception:
        # Waitress is already running — tear it down so the test
        # doesn't leak a subprocess on Caddy-side failures.
        kill_process_tree(proc)
        raise
    try:
        wait_for_caddy(port, caddy_proc)
    except Exception:
        stop_caddy(caddy_proc)
        kill_process_tree(proc)
        raise
    proc._caddy_proc = caddy_proc
    return proc


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
    proc.stdin.close()  # Immediate EOF → isatty() returns False
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


def wait_for_manager(base_url, timeout=90, proc=None):
    """Poll the manager until it responds to HTTPS requests.

    Args:
        base_url: The manager's HTTPS base URL.
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
            stdout, stderr = read_log_files(proc)
            raise RuntimeError(
                f"Manager process (PID {proc.pid}) exited with "
                f"code {proc.returncode} before becoming ready.\n"
                f"--- STDOUT ---\n{stdout[-2000:]}\n"
                f"--- STDERR ---\n{stderr[-2000:]}"
            )
        try:
            resp = requests.get(
                f"{base_url}/api/auth/csrf/", timeout=3,
                verify=False,
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
        stdout, stderr = peek_log_files(proc)
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
