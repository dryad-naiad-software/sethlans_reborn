# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The manager section of the ``RUNNING`` tray menu (FR-LN-4)::

    [Manager] Running
    Workers online: N                 (while the manager answers)
    Jobs queued: N  |  Rendering: N
    ---
    Open Dashboard / Restart Manager / View Manager Logs / Reset setup...
    About Sethlans
    ---
    Quit Manager
    ---
    v<version> -- :<port>

``refresh()`` re-reads the poller's snapshot for the header, counts
and footer, and greys out Restart, Reset and Quit while their request
is in flight. The port is the launcher's (``launcher_state.json``).
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from shared.tray import ipc, reset_confirm
from shared.tray.about import show_about_dialog
from shared.tray.menu_manager_helpers import open_manager_log
from shared.tray.menu_universal import RESET_LABEL, add_action, add_label, add_universal_actions, set_enabled
from shared.tray.notifications import NotificationEvent

logger = logging.getLogger(__name__)


class ManagerSection:
    """Manager-section menu state + callbacks for the Qt tray."""

    def __init__(
        self,
        data_dir: Path,
        manager_port: Optional[int],
        quit_requested_flag: threading.Event,
        get_snapshot: Callable,
        notify: Optional[Callable[[NotificationEvent], None]] = None,
        manager_host: str = "localhost",
    ) -> None:
        self.data_dir = Path(data_dir)
        self.host = manager_host
        self.port = manager_port
        self.quit_flag = quit_requested_flag
        self.get_snapshot = get_snapshot
        self._notify = notify

        # QAction references held so refresh() can mutate them.
        self._menu: Optional[QMenu] = None
        self._act_header: Optional[QAction] = None
        self._act_workers: Optional[QAction] = None
        self._act_jobs: Optional[QAction] = None
        self._act_restart: Optional[QAction] = None
        self._act_reset: Optional[QAction] = None
        self._act_quit: Optional[QAction] = None
        self._act_footer: Optional[QAction] = None

    # ---- click callbacks (no-arg; QAction.triggered signal) ----
    def on_open_dashboard(self) -> None:
        if self.port is not None:
            webbrowser.open(f"https://{self.host}:{self.port}/")

    def on_restart_manager(self) -> None:
        try:
            ipc.request_restart(self.data_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("request_restart failed: %s", exc)

    def on_view_logs(self) -> None:
        open_manager_log()

    def on_reset(self) -> None:
        reset_confirm.ask_reset(self.data_dir, self._notify)

    def on_quit_manager(self) -> None:
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="manager")
        except Exception as exc:  # noqa: BLE001
            logger.warning("request_quit(manager) failed: %s", exc)

    def on_about(self) -> None:
        show_about_dialog(self._menu)

    # ---- dynamic text ----
    def header_text(self) -> str:
        snap = self.get_snapshot()
        state = snap.state
        if state == "starting":
            return "[Manager] Starting..."
        if state == "running":
            return "[Manager] Running"
        if state == "stopped":
            return "[Manager] Stopped"
        if state == "error":
            return f"[Manager] Error: {snap.last_error[:40]}"
        return f"[Manager] {state}"

    def workers_line(self) -> str:
        return f"Workers online: {self.get_snapshot().workers_online}"

    def jobs_line(self) -> str:
        snap = self.get_snapshot()
        return f"Jobs queued: {snap.jobs_queued}  |  Rendering: {snap.jobs_rendering}"

    def footer_text(self) -> str:
        version = self.get_snapshot().version or "?"
        return f"v{version} -- :{self.port}" if self.port is not None else f"v{version}"

    # ---- menu construction ----
    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Build a fresh QMenu; ``refresh`` then works on its actions."""
        menu = QMenu(parent)
        self._act_header = add_label(menu, "")
        self._act_workers = add_label(menu, "")
        self._act_jobs = add_label(menu, "")
        menu.addSeparator()
        add_action(menu, "Open Dashboard", self.on_open_dashboard).setEnabled(self.port is not None)
        self._act_restart = add_action(menu, "Restart Manager", self.on_restart_manager)
        add_action(menu, "View Manager Logs", self.on_view_logs)
        self._act_reset = add_action(menu, RESET_LABEL, self.on_reset)
        _, self._act_quit = add_universal_actions(menu, self.on_about, self.on_quit_manager, quit_label="Quit Manager")
        menu.addSeparator()
        self._act_footer = add_label(menu, "")
        self._menu = menu
        self.refresh()
        return menu

    def refresh(self, snapshot=None) -> None:
        """Re-evaluate text / visibility / enabled on all QActions.

        ``snapshot`` is accepted for signal-slot convenience but ignored;
        the text reads ``self.get_snapshot()`` on each call.
        """
        del snapshot
        if self._menu is None:
            return
        counts = self.get_snapshot().state == "running"
        self._act_header.setText(self.header_text())
        self._act_workers.setText(self.workers_line())
        self._act_workers.setVisible(counts)
        self._act_jobs.setText(self.jobs_line())
        self._act_jobs.setVisible(counts)
        set_enabled(self._act_restart, not ipc.marker_exists(self.data_dir, ipc.MARKER_RESTART))
        set_enabled(self._act_reset, not ipc.marker_exists(self.data_dir, ipc.MARKER_RESET))
        set_enabled(self._act_quit, not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT))
        self._act_footer.setText(self.footer_text())
