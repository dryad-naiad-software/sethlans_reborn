# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What the launcher loop knows and holds (FR-LN-1), and its small waits.

:class:`LoopContext` is built once by ``run_launcher`` and passed to
every state of the loop (``machine_loop``, ``setup_step``, ``starting``,
``running``, ``reset_setup``). Its live fields (the children, the token
kept for FR-LN-6, how the setup before ended) are read and written by
the loop thread only.

A quit request (the tray's Quit or a signal, ``launcher.supervision``)
raises ``health_probe.QuitRequested`` out of every wait here, and
``machine_loop`` turns it into ``STOPPING``.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from launcher import supervision, tray_ipc
from launcher.components import MANAGER_START_LOG, SETUP_START_LOG
from launcher.health_probe import QuitRequested

logger = logging.getLogger(__name__)

EXIT_POLL_SECONDS = 0.25
WORKER_HEALTH_PORT = 8081
# The worker's own names for its public port (worker config: env first).
WORKER_PORT_ENVS = ("SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT", "SETHLANS_WORKER_UI_PORT")


def safe_invoke(cb: Optional[Callable[..., None]], *args) -> None:
    """Invoke a splash callback, swallowing its exceptions (the splash is best-effort UI)."""
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:  # noqa: BLE001
        logger.exception("splash callback raised; ignoring")


def local_health_url(port: int) -> str:
    """``/api/health/`` on this machine's ``port`` (loopback, through Caddy)."""
    return f"https://127.0.0.1:{int(port)}/api/health/"


def worker_health_port() -> int:
    """The worker page's port: the worker's own env setting, else 8081.

    The launcher's children get its environment, so it reads the same
    variable the worker does. A value that is not a port number is an
    error, never a silent default.
    """
    for name in WORKER_PORT_ENVS:
        raw = os.environ.get(name, "").strip()
        if raw:
            if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 65535):
                raise ValueError(f"{name}={raw!r} is not a port number")
            return int(raw)
    return WORKER_HEALTH_PORT


@dataclass
class LoopContext:
    """The loop's inputs and live state (see the module doc)."""

    settings_dir: Path
    secret: str = field(repr=False)
    tray: Optional[subprocess.Popen] = None
    tray_pid_override: Optional[int] = None  # tests: markers written by the test process
    params_dir: Optional[Path] = None  # broadcaster_params.json (default: the manager's folder)
    no_browser: bool = False
    print_url: bool = False
    on_ready: Optional[Callable[[], None]] = None
    on_failed: Optional[Callable[[str, str], None]] = None
    manager: Optional[subprocess.Popen] = None
    worker: Optional[subprocess.Popen] = None
    setup_proc: Optional[subprocess.Popen] = None
    token: Optional[str] = field(default=None, repr=False)  # the token of the setup before (FR-LN-6)
    setup_kind: Optional[str] = None  # how the setup before ended; None on a normal start
    mode: Optional[str] = None
    port: Optional[int] = None  # the manager's public port while it runs
    markers_since: Optional[_dt.datetime] = None  # reset/restart older than this are dropped

    def __post_init__(self) -> None:
        self.settings_dir = Path(self.settings_dir)
        if self.params_dir is None:
            from launcher.broadcaster_supervisor import broadcaster_params_dir
            self.params_dir = broadcaster_params_dir()

    @property
    def tray_pid(self) -> int:
        if self.tray_pid_override is not None:
            return self.tray_pid_override
        return self.tray.pid if self.tray is not None else -1

    @property
    def manager_log(self) -> Path:
        return self.settings_dir / "logs" / MANAGER_START_LOG

    @property
    def setup_log(self) -> Path:
        return self.settings_dir / "logs" / SETUP_START_LOG

    def ready(self) -> None:
        """The splash's success path (idempotent there)."""
        safe_invoke(self.on_ready)

    def failed(self, reason: str) -> None:
        """The splash's error card (idempotent there)."""
        safe_invoke(self.on_failed, reason, "")

    def accept_markers_from_now(self) -> None:
        """Drop reset and restart requests made before now (security review L5)."""
        self.markers_since = _dt.datetime.now(_dt.timezone.utc)

    def consume_markers(self) -> tuple:
        """``(quit, reset, restart)`` targets from the tray's markers."""
        return tray_ipc.consume_all_markers(self.settings_dir, self.secret, self.tray_pid, self.markers_since)

    def second_quit_check(self) -> bool:
        """A second Quit during a cascade (FR-21); it also stands as a quit request.

        With no quit pending there is no first click, so nothing is read.
        """
        if supervision.quit_target() is None:
            return False
        target = tray_ipc.consume_quit_marker(self.settings_dir, self.secret, self.tray_pid)
        if target is None:
            return False
        supervision.request_quit(supervision.QUIT_ALL)
        return True


def raise_if_quit() -> None:
    """Raise ``QuitRequested`` when any quit is pending."""
    if supervision.quit_target() is not None:
        raise QuitRequested()


def wait_for_exit(proc: subprocess.Popen, poll: float = EXIT_POLL_SECONDS) -> int:
    """Wait for ``proc`` to end and return its exit code; raise on a quit request."""
    while True:
        try:
            return proc.wait(timeout=poll)
        except subprocess.TimeoutExpired:
            raise_if_quit()


def end_process(proc: Optional[subprocess.Popen], grace: float) -> Optional[int]:
    """Stop ``proc`` if it still runs (terminate, then kill); its exit code, or
    ``None`` when it would not end."""
    if proc is None:
        return None
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                logger.error("pid %s did not end after kill", proc.pid)
        except OSError as exc:
            logger.warning("Could not stop pid %s: %s", proc.pid, exc)
    return proc.poll()
