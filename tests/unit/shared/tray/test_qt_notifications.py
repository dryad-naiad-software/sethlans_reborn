# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_notifications.py`` (FR-25 / FR-25a).

PySide6-based replacement for the legacy plyer notification dispatcher.
These tests mirror ``test_notifications.py`` where reasonable, but assert
on ``QSystemTrayIcon.showMessage`` semantics instead of plyer kwargs.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_notifications")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from shared.tray import qt_notifications  # noqa: E402
from shared.tray.qt_notifications import (  # noqa: E402
    APP_NAME,
    NotificationEvent,
    dispatch,
)


@pytest.fixture
def tray(qapp):
    """Provide a real ``QSystemTrayIcon`` instance bound to the qapp."""
    icon = QSystemTrayIcon()
    yield icon
    # Explicit cleanup to avoid lingering C++ objects across tests.
    icon.hide()


class TestNotificationEventDataclass:

    def test_fields_are_title_and_message(self):
        evt = NotificationEvent(title="T", message="M")
        assert evt.title == "T"
        assert evt.message == "M"

    def test_field_types_declared_as_str(self):
        fields = {f.name: f.type for f in dataclasses.fields(NotificationEvent)}
        assert fields == {"title": "str", "message": "str"}

    def test_instance_is_frozen(self):
        evt = NotificationEvent("T", "M")
        with pytest.raises(dataclasses.FrozenInstanceError):
            evt.title = "other"  # type: ignore[misc]

    def test_message_field_also_frozen(self):
        evt = NotificationEvent("T", "M")
        with pytest.raises(dataclasses.FrozenInstanceError):
            evt.message = "other"  # type: ignore[misc]

    def test_app_name_constant(self):
        assert APP_NAME == "Sethlans"


class TestHappyPath:

    def test_calls_showmessage_with_expected_args(self, tray, mocker):
        spy = mocker.patch.object(tray, "showMessage")
        evt = NotificationEvent(title="Hello", message="World")
        dispatch(tray, evt)
        spy.assert_called_once_with(
            "Hello",
            "World",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def test_dispatch_returns_none(self, tray, mocker):
        mocker.patch.object(tray, "showMessage")
        result = dispatch(tray, NotificationEvent("T", "M"))
        assert result is None

    def test_no_exception_raised(self, tray, mocker):
        mocker.patch.object(tray, "showMessage")
        # Simply must not raise.
        dispatch(tray, NotificationEvent("T", "M"))

    def test_no_warning_logs_on_success(self, tray, mocker, caplog):
        mocker.patch.object(tray, "showMessage")
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(tray, NotificationEvent("T", "M"))
        assert caplog.records == []


class TestGuardRails:

    def test_none_tray_icon_logs_warning_and_returns(self, caplog):
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            result = dispatch(None, NotificationEvent("T", "M"))
        assert result is None
        assert any(
            "not a QSystemTrayIcon" in rec.message
            for rec in caplog.records
        )

    def test_string_tray_icon_logs_warning_and_returns(self, caplog):
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            result = dispatch("not a tray", NotificationEvent("T", "M"))
        assert result is None
        assert any(
            "not a QSystemTrayIcon" in rec.message
            for rec in caplog.records
        )

    def test_int_tray_icon_logs_warning_and_returns(self, caplog):
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            result = dispatch(42, NotificationEvent("T", "M"))
        assert result is None
        assert any(
            "not a QSystemTrayIcon" in rec.message
            for rec in caplog.records
        )

    def test_guard_does_not_raise_for_any_bad_input(self):
        # None / str / int / arbitrary object — never propagate.
        for bogus in (None, "nope", 42, object(), 3.14, []):
            dispatch(bogus, NotificationEvent("T", "M"))

    def test_guard_does_not_call_showmessage(self, tray, mocker):
        # Spy on the *class* method so any instance call would register.
        spy = mocker.patch.object(
            QSystemTrayIcon, "showMessage", autospec=True,
        )
        dispatch(None, NotificationEvent("T", "M"))
        dispatch("not a tray", NotificationEvent("T", "M"))
        dispatch(42, NotificationEvent("T", "M"))
        spy.assert_not_called()


class TestExceptionSwallowing:

    def test_runtimeerror_is_swallowed(self, tray, mocker, caplog):
        mocker.patch.object(
            tray, "showMessage", side_effect=RuntimeError("simulated"),
        )
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            result = dispatch(tray, NotificationEvent("TitleX", "MsgY"))
        assert result is None

    def test_failure_log_mentions_dispatch_failed(self, tray, mocker, caplog):
        mocker.patch.object(
            tray, "showMessage", side_effect=RuntimeError("simulated"),
        )
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(tray, NotificationEvent("TitleX", "MsgY"))
        assert any(
            "Notification dispatch failed" in rec.message
            for rec in caplog.records
        )

    def test_failure_log_contains_title(self, tray, mocker, caplog):
        mocker.patch.object(
            tray, "showMessage", side_effect=RuntimeError("simulated"),
        )
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(tray, NotificationEvent("UniqueTitle_ABC", "body"))
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "UniqueTitle_ABC" in combined

    def test_oserror_is_also_swallowed(self, tray, mocker):
        mocker.patch.object(
            tray, "showMessage", side_effect=OSError("plugin dead"),
        )
        # Must not raise.
        dispatch(tray, NotificationEvent("T", "M"))

    def test_arbitrary_exception_is_swallowed(self, tray, mocker):
        class WeirdError(Exception):
            pass

        mocker.patch.object(
            tray, "showMessage", side_effect=WeirdError("???"),
        )
        dispatch(tray, NotificationEvent("T", "M"))


class TestTitleLoggedButNotMessage:

    def test_title_present_in_failure_logs(self, tray, mocker, caplog):
        mocker.patch.object(
            tray, "showMessage", side_effect=RuntimeError("boom"),
        )
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(
                tray,
                NotificationEvent("LOGGED_TITLE_XYZ", "SENSITIVE_MSG_QQQ"),
            )
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "LOGGED_TITLE_XYZ" in combined

    def test_message_absent_from_failure_logs(self, tray, mocker, caplog):
        mocker.patch.object(
            tray, "showMessage", side_effect=RuntimeError("boom"),
        )
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(
                tray,
                NotificationEvent("LOGGED_TITLE_XYZ", "SENSITIVE_MSG_QQQ"),
            )
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "SENSITIVE_MSG_QQQ" not in combined

    def test_title_present_in_guard_logs(self, caplog):
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(
                None,
                NotificationEvent("GUARD_TITLE_ABC", "GUARD_MSG_DEF"),
            )
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "GUARD_TITLE_ABC" in combined

    def test_message_absent_from_guard_logs(self, caplog):
        with caplog.at_level(
            logging.WARNING, logger=qt_notifications.logger.name,
        ):
            dispatch(
                None,
                NotificationEvent("GUARD_TITLE_ABC", "GUARD_MSG_DEF"),
            )
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "GUARD_MSG_DEF" not in combined


class TestMessageIconEnum:

    def test_third_arg_is_information_icon(self, tray, mocker):
        spy = mocker.patch.object(tray, "showMessage")
        dispatch(tray, NotificationEvent("T", "M"))
        args = spy.call_args.args
        assert args[2] is QSystemTrayIcon.MessageIcon.Information

    def test_third_arg_not_warning_or_critical(self, tray, mocker):
        spy = mocker.patch.object(tray, "showMessage")
        dispatch(tray, NotificationEvent("T", "M"))
        args = spy.call_args.args
        assert args[2] is not QSystemTrayIcon.MessageIcon.Warning
        assert args[2] is not QSystemTrayIcon.MessageIcon.Critical


class TestTimeoutMs:

    def test_fourth_arg_is_5000_ms(self, tray, mocker):
        spy = mocker.patch.object(tray, "showMessage")
        dispatch(tray, NotificationEvent("T", "M"))
        args = spy.call_args.args
        assert args[3] == 5000

    def test_timeout_is_int(self, tray, mocker):
        spy = mocker.patch.object(tray, "showMessage")
        dispatch(tray, NotificationEvent("T", "M"))
        args = spy.call_args.args
        assert isinstance(args[3], int)
