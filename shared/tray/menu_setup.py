# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The tray menus outside ``RUNNING`` (FR-LN-4, owner decision 2026-11-28).

:class:`SetupSection`, ``SETUP``::

    Sethlans setup is open
    <reason of the last failed start, when there is one>
    ---
    Open Setup / Copy Setup Token / View Logs / About Sethlans
    ---
    Quit Sethlans

:class:`StatusSection`, ``STARTING``, ``STOPPING``, ``FAILED``, and
``RUNNING`` when the settings file gives no mode::

    <header>                      ("Sethlans could not start" in FAILED)
    <reason, when there is one>
    ---
    View Logs / Reset setup... (RUNNING and FAILED only) / About Sethlans
    ---
    Quit Sethlans

Copy Setup Token reads ``<settings>/setup/setup_token`` when clicked,
and only while the launcher is in ``SETUP``; the token is never logged
(FR-SEC-1), and is cleared from the clipboard after 60 s or when setup
closes (``clipboard.clear_token_later``). Setup never reopens on its own: Reset is behind the
confirm dialog (:mod:`shared.tray.reset_confirm`).
"""

from __future__ import annotations

import logging
import re
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from shared import settings_file
from shared.settings_file_io import read_owned_file
from shared.tray import ipc, reset_confirm
from shared.tray import launcher_state as ls
from shared.tray.about import show_about_dialog
from shared.tray.clipboard import clear_token_later, copy_token_to_clipboard
from shared.tray.menu_manager_helpers import open_launcher_logs
from shared.tray.menu_universal import (
    RESET_LABEL, VIEW_LOGS_LABEL, add_action, add_label, add_universal_actions, reason_line, set_enabled,
)
from shared.tray.notifications import NotificationEvent
from shared.tray.phase import Phase

logger = logging.getLogger(__name__)

SETUP_PAGE_PATH = "/setup/"
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{30,60}$")
_MAX_TOKEN_BYTES = 1024
HEADERS = {
    ls.SETUP: "Sethlans setup is open",
    ls.STARTING: "Sethlans is starting...",
    ls.STOPPING: "Sethlans is stopping...",
    ls.FAILED: "Sethlans could not start",
    ls.RUNNING: "Sethlans is running",
}

Notify = Optional[Callable[[NotificationEvent], None]]


def read_setup_token(settings_dir: Path) -> str:
    """The setup token from ``<settings>/setup/setup_token``, or ``""``.

    Missing, unreadable, oversized, not ASCII or not token-shaped, or
    not a regular file this user owns (``read_owned_file``), all give ``""``. Never raises; never logs the token.
    """
    path = Path(settings_dir) / settings_file.SETUP_SUBDIR / settings_file.SETUP_TOKEN_NAME
    try:
        data = read_owned_file(path, _MAX_TOKEN_BYTES)
        token = data.decode("ascii").strip() if len(data) <= _MAX_TOKEN_BYTES else ""
    except Exception:  # noqa: BLE001 - missing, unreadable or not ASCII
        return ""
    return token if _TOKEN_SHAPE.match(token) else ""


class _Section:
    """What both sections share: the header, the reason, View Logs, About, Quit."""

    def __init__(self, settings_dir: Path, phase: Phase, quit_requested_flag: threading.Event,
                 notify: Notify = None) -> None:
        self.data_dir = Path(settings_dir)
        self.phase = phase
        self.quit_flag = quit_requested_flag
        self._notify = notify
        self._menu: Optional[QMenu] = None
        self._act_quit: Optional[QAction] = None
        self._act_reset: Optional[QAction] = None

    def header_text(self) -> str:
        return HEADERS.get(self.phase.state, "Sethlans")

    def _add_header(self, menu: QMenu) -> None:
        add_label(menu, self.header_text())
        if self.phase.reason:
            add_label(menu, reason_line(self.phase.reason))
        menu.addSeparator()

    def _add_tail(self, menu: QMenu) -> None:
        _, self._act_quit = add_universal_actions(menu, self.on_about, self.on_quit, quit_label="Quit Sethlans")

    def on_view_logs(self) -> None:
        open_launcher_logs(self.data_dir)

    def on_about(self) -> None:
        show_about_dialog(self._menu)

    def on_quit(self) -> None:
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="all")
        except Exception as exc:  # noqa: BLE001
            logger.warning("request_quit(all) failed: %s", exc)

    def refresh(self, snapshot=None) -> None:
        """Grey out Quit / Reset while their request is in flight."""
        del snapshot
        set_enabled(self._act_quit, not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT))
        set_enabled(self._act_reset, not ipc.marker_exists(self.data_dir, ipc.MARKER_RESET))


class SetupSection(_Section):
    """The ``SETUP`` menu (module doc)."""

    def __init__(self, settings_dir: Path, phase: Phase, quit_requested_flag: threading.Event,
                 notify: Notify = None) -> None:
        super().__init__(settings_dir, phase, quit_requested_flag, notify)
        self._act_open: Optional[QAction] = None

    def on_open_setup(self) -> None:
        if self.phase.port is None:
            return
        try:
            webbrowser.open(f"https://localhost:{self.phase.port}{SETUP_PAGE_PATH}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("webbrowser.open failed: %s", exc)

    def on_copy_token(self) -> None:
        """Copy the token, only while the launcher is still in ``SETUP``."""
        if ls.read_launcher_state(self.data_dir).state != ls.SETUP:
            logger.info("Copy token skipped: setup is not open")
            return
        token = read_setup_token(self.data_dir)
        ok = bool(token) and copy_token_to_clipboard(token)
        logger.info("Copy setup token result: %s", ok)
        if ok:
            clear_token_later(token)
        if self._notify is not None:
            message = "Setup token copied to clipboard." if ok else "The setup token is not available."
            self._notify(NotificationEvent("Sethlans setup", message))

    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        menu = QMenu(parent)
        self._add_header(menu)
        self._act_open = add_action(menu, "Open Setup", self.on_open_setup)
        self._act_open.setEnabled(self.phase.port is not None)
        add_action(menu, "Copy Setup Token", self.on_copy_token)
        add_action(menu, VIEW_LOGS_LABEL, self.on_view_logs)
        self._add_tail(menu)
        self._menu = menu
        self.refresh()
        return menu


class StatusSection(_Section):
    """``STARTING``, ``STOPPING``, ``FAILED``, or ``RUNNING`` with no mode."""

    def on_reset(self) -> None:
        reset_confirm.ask_reset(self.data_dir, self._notify)

    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        menu = QMenu(parent)
        self._add_header(menu)
        add_action(menu, VIEW_LOGS_LABEL, self.on_view_logs)
        if self.phase.offers_reset:
            self._act_reset = add_action(menu, RESET_LABEL, self.on_reset)
        self._add_tail(menu)
        self._menu = menu
        self.refresh()
        return menu
