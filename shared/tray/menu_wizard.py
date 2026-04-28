# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt wizard-section menu builder for the PySide6 tray (spec FR-5).

``WizardSection`` is the wizard-phase counterpart to ``ManagerSection``.
It exposes the same ``build_qmenu(parent)`` / ``rebuild(parent)`` /
``refresh(snapshot=None)`` API shape so the calling code in ``app.py``
can swap one section for the other transparently on phase transitions.

The wizard menu's QAction order is:

    [Wizard] Setup wizard running   (disabled header)
    -----
    Open Setup Wizard
    Copy Setup Token
    View Setup Wizard Logs
    About Sethlans
    -----
    Quit Sethlans
    -----
    v<version>                       (disabled footer)

URL contract (FR-7): ``Open Setup Wizard`` opens
``https://localhost:<wizard_state.port>/`` with NO ``?token=`` query
or ``#token`` fragment. The token is delivered out of band via
``Copy Setup Token`` because Chrome's self-signed-cert interstitial
strips query strings. The wizard backend's ``auth.py`` rejects any
URL whose query string contains a token-shaped key as defense-in-
depth.
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
from shared.tray.about import show_about_dialog
from shared.tray.clipboard import copy_token_to_clipboard
from shared.tray.menu_manager_helpers import open_logs
from shared.tray.menu_universal import add_universal_actions
from shared.tray.notifications import NotificationEvent
from shared.tray.wizard_state import read_wizard_state

logger = logging.getLogger(__name__)


class WizardSection:
    """Wizard-phase menu state + callbacks for the Qt tray.

    Mirrors the ``ManagerSection`` API surface (constructor keyword
    set, ``build_qmenu`` / ``rebuild`` / ``refresh``) so phase swaps
    in ``app.py`` can substitute one for the other.
    """

    def __init__(
        self,
        data_dir: Path,
        quit_requested_flag: threading.Event,
        get_version: Optional[Callable[[], str]] = None,
        notify: Optional[Callable[[NotificationEvent], None]] = None,
    ) -> None:
        self.data_dir = data_dir
        self.quit_flag = quit_requested_flag
        self._get_version = get_version or (lambda: "?")
        self._notify = notify

        # QAction references held so refresh() can mutate them.
        self._menu: Optional[QMenu] = None
        self._act_header: Optional[QAction] = None
        self._act_open_wizard: Optional[QAction] = None
        self._act_copy_token: Optional[QAction] = None
        self._act_view_logs: Optional[QAction] = None
        self._act_about: Optional[QAction] = None
        self._act_quit: Optional[QAction] = None
        self._act_footer: Optional[QAction] = None

    # ---- click callbacks (no-arg; QAction.triggered signal) ----
    def on_open_wizard(self) -> None:
        """Open the wizard URL, sourcing port from wizard state.

        FR-7: NEVER include a ``?token=`` or ``#token`` in the URL —
        the wizard backend rejects such requests and Chrome's cert
        interstitial would strip them anyway.
        """
        state = read_wizard_state(self.data_dir)
        if state is None:
            logger.warning(
                "Open Setup Wizard skipped: wizard state unavailable",
            )
            return
        try:
            webbrowser.open(f"https://localhost:{state.port}/")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("webbrowser.open failed: %s", exc)

    def on_copy_token(self) -> None:
        """Copy the wizard token to the clipboard via QClipboard.

        Reads from ``<data_dir>/wizard/.setup_token`` (NOT manager.ini).
        Token value is NEVER logged.
        """
        state = read_wizard_state(self.data_dir)
        if state is None:
            logger.info("Copy token skipped: wizard state unavailable")
            return
        ok = copy_token_to_clipboard(state.token)
        logger.info("Copy setup token result: %s", ok)
        if ok and self._notify is not None:
            try:
                self._notify(NotificationEvent(
                    title="Token copied",
                    message="Setup token copied to clipboard.",
                ))
            except Exception:  # pragma: no cover - defensive
                logger.exception("Notify after copy_token failed")

    def on_view_logs(self) -> None:
        """Open the wizard log; fall back to the launcher log.

        FR-9: prefer ``logs/wizard.log`` (written by the wizard
        subprocess) and fall back to ``logs/launcher.log`` (written
        by the launcher itself before / around wizard spawn).
        """
        if (self.data_dir / "logs" / "wizard.log").exists():
            open_logs(self.data_dir, "wizard.log")
        else:
            open_logs(self.data_dir, "launcher.log")

    def on_about(self) -> None:
        show_about_dialog(self._menu)

    def on_quit_sethlans(self) -> None:
        """Set the quit flag and write the IPC marker (target=all).

        Wizard mode has no manager process to quit selectively, so the
        wizard menu's quit MUST use ``target="all"``. The launcher's
        wizard-mode wait loops poll ``quit_requested`` per #163.
        """
        self.quit_flag.set()
        try:
            ipc.request_quit(self.data_dir, target="all")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("request_quit(all) failed: %s", exc)

    # ---- enabled gates ----
    def quit_enabled(self) -> bool:
        return not ipc.marker_exists(self.data_dir, ipc.MARKER_QUIT)

    def wizard_actions_enabled(self) -> bool:
        """True iff both wizard files (port, .setup_token) are present.

        Used to grey out Open Setup Wizard + Copy Setup Token during
        the split-second window between wizard subprocess start and
        the launcher's port-file write.
        """
        return read_wizard_state(self.data_dir) is not None

    # ---- dynamic text ----
    def header_text(self) -> str:
        return "[Wizard] Setup wizard running"

    def footer_text(self) -> str:
        return f"v{self._get_version()}"

    # ---- menu construction ----
    def build_qmenu(self, parent: Optional[QWidget] = None) -> QMenu:
        """Construct the wizard-section QMenu.

        Idempotent: each call returns a freshly-built ``QMenu`` and
        updates the stored ``QAction`` references so ``refresh``
        always operates on the most recently built menu.
        """
        menu = QMenu(parent)
        self._act_header = self._add_disabled(menu, "")
        menu.addSeparator()
        self._act_open_wizard = self._add(
            menu, "Open Setup Wizard", self.on_open_wizard,
        )
        self._act_copy_token = self._add(
            menu, "Copy Setup Token", self.on_copy_token,
        )
        self._act_view_logs = self._add(
            menu, "View Setup Wizard Logs", self.on_view_logs,
        )
        self._act_about, self._act_quit = add_universal_actions(
            menu, self.on_about, self.on_quit_sethlans,
            quit_label="Quit Sethlans",
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
        """Re-evaluate header / footer / enabled gates.

        ``snapshot`` is accepted for signal-slot convenience
        (``snapshot_changed.connect(section.refresh)``) but ignored —
        wizard predicates read from the filesystem on each call.
        Safe to call before ``build_qmenu``; a no-op in that case.
        """
        if self._menu is None:
            return
        self._set_text(self._act_header, self.header_text())
        ready = self.wizard_actions_enabled()
        self._set_enabled(self._act_open_wizard, ready)
        self._set_enabled(self._act_copy_token, ready)
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
    def _set_enabled(action: Optional[QAction], enabled: bool) -> None:
        if action is not None:
            action.setEnabled(enabled)
