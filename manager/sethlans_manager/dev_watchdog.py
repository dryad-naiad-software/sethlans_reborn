# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dev hot-reload watchdog wrapper (Phase 6).

Parent/child supervisor that provides ``--reload``-style hot-reload
semantics for the Waitress production path. The parent process owns the
filesystem observer and child-lifecycle signalling; the child actually
serves HTTP traffic. The child is ``run_manager.py`` itself without
``--dev`` (issue #293), so it takes the normal production boot:
migrations, collectstatic, then the Waitress listeners. On any
``*.py`` change under ``manager/`` or ``shared/``, the parent debounces
a 500 ms window of events and then restarts the child.

Design constraints:

* **Lazy imports**: ``watchdog`` is a dev-only dependency (see
  ``requirements-dev.txt``). The production serving path must not
  import this module at boot, and tests cover the ImportError path
  with a clear error message.
* **No Django boot in the parent**: the parent must not import
  ``sethlans_manager.wsgi`` or trigger ``django.setup()``. All Django
  work happens in the child.
* **Process tree**: the child runs in its own process group (Windows)
  or session (POSIX), so stop signals reach its ``migrate`` /
  ``collectstatic`` subprocesses too. See ``dev_watchdog_process.py``.

The filesystem event filter, the debouncer, and the watchdog event
handler live in ``dev_watchdog_handler.py``; the process-tree
signalling lives in ``dev_watchdog_process.py``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Iterable, Optional, Sequence

from sethlans_manager.dev_watchdog_handler import (
    _DebouncedRestarter,
    _make_event_handler,
)
from sethlans_manager.dev_watchdog_process import (  # noqa: F401
    build_child_command,
    build_child_env as _build_child_env,
    install_parent_signal_handlers as _install_parent_signal_handlers,
    kill_process_tree,
    popen_group_kwargs,
    send_stop_signal,
    strip_dev_flag as _strip_dev_flag,
)

logger = logging.getLogger(__name__)

_GRACEFUL_SHUTDOWN_SECONDS = 5.0
# Upper bound for waiting on an in-flight restart at shutdown: one
# graceful stop, one kill wait, plus the spawn.
_RESTART_DRAIN_SECONDS = 2 * _GRACEFUL_SHUTDOWN_SECONDS + 5.0


class _ChildSupervisor:
    """Spawns, signals, and respawns the Waitress child process."""

    def __init__(
        self,
        server_script: Path,
        child_argv: Sequence[str],
        shutdown_event: threading.Event,
    ):
        self._server_script = Path(server_script)
        self._child_argv = list(child_argv)
        self._shutdown_event = shutdown_event
        self._proc: Optional[subprocess.Popen] = None
        # RLock: the parent's signal handler calls ``stop()`` on the main
        # thread, which may already hold this lock when interrupted.
        self._lock = threading.RLock()
        # Serialises restarts so two debounce fires cannot each spawn a
        # child. Never taken by ``stop()`` or the signal handler.
        self._restart_lock = threading.Lock()
        self._restart_in_progress = threading.Event()

    def spawn(self) -> subprocess.Popen:
        """Start a fresh child process.

        If shutdown began while the child was being spawned, the new
        child is stopped at once so it is never left untracked.
        """
        cmd = build_child_command(self._server_script, self._child_argv)
        env = _build_child_env()
        logger.info("dev watchdog: spawning child %s", cmd)
        proc = subprocess.Popen(cmd, env=env, **popen_group_kwargs())
        with self._lock:
            self._proc = proc
        if self._shutdown_event.is_set():
            self.stop()
        return proc

    def stop(
        self, timeout: float = _GRACEFUL_SHUTDOWN_SECONDS,
    ) -> Optional[int]:
        """Signal the child and wait up to ``timeout`` for graceful exit."""
        with self._lock:
            proc = self._proc
        if proc is None:
            return None
        send_stop_signal(proc)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "dev watchdog: child did not exit in %.1fs, killing",
                timeout,
            )
            kill_process_tree(proc)
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:  # pragma: no cover
                return None

    def restart(self) -> None:
        """Stop the running child and spawn a new one (one at a time)."""
        with self._restart_lock:
            if self._shutdown_event.is_set():
                return
            self._restart_in_progress.set()
            try:
                self.stop()
                if self._shutdown_event.is_set():
                    return
                self.spawn()
            finally:
                self._restart_in_progress.clear()

    def wait_for_restart(
        self, timeout: float = _RESTART_DRAIN_SECONDS,
    ) -> None:
        """Wait for an in-flight restart to finish (shutdown path).

        Called after the shutdown event is set, so a restart that was
        mid-spawn has published (and stopped) its child before the
        parent's final ``stop()``.
        """
        if self._restart_lock.acquire(timeout=timeout):
            self._restart_lock.release()
        else:  # pragma: no cover - defensive
            logger.warning("dev watchdog: restart still running at exit")

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
            with self._lock:
                replaced = self._proc is not proc
            if replaced or self._restart_in_progress.is_set():
                # Expected exit from a restart (in flight, or already
                # done and the new child spawned) — loop and pick up
                # the freshly-spawned child on the next tick.
                continue
            if self._shutdown_event.is_set():
                return rc
            logger.error(
                "dev watchdog: child exited unexpectedly with rc=%s", rc,
            )
            return rc if rc is not None else 1
        return 0


def _start_observer(watch_dir: Path, handler) -> "object":
    """Start a watchdog ``Observer`` on ``watch_dir`` and return it."""
    from watchdog.observers import Observer

    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()
    return observer


def _watched_directories(server_script: Path) -> Iterable[Path]:
    """Return directories the parent should watch recursively.

    The manager tree (the directory holding ``run_manager.py``) and the
    repository's ``shared/`` package, which the manager imports.
    """
    manager_dir = Path(server_script).resolve().parent
    dirs = [manager_dir]
    shared_dir = manager_dir.parent / "shared"
    if shared_dir.is_dir():
        dirs.append(shared_dir)
    return dirs


def run_dev_watchdog(server_script: Path, argv: Sequence[str]) -> int:
    """Parent entry point for dev hot-reload.

    ``server_script`` is ``run_manager.py``; ``argv`` is its command
    line without the program name. The child runs ``server_script``
    with ``argv`` minus ``--dev``.

    Blocks until the child exits unexpectedly or the parent receives a
    stop signal (SIGINT/SIGTERM, plus SIGBREAK on Windows and SIGHUP on
    POSIX). Returns the intended process exit code.
    """
    # Parent-side env markers — the child inherits SETHLANS_DEV_MODE
    # via ``_build_child_env``; SETHLANS_DEV_IS_PARENT is stripped
    # before spawn so the child takes the production branch.
    os.environ["SETHLANS_DEV_MODE"] = "1"
    os.environ["SETHLANS_DEV_IS_PARENT"] = "1"

    server_script = Path(server_script).resolve()
    child_argv = _strip_dev_flag(argv)

    shutdown_event = threading.Event()
    supervisor = _ChildSupervisor(
        server_script=server_script,
        child_argv=child_argv,
        shutdown_event=shutdown_event,
    )

    restarter = _DebouncedRestarter(callback=supervisor.restart)
    handler = _make_event_handler(restarter)

    observers = []
    for watch_dir in _watched_directories(server_script):
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
        supervisor.wait_for_restart()
        supervisor.stop()

    return rc
