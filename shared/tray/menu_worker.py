# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The worker section of the ``RUNNING`` tray menu (FR-LN-4)::

    [Worker] Idle
    ---
    Open Sethlans Status
    View Logs / Reset setup... / About Sethlans     (worker-only mode)
    Quit Sethlans

In ``manager_worker`` the manager section above it owns View Manager
Logs, Reset and About, so ``worker_only=False`` leaves them out here.
Open Sethlans Status opens the worker page, HTTPS through the worker's
Caddy on port 8081.
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
from shared.tray.menu_manager_helpers import open_launcher_logs
from shared.tray.menu_universal import RESET_LABEL, VIEW_LOGS_LABEL, add_action, add_label, set_enabled
from shared.tray.notifications import NotificationEvent

logger = logging.getLogger(__name__)

WORKER_STATE_TEXT = {
    "rendering": "Rendering",
    "yielding": "Yielding (finishing current frame)",
    "yielded": "Yielded (waiting for idle)",
    "idle": "Idle",
}


class WorkerSection:
    """Worker-section menu state + callbacks for the Qt tray."""

    def __init__(
        self,
        data_dir: Path,
        quit_requested_flag: threading.Event,
        worker_only: bool = False,
        worker_host: str = "localhost",
        worker_port: int = 8081,
        get_worker_state: Optional[Callable[[], str]] = None,
        notify: Optional[Callable[[NotificationEvent], None]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.quit_flag = quit_requested_flag
        self.worker_only = worker_only
        self.host = worker_host
        self.port = worker_port
        self._get_state = get_worker_state or (lambda: "idle")
        self._notify = notify

        # QAction references held so refresh() can mutate them.
        self._menu: Optional[QMenu] = None
        self._act_header: Optional[QAction] = None
        self._act_reset: Optional[QAction] = None
        self._act_quit: Optional[QAction] = None

    def header_text(self) -> str:
        state = self._get_state()
        return f"[Worker] {WORKER_STATE_TEXT.get(state, state)}"

    # ---- click callbacks (no-arg; QAction.triggered signal) ----
    def on_open_worker_status(self) -> None:
        webbrowser.open(f"https://{self.host}:{self.port}/")

    def on_view_logs(self) -> None:
        open_launcher_logs(self.data_dir)

    def on_reset(self) -> None:
        reset_confirm.ask_reset(self.data_dir, self._notify)

    def on_quit_sethlans(self) -> None:
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="all")
        except Exception as exc:  # noqa: BLE001
            logger.warning("request_quit(all) failed: %s", exc)

    def on_about(self) -> None:
        show_about_dialog(self._menu)

    # ---- menu construction ----
    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Build a fresh QMenu; ``refresh`` then works on its actions."""
        menu = QMenu(parent)
        self._act_header = add_label(menu, "")
        menu.addSeparator()
        add_action(menu, "Open Sethlans Status", self.on_open_worker_status)
        self._act_reset = None
        if self.worker_only:
            add_action(menu, VIEW_LOGS_LABEL, self.on_view_logs)
            self._act_reset = add_action(menu, RESET_LABEL, self.on_reset)
            add_action(menu, "About Sethlans", self.on_about)
        self._act_quit = add_action(menu, "Quit Sethlans", self.on_quit_sethlans)
        self._menu = menu
        self.refresh()
        return menu

    def refresh(self, snapshot=None) -> None:
        """Header text, and Reset / Quit greyed out while in flight."""
        del snapshot
        if self._menu is None:
            return
        self._act_header.setText(self.header_text())
        set_enabled(self._act_reset, not ipc.marker_exists(self.data_dir, ipc.MARKER_RESET))
        set_enabled(self._act_quit, not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT))
