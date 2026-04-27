# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared fixtures for the wizard subprocess integration tests.

Each test that needs a live wizard process uses :func:`wizard_process`
which:

* Creates a per-test data directory under ``tmp_path``.
* Generates a fresh setup token and IPC secret, writes them to
  ``<data_dir>/wizard/.setup_token`` and ``.ipc_secret`` with
  chmod-600 permissions on POSIX (mirrors what the launcher does in
  prod).
* Allocates a free TCP port on 127.0.0.1.
* Spawns ``python wizard/run_wizard.py`` with ``SETHLANS_DATA_DIR``
  + ``SETHLANS_WIZARD_PORT`` set, polls the HTTPS root until it
  responds 200 (self-signed cert acceptance via
  :class:`ssl.SSLContext` with ``CERT_NONE``).
* Yields a :class:`WizardProcess` dataclass with ``proc``, ``port``,
  ``base_url``, ``data_dir``, ``setup_token`` (str), and
  ``ipc_secret`` (bytes).
* On teardown, sends SIGTERM, waits up to 10s, escalates to kill,
  then unlinks the data directory.
"""

from __future__ import annotations

import dataclasses
import os
import platform
import secrets
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

# Project root — the directory containing ``wizard/run_wizard.py``.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_WIZARD = PROJECT_ROOT / "wizard" / "run_wizard.py"

# Total bind-readiness timeout (s). The wizard starts cold each test
# (Python interpreter + import waitress + cert generation), so allow
# ample headroom for slow CI machines.
READY_TIMEOUT_S = 30.0

# SIGTERM → wait → SIGKILL escalation window for graceful shutdown.
TERM_TIMEOUT_S = 10.0


@dataclasses.dataclass
class WizardProcess:
    """Handle to a live wizard subprocess plus its provisioned secrets."""

    proc: subprocess.Popen
    port: int
    base_url: str
    data_dir: Path
    wizard_subdir: Path
    setup_token: str
    ipc_secret: bytes


def _find_free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        return sock.getsockname()[1]
    finally:
        sock.close()


def _no_verify_context() -> ssl.SSLContext:
    """Return a context that accepts the wizard's self-signed cert."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _write_secret(path: Path, value: bytes) -> None:
    """Atomically write *value* to *path* with chmod 600 on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, value)
    finally:
        os.close(fd)
    if platform.system() != "Windows":
        os.chmod(str(path), 0o600)


def _wait_for_ready(base_url: str, proc: subprocess.Popen) -> None:
    """Poll ``GET <base_url>/`` until 200, or raise if process exits."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    ctx = _no_verify_context()
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = _drain_streams(proc)
            raise RuntimeError(
                f"wizard exited with rc={proc.returncode} before ready\n"
                f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
            )
        try:
            req = urllib.request.Request(f"{base_url}/", method="GET")
            with urllib.request.urlopen(req, timeout=2, context=ctx) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError, ssl.SSLError) as exc:
            last_err = exc
        time.sleep(0.25)
    stdout, stderr = _peek_streams(proc)
    raise TimeoutError(
        f"wizard at {base_url} did not become ready within "
        f"{READY_TIMEOUT_S}s; last error: {last_err!r}\n"
        f"--- stdout (so far) ---\n{stdout}\n"
        f"--- stderr (so far) ---\n{stderr}"
    )


def _drain_streams(proc: subprocess.Popen) -> tuple[str, str]:
    """Drain stdout/stderr after the process exits."""
    try:
        out, err = proc.communicate(timeout=2)
    except Exception:
        return "", ""
    return (out or b"").decode(errors="replace"), (err or b"").decode(errors="replace")


def _peek_streams(proc: subprocess.Popen) -> tuple[str, str]:
    """Best-effort read of currently-buffered stdout/stderr."""
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    for stream, sink in (
        (proc.stdout, out_chunks),
        (proc.stderr, err_chunks),
    ):
        if stream is None:
            continue
        try:
            stream.flush()
        except Exception:
            pass
    # The streams are pipes; reading them now would block. Return empty
    # placeholders — the on-failure path in tests prefers SIGTERM-then-
    # communicate so we get the full buffer with a definite end.
    return "", ""


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM the wizard, escalate to kill after TERM_TIMEOUT_S."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=TERM_TIMEOUT_S)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


@pytest.fixture
def wizard_data_dir(tmp_path: Path) -> Path:
    """Return a fresh shared data directory with the ``wizard/`` subdir."""
    data_dir = tmp_path / "sethlans"
    (data_dir / "wizard").mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def wizard_secrets(wizard_data_dir: Path) -> tuple[str, bytes]:
    """Provision the launcher-written secret files; return ``(token, secret)``.

    Both the setup token and the IPC secret use
    ``secrets.token_urlsafe(32).encode("ascii")`` — URL-safe base64
    bytes (alphabet ``A-Z a-z 0-9 - _``). This MUST match the
    launcher's production shape (``launcher/wizard_orchestration.py``)
    because ``ipc.read_secret_file`` calls ``raw.strip()`` on read:
    against URL-safe bytes the strip is a no-op, but against random
    binary (``secrets.token_bytes(32)``) ~4.6% of secrets had whitespace
    edges that got stripped, corrupting the secret and triggering
    intermittent HMAC mismatches in marker validation. See issue #153.
    """
    setup_token = secrets.token_urlsafe(32)
    ipc_secret = secrets.token_urlsafe(32).encode("ascii")
    subdir = wizard_data_dir / "wizard"
    _write_secret(subdir / ".setup_token", setup_token.encode("ascii"))
    _write_secret(subdir / ".ipc_secret", ipc_secret)
    return setup_token, ipc_secret


@pytest.fixture
def wizard_process(
    wizard_data_dir: Path,
    wizard_secrets: tuple[str, bytes],
) -> Iterator[WizardProcess]:
    """Spawn ``run_wizard.py`` against a fresh tmpdir; yield + tear down.

    A free TCP port is allocated and passed via ``SETHLANS_WIZARD_PORT``
    so each test gets an isolated listener.
    """
    setup_token, ipc_secret = wizard_secrets
    port = _find_free_port()
    base_url = f"https://localhost:{port}"

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["SETHLANS_DATA_DIR"] = str(wizard_data_dir)
    env["SETHLANS_WIZARD_PORT"] = str(port)
    # Tests share PYTHONPATH with the integration suite (pytest.ini sets
    # ``pythonpath = . manager worker``); the wizard's run_wizard.py
    # adds its own paths when not frozen, so no extra setup is needed.

    cmd = [sys.executable, "-u", str(RUN_WIZARD)]
    popen_kwargs: dict = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(PROJECT_ROOT),
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(cmd, **popen_kwargs)
    handle = WizardProcess(
        proc=proc,
        port=port,
        base_url=base_url,
        data_dir=wizard_data_dir,
        wizard_subdir=wizard_data_dir / "wizard",
        setup_token=setup_token,
        ipc_secret=ipc_secret,
    )
    try:
        try:
            _wait_for_ready(base_url, proc)
        except Exception:
            _terminate(proc)
            raise
        yield handle
    finally:
        _terminate(proc)
