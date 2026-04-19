# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""State poller for the tray helper.

Runs as a daemon thread at 2s cadence.  Performs the following per
tick:

1. Checks ``is_launcher_alive()`` — calls ``icon.stop()`` and exits if
   the launcher is gone (orphan prevention, FR-19c).
2. Issues a plaintext HTTP GET against
   ``http://127.0.0.1:<loopback_port>/api/status/public/``.
3. Updates an in-memory ``ManagerSnapshot`` via
   ``dataclasses.replace`` (never ``__dict__`` mutation, per FR-15).
4. Enqueues ``('snapshot', snapshot)`` plus, on edge transitions,
   ``('state_change', prev, nxt)`` and ``('notification', event)``
   into the thread-safe ``queue.Queue`` drained by the pystray main
   thread.
5. Three consecutive probe failures transition manager_state to
   ``error`` — unless ``quit_requested_flag`` is set, in which case
   the transition is to ``stopped`` (FR-17a).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Optional

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


class StatePoller(threading.Thread):
    """Daemon thread polling the manager loopback endpoint."""

    def __init__(
        self,
        loopback_url: str,
        stop_event: threading.Event,
        update_queue: "queue.Queue",
        quit_requested_flag: threading.Event,
        on_launcher_dead=None,
    ) -> None:
        super().__init__(daemon=True, name="tray-state-poller")
        self._url = loopback_url
        self._stop = stop_event
        self._queue = update_queue
        self._quit_flag = quit_requested_flag
        self._snapshot = ManagerSnapshot()
        self._consecutive_failures = 0
        self._setup_notified = False
        self._saw_setup_mode_true = False
        self._on_launcher_dead = on_launcher_dead

    # ------------------------------------------------------------------
    # Thread entry
    # ------------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - thread loop
        while not self._stop.wait(_POLL_INTERVAL_SECONDS):
            self._tick()
            if self._stop.is_set():
                return

    def _tick(self) -> None:
        if not launcher_watch.is_launcher_alive():
            logger.info("Launcher process is gone; tray will exit.")
            if self._on_launcher_dead is not None:
                try:
                    self._on_launcher_dead()
                except Exception:  # pragma: no cover
                    logger.exception("on_launcher_dead callback failed")
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
        self._queue.put(("snapshot", nxt))
        if prev.state != nxt.state:
            self._queue.put(("state_change", prev.state, nxt.state))
            event = _notification_for(prev.state, nxt.state)
            if event is not None:
                self._queue.put(("notification", event))

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
        self._queue.put((
            "notification",
            NotificationEvent(
                "Sethlans Setup",
                "Setup complete. Open the dashboard from the tray "
                "menu.",
            ),
        ))

    # ------------------------------------------------------------------
    # Accessors (main thread)
    # ------------------------------------------------------------------
    @property
    def snapshot(self) -> ManagerSnapshot:
        return self._snapshot
