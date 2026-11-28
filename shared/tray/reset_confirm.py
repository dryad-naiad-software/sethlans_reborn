# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Reset setup from the tray: one confirm dialog for every menu (FR-RS-1).

:func:`ask_reset` shows the dialog. Nothing is sent until the person
clicks Reset; Cancel, Esc or closing the dialog sends nothing. On
Reset the launcher state is read again, and the marker is written
(``ipc.request_reset``) only when the launcher still acts on it,
``RUNNING`` or ``FAILED`` (security L5); otherwise the person is told
it was not sent.

The dialog is non-blocking (``show()``, like the About dialog), so the
tray's event loop keeps running; only one is open at a time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from shared.tray import ipc
from shared.tray import launcher_state as ls
from shared.tray.notifications import NotificationEvent
from shared.tray.phase import RESET_STATES

logger = logging.getLogger(__name__)

TITLE = "Reset setup"
QUESTION = "Reset setup?"
DETAILS = (
    "Sethlans stops the worker and the manager, then opens the setup "
    "page again. Your settings, database and renders are kept."
)

Notify = Optional[Callable[[NotificationEvent], None]]
_open_box: Optional[QMessageBox] = None


def send_reset(settings_dir: Path, notify: Notify = None) -> bool:
    """Write the Reset marker if the launcher still acts on it; True when sent."""
    state = ls.read_launcher_state(settings_dir).state
    if state not in RESET_STATES:
        logger.info("Reset not sent: the launcher is in %s", state)
        _tell(notify, NotificationEvent(TITLE, "Reset was not sent: Sethlans is busy. Try again in a moment."))
        return False
    try:
        ipc.request_reset(settings_dir)
    except Exception as exc:  # noqa: BLE001 - a click handler must not raise
        logger.warning("request_reset failed: %s", exc)
        _tell(notify, NotificationEvent(TITLE, "Reset could not be sent. See the logs."))
        return False
    return True


def _tell(notify: Notify, event: NotificationEvent) -> None:
    if notify is not None:
        try:
            notify(event)
        except Exception:  # pragma: no cover - notifications are best effort
            logger.exception("Reset notification failed")


def _closed() -> None:
    global _open_box
    _open_box = None


def ask_reset(settings_dir: Path, notify: Notify = None) -> None:
    """Show the confirm dialog; Reset calls :func:`send_reset`. Never raises."""
    global _open_box
    try:
        if _open_box is not None:
            _open_box.raise_()
            _open_box.activateWindow()
            return
        box = QMessageBox()
        buttons = QMessageBox.StandardButton
        box.setWindowTitle(TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(QUESTION)
        box.setInformativeText(DETAILS)
        box.setStandardButtons(buttons.Reset | buttons.Cancel)
        box.setDefaultButton(buttons.Cancel)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        def _clicked(button) -> None:
            if box.standardButton(button) == buttons.Reset:
                send_reset(settings_dir, notify)

        box.buttonClicked.connect(_clicked)
        box.finished.connect(_closed)
        _open_box = box
        box.show()
        box.raise_()  # no parent window: bring it to the front
        box.activateWindow()
    except Exception as exc:  # noqa: BLE001
        _open_box = None
        logger.warning("Failed to show the Reset dialog: %s", exc)
