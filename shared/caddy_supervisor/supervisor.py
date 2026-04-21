# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caddy supervisor lifecycle (start / stop / watchdog).

See ``shared/caddy_supervisor/__init__.py`` for the public API overview
and supervision semantics. Generic across manager and worker: the
caller supplies a pure-function Caddyfile renderer, a set of template
kwargs, the env-var name used by the external-Caddyfile branch
(Docker), and the mapping used to overlay those kwargs onto Caddy's
spawn env.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Union

from shared.caddy_supervisor import process as _proc
from shared.caddy_supervisor.io import atomic_write_text

logger = logging.getLogger(__name__)

# Lifecycle constants (mirror manager lifecycle table).
MAX_RESTART_ATTEMPTS = 3
RESTART_BACKOFF_SECONDS = 1.0
STABLE_UPTIME_RESET_SECONDS = 60.0
WATCHDOG_POLL_SECONDS = 0.5
SHUTDOWN_DRAIN_DEFAULT_SECONDS = 10.0


class CaddyBinaryNotFoundError(RuntimeError):
    """Raised when the Caddy binary is missing or not executable."""


class CaddyfileNotFoundError(RuntimeError):
    """Raised when the external-Caddyfile env var points to a missing/unreadable path."""


class CaddySupervisor:
    """Supervise a Caddy subprocess.

    Generic across manager and worker. The caller supplies the
    Caddyfile renderer (pure function returning a str) plus template
    kwargs, the env-var name whose presence activates the external
    (Docker) branch, and the mapping from template-kwarg name to env
    var name used to overlay kwargs onto Caddy's spawn env in the
    external branch.
    """

    def __init__(
        self,
        *,
        binary_path: Union[str, Path],
        caddyfile_path: Union[str, Path],
        caddyfile_renderer: Callable[..., str],
        template_kwargs: Mapping[str, object],
        caddyfile_path_env: str,
        env_overlay_mapping: Mapping[str, str],
    ) -> None:
        """Construct a supervisor. See module docstring for semantics."""
        self._binary_path = Path(binary_path)
        self._caddyfile_path = Path(caddyfile_path)
        self._caddyfile_renderer = caddyfile_renderer
        self._template_kwargs: Dict[str, object] = dict(template_kwargs)
        self._caddyfile_path_env = caddyfile_path_env
        self._env_overlay_mapping: Dict[str, str] = dict(env_overlay_mapping)
        self._process: Optional[subprocess.Popen] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._process_lock = threading.Lock()
        # Populated with the env-var overlay passed to Caddy when the
        # external-Caddyfile branch is active; ``None`` means "inherit
        # parent env unchanged" (native-dev / frozen-install).
        self._spawn_env: Optional[dict] = None

        #: Set when supervision has given up (retry budget exhausted).
        #: The caller polls this flag and exits on detection.
        self.error_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Template Caddyfile, spawn Caddy, start watchdog thread.

        If ``<caddyfile_path_env>`` is set in ``os.environ``, use the
        pre-baked Caddyfile at that path (Docker). Otherwise render via
        ``caddyfile_renderer`` and atomically write to
        ``caddyfile_path``.
        """
        self._validate_binary()
        external_path = os.environ.get(self._caddyfile_path_env)
        if external_path:
            self._use_external_caddyfile(Path(external_path))
        else:
            content = self._caddyfile_renderer(**self._template_kwargs)
            atomic_write_text(self._caddyfile_path, content)
            logger.info("Caddyfile written to %s", self._caddyfile_path)
        self._spawn_locked()

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="caddy-supervisor-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self, timeout: float = SHUTDOWN_DRAIN_DEFAULT_SECONDS) -> None:
        """Stop Caddy gracefully; escalate to kill on timeout."""
        self._shutdown_event.set()
        with self._process_lock:
            proc = self._process
        if proc is not None and proc.poll() is None:
            _proc.signal_graceful_stop(proc)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Caddy did not exit within %.1fs; escalating to kill",
                    timeout,
                )
                _proc.force_kill(proc)
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    logger.error(
                        "Caddy did not exit after kill escalation"
                    )
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=5.0)
            if self._watchdog_thread.is_alive():
                logger.warning(
                    "Caddy watchdog thread did not terminate within 5s"
                )

    def is_running(self) -> bool:
        """Return True if Caddy's process appears to be live."""
        with self._process_lock:
            proc = self._process
        return proc is not None and proc.poll() is None

    def update_template_kwargs(self, **new_kwargs) -> None:
        """Update template kwargs; call :meth:`restart` to apply."""
        self._template_kwargs.update(new_kwargs)

    def restart(self, timeout: float = SHUTDOWN_DRAIN_DEFAULT_SECONDS) -> None:
        """Stop Caddy and restart with a freshly rendered Caddyfile."""
        self.stop(timeout=timeout)
        self._shutdown_event.clear()
        self.error_event.clear()
        self.start()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_binary(self) -> None:
        """Assert the Caddy binary exists and is executable."""
        if not self._binary_path.is_file():
            raise CaddyBinaryNotFoundError(
                f"Caddy binary not found at {self._binary_path}"
            )
        if platform.system() != "Windows" and not os.access(
            self._binary_path, os.X_OK
        ):
            raise CaddyBinaryNotFoundError(
                f"Caddy binary at {self._binary_path} is not executable"
            )

    def _use_external_caddyfile(self, external: Path) -> None:
        """Switch to an operator-supplied Caddyfile (Docker case)."""
        if not external.is_file():
            raise CaddyfileNotFoundError(
                f"{self._caddyfile_path_env} points to a non-existent "
                f"file: {external}"
            )
        if not os.access(external, os.R_OK):
            raise CaddyfileNotFoundError(
                f"{self._caddyfile_path_env} target is not readable: "
                f"{external}"
            )
        self._caddyfile_path = external
        overlay: Dict[str, str] = {}
        for kwarg_name, env_name in self._env_overlay_mapping.items():
            if kwarg_name not in self._template_kwargs:
                continue
            overlay[env_name] = str(self._template_kwargs[kwarg_name])
        self._spawn_env = overlay
        logger.info(
            "Using external Caddyfile at %s (skipping template)",
            external,
        )

    def _spawn_locked(self) -> None:
        """Spawn Caddy under the process lock."""
        proc = _proc.spawn(
            self._binary_path, self._caddyfile_path, env=self._spawn_env,
        )
        with self._process_lock:
            self._process = proc

    def _watchdog_loop(self) -> None:
        """Poll Caddy and restart on crash within the retry budget."""
        last_spawn_monotonic = time.monotonic()
        restart_attempts = 0
        while not self._shutdown_event.is_set():
            if self._shutdown_event.wait(WATCHDOG_POLL_SECONDS):
                return
            with self._process_lock:
                proc = self._process
            if proc is None:
                continue
            exit_code = proc.poll()
            if exit_code is None:
                restart_attempts = self._maybe_reset_counter(
                    restart_attempts, last_spawn_monotonic,
                )
                continue
            if self._shutdown_event.is_set():
                return
            last_spawn_monotonic, restart_attempts, stop = (
                self._handle_crash(exit_code, restart_attempts)
            )
            if stop:
                return

    def _maybe_reset_counter(
        self, restart_attempts: int, last_spawn_monotonic: float,
    ) -> int:
        """Reset the restart counter if Caddy has been stable long enough."""
        if restart_attempts <= 0:
            return restart_attempts
        uptime = time.monotonic() - last_spawn_monotonic
        if uptime < STABLE_UPTIME_RESET_SECONDS:
            return restart_attempts
        logger.info(
            "Caddy stable for %.0fs; resetting restart counter", uptime,
        )
        return 0

    def _handle_crash(
        self, exit_code: int, restart_attempts: int,
    ) -> tuple[float, int, bool]:
        """Process a detected Caddy exit; return (last_spawn_ts, attempts, stop)."""
        logger.warning(
            "Caddy exited with code %s (attempt %d/%d)",
            exit_code, restart_attempts + 1, MAX_RESTART_ATTEMPTS,
        )
        restart_attempts += 1
        if restart_attempts > MAX_RESTART_ATTEMPTS:
            logger.error(
                "Caddy restart budget exhausted after %d attempts; "
                "signalling supervision error",
                MAX_RESTART_ATTEMPTS,
            )
            self.error_event.set()
            return (time.monotonic(), restart_attempts, True)
        if self._shutdown_event.wait(RESTART_BACKOFF_SECONDS):
            return (time.monotonic(), restart_attempts, True)
        try:
            self._spawn_locked()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to restart Caddy: %s", exc)
            self.error_event.set()
            return (time.monotonic(), restart_attempts, True)
        return (time.monotonic(), restart_attempts, False)
