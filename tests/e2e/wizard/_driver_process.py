# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Subprocess spawn / readiness-poll / termination helpers for the
wizard E2E driver."""

from __future__ import annotations

import os
import platform
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.e2e.wizard import _proctree

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DRIVER_SCRIPT = Path(__file__).parent / "_driver.py"

# Wizard subprocess port file is written within the first ~5 s. Allow a
# generous ceiling for cold-start cert generation on slow machines / CI.
WIZARD_PORT_TIMEOUT_S = 30.0

# SIGTERM-then-SIGKILL escalation budget for the driver subprocess.
TERM_TIMEOUT_S = 15.0


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if *port* is bound on *host* (TCP)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        try:
            sock.close()
        except OSError:
            pass


def find_free_port() -> int:
    """Bind a transient socket on 127.0.0.1:0 and return the assigned port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _no_verify_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def read_wizard_port(data_dir: Path) -> int | None:
    """Read the port file the wizard writes after binding."""
    port_file = data_dir / "wizard" / "port"
    try:
        raw = port_file.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def wait_for_wizard_ready(
    data_dir: Path, proc: subprocess.Popen,
    timeout: float = WIZARD_PORT_TIMEOUT_S,
) -> tuple[int, str]:
    """Wait until the wizard's port file appears AND the URL returns 200."""
    deadline = time.monotonic() + timeout
    ctx = _no_verify_context()
    last_err: Exception | None = None
    port: int | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = drain_streams(proc)
            raise RuntimeError(
                f"driver exited with rc={proc.returncode} before wizard ready\n"
                f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
            )
        if port is None:
            port = read_wizard_port(data_dir)
        if port is not None:
            base = f"https://localhost:{port}"
            try:
                req = urllib.request.Request(f"{base}/", method="GET")
                with urllib.request.urlopen(
                    req, timeout=2, context=ctx,
                ) as resp:
                    if resp.status == 200:
                        return port, base
            except (
                urllib.error.URLError, ConnectionError,
                OSError, ssl.SSLError,
            ) as exc:
                last_err = exc
        time.sleep(0.25)
    raise TimeoutError(
        f"wizard did not become ready within {timeout}s; "
        f"port={port} last_err={last_err!r}"
    )


def drain_streams(proc: subprocess.Popen) -> tuple[str, str]:
    """Drain stdout / stderr after the process exits.

    CONC-HIGH-2 (Phase F4): drainer threads spawned at fixture
    startup continuously read ``proc.stdout`` / ``proc.stderr`` into
    in-memory buffers, so the kernel pipe buffer can never fill and
    deadlock the child. We just snapshot the buffers here.
    """
    accumulator = getattr(proc, "_e2e_drain_buffers", None)
    if accumulator is None:
        return "", ""
    out_buf, err_buf, lock = accumulator
    with lock:
        return (
            b"".join(out_buf).decode(errors="replace"),
            b"".join(err_buf).decode(errors="replace"),
        )


def _start_drainer_threads(proc: subprocess.Popen) -> None:
    """Spawn background readers for ``proc.stdout`` / ``proc.stderr``.

    Stashes ``(out_buf, err_buf, lock)`` on the Popen so
    ``drain_streams`` can snapshot the captured bytes after the
    process exits.
    """
    out_buf: list[bytes] = []
    err_buf: list[bytes] = []
    lock = threading.Lock()
    proc._e2e_drain_buffers = (  # type: ignore[attr-defined]
        out_buf, err_buf, lock,
    )

    def _drain(stream, sink):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with lock:
                    sink.append(chunk)
        except (OSError, ValueError):
            # Stream closed underneath us during shutdown — fine.
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    for stream, sink in ((proc.stdout, out_buf), (proc.stderr, err_buf)):
        if stream is None:
            continue
        t = threading.Thread(
            target=_drain, args=(stream, sink), daemon=True,
        )
        t.start()


def terminate(proc: subprocess.Popen, data_dir: Path | None = None) -> None:
    """SIGTERM the driver + ALL its descendants, escalate to SIGKILL.

    Windows ``Popen.terminate`` is ``TerminateProcess`` (instant kill,
    no Python signal handler fires) so the driver can't run its own
    cleanup; descendant cleanup is delegated to ``_proctree``.
    """
    if proc.poll() is not None:
        _proctree.kill_descendants(proc.pid, data_dir)
        return
    descendants = _proctree.enumerate_descendants(proc.pid)
    _politely_terminate(proc)
    for child in descendants:
        try:
            child.terminate()
        except Exception:  # noqa: BLE001 — psutil raises various
            pass
    _wait_then_kill(proc)
    _proctree.kill_descendants(proc.pid, data_dir)


def _politely_terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass


def _wait_then_kill(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=TERM_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def spawn_driver(
    data_dir: Path,
    runtime_mode: str = "health-ok",
    runtime_port: int = 8080,
    idle_timeout: float | None = None,
    extra_env: dict[str, str] | None = None,
    setup_token: str | None = None,
    ipc_secret: bytes | None = None,
) -> subprocess.Popen:
    """Spawn the ``_driver.py`` subprocess; return the Popen handle."""
    cmd = [
        sys.executable, "-u", str(DRIVER_SCRIPT),
        "--data-dir", str(data_dir),
        "--runtime-mode", runtime_mode,
        "--runtime-port", str(runtime_port),
    ]
    if idle_timeout is not None:
        cmd += ["--idle-timeout", str(idle_timeout)]
    # Use ``--flag=value`` so argparse doesn't try to interpret a
    # base64-token leading-hyphen as a separate flag.
    if setup_token is not None:
        cmd += [f"--setup-token={setup_token}"]
    if ipc_secret is not None:
        cmd += [f"--ipc-secret={ipc_secret.hex()}"]

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # Honored by the wizard subprocess via shared.frozen_paths.
    env["SETHLANS_DATA_DIR"] = str(data_dir)
    if extra_env:
        env.update(extra_env)

    # CONC-HIGH-2 (Phase F4): subprocess.PIPE is fine ONLY if a
    # concurrent reader drains it. The pytest test calls
    # ``proc.wait(timeout=45.0)`` without a concurrent reader, so an
    # INFO-logging driver across a 30-45s test fills the kernel pipe
    # buffer (~4 KiB on Windows, ~64 KiB on POSIX) and blocks on its
    # next write — the blocked driver cannot run its
    # ``terminate_all()`` cleanup, child processes leak, and
    # subsequent tests fail port-bind. Spawn drainer threads at
    # fixture startup that continuously read from ``proc.stdout`` /
    # ``proc.stderr`` so the pipe never fills.
    popen_kwargs: dict = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(PROJECT_ROOT),
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **popen_kwargs)
    _start_drainer_threads(proc)
    return proc
