# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""PySide6 tray bootstrap.

Qt replacement for :mod:`shared.tray.app`.  Configures logging and
the launcher watchdog, constructs a :class:`QApplication`, a
:class:`QSystemTrayIcon`, and a ``_TrayContext`` holding the
topology-derived sections and the :class:`QtStatePoller`.  Wires the
poller's Qt signals to GUI-thread slots with explicit
:attr:`Qt.QueuedConnection` (spec FR-6), starts the poller via
``QTimer.singleShot(0, ...)`` so ``start`` runs inside the event
loop's first iteration (FR-6), runs the event loop, then performs the
documented shutdown sequence: ``stop_event.set()`` →
``poller.join(timeout=3.0)`` → ``QObject.disconnect(poller)`` (static
form only — the zero-arg instance form raises ``TypeError`` in
PySide6).
"""

from __future__ import annotations

import configparser
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from shared.frozen_paths import get_data_dir, get_shared_data_dir
from shared.tray import launcher_watch, qt_icons, qt_notifications
from shared.tray import topology as topo_mod
from shared.tray.qt_menu_manager import ManagerSection
from shared.tray.qt_menu_worker import WorkerSection
from shared.tray.qt_notifications import NotificationEvent
from shared.tray.qt_poller import ManagerSnapshot, QtStatePoller

logger = logging.getLogger(__name__)

_DEFAULT_MAIN_PORT = 8080
_DEFAULT_LOOPBACK_PORT = 8088
_DEFAULT_HOST = "localhost"
_TOOLTIP = "Sethlans"


def _read_manager_ports(manager_data_dir: Path) -> tuple[str, int, int]:
    """Return ``(host, main_port, loopback_port)`` from ``manager.ini``.

    Mirrors the legacy ``shared.tray.app._read_manager_ports`` behavior
    verbatim — same defaults, same fallback on parse error.  Kept here
    (rather than imported from the legacy module) so Phase 9 can
    delete ``shared/tray/app.py`` cleanly.
    """
    host = _DEFAULT_HOST
    main_port = _DEFAULT_MAIN_PORT
    loopback_port = _DEFAULT_LOOPBACK_PORT
    ini = manager_data_dir / "manager.ini"
    if not ini.exists():
        return host, main_port, loopback_port
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        logger.warning("Could not parse manager.ini: %s", exc)
        return host, main_port, loopback_port
    if cfg.has_section("server"):
        main_port = cfg.getint("server", "port", fallback=main_port)
        loopback_port = cfg.getint(
            "server", "loopback_port", fallback=loopback_port,
        )
    return host, main_port, loopback_port


def _configure_logging() -> None:
    try:
        from launcher.logging_setup import configure as _cfg_log
        _cfg_log("tray", data_dir=get_shared_data_dir())
    except Exception:
        logging.basicConfig(level=logging.DEBUG)


class _TrayContext:
    """Bundle of tray state, separated from ``main`` to cap complexity.

    Holds the topology-derived sections, the poller, the stop event,
    and any other objects ``main`` needs to wire signals and run the
    documented shutdown sequence.
    """

    def __init__(self) -> None:
        self.data_dir = get_shared_data_dir()
        self.manager_data_dir = get_data_dir("manager")
        self.worker_data_dir = get_data_dir("worker")
        self.topology = topo_mod.read_topology(self.data_dir)
        host, main_port, lb_port = _read_manager_ports(
            self.manager_data_dir,
        )
        self.host = host
        self.main_port = main_port
        self.loopback_port = lb_port
        self.loopback_url = (
            f"http://127.0.0.1:{lb_port}/api/status/public/"
        )
        self.stop_event = threading.Event()
        self.quit_flag = threading.Event()
        self.wants_manager = self.topology in (
            topo_mod.TOPOLOGY_MANAGER, topo_mod.TOPOLOGY_BOTH,
        )
        self.wants_worker = self.topology in (
            topo_mod.TOPOLOGY_WORKER, topo_mod.TOPOLOGY_BOTH,
        )
        # Placeholders populated by ``wire(tray)`` once the tray icon
        # exists (the ManagerSection's notify closure captures it).
        self.manager_section: Optional[ManagerSection] = None
        self.worker_section: Optional[WorkerSection] = None
        self.poller: Optional[QtStatePoller] = None

    def current_snapshot(self) -> ManagerSnapshot:
        if self.poller is None:
            return ManagerSnapshot()
        return self.poller.snapshot

    def icon_states(self) -> tuple[Optional[str], Optional[str]]:
        mgr = self.current_snapshot().state if self.wants_manager else None
        wk = "idle" if self.wants_worker else None
        return mgr, wk


def _build_sections(ctx: _TrayContext, tray: QSystemTrayIcon) -> None:
    """Populate ``ctx.manager_section`` / ``worker_section`` / ``poller``.

    Must be called AFTER the tray icon exists so the notify closure
    can capture it.  The poller is constructed here on the GUI thread;
    its thread affinity is the creating thread (spec FR-6).
    """

    def _notify(event: NotificationEvent) -> None:
        qt_notifications.dispatch(tray, event)

    if ctx.wants_manager:
        ctx.manager_section = ManagerSection(
            data_dir=ctx.data_dir,
            manager_data_dir=ctx.manager_data_dir,
            manager_host=ctx.host,
            manager_port=ctx.main_port,
            quit_requested_flag=ctx.quit_flag,
            get_snapshot=ctx.current_snapshot,
            notify=_notify,
        )
    if ctx.wants_worker:
        # Worker-only topology: the manager section is absent, so the
        # About Sethlans action must live on the worker section (spec
        # FR-2 sub-bullet).  When both sections are present the
        # manager owns About.
        worker_only = ctx.wants_worker and not ctx.wants_manager
        ctx.worker_section = WorkerSection(
            data_dir=ctx.worker_data_dir.parent,
            quit_requested_flag=ctx.quit_flag,
            include_about=worker_only,
        )
    ctx.poller = QtStatePoller(
        loopback_url=ctx.loopback_url,
        stop_event=ctx.stop_event,
        quit_requested_flag=ctx.quit_flag,
    )


def _build_menu(ctx: _TrayContext) -> QMenu:
    """Build the combined QMenu from the topology-appropriate sections.

    Both sections (when present) have their QActions parented to the
    same top-level QMenu.  A separator is inserted between them when
    both are present (mirroring the legacy pystray layout).
    """
    root = QMenu()
    if ctx.manager_section is not None:
        mgr_menu = ctx.manager_section.build_qmenu(parent=root)
        for action in mgr_menu.actions():
            root.addAction(action)
    if ctx.manager_section is not None and ctx.worker_section is not None:
        root.addSeparator()
    if ctx.worker_section is not None:
        worker_menu = ctx.worker_section.build_qmenu(parent=root)
        for action in worker_menu.actions():
            root.addAction(action)
    return root


def _refresh_icon(ctx: _TrayContext, tray: QSystemTrayIcon) -> None:
    try:
        mgr, wk = ctx.icon_states()
        pixmap = qt_icons.get_icon(mgr, wk)
        tray.setIcon(QIcon(pixmap))
    except Exception:
        logger.exception("Failed to refresh tray icon")


def _refresh_sections(ctx: _TrayContext) -> None:
    # Sections mutate held QAction text / visible / enabled in place
    # — no QMenu rebuild needed (spec "Technical Approach").
    if ctx.manager_section is not None:
        try:
            ctx.manager_section.refresh()
        except Exception:
            logger.exception("ManagerSection.refresh failed")
    if ctx.worker_section is not None:
        try:
            ctx.worker_section.refresh()
        except Exception:
            logger.exception("WorkerSection.refresh failed")


def _connect_signals(
    ctx: _TrayContext, tray: QSystemTrayIcon, app: QApplication,
) -> None:
    """Wire poller signals to GUI-thread slots via QueuedConnection."""

    def on_snapshot(snapshot: ManagerSnapshot) -> None:
        del snapshot  # predicates read from ctx.current_snapshot()
        _refresh_icon(ctx, tray)
        _refresh_sections(ctx)

    def on_notification(event: NotificationEvent) -> None:
        qt_notifications.dispatch(tray, event)

    poller = ctx.poller
    assert poller is not None  # _build_sections populates this
    poller.snapshot_changed.connect(on_snapshot, Qt.QueuedConnection)
    poller.notification.connect(on_notification, Qt.QueuedConnection)
    poller.launcher_gone.connect(app.quit, Qt.QueuedConnection)


def _shutdown(ctx: _TrayContext) -> None:
    """Run the documented shutdown sequence; never raises.

    Spec "Shutdown sequence": ``stop_event.set()`` →
    ``poller.join(timeout=3.0)`` → ``QObject.disconnect(poller)``
    (static form only).  Each step is guarded so a failure in one
    does not skip the next.
    """
    try:
        ctx.stop_event.set()
    except Exception:
        logger.exception("stop_event.set() failed during shutdown")

    poller = ctx.poller
    if poller is not None:
        try:
            poller.join(timeout=3.0)
        except Exception:
            logger.exception("poller.join() failed during shutdown")
        try:
            # STATIC FORM ONLY — zero-arg instance form raises
            # TypeError in PySide6.
            QObject.disconnect(poller)
        except Exception:
            logger.exception(
                "QObject.disconnect(poller) failed during shutdown",
            )


def main() -> None:
    _configure_logging()
    import os
    logger.debug("Tray starting; pid=%d", os.getpid())
    launcher_watch.init()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    ctx = _TrayContext()
    logger.info("Tray topology: %s", ctx.topology)

    tray = QSystemTrayIcon()
    mgr, wk = ctx.icon_states()
    tray.setIcon(QIcon(qt_icons.get_icon(mgr, wk)))
    tray.setToolTip(_TOOLTIP)

    _build_sections(ctx, tray)
    tray.setContextMenu(_build_menu(ctx))
    _connect_signals(ctx, tray, app)

    # Start the poller inside the event loop's first iteration so we
    # don't rely on undocumented ordering of cross-thread signal
    # queuing before exec() (spec FR-6).  Only start when the manager
    # section is present — worker-only topologies have no manager
    # state to poll.
    if ctx.wants_manager and ctx.poller is not None:
        QTimer.singleShot(0, ctx.poller.start)

    tray.show()
    try:
        app.exec()
    finally:
        _shutdown(ctx)
