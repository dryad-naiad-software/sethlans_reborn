# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Manager subprocess lifecycle for E2E tests.

Spawns Waitress + Caddy as the manager's serving path and provides a
readiness probe. Split out of ``process_manager`` so the file stays
under the project's 300-line cap.
"""

import logging
import platform
import subprocess
import sys
import time
from pathlib import Path

from tests.e2e.caddy_process import (
    start_caddy,
    stop_caddy,
    wait_for_waitress_plaintext,
    wait_for_caddy,
)
from tests.e2e.env_config import RUN_MANAGER, find_free_port
from tests.e2e.log_capture import (
    open_log_files,
    peek_log_files,
    read_log_files,
)
from tests.e2e.process_lifecycle import kill_process_tree
# ``manager/`` is on the pythonpath via pytest.ini, so the manager-side
# sentinel helper is importable from test code without sys.path tricks.
from workers.services.sentinel import create_sentinel

logger = logging.getLogger(__name__)


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

    # Mark the manager's setup wizard as complete. After GH #126 the
    # worker's main loop refuses to claim jobs (or download Blender)
    # until the heartbeat reports ``manager_setup_complete: true``,
    # which the manager only does once a sentinel JSON with a truthy
    # ``completed_at`` is on disk. The e2e harness skips the wizard,
    # so we synthesise the post-wizard sentinel here. ``manager_worker``
    # is the closest production analog to the e2e topology (manager +
    # worker on the same box); the checkpoint list mirrors the
    # state ``_infer_current_step`` treats as fully done.
    create_sentinel(
        manager_data_dir,
        topology="manager_worker",
        checkpoints=["topology_chosen", "verified"],
    )
    return proc


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
