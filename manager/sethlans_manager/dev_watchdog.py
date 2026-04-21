# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dev hot-reload watchdog wrapper (Phase 6).

Parent/child supervisor that mirrors uvicorn's ``--reload`` semantics
for the Waitress production path. The parent process owns the
filesystem observer and child-lifecycle signalling; the child actually
serves HTTP traffic. On any ``*.py`` change under ``manager/``, the
parent debounces a 500 ms window of events and then restarts the
child.

Design constraints:

* **Lazy imports**: ``watchdog`` is a dev-only dependency (see
  ``requirements-dev.txt``). The production serving path must not
  import this module at boot, and tests cover the ImportError path
  with a clear error message.
* **No Django boot in the parent**: the parent must not import
  ``sethlans_manager.wsgi`` or trigger ``django.setup()``. All Django
  work happens in the child.
* **Windows signalling**: we spawn the child with
  ``subprocess.CREATE_NEW_PROCESS_GROUP`` so ``CTRL_BREAK_EVENT`` can
  be delivered to the child independently of the parent's own Ctrl+C
  handling.

The filesystem event filter, the debouncer, and the watchdog event
handler live in ``dev_watchdog_handler.py`` to keep both files under
the 300-line ceiling.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from sethlans_manager.dev_watchdog_handler import (
    _DebouncedRestarter,
    _make_event_handler,
)

logger = logging.getLogger(__name__)

_GRACEFUL_SHUTDOWN_SECONDS = 5.0


def _strip_dev_flag(argv: Sequence[str]) -> List[str]:
    """Return ``argv`` with the first ``--dev`` occurrence removed.

    The child is respawned via the production path; keeping ``--dev``
    in its argv would cause infinite recursion.
    """
    out: List[str] = []
    dropped = False
    for token in argv:
        if token == "--dev" and not dropped:
            dropped = True
            continue
        out.append(token)
    return out


def _build_child_env(parent_env: Optional[dict] = None) -> dict:
    """Build the environment for the child process.

    Copies the parent env, preserves ``SETHLANS_DEV_MODE=1``, and
    strips ``SETHLANS_DEV_IS_PARENT`` so the child takes the
    production branch in ``run_manager.main()``.
    """
    src = dict(os.environ if parent_env is None else parent_env)
    src["SETHLANS_DEV_MODE"] = "1"
    src.pop("SETHLANS_DEV_IS_PARENT", None)
    return src


def _popen_creationflags() -> int:
    """Return the Windows ``CREATE_NEW_PROCESS_GROUP`` flag or 0."""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return 0


def _terminate_signal() -> int:
    """Return the platform-appropriate graceful-stop signal."""
    if sys.platform == "win32":
        return getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
    return signal.SIGTERM


class _ChildSupervisor:
    """Spawns, signals, and respawns the Waitress child process."""

    def __init__(
        self,
        manage_script: Path,
        child_argv: Sequence[str],
        shutdown_event: threading.Event,
    ):
        self._manage_script = Path(manage_script)
        self._child_argv = list(child_argv)
        self._shutdown_event = shutdown_event
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._restart_in_progress = threading.Event()

    def spawn(self) -> subprocess.Popen:
        """Start a fresh child process."""
        cmd = [sys.executable, str(self._manage_script), *self._child_argv]
        env = _build_child_env()
        creationflags = _popen_creationflags()
        logger.info("dev watchdog: spawning child %s", cmd)
        proc = subprocess.Popen(
            cmd,
            env=env,
            creationflags=creationflags,
        )
        with self._lock:
            self._proc = proc
        return proc

    def _send_stop_signal(self, proc: subprocess.Popen) -> None:
        try:
            proc.send_signal(_terminate_signal())
        except (ProcessLookupError, OSError) as exc:  # pragma: no cover
            logger.warning("dev watchdog: signal to child failed: %s", exc)

    def stop(
        self, timeout: float = _GRACEFUL_SHUTDOWN_SECONDS,
    ) -> Optional[int]:
        """Signal the child and wait up to ``timeout`` for graceful exit."""
        with self._lock:
            proc = self._proc
        if proc is None:
            return None
        self._send_stop_signal(proc)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "dev watchdog: child did not exit in %.1fs, killing",
                timeout,
            )
            proc.kill()
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover
                return None

    def restart(self) -> None:
        """Stop the running child and spawn a new one."""
        self._restart_in_progress.set()
        try:
            self.stop()
            if self._shutdown_event.is_set():
                return
            self.spawn()
        finally:
            self._restart_in_progress.clear()

    def wait_for_unexpected_exit(self) -> int:
        """Block until the child exits outside a restart; return rc.

        Returns promptly with ``0`` if the shutdown event is set before
        the child exits unexpectedly.
        """
        while not self._shutdown_event.is_set():
            with self._lock:
                proc = self._proc
            if proc is None:
                return 0
            try:
                rc = proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
            if self._restart_in_progress.is_set():
                # Expected exit from an in-flight restart — loop and
                # pick up the freshly-spawned child on the next tick.
                continue
            if self._shutdown_event.is_set():
                return rc
            logger.error(
                "dev watchdog: child exited unexpectedly with rc=%s", rc,
            )
            return rc if rc is not None else 1
        return 0


def _install_parent_signal_handlers(
    shutdown_event: threading.Event,
    supervisor: _ChildSupervisor,
) -> None:
    """Forward SIGINT/SIGTERM to the child and trip shutdown_event."""

    def _handler(signum, _frame):  # pragma: no cover - signal path
        logger.info("dev watchdog: parent received signal %s", signum)
        shutdown_event.set()
        supervisor.stop()

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):  # pragma: no cover
        pass
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError):  # pragma: no cover
            pass


def _start_observer(watch_dir: Path, handler) -> "object":
    """Start a watchdog ``Observer`` on ``watch_dir`` and return it."""
    from watchdog.observers import Observer

    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()
    return observer


def _watched_directories(manage_script: Path) -> Iterable[Path]:
    """Return directories the parent should watch recursively."""
    return [Path(manage_script).resolve().parent]


def run_dev_watchdog(manage_script: Path, argv: Sequence[str]) -> int:
    """Parent entry point for dev hot-reload.

    Blocks until the child exits unexpectedly or the parent receives
    SIGINT/SIGTERM. Returns the intended process exit code.
    """
    # Parent-side env markers — the child inherits SETHLANS_DEV_MODE
    # via ``_build_child_env``; SETHLANS_DEV_IS_PARENT is stripped
    # before spawn so the child takes the production branch.
    os.environ["SETHLANS_DEV_MODE"] = "1"
    os.environ["SETHLANS_DEV_IS_PARENT"] = "1"

    manage_script = Path(manage_script).resolve()
    child_argv = _strip_dev_flag(argv)

    shutdown_event = threading.Event()
    supervisor = _ChildSupervisor(
        manage_script=manage_script,
        child_argv=child_argv,
        shutdown_event=shutdown_event,
    )

    restarter = _DebouncedRestarter(callback=supervisor.restart)
    handler = _make_event_handler(restarter)

    observers = []
    for watch_dir in _watched_directories(manage_script):
        observers.append(_start_observer(watch_dir, handler))

    _install_parent_signal_handlers(shutdown_event, supervisor)

    try:
        supervisor.spawn()
        rc = supervisor.wait_for_unexpected_exit()
    finally:
        shutdown_event.set()
        restarter.cancel()
        for obs in observers:
            try:
                obs.stop()
            except Exception:  # pragma: no cover
                logger.exception("observer.stop() failed")
        for obs in observers:
            try:
                obs.join(timeout=2.0)
            except Exception:  # pragma: no cover
                logger.exception("observer.join() failed")
        supervisor.stop()

    return rc
