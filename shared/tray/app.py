# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""PySide6 tray bootstrap (FR-LN-4).

The tray follows the launcher: a state QTimer (GUI thread) runs
``app_phase.tick`` each second, which reads ``launcher_state.json`` and
``setup_finished()`` and rebuilds the menu when the phase changed. The
launcher's state is the source of truth; the tray never decides it.

The :class:`QtStatePoller` thread polls the manager's loopback status
for the manager section's counts, and watches the launcher process
(``launcher_gone`` quits the tray). It runs in every mode, so a
worker-only tray also exits with its launcher. Its own notifications
are not shown: the launcher's state and reason give them
(``phase.edge_notice``).

Shutdown after ``app.exec()``: the timer stops, then ``stop_event.set()``
-> ``poller.join(timeout=3.0)`` -> ``QObject.disconnect(poller)`` (the
static form; the zero-arg instance form raises ``TypeError`` in
PySide6).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from shared.settings_file import get_settings_dir, read_settings, settings_path
from shared.tray import app_phase, launcher_watch, notifications
from shared.tray import launcher_state as ls
from shared.tray.notifications import NotificationEvent
from shared.tray.phase import Phase, read_phase
from shared.tray.poller import ManagerSnapshot, QtStatePoller

logger = logging.getLogger(__name__)

_DEFAULT_LOOPBACK_PORT = 8088
_LOOPBACK_KEYS = ("waitress_loopback_port_internal", "loopback_port")
STATE_POLL_MS = 1000
_TOOLTIP = "Sethlans"


def read_loopback_port(ini: Path) -> int:
    """The manager's loopback status port from the settings file, else 8088.

    A value that is not a port number (1..65535) is logged and 8088 is used.
    """
    try:
        cfg = read_settings(ini)
        for key in _LOOPBACK_KEYS:
            if cfg.has_option("server", key):
                port = cfg.getint("server", key)
                if not 1 <= port <= 65535:
                    raise ValueError(f"[server] {key} = {port} is not a port number")
                return port
    except (OSError, ValueError) as exc:
        logger.warning("Could not read the loopback port from %s: %s", ini.name, exc)
    return _DEFAULT_LOOPBACK_PORT


def _configure_logging() -> None:
    try:
        from launcher.logging_setup import configure as _cfg_log
        _cfg_log("tray", data_dir=get_settings_dir())
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=logging.DEBUG)


class _TrayContext:
    """The tray's state: the phase, its sections and menu, the poller."""

    def __init__(self) -> None:
        self.settings_dir = get_settings_dir()
        self.stop_event = threading.Event()
        self.quit_flag = threading.Event()
        self.tray: Optional[QSystemTrayIcon] = None
        self.poller: Optional[QtStatePoller] = None
        self.phase = Phase(ls.DEFAULT_STATE.state)
        self.sections: list = []
        self.menu = None
        self.previous_menu = None

    def current_snapshot(self) -> ManagerSnapshot:
        if self.poller is None:
            return ManagerSnapshot()
        return self.poller.snapshot

    def notify(self, event: NotificationEvent) -> None:
        notifications.dispatch(self.tray, event)


def _connect_poller(ctx: _TrayContext, tray: QSystemTrayIcon, app: QApplication) -> None:
    """Snapshots refresh the icon and sections; ``launcher_gone`` quits."""

    def on_snapshot(snapshot: ManagerSnapshot) -> None:
        del snapshot  # the sections read ctx.current_snapshot()
        app_phase.refresh_icon(ctx, tray)
        app_phase.refresh_sections(ctx)

    ctx.poller.snapshot_changed.connect(on_snapshot, type=Qt.QueuedConnection)
    ctx.poller.launcher_gone.connect(app.quit, type=Qt.QueuedConnection)


def _guarded(what: str, step, *args, **kwargs) -> None:
    try:
        step(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("%s failed during shutdown", what)


def _shutdown(ctx: _TrayContext, timer: Optional[QTimer] = None) -> None:
    """Stop the timer and the poller; each step guarded; never raises."""
    if timer is not None:
        _guarded("timer.stop()", timer.stop)
    _guarded("stop_event.set()", ctx.stop_event.set)
    poller = ctx.poller
    if poller is not None:
        _guarded("poller.join()", poller.join, timeout=3.0)
        _guarded("QObject.disconnect(poller)", QObject.disconnect, poller)  # static form only


def main() -> None:
    _configure_logging()
    logger.debug("Tray starting; pid=%d", os.getpid())
    launcher_watch.init()

    app = QApplication(sys.argv)
    # Brand Qt so taskbar/Alt+Tab read "Sethlans" (issue #106).
    app.setApplicationName("Sethlans")
    app.setApplicationDisplayName("Sethlans")
    app.setOrganizationName("Dryad and Naiad Software LLC")
    app.setQuitOnLastWindowClosed(False)

    ctx = _TrayContext()
    tray = QSystemTrayIcon()
    tray.setToolTip(_TOOLTIP)
    ctx.tray = tray
    lb_port = read_loopback_port(settings_path())
    ctx.poller = QtStatePoller(
        loopback_url=f"http://127.0.0.1:{lb_port}/api/status/public/",
        stop_event=ctx.stop_event,
        quit_requested_flag=ctx.quit_flag,
    )
    # The first menu already matches the launcher's state.
    app_phase.install(ctx, tray, read_phase(ctx.settings_dir))
    logger.info("Tray phase: %s", ctx.phase.state)
    _connect_poller(ctx, tray, app)

    timer = QTimer()
    timer.setInterval(STATE_POLL_MS)
    timer.timeout.connect(lambda: app_phase.tick(ctx, tray))
    # Start inside the event loop's first iteration (the poller's
    # cross-thread signals need the loop running).
    QTimer.singleShot(0, ctx.poller.start)
    QTimer.singleShot(0, timer.start)

    tray.show()
    try:
        app.exec()
    finally:
        _shutdown(ctx, timer)
