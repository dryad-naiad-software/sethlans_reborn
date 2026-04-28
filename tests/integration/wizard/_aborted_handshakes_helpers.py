# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Spawn / poll helpers for the issue #170 aborted-handshake regression.

Split from ``test_aborted_handshakes.py`` so the test file stays under
the 300-line ceiling enforced by CLAUDE.md.
"""

from __future__ import annotations

import os
import platform
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_WIZARD = PROJECT_ROOT / "wizard" / "run_wizard.py"

READY_TIMEOUT_S = 30.0
TERM_TIMEOUT_S = 10.0


def free_loopback_port() -> int:
    """Bind 127.0.0.1:0 and return the assigned port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        return sock.getsockname()[1]
    finally:
        sock.close()


def no_verify_ctx() -> ssl.SSLContext:
    """SSLContext that accepts the wizard's self-signed cert."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def write_secret(path: Path, value: bytes) -> None:
    """Write *value* to *path* with chmod-600 on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
    )
    try:
        os.write(fd, value)
    finally:
        os.close(fd)
    if platform.system() != "Windows":
        os.chmod(str(path), 0o600)


def spawn_wizard(data_dir: Path, loopback_port: int):
    """Spawn ``run_wizard.py`` against *data_dir* on *loopback_port*."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["SETHLANS_DATA_DIR"] = str(data_dir)
    env["SETHLANS_WIZARD_PORT"] = str(loopback_port)
    cmd = [sys.executable, "-u", str(RUN_WIZARD)]
    popen_kwargs: dict = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(PROJECT_ROOT),
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **popen_kwargs)


def wait_for_loopback_ready(port: int, proc, deadline: float) -> bool:
    """Poll ``GET http://127.0.0.1:<port>/`` until 200 or *deadline*."""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=2,
            ) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.25)
    return False


def wait_for_caddy_ready(port: int, deadline: float) -> bool:
    """Poll ``GET https://127.0.0.1:<port>/api/health/`` until 200."""
    ctx = no_verify_ctx()
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"https://127.0.0.1:{port}/api/health/",
                timeout=2, context=ctx,
            ) as resp:
                if resp.status == 200:
                    return True
        except (
            urllib.error.URLError, ConnectionError, OSError, ssl.SSLError,
        ):
            pass
        time.sleep(0.25)
    return False


def abort_handshake(port: int) -> None:
    """Connect TCP, then close before sending any TLS bytes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return
    finally:
        try:
            sock.close()
        except OSError:
            pass


def terminate(proc) -> None:
    """SIGTERM with escalation to kill after TERM_TIMEOUT_S."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=TERM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def drain(proc) -> tuple[str, str]:
    """Drain the subprocess stdout/stderr pipes after exit."""
    try:
        out, err = proc.communicate(timeout=2)
    except Exception:
        return "", ""
    return (
        (out or b"").decode(errors="replace"),
        (err or b"").decode(errors="replace"),
    )
