# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Qt-based desktop-notification dispatcher.

PySide6 replacement for :mod:`shared.tray.notifications`.  Wraps
``QSystemTrayIcon.showMessage`` instead of plyer.  Per tray spec
FR-25 / FR-25a / FR-26a, notifications are:

* State-transition edge-triggered — the poller decides when to enqueue.
* Main-thread only — ``dispatch()`` MUST be called on the Qt GUI
  thread.  In Phase 6+ this is driven by a ``Signal(NotificationEvent)``
  routed through the Qt event loop, which guarantees main-thread
  delivery.  No runtime thread-check is performed here; Qt will warn
  or crash if violated, which is the desired failure mode during
  development.
* Non-essential UI — any exception (destroyed tray icon, platform
  plugin failure, etc.) is swallowed with a WARNING log.

The caller owns the ``QSystemTrayIcon`` instance and passes it in
explicitly, which keeps this module pure and testable (no reliance
on global Qt state).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtWidgets import QSystemTrayIcon

logger = logging.getLogger(__name__)

APP_NAME = "Sethlans"

# Notification timeout in milliseconds.  Matches the old plyer-based
# module's ``timeout=5`` (seconds).  Note: many platforms ignore this
# value and use their own OS-level default, but we pass it anyway for
# platforms that honor it.
_NOTIFICATION_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class NotificationEvent:
    """Enqueued from poller, consumed on main thread."""
    title: str
    message: str


def dispatch(tray_icon: QSystemTrayIcon, event: NotificationEvent) -> None:
    """Show a desktop notification via ``QSystemTrayIcon.showMessage``.

    Main-thread only (Qt constraint).  Never raises.

    :param tray_icon: The application's tray icon handle.  If ``None``
        or not a ``QSystemTrayIcon`` instance, the call is logged and
        ignored.
    :param event: The notification payload (title + message).
    """
    if not isinstance(tray_icon, QSystemTrayIcon):
        logger.warning(
            "Notification dispatch skipped: tray_icon is %r, not a "
            "QSystemTrayIcon; title=%r",
            type(tray_icon).__name__, event.title,
        )
        return
    try:
        tray_icon.showMessage(
            event.title,
            event.message,
            QSystemTrayIcon.MessageIcon.Information,
            _NOTIFICATION_TIMEOUT_MS,
        )
    except Exception as exc:
        # QSystemTrayIcon.showMessage can raise RuntimeError if the
        # underlying C++ object has been destroyed, plus arbitrary
        # platform-plugin failures.  Swallow them — notifications
        # are non-essential UI.
        logger.warning(
            "Notification dispatch failed (%s); title=%r",
            exc, event.title,
        )
