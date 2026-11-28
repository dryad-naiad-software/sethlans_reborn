# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt-signal state poller for the tray helper (PySide6).

PySide6-based state poller with 2s cadence, 1.5s HTTP timeout,
3-consecutive-failure threshold, and edge-triggered state transitions.
State changes are delivered to the GUI thread via Qt ``Signal`` emits.

Threading model: the polling loop runs in a regular
``threading.Thread`` (daemon, named ``tray-state-poller``).  The
``threading.Event.wait()``-based sleep is retained verbatim — do NOT
replace with ``QWaitCondition``, ``QThread``, or ``QTimer``.  This
class is a ``QObject`` composing a ``threading.Thread`` (not
inheriting from both — Python MRO and Qt's metaclass don't cooperate
with that shape).  Signals are emitted from the worker thread; Qt's
queued-connection semantics marshal slot invocations back to the GUI
thread.

Thread affinity: the ``QObject`` MUST be constructed on the GUI
thread.  ``moveToThread`` is NOT called; affinity stays with the
creator.  This lets ``Qt.AutoConnection`` resolve to
``QueuedConnection`` for cross-thread emits from the polling thread.

Callers start the poller via ``QTimer.singleShot(0, poller.start)``
on the first iteration of the event loop.  Graceful shutdown after
``app.exec()`` returns: ``stop_event.set()`` →
``poller.join(timeout=3.0)`` → ``QObject.disconnect(poller)``
(static form — the zero-arg instance form raises in PySide6).

Signal delivery is ordered and non-coalescing: each state change
produces exactly one ``QMetaCallEvent`` per signal emit.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Optional

from PySide6.QtCore import QObject, Signal

from shared.tray import launcher_watch

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 3
_POLL_INTERVAL_SECONDS = 2.0
_HTTP_TIMEOUT_SECONDS = 1.5
# The status answer is a few hundred bytes; more is refused, never read.
MAX_STATUS_BYTES = 64 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: the status is a 200 from the URL asked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# No proxy (loopback only) and no redirects, like ``launcher.health_probe``.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True)
class ManagerSnapshot:
    """Immutable snapshot of manager-side state."""

    state: str = "starting"
    workers_online: int = 0
    jobs_queued: int = 0
    jobs_rendering: int = 0
    version: str = ""
    boot_id: str = ""
    last_error: str = ""


class QtStatePoller(QObject):
    """State poller emitting Qt signals; worker stays threading.Thread.

    Signals:
      - ``snapshot_changed(ManagerSnapshot)`` — every tick where the
        snapshot changes (includes the first successful tick).
        The tray's notifications come from the launcher's state
        (``shared.tray.phase.edge_notice``), not from here.
      - ``launcher_gone()`` — emitted once when the launcher process
        is detected dead (FR-19c); the connected slot should typically
        call ``QApplication.instance().quit()``.
    """

    snapshot_changed = Signal(ManagerSnapshot)
    launcher_gone = Signal()

    def __init__(
        self,
        loopback_url: str,
        stop_event: threading.Event,
        quit_requested_flag: threading.Event,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._url = loopback_url
        self._stop = stop_event
        self._quit_flag = quit_requested_flag
        self._snapshot = ManagerSnapshot()
        self._consecutive_failures = 0
        self._launcher_gone_emitted = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the polling thread.

        MUST be called on the GUI thread, preferably via
        ``QTimer.singleShot(0, poller.start)`` inside the event loop's
        first iteration — after ``tray.show()`` and immediately before
        ``app.exec()``.  Idempotent: a second call while the thread is
        alive logs a warning and returns.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning(
                "QtStatePoller.start() called while thread is already "
                "running; ignoring.",
            )
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="tray-state-poller",
        )
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        """Convenience: wait for the polling thread to exit."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Thread entry (worker thread)
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:  # pragma: no cover - thread loop
        # Top-level try/except is a survivability guard: keeps the
        # polling thread alive if _tick() (or a synchronous slot)
        # raises an exception _fetch didn't convert.
        while not self._stop.wait(_POLL_INTERVAL_SECONDS):
            try:
                self._tick()
            except Exception:
                logger.exception("QtStatePoller tick failed")
            if self._stop.is_set():
                return

    def _tick(self) -> None:
        if not launcher_watch.is_launcher_alive():
            logger.info("Launcher process is gone; tray will exit.")
            if not self._launcher_gone_emitted:
                self._launcher_gone_emitted = True
                try:
                    self.launcher_gone.emit()
                except Exception:  # pragma: no cover
                    logger.exception("launcher_gone emit failed")
            self._stop.set()
            return
        try:
            data = self._fetch()
        except Exception as exc:
            self._apply_failure(str(exc))
            return
        self._apply_success(data)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _fetch(self) -> dict:
        """One GET of the status (no proxy, no redirects, at most 64 KiB)."""
        req = urllib.request.Request(self._url, method="GET")
        try:
            with _OPENER.open(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                code = resp.getcode()
                if code != 200:
                    raise RuntimeError(f"HTTP {code}")
                raw = resp.read(MAX_STATUS_BYTES + 1)
        except urllib.error.HTTPError as exc:  # a refused redirect, a 4xx / 5xx
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"urlerror: {exc.reason}") from exc
        if len(raw) > MAX_STATUS_BYTES:
            raise RuntimeError(f"status answer larger than {MAX_STATUS_BYTES} bytes")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"bad json: {exc}") from exc

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def _apply_success(self, data: dict) -> None:
        prev = self._snapshot
        # A new boot_id (the manager restarted) passes through 'starting'
        # first, so the menu shows the restart.
        new_boot_id = str(data.get("boot_id", ""))
        if new_boot_id and new_boot_id != prev.boot_id and prev.boot_id:
            transitional = replace(prev, state="starting",
                                   boot_id=new_boot_id)
            self._emit(transitional)
            prev = transitional

        nxt = replace(
            prev,
            state="running",
            workers_online=int(data.get("workers_online", 0)),
            jobs_queued=int(data.get("jobs_queued", 0)),
            jobs_rendering=int(data.get("jobs_rendering", 0)),
            version=str(data.get("version", prev.version)),
            boot_id=new_boot_id or prev.boot_id,
            last_error="",
        )
        self._consecutive_failures = 0
        if self._quit_flag.is_set():
            # Manager returned after a deliberate Quit Manager - clear
            # the flag so future probe failures flag as Error again.
            self._quit_flag.clear()
        self._emit(nxt)

    def _apply_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < _FAILURE_THRESHOLD:
            return
        prev = self._snapshot
        new_state = "stopped" if self._quit_flag.is_set() else "error"
        if prev.state == new_state:
            # Already in the target state: no new snapshot (edge-triggered).
            return
        self._emit(replace(prev, state=new_state, last_error=reason))

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------
    def _emit(self, nxt: ManagerSnapshot) -> None:
        self._snapshot = nxt
        self.snapshot_changed.emit(nxt)

    # ------------------------------------------------------------------
    # Accessors (GUI thread)
    # ------------------------------------------------------------------
    @property
    def snapshot(self) -> ManagerSnapshot:
        """Current snapshot; intended for GUI-thread callers."""
        return self._snapshot
