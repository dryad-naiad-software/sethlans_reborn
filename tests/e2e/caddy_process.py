# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Caddy subprocess lifecycle for E2E tests.

Post-Waitress-migration, the production topology is Caddy (TLS front)
-> plaintext Waitress (two loopback listeners). The e2e harness used
to target uvicorn's HTTPS listener directly; this module replaces that
path by starting Caddy in front of Waitress so the tests exercise the
same topology operators ship.

The module uses ``subprocess.Popen`` directly (not the production
``shared.caddy_supervisor.CaddySupervisor``) so that log capture, temp
file handling, and cleanup match the pattern used by
``tests.e2e.process_manager`` — supervision/watchdog concerns are
out-of-scope for tests.
"""

import logging
import platform
import subprocess
import time
from pathlib import Path

import requests
import urllib3

from sethlans_manager.caddy_template import render_manager_caddyfile
from tests.e2e.env_config import REPO_ROOT
from tests.e2e.log_capture import (
    open_log_files,
    peek_log_files,
)

# Silence the self-signed cert warning for the Caddy HTTPS probe.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Location where ``tools/fetch_caddy.py`` drops the Caddy binary.
_CADDY_DIR = REPO_ROOT / ".venv-build" / "caddy"
_CADDY_BINARY_NAMES = ("caddy.exe", "caddy")


class CaddyBinaryMissingError(RuntimeError):
    """Raised when the Caddy binary is absent from .venv-build/caddy/."""


def _find_caddy_binary() -> Path:
    """Return the path to the Caddy binary, or raise a clear error."""
    for name in _CADDY_BINARY_NAMES:
        candidate = _CADDY_DIR / name
        if candidate.is_file():
            return candidate
    raise CaddyBinaryMissingError(
        f"Caddy binary not found under {_CADDY_DIR}. Run:\n"
        f"    python tools/fetch_caddy.py "
        f"--target-dir .venv-build/caddy\n"
        f"before invoking the e2e suite."
    )


def _write_caddyfile(
    caddyfile_path: Path,
    *,
    public_tls_port: int,
    waitress_public_port: int,
    waitress_internal_port: int,
    loopback_plaintext_port: int,
    cert_path: Path,
    key_path: Path,
    manager_data_dir: Path,
) -> None:
    """Render and atomically write a manager Caddyfile for tests.

    Reuses the production ``render_manager_caddyfile`` so the e2e
    harness exercises the exact template operators get in the field.
    """
    content = render_manager_caddyfile(
        public_tls_port=public_tls_port,
        loopback_plaintext_port=loopback_plaintext_port,
        cert_path=cert_path,
        key_path=key_path,
        manager_data_dir=manager_data_dir,
        waitress_public_port=waitress_public_port,
        waitress_internal_port=waitress_internal_port,
    )
    caddyfile_path.parent.mkdir(parents=True, exist_ok=True)
    caddyfile_path.write_text(content, encoding="utf-8", newline="\n")


def start_caddy(
    *,
    manager_data_dir: Path,
    public_tls_port: int,
    waitress_public_port: int,
    waitress_internal_port: int,
    loopback_plaintext_port: int,
    cert_path: Path,
    key_path: Path,
) -> subprocess.Popen:
    """Start Caddy as a test subprocess and return the Popen handle.

    The caller is responsible for calling :func:`stop_caddy` (or
    ``kill_process_tree`` via ``process_manager``) during teardown.

    Raises:
        CaddyBinaryMissingError: ``.venv-build/caddy/caddy[.exe]``
            is absent.
        ValueError: any port/path argument fails the production
            template's validation.
    """
    binary = _find_caddy_binary()
    caddyfile_path = Path(manager_data_dir) / "caddy" / "Caddyfile"
    _write_caddyfile(
        caddyfile_path,
        public_tls_port=public_tls_port,
        waitress_public_port=waitress_public_port,
        waitress_internal_port=waitress_internal_port,
        loopback_plaintext_port=loopback_plaintext_port,
        cert_path=cert_path,
        key_path=key_path,
        manager_data_dir=manager_data_dir,
    )

    argv = [
        str(binary),
        "run",
        "--config", str(caddyfile_path),
        "--adapter", "caddyfile",
    ]
    stdout_f, stderr_f = open_log_files("caddy")
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_f,
        "stderr": stderr_f,
        "close_fds": True,
    }
    if platform.system() == "Windows":
        # CREATE_NEW_PROCESS_GROUP keeps Caddy out of the parent's
        # Ctrl-C handler so kill_process_tree can terminate cleanly.
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0,
        )
    else:
        popen_kwargs["start_new_session"] = True

    logger.info("Starting Caddy: %s", " ".join(argv))
    proc = subprocess.Popen(argv, **popen_kwargs)  # nosec B603 - list-form
    proc._log_files = (stdout_f, stderr_f)
    proc._caddyfile_path = caddyfile_path
    logger.info(
        "Started Caddy (PID %d) — public TLS %d -> Waitress %d",
        proc.pid, public_tls_port, waitress_public_port,
    )
    return proc


def wait_for_caddy(
    public_tls_port: int,
    proc: subprocess.Popen,
    timeout: float = 30.0,
) -> None:
    """Poll Caddy's public TLS listener until it responds.

    Raises:
        RuntimeError: Caddy exited before becoming ready.
        TimeoutError: Caddy did not answer within ``timeout`` seconds.
    """
    base_url = f"https://127.0.0.1:{public_tls_port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = peek_log_files(proc)
            raise RuntimeError(
                f"Caddy (PID {proc.pid}) exited with code "
                f"{proc.returncode} before becoming ready.\n"
                f"--- STDOUT ---\n{stdout[-2000:]}\n"
                f"--- STDERR ---\n{stderr[-2000:]}"
            )
        try:
            resp = requests.get(
                f"{base_url}/api/auth/csrf/",
                timeout=3,
                verify=False,
            )
            if resp.status_code == 200:
                logger.info("Caddy is ready at %s", base_url)
                return
        except requests.ConnectionError:
            pass
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)

    stdout, stderr = peek_log_files(proc)
    raise TimeoutError(
        f"Caddy at {base_url} did not become ready within "
        f"{timeout}s\n"
        f"Process alive: {proc.poll() is None}, "
        f"returncode: {proc.returncode}\n"
        f"--- STDOUT (last 2000 chars) ---\n{stdout[-2000:]}\n"
        f"--- STDERR (last 2000 chars) ---\n{stderr[-2000:]}"
    )


def wait_for_waitress_plaintext(
    waitress_public_port: int,
    proc: subprocess.Popen,
    timeout: float = 90.0,
) -> None:
    """Poll Waitress' plaintext public listener until it responds.

    This is the intermediate gate before starting Caddy — it confirms
    Waitress has bound AND the manager has generated its TLS cert on
    disk, so Caddy's subsequent start has a valid cert/key to read.
    """
    from tests.e2e.log_capture import read_log_files

    base_url = f"http://127.0.0.1:{waitress_public_port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = read_log_files(proc)
            raise RuntimeError(
                f"Manager (PID {proc.pid}) exited with code "
                f"{proc.returncode} before Waitress became ready.\n"
                f"--- STDOUT ---\n{stdout[-2000:]}\n"
                f"--- STDERR ---\n{stderr[-2000:]}"
            )
        try:
            resp = requests.get(
                f"{base_url}/api/auth/csrf/", timeout=3,
            )
            if resp.status_code == 200:
                logger.info(
                    "Waitress plaintext listener ready at %s", base_url,
                )
                return
        except requests.ConnectionError:
            pass
        time.sleep(1)

    stdout, stderr = peek_log_files(proc)
    raise TimeoutError(
        f"Waitress at {base_url} did not become ready within "
        f"{timeout}s\n"
        f"Process alive: {proc.poll() is None}, "
        f"returncode: {proc.returncode}\n"
        f"--- STDOUT (last 2000 chars) ---\n{stdout[-2000:]}\n"
        f"--- STDERR (last 2000 chars) ---\n{stderr[-2000:]}"
    )


def stop_caddy(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Terminate a Caddy subprocess started by :func:`start_caddy`.

    Safe to call multiple times; handles the process-group semantics
    on POSIX / Windows consistent with ``shared.caddy_supervisor``.
    """
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if platform.system() == "Windows":
            proc.terminate()
        else:
            proc.terminate()
    except (ProcessLookupError, OSError) as exc:
        logger.debug("Caddy already exited before terminate: %s", exc)
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Caddy (PID %d) did not stop within %.1fs; killing.",
            proc.pid, timeout,
        )
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (ProcessLookupError, OSError):
            pass
