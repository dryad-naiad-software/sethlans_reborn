# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Click-handler and notify-wiring tests for
``shared/tray/menu_manager.py``.

Covers:

* ``TestClickHandlers`` — handler bodies for Open Dashboard, Open
  Wizard, View Logs, About, Restart Manager, Quit Manager.
* ``TestCopyTokenHandler`` — the guarded ``on_copy_token`` flow
  (sentinel, missing token, malformed token, valid token).
* ``TestTriggeredWiring`` — ``QAction.triggered`` actually reaches the
  bound handler once ``build_qmenu`` has run.
* ``TestNotifyWiring`` — the optional ``notify`` closure added in
  Phase 8 (desktop notification on successful token copy; token value
  never appears in the payload; notify exceptions are swallowed).

Structural / refresh tests live in ``test_menu_manager_core.py`` and
``test_menu_manager_refresh.py``.  Shared fixtures (``section``,
``section_factory``, ``make_snapshot``) come from ``conftest.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for menu_manager")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from shared.tray import menu_manager as qmm  # noqa: E402
from shared.tray.notifications import NotificationEvent  # noqa: E402


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
        args, _ = spy.call_args
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


# ------------------------------------------------------------------ #
# on_copy_token — guarded against missing / malformed tokens
# ------------------------------------------------------------------ #

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
# QAction.triggered wiring
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
        self, section_factory, mocker,
    ):
        notify = mocker.MagicMock()
        sec = section_factory(notify=notify, token="a" * 40)
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
        self, section_factory, mocker,
    ):
        notify = mocker.MagicMock()
        sec = section_factory(notify=notify, token="a" * 40)
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=False,
        )
        sec.on_copy_token()
        notify.assert_not_called()

    def test_token_value_never_appears_in_notification(
        self, section_factory, mocker,
    ):
        """Security invariant: the user-facing notification carries no
        token data even on successful copy."""
        # Identifiable in a payload scan.
        token = "secretTOKEN" + "X" * 30
        captured = []

        def _capture(evt):
            captured.append(evt)

        sec = section_factory(notify=_capture, token=token)
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        sec.on_copy_token()
        assert len(captured) == 1
        evt = captured[0]
        assert token not in evt.title
        assert token not in evt.message

    def test_notify_exception_is_swallowed(self, section_factory, mocker):
        notify = mocker.MagicMock(side_effect=RuntimeError("boom"))
        sec = section_factory(notify=notify, token="a" * 40)
        mocker.patch.object(
            qmm, "copy_token_to_clipboard", return_value=True,
        )
        # Must not raise; ``on_copy_token`` catches notify failures.
        sec.on_copy_token()
