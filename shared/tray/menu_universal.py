# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared QAction builder for actions that exist in EVERY tray phase.

Both ``ManagerSection`` (runtime menu) and ``WizardSection`` (wizard
menu) need an "About Sethlans" + "Quit ..." pair. Centralizing the
construction here prevents drift between the two phases (spec FR-6).

The quit-target is intentionally NOT baked in: the manager menu wants
``ipc.request_quit(target="manager")`` while the wizard menu wants
``target="all"`` (the launcher tree is the only thing alive during
wizard mode). The caller passes a no-arg ``on_quit`` slot that does
the right thing for its phase.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu


def add_universal_actions(
    menu: QMenu,
    on_about: Callable[[], None],
    on_quit: Callable[[], None],
    quit_label: str = "Quit Sethlans",
) -> tuple[QAction, QAction]:
    """Append About + Quit actions to *menu*; return ``(about, quit)``.

    A separator is inserted between the About row and the Quit row so
    Quit is visually grouped at the bottom of its section. Both
    QActions are parented to *menu* (Qt parents QActions to the QMenu
    they were created on); callers may store the returned references
    to mutate enabled / visible / text later.
    """
    about_action = menu.addAction("About Sethlans")
    about_action.triggered.connect(on_about)
    menu.addSeparator()
    quit_action = menu.addAction(quit_label)
    quit_action.triggered.connect(on_quit)
    return about_action, quit_action
