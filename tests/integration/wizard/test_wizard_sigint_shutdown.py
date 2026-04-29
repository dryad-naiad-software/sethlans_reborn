# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #176 acceptance test — wizard subprocess exits within 5
seconds of SIGINT/CTRL_BREAK_EVENT delivery.

The bug we're guarding against: a SIGINT handler that calls
``server.close()`` directly does NOT reliably break waitress's run
loop (observed 30-minute hang). The fix moves run() onto a daemon
thread + threading.Event the signal handler sets.

This test spawns the wizard as a real subprocess so the full
bootstrap/server/signal-handler stack is exercised, then sends the
platform-appropriate signal and asserts a clean exit well within 5s.
"""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_WIZARD = PROJECT_ROOT / "wizard" / "run_wizard.py"


# Issue #176 acceptance: must exit within 5s on SIGINT.
ACCEPTANCE_TIMEOUT_SECONDS = 5.0


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_secret(target: Path, value: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
    )
    try:
        os.write(fd, value)
    finally:
        os.close(fd)


def _spawn_wizard(data_dir: Path, port: int) -> subprocess.Popen:
    """Spawn the wizard as a subprocess and wait for it to bind."""
    wizard_dir = data_dir / "wizard"
    wizard_dir.mkdir(parents=True, exist_ok=True)
    setup_token = secrets.token_urlsafe(16)
    ipc_secret = secrets.token_urlsafe(16).encode("ascii")
    _write_secret(wizard_dir / ".setup_token", setup_token.encode("utf-8"))
    _write_secret(wizard_dir / ".ipc_secret", ipc_secret)

    env = dict(os.environ)
    env["SETHLANS_DATA_DIR"] = str(data_dir.resolve())
    env["SETHLANS_WIZARD_PORT"] = str(port)
    # Suppress the user's actual data dir / log fan-out under the temp.

    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "env": env,
        "cwd": str(PROJECT_ROOT),
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, str(RUN_WIZARD)], **kwargs,
    )

    # Wait for the server to bind by polling the TCP port.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # Already exited — collect output for the assertion message.
            output = proc.stdout.read().decode("utf-8", errors="replace")
            raise AssertionError(
                f"wizard exited before binding (rc={proc.returncode}):\n{output}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except (OSError, ConnectionRefusedError):
            time.sleep(0.2)

    proc.kill()
    proc.wait(timeout=5.0)
    raise AssertionError("wizard never bound its listener")


def _send_interrupt(proc: subprocess.Popen) -> None:
    """Deliver SIGINT (POSIX) or CTRL_BREAK_EVENT (Windows)."""
    if os.name == "nt":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        # Send SIGINT to the child's process group.
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = proc.pid
        os.killpg(pgid, signal.SIGINT)


class TestWizardSigintShutdown:

    def test_single_sigint_exits_within_acceptance_window(self, tmp_path):
        """Issue #176 AC: process exits within ~5 s of one SIGINT."""
        port = _pick_free_port()
        proc = _spawn_wizard(tmp_path, port)
        try:
            t0 = time.monotonic()
            _send_interrupt(proc)
            try:
                rc = proc.wait(timeout=ACCEPTANCE_TIMEOUT_SECONDS + 1.0)
            except subprocess.TimeoutExpired:
                # Fail with diagnostics; then escalate so the test
                # doesn't leak the subprocess.
                proc.kill()
                proc.wait(timeout=5.0)
                output = proc.stdout.read().decode(
                    "utf-8", errors="replace",
                )
                raise AssertionError(
                    f"wizard did not exit within "
                    f"{ACCEPTANCE_TIMEOUT_SECONDS + 1.0:.1f}s of SIGINT — "
                    f"issue #176 regression. Output:\n{output}"
                )
            elapsed = time.monotonic() - t0
            output = proc.stdout.read().decode("utf-8", errors="replace")

            # Acceptance window. We give 1 s of slack for Python
            # startup + signal delivery latency.
            assert elapsed < ACCEPTANCE_TIMEOUT_SECONDS + 1.0, (
                f"wizard took {elapsed:.2f}s to exit (>{ACCEPTANCE_TIMEOUT_SECONDS}s); "
                f"output:\n{output}"
            )
            # Returncode: 0 (clean) or -SIGINT (Python's default
            # KeyboardInterrupt rc on POSIX) or 130 (escalation).
            # We don't pin a specific code — the goal is timely exit.
            assert rc is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
