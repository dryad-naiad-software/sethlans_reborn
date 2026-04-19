# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Top-level tray helper wiring.

Invoked by ``shared/run_tray.py``.  Responsibilities:

1. Load topology.json.
2. Resolve data dirs + loopback port (read from ``manager.ini``).
3. Construct the state poller + section objects.
4. Build the pystray menu and start the icon event loop.
5. Drain the poller's update queue from the main thread — this is
   where icon mutations and notification dispatches actually happen
   (FR-15 / FR-26a).
"""

from __future__ import annotations

import configparser
import logging
import queue
import threading
from pathlib import Path

from shared.frozen_paths import get_data_dir, get_shared_data_dir
from shared.tray import icons, launcher_watch, topology as topo_mod
from shared.tray.menu_manager import ManagerSection
from shared.tray.menu_worker import WorkerSection
from shared.tray.notifications import dispatch as dispatch_notification
from shared.tray.poller import ManagerSnapshot, StatePoller

logger = logging.getLogger(__name__)

_DEFAULT_MAIN_PORT = 8080
_DEFAULT_LOOPBACK_PORT = 8088
_DEFAULT_HOST = "localhost"


def _read_manager_ports(manager_data_dir: Path) -> tuple[str, int, int]:
    """Return ``(host, main_port, loopback_port)`` from manager.ini."""
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


def _drain_queue(q: "queue.Queue", icon, update_menu) -> None:
    """Drain all pending events from the poller's queue.

    Runs on the pystray main thread.
    """
    try:
        while True:
            event = q.get_nowait()
            kind = event[0]
            if kind == "snapshot":
                update_menu()
            elif kind == "state_change":
                update_menu()
            elif kind == "notification":
                dispatch_notification(event[1])
    except queue.Empty:
        return


class _TrayContext:
    """All tray state bundled so ``main`` stays under the complexity cap."""

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
        self.loopback_url = (
            f"http://127.0.0.1:{lb_port}/api/status/public/"
        )
        self.update_queue: "queue.Queue" = queue.Queue()
        self.stop_event = threading.Event()
        self.quit_flag = threading.Event()
        self.snapshot = {"value": ManagerSnapshot()}
        self.wants_manager = self.topology in (
            topo_mod.TOPOLOGY_MANAGER, topo_mod.TOPOLOGY_BOTH,
        )
        self.wants_worker = self.topology in (
            topo_mod.TOPOLOGY_WORKER, topo_mod.TOPOLOGY_BOTH,
        )
        self.manager_section = self._make_manager_section()
        self.worker_section = self._make_worker_section()
        self.poller = StatePoller(
            loopback_url=self.loopback_url,
            stop_event=self.stop_event,
            update_queue=self.update_queue,
            quit_requested_flag=self.quit_flag,
        )

    def get_snapshot(self) -> ManagerSnapshot:
        return self.snapshot["value"]

    def _make_manager_section(self):
        if not self.wants_manager:
            return None
        return ManagerSection(
            data_dir=self.data_dir,
            manager_data_dir=self.manager_data_dir,
            manager_host=self.host,
            manager_port=self.main_port,
            quit_requested_flag=self.quit_flag,
            get_snapshot=self.get_snapshot,
        )

    def _make_worker_section(self):
        if not self.wants_worker:
            return None
        return WorkerSection(
            data_dir=self.worker_data_dir.parent,
            quit_requested_flag=self.quit_flag,
        )

    def icon_image(self):
        mgr = self.get_snapshot().state if self.wants_manager else None
        wk = "idle" if self.wants_worker else None
        try:
            return icons.get_icon(mgr, wk)
        except Exception:  # pragma: no cover
            logger.exception("Failed to render icon")
            return None

    def build_menu(self, pystray):
        self.snapshot["value"] = self.poller.snapshot
        items: list = []
        if self.manager_section is not None:
            items.extend(_manager_items(self.manager_section, pystray))
        if self.manager_section and self.worker_section:
            items.append(pystray.Menu.SEPARATOR)
        if self.worker_section is not None:
            items.extend(_worker_items(self.worker_section, pystray))
        return pystray.Menu(*items)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    launcher_watch.init()
    try:
        import pystray  # type: ignore[import-not-found]
    except Exception:
        logger.error(
            "pystray unavailable; tray cannot start.  The launcher "
            "will continue without a tray icon.",
        )
        return

    ctx = _TrayContext()
    logger.info("Tray topology: %s", ctx.topology)
    if ctx.wants_manager:
        ctx.poller.start()

    icon = pystray.Icon(
        "sethlans", icon=ctx.icon_image(),
        title="Sethlans", menu=ctx.build_menu(pystray),
    )

    def _pump(icon_arg) -> None:
        """Run on pystray's main thread via ``icon.run(setup=...)``.

        All icon.icon / icon.update_menu mutations and notification
        dispatches happen here — mandatory on macOS AppKit and Linux
        GTK, where UI mutations must occur on the thread that owns the
        icon event loop.
        """
        icon_arg.visible = True
        while not ctx.stop_event.is_set():
            _drain_queue(ctx.update_queue, icon_arg, icon_arg.update_menu)
            img = ctx.icon_image()
            if img is not None:
                icon_arg.icon = img
            if ctx.stop_event.wait(timeout=1.0):
                break
        icon_arg.stop()

    try:
        icon.run(setup=_pump)
    finally:
        ctx.stop_event.set()


def _manager_items(section: ManagerSection, pystray):
    MI = pystray.MenuItem
    items = [
        MI(lambda _i: section.header_text(), None, enabled=False),
        MI(lambda _i: section.setup_line(), None, enabled=False),
        MI(lambda _i: section.workers_line(), None,
           enabled=False, visible=lambda _i: section.counts_visible()),
        MI(lambda _i: section.jobs_line(), None,
           enabled=False, visible=lambda _i: section.counts_visible()),
        pystray.Menu.SEPARATOR,
        MI("Open Dashboard", section.on_open_dashboard),
        MI("Copy Setup Token", section.on_copy_token,
           visible=lambda _i: section.setup_token_available()),
        MI("Open Setup Wizard", section.on_open_wizard,
           visible=lambda _i: section.wizard_visible()),
        MI("Restart Manager", section.on_restart_manager,
           enabled=lambda _i: section.restart_enabled()),
        MI("View Manager Logs", section.on_view_logs),
        MI("Quit Manager", section.on_quit_manager,
           enabled=lambda _i: section.quit_enabled()),
        pystray.Menu.SEPARATOR,
        MI(lambda _i: section.footer_text(), None, enabled=False),
    ]
    return items


def _worker_items(section: WorkerSection, pystray):
    MI = pystray.MenuItem
    items = [
        MI(lambda _i: section.header_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        MI("Open Sethlans Status", section.on_open_worker_status),
        MI("Quit Sethlans", section.on_quit_sethlans,
           enabled=lambda _i: section.quit_sethlans_enabled()),
    ]
    return items
