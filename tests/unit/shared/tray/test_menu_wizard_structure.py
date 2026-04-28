# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Structural tests for ``shared/tray/menu_wizard.py`` (spec FR-5,
AC-WizardMenu).

Covers the constructor signature, ``build_qmenu`` action order +
labels, ``rebuild`` alias, the disabled header / footer, and the
no-manager-actions-leak invariant. Refresh + click handler behaviour
lives in ``test_menu_wizard_handlers.py``.
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 required for menu_wizard",
)
pytest.importorskip(
    "pytestqt", reason="pytest-qt required for qapp fixture",
)

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray.menu_wizard import WizardSection  # noqa: E402


# ------------------------------------------------------------------ #
# Local constants — wizard-phase regression contract.
#
# Action attribute names + isSeparator markers, in build order.  The
# inner separator (between About and Quit) is added by
# ``add_universal_actions`` and MUST be present per FR-6.
# ------------------------------------------------------------------ #

EXPECTED_ACTIONS_IN_ORDER = [
    ("_act_header", False),
    ("__sep__", None),
    ("_act_open_wizard", True),
    ("_act_copy_token", True),
    ("_act_view_logs", True),
    ("_act_about", True),
    ("__sep__", None),
    ("_act_quit", True),
    ("__sep__", None),
    ("_act_footer", False),
]

EXPECTED_ENABLED_TEXTS = [
    "Open Setup Wizard",
    "Copy Setup Token",
    "View Setup Wizard Logs",
    "About Sethlans",
    "Quit Sethlans",
]


@pytest.fixture
def wizard_section(qapp, tmp_path):
    """Build a fresh ``WizardSection`` rooted at ``tmp_path``."""
    return WizardSection(
        data_dir=tmp_path,
        quit_requested_flag=threading.Event(),
        get_version=lambda: "1.2.3",
    )


# ------------------------------------------------------------------ #
# Constructor signature
# ------------------------------------------------------------------ #

class TestConstructorSignature:

    def test_constructor_param_order(self):
        params = list(
            inspect.signature(WizardSection.__init__).parameters.keys(),
        )
        # Required: self, data_dir, quit_requested_flag.
        # Optional: get_version, notify.
        assert params[:3] == [
            "self", "data_dir", "quit_requested_flag",
        ]
        # Optional kwargs.
        for name in ("get_version", "notify"):
            assert name in params

    def test_get_version_kwarg_is_optional(self):
        param = inspect.signature(
            WizardSection.__init__,
        ).parameters["get_version"]
        assert param.default is None

    def test_notify_kwarg_is_optional(self):
        param = inspect.signature(
            WizardSection.__init__,
        ).parameters["notify"]
        assert param.default is None

    def test_constructor_stores_data_dir(self, qapp, tmp_path):
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        assert sec.data_dir == tmp_path
        assert isinstance(sec.data_dir, Path)

    def test_constructor_stores_quit_flag(self, qapp, tmp_path):
        flag = threading.Event()
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=flag,
        )
        assert sec.quit_flag is flag


# ------------------------------------------------------------------ #
# build_qmenu — structural / order
# ------------------------------------------------------------------ #

class TestBuildQMenuStructure:

    def test_returns_qmenu(self, wizard_section):
        menu = wizard_section.build_qmenu()
        assert isinstance(menu, QMenu)

    def test_action_order_and_separators(self, wizard_section):
        menu = wizard_section.build_qmenu()
        actions = menu.actions()
        assert len(actions) == len(EXPECTED_ACTIONS_IN_ORDER), (
            f"Expected {len(EXPECTED_ACTIONS_IN_ORDER)} actions, "
            f"got {len(actions)}: "
            f"{[(a.text(), a.isSeparator()) for a in actions]}"
        )
        for action, (attr, _) in zip(actions, EXPECTED_ACTIONS_IN_ORDER):
            if attr == "__sep__":
                assert action.isSeparator()
            else:
                assert not action.isSeparator()
                assert action is getattr(wizard_section, attr)

    def test_enabled_action_texts_match_spec(self, wizard_section, mocker):
        """The five enabled actions, in order, expose the FR-5 labels."""
        # Patch wizard_actions_enabled so refresh doesn't grey them
        # (we want to read the labels, not the enabled state).
        mocker.patch.object(
            wizard_section, "wizard_actions_enabled", return_value=True,
        )
        mocker.patch.object(
            wizard_section, "quit_enabled", return_value=True,
        )
        wizard_section.build_qmenu()
        texts = [
            wizard_section._act_open_wizard.text(),
            wizard_section._act_copy_token.text(),
            wizard_section._act_view_logs.text(),
            wizard_section._act_about.text(),
            wizard_section._act_quit.text(),
        ]
        assert texts == EXPECTED_ENABLED_TEXTS

    def test_no_manager_only_actions_present(self, wizard_section):
        """AC-WizardMenu: wizard menu must NOT contain manager-only
        items (Open Dashboard, Workers online, Restart Manager, etc.)."""
        menu = wizard_section.build_qmenu()
        forbidden = {
            "Open Dashboard",
            "Restart Manager",
            "View Manager Logs",
            "Quit Manager",
            "Workers online",
        }
        labels = {a.text() for a in menu.actions() if not a.isSeparator()}
        # Filter out the dynamic header/footer / leakage substrings.
        for forbidden_text in forbidden:
            for label in labels:
                assert forbidden_text not in label, (
                    f"Forbidden manager-phase label leaked into "
                    f"wizard menu: {label!r}"
                )

    def test_disabled_header_and_footer(self, wizard_section):
        wizard_section.build_qmenu()
        assert wizard_section._act_header.isEnabled() is False
        assert wizard_section._act_footer.isEnabled() is False

    def test_enabled_actions_use_qaction_class(self, wizard_section):
        from PySide6.QtGui import QAction
        wizard_section.build_qmenu()
        for attr in (
            "_act_open_wizard", "_act_copy_token",
            "_act_view_logs", "_act_about", "_act_quit",
        ):
            assert isinstance(getattr(wizard_section, attr), QAction)

    def test_menu_reference_stored(self, wizard_section):
        menu = wizard_section.build_qmenu()
        assert wizard_section._menu is menu

    def test_build_qmenu_idempotent_rebuild(self, wizard_section):
        first = wizard_section.build_qmenu()
        second = wizard_section.build_qmenu()
        assert first is not second
        assert wizard_section._menu is second


# ------------------------------------------------------------------ #
# rebuild() aliases build_qmenu
# ------------------------------------------------------------------ #

class TestRebuildAlias:

    def test_rebuild_returns_qmenu(self, wizard_section):
        menu = wizard_section.rebuild()
        assert isinstance(menu, QMenu)

    def test_rebuild_delegates_to_build_qmenu(self, wizard_section, mocker):
        spy = mocker.spy(wizard_section, "build_qmenu")
        wizard_section.rebuild()
        spy.assert_called_once()


# ------------------------------------------------------------------ #
# Header + footer text
# ------------------------------------------------------------------ #

class TestHeaderFooter:

    def test_header_text_static_string(self, wizard_section):
        assert wizard_section.header_text() == "[Wizard] Setup wizard running"

    def test_footer_uses_get_version(self, wizard_section):
        assert wizard_section.footer_text() == "v1.2.3"

    def test_footer_question_mark_when_no_version(self, qapp, tmp_path):
        sec = WizardSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        # Default get_version returns "?".
        assert sec.footer_text() == "v?"

    def test_refresh_updates_header_and_footer_text(
        self, wizard_section, mocker,
    ):
        wizard_section.build_qmenu()
        # After build, refresh() runs once internally and sets text.
        assert wizard_section._act_header.text() == (
            "[Wizard] Setup wizard running"
        )
        assert wizard_section._act_footer.text() == "v1.2.3"


# ------------------------------------------------------------------ #
# refresh — tolerance (no menu)
# ------------------------------------------------------------------ #

class TestRefreshTolerance:

    def test_refresh_before_build_is_noop(self, wizard_section):
        # Must not raise even though no menu has been built.
        wizard_section.refresh()

    def test_refresh_accepts_snapshot_arg_but_ignores_it(
        self, wizard_section, mocker,
    ):
        wizard_section.build_qmenu()
        # refresh accepts a snapshot kwarg for signal-slot parity but
        # the wizard predicates read from disk on each call.
        bogus = object()
        # Must not raise.
        wizard_section.refresh(snapshot=bogus)


# ------------------------------------------------------------------ #
# Data dir parameter type invariant
# ------------------------------------------------------------------ #

class TestDataDirPath:

    def test_data_dir_is_pathlib_path(self, wizard_section):
        assert isinstance(wizard_section.data_dir, Path)
