# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cross-platform desktop-notification dispatcher.

Wraps ``plyer.notification.notify``.  Per tray spec FR-25 / FR-25a /
FR-26a, notifications are:

* State-transition edge-triggered — the poller decides when to enqueue.
* Main-thread only — ``dispatch()`` MUST be called on the pystray main
  thread (driven by a queue drained from the main loop).
* Non-essential UI — any exception (including library-internal
  failures) is swallowed with a WARNING log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

APP_NAME = "Sethlans"


@dataclass(frozen=True)
class NotificationEvent:
    """Enqueued from poller, consumed on main thread."""
    title: str
    message: str


def dispatch(event: NotificationEvent) -> None:
    """Show a desktop notification.

    Main-thread only.  Never raises.
    """
    try:
        from plyer import notification  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover
        logger.warning("plyer unavailable (%s); skipping notification", exc)
        return
    try:
        notification.notify(
            title=event.title,
            message=event.message,
            app_name=APP_NAME,
            timeout=5,
        )
    except Exception as exc:
        # plyer can raise arbitrary exceptions (NotificationError,
        # OSError, DLL load failures on Windows, etc.); swallow them.
        logger.warning(
            "Notification dispatch failed (%s); title=%r",
            exc, event.title,
        )
