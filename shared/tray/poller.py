# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt-signal state poller for the tray helper (PySide6).

PySide6-based state poller with 2s cadence, 1.5s HTTP timeout,
3-consecutive-failure threshold, and edge-triggered state transitions
plus setup-complete notification.  State changes are delivered to the
GUI thread via Qt ``Signal`` emits.

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
from shared.tray.notifications import NotificationEvent

logger = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 3
_POLL_INTERVAL_SECONDS = 2.0
_HTTP_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True)
class ManagerSnapshot:
    """Immutable snapshot of manager-side state."""

    state: str = "starting"
    setup_mode: bool = False
    workers_online: int = 0
    jobs_queued: int = 0
    jobs_rendering: int = 0
    version: str = ""
    boot_id: str = ""
    last_error: str = ""


def _notification_for(prev: str, nxt: str) -> Optional[NotificationEvent]:
    """Return a notification event for the given edge, or None."""
    if prev == "starting" and nxt == "running":
        return NotificationEvent(
            "Sethlans Manager", "Manager is running.",
        )
    if prev == "running" and nxt == "error":
        return NotificationEvent(
            "Sethlans Manager",
            "Manager entered error state. Click the tray icon for "
            "details.",
        )
    if prev == "error" and nxt == "running":
        return NotificationEvent(
            "Sethlans Manager", "Manager recovered.",
        )
    return None


class QtStatePoller(QObject):
    """State poller emitting Qt signals; worker stays threading.Thread.

    Signals:
      - ``snapshot_changed(ManagerSnapshot)`` — every tick where the
        snapshot changes (includes the first successful tick).
      - ``state_changed(str, str)`` — each state edge transition as
        ``(prev_state, next_state)``.  Mirrors the legacy
        ``('state_change', prev, nxt)`` queue event.
      - ``notification(NotificationEvent)`` — events derived from
        state transitions or the one-shot setup-complete edge.
      - ``launcher_gone()`` — emitted once when the launcher process
        is detected dead (FR-19c); the connected slot should typically
        call ``QApplication.instance().quit()``.
    """

    snapshot_changed = Signal(ManagerSnapshot)
    state_changed = Signal(str, str)
    notification = Signal(NotificationEvent)
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
        self._setup_notified = False
        self._saw_setup_mode_true = False
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
        req = urllib.request.Request(self._url, method="GET")
        try:
            with urllib.request.urlopen(
                req, timeout=_HTTP_TIMEOUT_SECONDS,
            ) as resp:
                code = resp.getcode()
                if code != 200:
                    raise RuntimeError(f"HTTP {code}")
                raw = resp.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"urlerror: {exc.reason}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"bad json: {exc}") from exc

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def _apply_success(self, data: dict) -> None:
        prev = self._snapshot
        # boot_id change always forces the transition through 'starting'
        # first so the "Starting -> Running" notification fires.
        new_boot_id = str(data.get("boot_id", ""))
        if new_boot_id and new_boot_id != prev.boot_id and prev.boot_id:
            transitional = replace(prev, state="starting",
                                   boot_id=new_boot_id)
            self._emit(prev, transitional)
            prev = transitional

        nxt = replace(
            prev,
            state="running",
            setup_mode=bool(data.get("setup_mode")),
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
        self._emit(prev, nxt)
        self._maybe_notify_setup_complete(nxt)

    def _apply_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures < _FAILURE_THRESHOLD:
            return
        prev = self._snapshot
        new_state = "stopped" if self._quit_flag.is_set() else "error"
        if prev.state == new_state:
            # Already in the target state - just refresh last_error,
            # but do NOT re-emit the notification (edge-triggered).
            return
        nxt = replace(prev, state=new_state, last_error=reason)
        self._emit(prev, nxt)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------
    def _emit(self, prev: ManagerSnapshot, nxt: ManagerSnapshot) -> None:
        self._snapshot = nxt
        self.snapshot_changed.emit(nxt)
        if prev.state != nxt.state:
            self.state_changed.emit(prev.state, nxt.state)
            event = _notification_for(prev.state, nxt.state)
            if event is not None:
                self.notification.emit(event)

    def _maybe_notify_setup_complete(
        self, snapshot: ManagerSnapshot,
    ) -> None:
        # FR-26 edge: fire exactly once, only on a True -> False
        # transition observed during THIS tray session.  If the tray
        # starts after setup is already complete, setup_mode=True is
        # never observed and the notification must stay silent.
        if snapshot.setup_mode:
            self._saw_setup_mode_true = True
            return
        if self._setup_notified or not self._saw_setup_mode_true:
            return
        self._setup_notified = True
        self.notification.emit(NotificationEvent(
            "Sethlans Setup",
            "Setup complete. Open the dashboard from the tray "
            "menu.",
        ))

    # ------------------------------------------------------------------
    # Accessors (GUI thread)
    # ------------------------------------------------------------------
    @property
    def snapshot(self) -> ManagerSnapshot:
        """Current snapshot; intended for GUI-thread callers."""
        return self._snapshot
