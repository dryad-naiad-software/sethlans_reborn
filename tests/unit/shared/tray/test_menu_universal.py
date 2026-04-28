# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/menu_universal.py`` (spec FR-6,
AC-UniversalActions).

The helper ``add_universal_actions(menu, on_about, on_quit, quit_label)``
is shared between ``ManagerSection`` and ``WizardSection`` so the
About + Quit pair stays consistent across phases. Order on the menu
MUST be: About row, separator, Quit row.

The quit-target is intentionally NOT baked into the helper — the
caller passes the slot. Manager phase uses ``target="manager"``;
wizard phase uses ``target="all"``. Tests verify the exact slot
binding via QAction.trigger().
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 required for menu_universal",
)
pytest.importorskip(
    "pytestqt", reason="pytest-qt required for qapp fixture",
)

from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray.menu_universal import add_universal_actions  # noqa: E402


# ------------------------------------------------------------------ #
# Return contract
# ------------------------------------------------------------------ #

class TestReturnContract:

    def test_returns_about_and_quit_qactions(self, qapp):
        menu = QMenu()
        about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
        )
        assert isinstance(about, QAction)
        assert isinstance(quit_, QAction)
        assert about.text() == "About Sethlans"
        assert quit_.text() == "Quit Sethlans"

    def test_returns_two_distinct_actions(self, qapp):
        menu = QMenu()
        about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
        )
        assert about is not quit_


# ------------------------------------------------------------------ #
# Menu insertion order: About, separator, Quit
# ------------------------------------------------------------------ #

class TestMenuOrder:

    def test_action_order_about_separator_quit(self, qapp):
        menu = QMenu()
        about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
        )
        actions = menu.actions()
        # Exactly 3 entries appended.
        assert len(actions) == 3
        assert actions[0] is about
        assert actions[1].isSeparator()
        assert actions[2] is quit_

    def test_separator_inserted_between_about_and_quit(self, qapp):
        menu = QMenu()
        add_universal_actions(menu, lambda: None, lambda: None)
        actions = menu.actions()
        # The middle entry must be a separator.
        assert actions[1].isSeparator() is True
        # Bounds are not separators.
        assert actions[0].isSeparator() is False
        assert actions[2].isSeparator() is False

    def test_appends_to_existing_menu(self, qapp):
        """Helper appends — does not clear the menu."""
        menu = QMenu()
        menu.addAction("preexisting")
        about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
        )
        actions = menu.actions()
        assert len(actions) == 4
        assert actions[0].text() == "preexisting"
        assert actions[1] is about
        assert actions[2].isSeparator()
        assert actions[3] is quit_


# ------------------------------------------------------------------ #
# Quit label customisation (manager uses "Quit Manager"; wizard
# defaults to "Quit Sethlans")
# ------------------------------------------------------------------ #

class TestQuitLabel:

    def test_default_quit_label_is_quit_sethlans(self, qapp):
        menu = QMenu()
        _about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
        )
        assert quit_.text() == "Quit Sethlans"

    def test_custom_quit_label_quit_manager(self, qapp):
        menu = QMenu()
        _about, quit_ = add_universal_actions(
            menu, lambda: None, lambda: None,
            quit_label="Quit Manager",
        )
        assert quit_.text() == "Quit Manager"

    def test_about_label_is_fixed(self, qapp):
        """About label is not customisable — same in every phase."""
        menu = QMenu()
        about, _quit = add_universal_actions(
            menu, lambda: None, lambda: None,
            quit_label="Quit Manager",
        )
        assert about.text() == "About Sethlans"


# ------------------------------------------------------------------ #
# Slot connection — triggered() reaches the supplied callable
# ------------------------------------------------------------------ #

class TestSlotConnections:

    def test_about_triggered_invokes_on_about(self, qapp, mocker):
        on_about = mocker.MagicMock()
        on_quit = mocker.MagicMock()
        menu = QMenu()
        about, _quit = add_universal_actions(menu, on_about, on_quit)
        about.trigger()
        on_about.assert_called_once_with()
        on_quit.assert_not_called()

    def test_quit_triggered_invokes_on_quit(self, qapp, mocker):
        on_about = mocker.MagicMock()
        on_quit = mocker.MagicMock()
        menu = QMenu()
        _about, quit_ = add_universal_actions(menu, on_about, on_quit)
        quit_.trigger()
        on_quit.assert_called_once_with()
        on_about.assert_not_called()

    def test_each_callable_independent(self, qapp, mocker):
        """Sanity: triggering one slot does not invoke the other."""
        on_about = mocker.MagicMock()
        on_quit = mocker.MagicMock()
        menu = QMenu()
        about, quit_ = add_universal_actions(menu, on_about, on_quit)
        about.trigger()
        quit_.trigger()
        about.trigger()
        assert on_about.call_count == 2
        assert on_quit.call_count == 1
