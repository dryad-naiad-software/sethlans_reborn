# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt manager-section menu builder for the PySide6 tray.

Mirrors ``shared/tray/menu_manager.py`` (spec FR-2, NFR-1).
``ManagerSection`` exposes ``build_qmenu(parent=None)`` /
``rebuild(parent=None)`` and ``refresh(snapshot=None)`` to re-evaluate
dynamic text / visibility / enabled state from the current snapshot.
Pure-Python helpers live in ``qt_menu_manager_helpers``; the shared
About dialog lives in ``qt_about``.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from shared.tray import ipc
from shared.tray.qt_about import show_about_dialog
from shared.tray.qt_clipboard import copy_token_to_clipboard
from shared.tray.qt_menu_manager_helpers import (
    open_logs,
    read_token,
    sentinel_exists,
    validate_token,
)
from shared.tray.qt_notifications import NotificationEvent

logger = logging.getLogger(__name__)


class ManagerSection:
    """Manager-section menu state + callbacks for the Qt tray.

    Constructor signature mirrors the legacy pystray ``ManagerSection``
    in ``shared.tray.menu_manager`` so Phase 8's app bootstrap does not
    need a signature rewrite.
    """

    def __init__(
        self,
        data_dir: Path,
        manager_data_dir: Path,
        manager_host: str,
        manager_port: int,
        quit_requested_flag: threading.Event,
        get_snapshot: Callable,
        notify: Optional[Callable[[NotificationEvent], None]] = None,
    ) -> None:
        self.data_dir = data_dir
        self.manager_data_dir = manager_data_dir
        self.host = manager_host
        self.port = manager_port
        self.quit_flag = quit_requested_flag
        self.get_snapshot = get_snapshot
        self._notify = notify

        # QAction references held so refresh() can mutate them.
        self._menu: Optional[QMenu] = None
        self._act_header: Optional[QAction] = None
        self._act_setup: Optional[QAction] = None
        self._act_workers: Optional[QAction] = None
        self._act_jobs: Optional[QAction] = None
        self._act_open_dashboard: Optional[QAction] = None
        self._act_copy_token: Optional[QAction] = None
        self._act_open_wizard: Optional[QAction] = None
        self._act_restart: Optional[QAction] = None
        self._act_view_logs: Optional[QAction] = None
        self._act_quit: Optional[QAction] = None
        self._act_about: Optional[QAction] = None
        self._act_footer: Optional[QAction] = None

    # ---- visibility ----
    def setup_token_available(self) -> bool:
        if sentinel_exists(self.manager_data_dir):
            return False
        token = read_token(self.manager_data_dir)
        return validate_token(token)

    def wizard_visible(self) -> bool:
        return not sentinel_exists(self.manager_data_dir)

    def counts_visible(self) -> bool:
        return self.get_snapshot().state == "running"

    # ---- enabled (marker-in-flight gating) ----
    def restart_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_RESTART)

    def quit_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT)

    # ---- click callbacks (no-arg; QAction.triggered signal) ----
    def on_open_dashboard(self) -> None:
        webbrowser.open(f"https://{self.host}:{self.port}/")

    def on_open_wizard(self) -> None:
        webbrowser.open(f"https://{self.host}:{self.port}/setup/")

    def on_copy_token(self) -> None:
        """Re-check token availability, then copy via QClipboard.

        Never raises.  On success, dispatches a "Token copied" notice
        when a ``notify`` callable was supplied; failure only logs.
        Token never logged.
        """
        if sentinel_exists(self.manager_data_dir):
            logger.info("Copy token skipped: setup already complete")
            return
        token = read_token(self.manager_data_dir)
        if not validate_token(token):
            logger.info("Copy token skipped: token not available")
            return
        ok = copy_token_to_clipboard(token)
        logger.info("Copy setup token result: %s", ok)
        if ok and self._notify is not None:
            try:
                self._notify(NotificationEvent(
                    title="Token copied",
                    message="Setup token copied to clipboard.",
                ))
            except Exception:  # pragma: no cover
                logger.exception("Notify after copy_token failed")

    def on_restart_manager(self) -> None:
        try:
            ipc.request_restart(self.data_dir)
        except Exception as exc:  # pragma: no cover
            logger.warning("request_restart failed: %s", exc)

    def on_quit_manager(self) -> None:
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="manager")
        except Exception as exc:  # pragma: no cover
            logger.warning("request_quit(manager) failed: %s", exc)

    def on_view_logs(self) -> None:
        open_logs(self.data_dir)

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

    def setup_line(self) -> str:
        snap = self.get_snapshot()
        if sentinel_exists(self.manager_data_dir):
            return "Setup: Ready"
        if snap.setup_mode:
            return "Setup: In progress"
        return "Setup: Needed"

    def workers_line(self) -> str:
        return f"Workers online: {self.get_snapshot().workers_online}"

    def jobs_line(self) -> str:
        snap = self.get_snapshot()
        return (
            f"Jobs queued: {snap.jobs_queued}  |  "
            f"Rendering: {snap.jobs_rendering}"
        )

    def footer_text(self) -> str:
        snap = self.get_snapshot()
        version = snap.version or "?"
        return f"v{version} -- :{self.port}"

    # ---- menu construction ----
    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Construct the manager-section QMenu.

        Idempotent: each call returns a freshly-built ``QMenu`` and
        updates the stored ``QAction`` references so ``refresh``
        always operates on the most recently built menu.
        """
        menu = QMenu(parent)
        self._act_header = self._add_disabled(menu, "")
        self._act_setup = self._add_disabled(menu, "")
        self._act_workers = self._add_disabled(menu, "")
        self._act_jobs = self._add_disabled(menu, "")
        menu.addSeparator()
        self._act_open_dashboard = self._add(
            menu, "Open Dashboard", self.on_open_dashboard,
        )
        self._act_copy_token = self._add(
            menu, "Copy Setup Token", self.on_copy_token,
        )
        self._act_open_wizard = self._add(
            menu, "Open Setup Wizard", self.on_open_wizard,
        )
        self._act_restart = self._add(
            menu, "Restart Manager", self.on_restart_manager,
        )
        self._act_view_logs = self._add(
            menu, "View Manager Logs", self.on_view_logs,
        )
        self._act_quit = self._add(
            menu, "Quit Manager", self.on_quit_manager,
        )
        # About Sethlans action (NFR-1) placed above the final
        # separator / footer per spec FR-2.
        self._act_about = self._add(
            menu, "About Sethlans", self.on_about,
        )
        menu.addSeparator()
        self._act_footer = self._add_disabled(menu, "")
        self._menu = menu
        self.refresh()
        return menu

    def rebuild(self, parent: Optional[QWidget] = None) -> QMenu:
        """Alias for ``build_qmenu``."""
        return self.build_qmenu(parent)

    def refresh(self, snapshot=None) -> None:
        """Re-evaluate text / visibility / enabled on all QActions.

        ``snapshot`` is accepted for signal-slot convenience
        (``snapshot_changed.connect(section.refresh)``) but ignored —
        the predicates read from ``self.get_snapshot()`` on each call.
        """
        if self._menu is None:
            return
        self._set_text(self._act_header, self.header_text())
        self._set_text(self._act_setup, self.setup_line())
        counts = self.counts_visible()
        self._set_text_visible(
            self._act_workers, self.workers_line(), counts,
        )
        self._set_text_visible(
            self._act_jobs, self.jobs_line(), counts,
        )
        self._set_visible(
            self._act_copy_token, self.setup_token_available(),
        )
        self._set_visible(
            self._act_open_wizard, self.wizard_visible(),
        )
        self._set_enabled(self._act_restart, self.restart_enabled())
        self._set_enabled(self._act_quit, self.quit_enabled())
        self._set_text(self._act_footer, self.footer_text())

    # ---- internals ----
    @staticmethod
    def _add(menu: QMenu, label: str, slot: Callable) -> QAction:
        action = menu.addAction(label)
        action.triggered.connect(slot)
        return action

    @staticmethod
    def _add_disabled(menu: QMenu, text: str) -> QAction:
        action = menu.addAction(text)
        action.setEnabled(False)
        return action

    @staticmethod
    def _set_text(action: Optional[QAction], text: str) -> None:
        if action is not None:
            action.setText(text)

    @staticmethod
    def _set_visible(action: Optional[QAction], visible: bool) -> None:
        if action is not None:
            action.setVisible(visible)

    @staticmethod
    def _set_enabled(action: Optional[QAction], enabled: bool) -> None:
        if action is not None:
            action.setEnabled(enabled)

    @staticmethod
    def _set_text_visible(
        action: Optional[QAction], text: str, visible: bool,
    ) -> None:
        if action is not None:
            action.setText(text)
            action.setVisible(visible)
