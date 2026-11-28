# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""QAction helpers shared by every tray menu section.

* :func:`add_action` / :func:`add_label`: a clickable item, or a
  disabled line of text (headers, the reason).
* :func:`add_universal_actions`: "About Sethlans", a separator and
  "Quit ...", in every menu. The caller's ``on_quit`` picks the target
  (``"manager"`` for Quit Manager, ``"all"`` for Quit Sethlans).
* :data:`RESET_LABEL` / :data:`VIEW_LOGS_LABEL`: one wording for Reset
  and View Logs in every menu (FR-LN-4).
* :func:`set_enabled`: tolerates an item that was not built.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

RESET_LABEL = "Reset setup..."
VIEW_LOGS_LABEL = "View Logs"
REASON_CHARS = 100


def add_action(menu: QMenu, label: str, slot: Callable[[], None]) -> QAction:
    """Append a clickable item; ``slot`` runs on click."""
    action = menu.addAction(label)
    action.triggered.connect(slot)
    return action


def add_label(menu: QMenu, text: str) -> QAction:
    """Append a disabled line of text."""
    action = menu.addAction(text)
    action.setEnabled(False)
    return action


def reason_line(reason: str) -> str:
    """``reason`` short enough for one menu line (the full text is in the
    notification and the logs)."""
    return reason if len(reason) <= REASON_CHARS else reason[:REASON_CHARS - 3] + "..."


def set_enabled(action: Optional[QAction], enabled: bool) -> None:
    if action is not None:
        action.setEnabled(enabled)


def add_universal_actions(
    menu: QMenu,
    on_about: Callable[[], None],
    on_quit: Callable[[], None],
    quit_label: str = "Quit Sethlans",
) -> tuple[QAction, QAction]:
    """Append About + Quit actions to *menu*; return ``(about, quit)``.

    A separator is inserted between the About row and the Quit row so
    Quit is visually grouped at the bottom of its section.
    """
    about_action = add_action(menu, "About Sethlans", on_about)
    menu.addSeparator()
    quit_action = add_action(menu, quit_label, on_quit)
    return about_action, quit_action
