# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Structural tests for ``shared/tray/menu_manager.py`` (FR-2, NFR-1).

Covers constructor signature, ``build_qmenu`` action order/labels,
``rebuild`` alias, ``refresh`` tolerance (missing menu / ignored arg),
the visibility/enabled predicate implementations, and data-dir type
invariants.  Dynamic-text / visibility / enabled refresh behaviour
lives in ``test_menu_manager_refresh.py``.  Click handlers and notify
wiring live in ``test_menu_manager_handlers.py``.  Shared fixtures
(``section``, ``snapshot_holder``, ``make_snapshot``) come from
``conftest.py``.
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for menu_manager")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray.menu_manager import ManagerSection  # noqa: E402


# ------------------------------------------------------------------ #
# Local constants (structural expectations)
# ------------------------------------------------------------------ #

#
# NOTE: action order updated for spec FR-6 (#166). The
# ``add_universal_actions`` helper now installs About + Quit in the
# order ``About -> separator -> Quit``, which means the manager menu
# shows ``View Manager Logs -> About Sethlans -> (universal sep) ->
# Quit Manager -> (final sep) -> footer`` (15 entries total,
# previously 14). The QActions and their click semantics are
# unchanged — only the on-screen ordering moved.
EXPECTED_ACTIONS_IN_ORDER = [
    ("_act_header", False),
    ("_act_setup", False),
    ("_act_workers", False),
    ("_act_jobs", False),
    ("__sep__", None),
    ("_act_open_dashboard", True),
    ("_act_copy_token", True),
    ("_act_open_wizard", True),
    ("_act_restart", True),
    ("_act_view_logs", True),
    ("_act_about", True),
    ("__sep__", None),
    ("_act_quit", True),
    ("__sep__", None),
    ("_act_footer", False),
]

EXPECTED_ENABLED_TEXTS = [
    "Open Dashboard",
    "Copy Setup Token",
    "Open Setup Wizard",
    "Restart Manager",
    "View Manager Logs",
    "About Sethlans",
    "Quit Manager",
]


# ------------------------------------------------------------------ #
# Constructor signature
# ------------------------------------------------------------------ #

class TestConstructorSignature:

    def test_constructor_param_order(self):
        params = list(
            inspect.signature(ManagerSection.__init__).parameters.keys(),
        )
        expected = [
            "self",
            "data_dir",
            "manager_data_dir",
            "manager_host",
            "manager_port",
            "quit_requested_flag",
            "get_snapshot",
            "notify",
        ]
        assert params == expected

    def test_notify_kwarg_is_optional(self):
        # ``notify`` must be optional (default None) so callers that
        # skip it still construct cleanly.
        notify_param = inspect.signature(
            ManagerSection.__init__,
        ).parameters["notify"]
        assert notify_param.default is None

    def test_constructor_stores_all_args(self, qapp, tmp_path, make_snapshot):
        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "d",
            manager_data_dir=tmp_path / "m",
            manager_host="h",
            manager_port=1234,
            quit_requested_flag=flag,
            get_snapshot=lambda: make_snapshot(),
        )
        assert sec.data_dir == tmp_path / "d"
        assert sec.manager_data_dir == tmp_path / "m"
        assert sec.host == "h"
        assert sec.port == 1234
        assert sec.quit_flag is flag
        assert callable(sec.get_snapshot)


# ------------------------------------------------------------------ #
# build_qmenu — structure
# ------------------------------------------------------------------ #

class TestBuildQMenuStructure:

    def test_returns_qmenu(self, section, tmp_path):
        menu = section.build_qmenu()
        assert isinstance(menu, QMenu)

    def test_action_order_and_separators(self, section):
        menu = section.build_qmenu()
        actions = menu.actions()
        # 14 entries: 12 labeled + 2 separators.
        assert len(actions) == len(EXPECTED_ACTIONS_IN_ORDER)

        for action, (attr, _) in zip(actions, EXPECTED_ACTIONS_IN_ORDER):
            if attr == "__sep__":
                assert action.isSeparator()
            else:
                assert not action.isSeparator()
                # Matches the QAction stored on the section.
                assert action is getattr(section, attr)

    def test_enabled_action_texts_match_spec(self, section):
        section.build_qmenu()
        # Order MUST match the visual order in the QMenu, which after
        # spec FR-6 (#166) is: Open Dashboard, Copy Setup Token, Open
        # Setup Wizard, Restart Manager, View Manager Logs, About
        # Sethlans, Quit Manager (About moved up, Quit moved down).
        texts = [
            section._act_open_dashboard.text(),
            section._act_copy_token.text(),
            section._act_open_wizard.text(),
            section._act_restart.text(),
            section._act_view_logs.text(),
            section._act_about.text(),
            section._act_quit.text(),
        ]
        assert texts == EXPECTED_ENABLED_TEXTS

    def test_disabled_actions_are_disabled(self, section):
        section.build_qmenu()
        for attr in ("_act_header", "_act_setup",
                     "_act_workers", "_act_jobs", "_act_footer"):
            action = getattr(section, attr)
            assert action.isEnabled() is False, attr

    def test_enabled_actions_default_enabled(self, section):
        section.build_qmenu()
        # These default to enabled; refresh may later toggle the two
        # marker-gated ones or the visibility-toggled ones.  The
        # non-toggled always-on actions must report enabled.
        for attr in ("_act_open_dashboard", "_act_restart",
                     "_act_view_logs", "_act_quit", "_act_about"):
            assert getattr(section, attr).isEnabled() is True, attr

    def test_menu_reference_stored(self, section):
        menu = section.build_qmenu()
        assert section._menu is menu

    def test_build_qmenu_idempotent_rebuild(self, section):
        first = section.build_qmenu()
        second = section.build_qmenu()
        assert first is not second
        assert section._menu is second


# ------------------------------------------------------------------ #
# rebuild() aliases build_qmenu
# ------------------------------------------------------------------ #

class TestRebuildAlias:

    def test_rebuild_returns_qmenu(self, section):
        menu = section.rebuild()
        assert isinstance(menu, QMenu)

    def test_rebuild_delegates_to_build_qmenu(self, section, mocker):
        spy = mocker.spy(section, "build_qmenu")
        section.rebuild()
        spy.assert_called_once()


# ------------------------------------------------------------------ #
# refresh — tolerances
# ------------------------------------------------------------------ #

class TestRefreshTolerance:

    def test_refresh_before_build_is_noop(self, section):
        # Must not raise even though no menu has been built.
        section.refresh()

    def test_refresh_accepts_snapshot_arg_but_ignores_it(
        self, section, snapshot_holder, make_snapshot, mocker,
    ):
        section.build_qmenu()
        # Replace get_snapshot with a Mock wrapping the real callable
        # so we can both observe call count and return a fresh value.
        snapshot_holder["snap"] = make_snapshot(
            state="running", workers_online=9,
        )
        get_mock = mocker.Mock(wraps=snapshot_holder["get"])
        section.get_snapshot = get_mock
        bogus = object()
        section.refresh(snapshot=bogus)
        # The passed object is ignored; the section re-queries.
        assert get_mock.call_count >= 1
        assert "9" in section._act_workers.text()


# ------------------------------------------------------------------ #
# Predicate implementations
# ------------------------------------------------------------------ #

class TestPredicates:

    def test_setup_token_available_false_when_sentinel(
        self, section, tmp_path,
    ):
        (tmp_path / "manager" / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        assert section.setup_token_available() is False

    def test_setup_token_available_false_without_ini(self, section):
        # No sentinel, no manager.ini.
        assert section.setup_token_available() is False

    def test_setup_token_available_true_with_valid_ini_token(
        self, section, tmp_path,
    ):
        ini = tmp_path / "manager" / "manager.ini"
        ini.write_text(
            "[setup]\ntoken = " + "a" * 32 + "\n", encoding="utf-8",
        )
        assert section.setup_token_available() is True

    def test_wizard_visible_true_when_no_sentinel(self, section):
        assert section.wizard_visible() is True

    def test_wizard_visible_false_when_sentinel(self, section, tmp_path):
        (tmp_path / "manager" / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        assert section.wizard_visible() is False

    def test_counts_visible_true_when_state_running(
        self, section, snapshot_holder, make_snapshot,
    ):
        snapshot_holder["snap"] = make_snapshot(state="running")
        assert section.counts_visible() is True

    def test_counts_visible_false_when_state_starting(
        self, section, snapshot_holder, make_snapshot,
    ):
        snapshot_holder["snap"] = make_snapshot(state="starting")
        assert section.counts_visible() is False

    def test_restart_enabled_true_without_marker(self, section):
        assert section.restart_enabled() is True

    def test_restart_enabled_false_with_marker(self, section, tmp_path):
        (tmp_path / "data" / ".restart_requested").write_text(
            "{}", encoding="utf-8",
        )
        assert section.restart_enabled() is False

    def test_quit_enabled_true_without_marker(self, section):
        assert section.quit_enabled() is True

    def test_quit_enabled_false_with_marker(self, section, tmp_path):
        (tmp_path / "data" / ".quit_requested").write_text(
            "{}", encoding="utf-8",
        )
        assert section.quit_enabled() is False


# ------------------------------------------------------------------ #
# Data dir parameter types
# ------------------------------------------------------------------ #

class TestDataDirPaths:

    def test_data_dir_is_pathlib_path(self, section):
        assert isinstance(section.data_dir, Path)
        assert isinstance(section.manager_data_dir, Path)
