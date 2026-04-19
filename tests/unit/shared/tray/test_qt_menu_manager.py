# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_menu_manager.py`` (FR-2, NFR-1).

Covers QMenu construction, refresh-driven dynamic state updates,
click-handler wiring, and signature-preservation vs legacy pystray
``menu_manager.ManagerSection``.
"""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_menu_manager")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray import qt_menu_manager as qmm  # noqa: E402
from shared.tray.menu_manager import (  # noqa: E402
    ManagerSection as LegacyManagerSection,
)
from shared.tray.qt_menu_manager import ManagerSection  # noqa: E402
from shared.tray.qt_poller import ManagerSnapshot  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers / fixtures
# ------------------------------------------------------------------ #

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
    ("_act_quit", True),
    ("_act_about", True),
    ("__sep__", None),
    ("_act_footer", False),
]

EXPECTED_ENABLED_TEXTS = [
    "Open Dashboard",
    "Copy Setup Token",
    "Open Setup Wizard",
    "Restart Manager",
    "View Manager Logs",
    "Quit Manager",
    "About Sethlans",
]


def _make_snapshot(**overrides):
    defaults = dict(
        state="running",
        setup_mode=False,
        workers_online=3,
        jobs_queued=7,
        jobs_rendering=2,
        version="1.2.3",
        boot_id="boot",
        last_error="",
    )
    defaults.update(overrides)
    return ManagerSnapshot(**defaults)


@pytest.fixture
def snapshot_holder():
    """Mutable holder so tests can swap the snapshot returned by the
    ``get_snapshot`` callable after ``build_qmenu`` is called."""
    holder = {"snap": _make_snapshot()}

    def _get():
        return holder["snap"]

    holder["get"] = _get
    return holder


@pytest.fixture
def section(qapp, tmp_path, snapshot_holder):
    quit_flag = threading.Event()
    sec = ManagerSection(
        data_dir=tmp_path / "data",
        manager_data_dir=tmp_path / "manager",
        manager_host="localhost",
        manager_port=8443,
        quit_requested_flag=quit_flag,
        get_snapshot=snapshot_holder["get"],
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "manager").mkdir()
    return sec


# ------------------------------------------------------------------ #
# Constructor signature preservation
# ------------------------------------------------------------------ #

class TestConstructorSignature:

    def test_constructor_mirrors_legacy_section(self):
        qt_params = list(
            inspect.signature(ManagerSection.__init__).parameters.keys(),
        )
        legacy_params = list(
            inspect.signature(
                LegacyManagerSection.__init__,
            ).parameters.keys(),
        )
        # Qt version extends the legacy signature with an optional
        # ``notify`` kwarg (Phase 8 wiring for the "Token copied"
        # desktop notification).  Legacy params must be preserved in
        # order at the front of the Qt signature so existing callers
        # work unchanged.
        assert qt_params[:len(legacy_params)] == legacy_params
        # ``notify`` must be the only new parameter and it must be
        # optional (default None) so constructing without it is
        # backwards-compatible.
        assert qt_params[len(legacy_params):] == ["notify"]
        notify_param = inspect.signature(
            ManagerSection.__init__,
        ).parameters["notify"]
        assert notify_param.default is None

    def test_constructor_stores_all_args(self, qapp, tmp_path):
        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "d",
            manager_data_dir=tmp_path / "m",
            manager_host="h",
            manager_port=1234,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
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
        texts = [
            section._act_open_dashboard.text(),
            section._act_copy_token.text(),
            section._act_open_wizard.text(),
            section._act_restart.text(),
            section._act_view_logs.text(),
            section._act_quit.text(),
            section._act_about.text(),
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
        self, section, snapshot_holder, mocker,
    ):
        section.build_qmenu()
        # Replace get_snapshot with a Mock wrapping the real callable
        # so we can both observe call count and return a fresh value.
        snapshot_holder["snap"] = _make_snapshot(
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
# refresh — dynamic text
# ------------------------------------------------------------------ #

class TestRefreshText:

    def test_header_reflects_running(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(state="running")
        section.refresh()
        assert section._act_header.text() == "[Manager] Running"

    def test_header_reflects_starting(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(state="starting")
        section.refresh()
        assert section._act_header.text() == "[Manager] Starting..."

    def test_header_reflects_stopped(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(state="stopped")
        section.refresh()
        assert section._act_header.text() == "[Manager] Stopped"

    def test_header_reflects_error_with_truncation(
        self, section, snapshot_holder,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(
            state="error", last_error="x" * 80,
        )
        section.refresh()
        # Only first 40 chars of last_error in the header.
        assert section._act_header.text().startswith("[Manager] Error: ")
        assert section._act_header.text().count("x") == 40

    def test_setup_line_ready_when_sentinel_exists(
        self, section, tmp_path,
    ):
        section.build_qmenu()
        (tmp_path / "manager" / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        section.refresh()
        assert section._act_setup.text() == "Setup: Ready"

    def test_setup_line_in_progress(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(setup_mode=True)
        section.refresh()
        assert section._act_setup.text() == "Setup: In progress"

    def test_setup_line_needed(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(setup_mode=False)
        section.refresh()
        assert section._act_setup.text() == "Setup: Needed"

    def test_workers_line_reflects_snapshot(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(workers_online=42)
        section.refresh()
        assert section._act_workers.text() == "Workers online: 42"

    def test_jobs_line_reflects_snapshot(self, section, snapshot_holder):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(
            jobs_queued=11, jobs_rendering=4,
        )
        section.refresh()
        assert "Jobs queued: 11" in section._act_jobs.text()
        assert "Rendering: 4" in section._act_jobs.text()

    def test_footer_reflects_version_and_port(
        self, section, snapshot_holder,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(version="9.9.9")
        section.refresh()
        assert section._act_footer.text() == "v9.9.9 -- :8443"

    def test_footer_uses_question_mark_when_no_version(
        self, section, snapshot_holder,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = _make_snapshot(version="")
        section.refresh()
        assert section._act_footer.text() == "v? -- :8443"


# ------------------------------------------------------------------ #
# refresh — visibility predicates
# ------------------------------------------------------------------ #

class TestRefreshVisibility:

    def test_copy_token_visible_when_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "setup_token_available", return_value=True,
        )
        section.refresh()
        assert section._act_copy_token.isVisible() is True

    def test_copy_token_hidden_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "setup_token_available", return_value=False,
        )
        section.refresh()
        assert section._act_copy_token.isVisible() is False

    def test_open_wizard_visible_when_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "wizard_visible", return_value=True,
        )
        section.refresh()
        assert section._act_open_wizard.isVisible() is True

    def test_open_wizard_hidden_when_predicate_false(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "wizard_visible", return_value=False,
        )
        section.refresh()
        assert section._act_open_wizard.isVisible() is False

    def test_workers_and_jobs_visible_when_counts_visible_true(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "counts_visible", return_value=True,
        )
        section.refresh()
        assert section._act_workers.isVisible() is True
        assert section._act_jobs.isVisible() is True

    def test_workers_and_jobs_hidden_when_counts_visible_false(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "counts_visible", return_value=False,
        )
        section.refresh()
        assert section._act_workers.isVisible() is False
        assert section._act_jobs.isVisible() is False


# ------------------------------------------------------------------ #
# refresh — enabled predicates
# ------------------------------------------------------------------ #

class TestRefreshEnabled:

    def test_restart_enabled_reflects_predicate_true(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(section, "restart_enabled", return_value=True)
        section.refresh()
        assert section._act_restart.isEnabled() is True

    def test_restart_disabled_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "restart_enabled", return_value=False)
        section.refresh()
        assert section._act_restart.isEnabled() is False

    def test_quit_enabled_reflects_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "quit_enabled", return_value=True)
        section.refresh()
        assert section._act_quit.isEnabled() is True

    def test_quit_disabled_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "quit_enabled", return_value=False)
        section.refresh()
        assert section._act_quit.isEnabled() is False


# ------------------------------------------------------------------ #
# Predicate implementations (setup_token_available etc.)
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
        self, section, snapshot_holder,
    ):
        snapshot_holder["snap"] = _make_snapshot(state="running")
        assert section.counts_visible() is True

    def test_counts_visible_false_when_state_starting(
        self, section, snapshot_holder,
    ):
        snapshot_holder["snap"] = _make_snapshot(state="starting")
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
# Click handlers
# ------------------------------------------------------------------ #

class TestClickHandlers:

    def test_on_open_dashboard_opens_https_url(self, section, mocker):
        open_spy = mocker.patch.object(qmm.webbrowser, "open")
        section.on_open_dashboard()
        open_spy.assert_called_once_with("https://localhost:8443/")

    def test_on_open_wizard_opens_setup_url(self, section, mocker):
        open_spy = mocker.patch.object(qmm.webbrowser, "open")
        section.on_open_wizard()
        open_spy.assert_called_once_with("https://localhost:8443/setup/")

    def test_on_view_logs_invokes_helper(self, section, mocker, tmp_path):
        open_spy = mocker.patch.object(qmm, "open_logs")
        section.on_view_logs()
        open_spy.assert_called_once_with(section.data_dir)

    def test_on_about_invokes_show_dialog(self, section, mocker):
        spy = mocker.patch.object(qmm, "show_about_dialog")
        section.build_qmenu()
        section.on_about()
        spy.assert_called_once()

    def test_on_about_passes_menu_as_parent(self, section, mocker):
        spy = mocker.patch.object(qmm, "show_about_dialog")
        section.build_qmenu()
        section.on_about()
        args, kwargs = spy.call_args
        assert args[0] is section._menu

    def test_on_restart_manager_calls_ipc_request_restart(
        self, section, mocker,
    ):
        spy = mocker.patch.object(qmm.ipc, "request_restart")
        section.on_restart_manager()
        spy.assert_called_once_with(section.data_dir)

    def test_on_quit_manager_sets_flag_and_writes_marker(
        self, section, mocker,
    ):
        spy = mocker.patch.object(qmm.ipc, "request_quit")
        assert not section.quit_flag.is_set()
        section.on_quit_manager()
        assert section.quit_flag.is_set()
        spy.assert_called_once_with(section.data_dir, target="manager")


class TestCopyTokenHandler:

    def test_noop_when_sentinel_exists(self, section, mocker, tmp_path):
        (tmp_path / "manager" / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        copy_spy = mocker.patch.object(qmm, "copy_token_to_clipboard")
        # Must not raise.
        section.on_copy_token()
        copy_spy.assert_not_called()

    def test_noop_when_token_missing(self, section, mocker):
        # No manager.ini written.
        copy_spy = mocker.patch.object(qmm, "copy_token_to_clipboard")
        section.on_copy_token()
        copy_spy.assert_not_called()

    def test_noop_when_token_malformed(self, section, mocker, tmp_path):
        (tmp_path / "manager" / "manager.ini").write_text(
            "[setup]\ntoken = bad!\n", encoding="utf-8",
        )
        copy_spy = mocker.patch.object(qmm, "copy_token_to_clipboard")
        section.on_copy_token()
        copy_spy.assert_not_called()

    def test_copies_valid_token(self, section, mocker, tmp_path):
        token = "a" * 40
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        copy_spy = mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        section.on_copy_token()
        copy_spy.assert_called_once_with(token)

    def test_does_not_raise_when_copy_returns_false(
        self, section, mocker, tmp_path,
    ):
        token = "a" * 40
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=False,
        )
        # Must not raise.
        section.on_copy_token()


# ------------------------------------------------------------------ #
# QAction.triggered wiring verifies slots reach handlers
# ------------------------------------------------------------------ #

class TestTriggeredWiring:

    def test_open_dashboard_triggered_invokes_handler(
        self, section, mocker,
    ):
        open_spy = mocker.patch.object(qmm.webbrowser, "open")
        section.build_qmenu()
        section._act_open_dashboard.trigger()
        open_spy.assert_called_once_with("https://localhost:8443/")

    def test_about_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(qmm, "show_about_dialog")
        section.build_qmenu()
        section._act_about.trigger()
        spy.assert_called_once()

    def test_view_logs_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(qmm, "open_logs")
        section.build_qmenu()
        section._act_view_logs.trigger()
        spy.assert_called_once_with(section.data_dir)


# ------------------------------------------------------------------ #
# Data dir parameter types
# ------------------------------------------------------------------ #

class TestDataDirPaths:

    def test_data_dir_is_pathlib_path(self, section):
        assert isinstance(section.data_dir, Path)
        assert isinstance(section.manager_data_dir, Path)


# ------------------------------------------------------------------ #
# Phase 8 re-wire: notify callable + "Token copied" notification
# ------------------------------------------------------------------ #

class TestNotifyWiring:
    """Phase 8 re-wires the "Token copied" desktop notification via the
    optional ``notify`` kwarg on ``ManagerSection``."""

    def test_notify_defaults_to_none(self, section):
        assert section._notify is None

    def test_on_copy_token_does_not_raise_when_notify_is_none(
        self, section, mocker, tmp_path,
    ):
        token = "a" * 40
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        # Must not raise even with notify=None (the default).
        section.on_copy_token()

    def test_on_copy_token_emits_notification_on_success(
        self, qapp, mocker, tmp_path,
    ):
        from shared.tray.qt_notifications import NotificationEvent
        token = "a" * 40
        (tmp_path / "manager").mkdir()
        (tmp_path / "data").mkdir()
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        notify = mocker.MagicMock()
        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "data",
            manager_data_dir=tmp_path / "manager",
            manager_host="localhost",
            manager_port=8443,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
            notify=notify,
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        sec.on_copy_token()
        assert notify.call_count == 1
        (args, _) = notify.call_args
        evt = args[0]
        assert isinstance(evt, NotificationEvent)
        assert evt.title == "Token copied"
        assert evt.message == "Setup token copied to clipboard."

    def test_on_copy_token_does_not_emit_notification_on_failure(
        self, qapp, mocker, tmp_path,
    ):
        token = "a" * 40
        (tmp_path / "manager").mkdir()
        (tmp_path / "data").mkdir()
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        notify = mocker.MagicMock()
        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "data",
            manager_data_dir=tmp_path / "manager",
            manager_host="localhost",
            manager_port=8443,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
            notify=notify,
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=False,
        )
        sec.on_copy_token()
        notify.assert_not_called()

    def test_token_value_never_appears_in_notification(
        self, qapp, mocker, tmp_path,
    ):
        """Security invariant: the user-facing notification carries no
        token data even on successful copy."""
        token = "secretTOKEN" + "X" * 30  # identifiable in a payload scan
        (tmp_path / "manager").mkdir()
        (tmp_path / "data").mkdir()
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        captured = []

        def _capture(evt):
            captured.append(evt)

        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "data",
            manager_data_dir=tmp_path / "manager",
            manager_host="localhost",
            manager_port=8443,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
            notify=_capture,
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        sec.on_copy_token()
        assert len(captured) == 1
        evt = captured[0]
        assert token not in evt.title
        assert token not in evt.message

    def test_notify_exception_is_swallowed(self, qapp, mocker, tmp_path):
        token = "a" * 40
        (tmp_path / "manager").mkdir()
        (tmp_path / "data").mkdir()
        (tmp_path / "manager" / "manager.ini").write_text(
            f"[setup]\ntoken = {token}\n", encoding="utf-8",
        )
        notify = mocker.MagicMock(side_effect=RuntimeError("boom"))
        flag = threading.Event()
        sec = ManagerSection(
            data_dir=tmp_path / "data",
            manager_data_dir=tmp_path / "manager",
            manager_host="localhost",
            manager_port=8443,
            quit_requested_flag=flag,
            get_snapshot=lambda: _make_snapshot(),
            notify=notify,
        )
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        # Must not raise; ``on_copy_token`` catches notify failures.
        sec.on_copy_token()
