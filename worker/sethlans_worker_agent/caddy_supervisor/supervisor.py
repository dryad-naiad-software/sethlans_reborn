# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caddy supervisor lifecycle (start / stop / watchdog).

See ``sethlans_worker_agent/caddy_supervisor/__init__.py`` for the
public API overview and supervision semantics.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Union

from sethlans_worker_agent.caddy_template import render_worker_caddyfile
from sethlans_worker_agent.caddy_supervisor import process as _proc
from sethlans_worker_agent.caddy_supervisor.io import atomic_write_text

logger = logging.getLogger(__name__)

# Lifecycle constants (mirror manager lifecycle table).
MAX_RESTART_ATTEMPTS = 3
RESTART_BACKOFF_SECONDS = 1.0
STABLE_UPTIME_RESET_SECONDS = 60.0
WATCHDOG_POLL_SECONDS = 0.5
SHUTDOWN_DRAIN_DEFAULT_SECONDS = 10.0

# When set, swaps templating for a pre-baked external Caddyfile
# (Docker images, Phase 5c). Caddy resolves ``{$VAR}`` placeholders
# from its process env. Unset on native dev / frozen-install.
CADDYFILE_PATH_ENV = "SETHLANS_WORKER_CADDYFILE_PATH"


class CaddyBinaryNotFoundError(RuntimeError):
    """Raised when the Caddy binary is missing or not executable."""


class CaddyfileNotFoundError(RuntimeError):
    """Raised when ``$SETHLANS_WORKER_CADDYFILE_PATH`` is missing/unreadable."""


class CaddySupervisor:
    """Supervise a Caddy subprocess for the worker agent.

    The supervisor owns: Caddyfile templating/write, subprocess spawn,
    watchdog thread for crash-restart, and graceful shutdown.
    """

    def __init__(
        self,
        *,
        binary_path: Path,
        caddyfile_path: Path,
        public_tls_port: int,
        loopback_plaintext_port: int,
        waitress_upstream_port: int,
        cert_path: Union[str, Path],
        key_path: Union[str, Path],
        worker_data_dir: Union[str, Path],
    ) -> None:
        self._binary_path = Path(binary_path)
        self._caddyfile_path = Path(caddyfile_path)
        self._template_kwargs = {
            "public_tls_port": public_tls_port,
            "loopback_plaintext_port": loopback_plaintext_port,
            "waitress_upstream_port": waitress_upstream_port,
            "cert_path": cert_path,
            "key_path": key_path,
            "worker_data_dir": worker_data_dir,
        }
        self._process: Optional[subprocess.Popen] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._process_lock = threading.Lock()
        # Populated with the env-var overlay passed to Caddy when the
        # external-Caddyfile branch is active; ``None`` means "inherit
        # parent env unchanged" (native-dev / frozen-install).
        self._spawn_env: Optional[dict] = None

        #: Set when supervision has given up (retry budget exhausted).
        #: The agent main loop polls this flag and exits 1 on detection.
        self.error_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Template Caddyfile, spawn Caddy, start watchdog thread.

        If ``$SETHLANS_WORKER_CADDYFILE_PATH`` is set, the supervisor
        uses that path verbatim and SKIPS rendering/writing — the Docker
        image ships a pre-baked static Caddyfile whose ``{$VAR}``
        placeholders Caddy resolves from its process environment. In
        that branch we also merge the template inputs into Caddy's
        spawn env so the placeholders have values to resolve.

        :raises CaddyBinaryNotFoundError: binary missing / not executable.
        :raises CaddyfileNotFoundError: external path missing/unreadable.
        :raises ValueError: template inputs fail validation (native only).
        """
        self._validate_binary()
        external_path = os.environ.get(CADDYFILE_PATH_ENV)
        if external_path:
            self._use_external_caddyfile(Path(external_path))
        else:
            content = render_worker_caddyfile(**self._template_kwargs)
            atomic_write_text(self._caddyfile_path, content)
            logger.info(
                "Worker Caddyfile written to %s", self._caddyfile_path,
            )
        self._spawn_locked()

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="caddy-supervisor-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self, timeout: float = SHUTDOWN_DRAIN_DEFAULT_SECONDS) -> None:
        """Stop Caddy gracefully; escalate to kill on timeout.

        Signals shutdown to the watchdog first so a post-SIGTERM exit
        does not trigger a spurious restart. Sends SIGTERM (POSIX) or
        CTRL_BREAK_EVENT (Windows), waits up to ``timeout`` seconds,
        then terminates / kills. Finally joins the watchdog thread.
        """
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
        """Switch to an operator-supplied Caddyfile (Docker case).

        Validates the file exists and is readable, then stages the
        cert/key/port overlay merged into Caddy's spawn env so the
        static Caddyfile's ``{$VAR}`` placeholders resolve.
        """
        if not external.is_file():
            raise CaddyfileNotFoundError(
                f"{CADDYFILE_PATH_ENV} points to a non-existent file: "
                f"{external}"
            )
        if not os.access(external, os.R_OK):
            raise CaddyfileNotFoundError(
                f"{CADDYFILE_PATH_ENV} target is not readable: {external}"
            )
        self._caddyfile_path = external
        tk = self._template_kwargs
        self._spawn_env = {
            "SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT": str(tk["public_tls_port"]),
            "SETHLANS_WORKER_CADDY_LOOPBACK_PORT": str(tk["loopback_plaintext_port"]),
            "SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT": str(tk["waitress_upstream_port"]),
            "SETHLANS_WORKER_CERT_PATH": str(tk["cert_path"]),
            "SETHLANS_WORKER_KEY_PATH": str(tk["key_path"]),
        }
        logger.info(
            "Using external worker Caddyfile at %s (skipping template)",
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
        """Poll Caddy for crashes and restart within the retry budget.

        Restart counter resets after ``STABLE_UPTIME_RESET_SECONDS`` of
        stable uptime, which prevents a long-running worker from burning
        its retries on rare transient crashes.
        """
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
        """Process a detected Caddy exit; return (last_spawn_ts, attempts, stop).

        ``stop`` is True when the watchdog should exit its loop (either
        retry budget exhausted or shutdown signalled during backoff).
        """
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
