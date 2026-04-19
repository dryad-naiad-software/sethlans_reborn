# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared About Sethlans dialog for the PySide6 tray (spec NFR-1).

Placed in its own module so both ``qt_menu_manager`` and
``qt_menu_worker`` (Phase 7b) can show the same LGPLv3 attribution
dialog without duplicating the payload text.

The bundled license files (``licenses/LICENSE.LGPLv3``,
``licenses/Qt-NOTICE.txt``) are written by Phase 10 packaging; the
body text references the path but does not attempt to read the files
at runtime.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

logger = logging.getLogger(__name__)

ABOUT_TITLE = "About Sethlans"
ABOUT_BODY = (
    "Sethlans Reborn\n"
    "\n"
    "This application uses Qt via the PySide6 Python bindings\n"
    "(LGPLv3).  Qt and PySide6 are copyright their respective\n"
    "authors.  The LGPLv3 license text is bundled with this\n"
    "distribution under the 'licenses/' directory\n"
    "(licenses/LICENSE.LGPLv3, licenses/Qt-NOTICE.txt).\n"
    "\n"
    "Sethlans Reborn itself is licensed GPL-2.0-or-later.\n"
    "Copyright 2025 Dryad and Naiad Software LLC."
)


def show_about_dialog(parent: Optional[QWidget] = None) -> None:
    """Display the shared About Sethlans dialog (non-blocking).

    Uses ``QMessageBox.show()`` with ``setModal(False)`` so the tray
    event loop is not blocked on macOS.  The dialog is reaped
    automatically via ``WA_DeleteOnClose``.
    """
    try:
        box = QMessageBox(parent)
        box.setWindowTitle(ABOUT_TITLE)
        box.setText(ABOUT_BODY)
        box.setIcon(QMessageBox.Icon.Information)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.show()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to show About dialog: %s", exc)
