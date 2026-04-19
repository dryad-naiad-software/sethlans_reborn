# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_clipboard.py`` (tray Phase 3: FR-4, AC-5).

Covers token-never-logged, Mode.Clipboard pinning, graceful failure on
missing QApplication/clipboard, and the qInstallMessageHandler
redaction of qt.gui.clipboard records (other Qt categories forwarded).
"""

from __future__ import annotations

import logging
import re

import pytest

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QClipboard, QGuiApplication

from shared.tray import qt_clipboard


_TOKEN = "Bb4BMMMGqXqAPv9M-sY8Y6q2VPl0kjsqQAQYcH30Qoc"


@pytest.fixture(autouse=True)
def _reset_qt_message_handler():
    """Snapshot/restore process-global Qt message handler + install flag."""
    prior_installed = qt_clipboard._handler_installed
    prior_previous = qt_clipboard._previous_handler
    qInstallMessageHandler(None)
    qt_clipboard._handler_installed = False
    qt_clipboard._previous_handler = None
    try:
        yield
    finally:
        qInstallMessageHandler(None)
        qt_clipboard._handler_installed = prior_installed
        qt_clipboard._previous_handler = prior_previous


class _FakeContext:
    """Stand-in for ``QMessageLogContext`` for direct handler invocation."""

    def __init__(self, category: str = ""):
        self.category = category
        self.file = None
        self.line = 0
        self.function = None


def _joined(caplog):
    return " ".join(rec.getMessage() for rec in caplog.records)


def _assert_no_token(caplog):
    assert _TOKEN not in _joined(caplog), "Token appeared in log output"


def _assert_token_len_logged(caplog):
    assert re.search(r"token_len=\d+", _joined(caplog)) is not None


class TestInputValidation:

    def test_empty_token_returns_false_no_qt_call(self, mocker):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        assert qt_clipboard.copy_token_to_clipboard("") is False
        fake.instance.assert_not_called()
        fake.clipboard.assert_not_called()

    def test_none_returns_false_no_qt_call(self, mocker):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        assert qt_clipboard.copy_token_to_clipboard(None) is False  # type: ignore[arg-type]
        fake.instance.assert_not_called()

    @pytest.mark.parametrize("value", [123, b"bytes", ["list"], 1.5, object()])
    def test_non_string_types_return_false_no_qt_call(self, mocker, value):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        assert qt_clipboard.copy_token_to_clipboard(value) is False  # type: ignore[arg-type]
        fake.instance.assert_not_called()

    def test_never_raises_on_bad_input(self):
        try:
            for bad in (None, "", 123, b"bytes"):
                qt_clipboard.copy_token_to_clipboard(bad)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"copy_token_to_clipboard raised {exc!r}")


class TestHappyPath:

    def test_valid_token_returns_true(self, qapp):
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is True

    def test_round_trip_clipboard_mode(self, qapp):
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is True
        text = QGuiApplication.clipboard().text(QClipboard.Mode.Clipboard)
        assert text == _TOKEN


class TestModePinning:
    """Spec FR-4: Mode.Clipboard only; never Selection / FindBuffer."""

    def test_setText_called_with_clipboard_mode(self, mocker):
        fake_clip = mocker.MagicMock()
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = mocker.sentinel.app
        fake.clipboard.return_value = fake_clip
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is True
        fake_clip.setText.assert_called_once_with(
            _TOKEN, QClipboard.Mode.Clipboard,
        )

    def test_selection_mode_never_written(self, qapp):
        """X11 primary-selection guard: Mode.Selection stays untouched."""
        real = QGuiApplication.clipboard()
        sentinel = "PRE_EXISTING_SELECTION_VALUE"
        real.setText(sentinel, QClipboard.Mode.Selection)
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is True
        selection = real.text(QClipboard.Mode.Selection)
        assert _TOKEN not in selection
        assert selection in (sentinel, "")

    def test_findbuffer_mode_never_written(self, qapp):
        real = QGuiApplication.clipboard()
        if not real.supportsFindBuffer():
            pytest.skip("Platform has no FindBuffer")
        real.setText("PRIOR_FIND", QClipboard.Mode.FindBuffer)
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is True
        assert _TOKEN not in real.text(QClipboard.Mode.FindBuffer)


class TestSecurityInvariants:

    def test_no_token_in_logs_on_success(self, qapp, caplog):
        with caplog.at_level(logging.DEBUG, logger=qt_clipboard.logger.name):
            qt_clipboard.copy_token_to_clipboard(_TOKEN)
        _assert_no_token(caplog)

    def test_no_qapplication_logs_token_len_only(self, mocker, caplog):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = None
        with caplog.at_level(logging.WARNING, logger=qt_clipboard.logger.name):
            result = qt_clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        _assert_no_token(caplog)
        _assert_token_len_logged(caplog)

    def test_clipboard_none_logs_token_len_only(self, mocker, caplog):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = mocker.sentinel.app
        fake.clipboard.return_value = None
        with caplog.at_level(logging.WARNING, logger=qt_clipboard.logger.name):
            result = qt_clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        _assert_no_token(caplog)
        _assert_token_len_logged(caplog)

    def test_setText_raises_returns_false_and_logs_token_len(
        self, mocker, caplog,
    ):
        fake_clip = mocker.MagicMock()
        fake_clip.setText.side_effect = RuntimeError("boom")
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = mocker.sentinel.app
        fake.clipboard.return_value = fake_clip
        with caplog.at_level(logging.WARNING, logger=qt_clipboard.logger.name):
            result = qt_clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        _assert_no_token(caplog)
        _assert_token_len_logged(caplog)

    def test_empty_token_logs_no_token_value(self, caplog):
        with caplog.at_level(logging.WARNING, logger=qt_clipboard.logger.name):
            qt_clipboard.copy_token_to_clipboard("")
        _assert_no_token(caplog)


class TestQtLoggingRedaction:
    """Spec AC-5: qt.gui.clipboard records must not leak the token."""

    def test_handler_installed_after_first_copy(self, qapp):
        assert qt_clipboard._handler_installed is False
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        assert qt_clipboard._handler_installed is True

    def test_handler_install_is_idempotent(self, qapp, mocker):
        spy = mocker.spy(qt_clipboard, "qInstallMessageHandler")
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        assert spy.call_count == 1
        assert qt_clipboard._handler_installed is True

    def test_clipboard_category_message_redacted(self, qapp, capsys):
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        capsys.readouterr()
        ctx = _FakeContext(category="qt.gui.clipboard")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtDebugMsg, ctx, f"Clipboard content: {_TOKEN}",
        )
        captured = capsys.readouterr()
        assert _TOKEN not in captured.err
        assert _TOKEN not in captured.out

    def test_clipboard_subcategory_also_redacted(self, qapp, capsys):
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        capsys.readouterr()
        ctx = _FakeContext(category="qt.gui.clipboard.debug")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtDebugMsg, ctx, f"leaked={_TOKEN}",
        )
        assert _TOKEN not in capsys.readouterr().err

    def test_non_clipboard_category_forwarded_to_previous_handler(
        self, qapp, mocker,
    ):
        previous = mocker.MagicMock()
        qInstallMessageHandler(previous)
        qt_clipboard._handler_installed = False
        qt_clipboard._previous_handler = None
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        ctx = _FakeContext(category="qt.core.plugin")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtWarningMsg, ctx, "plugin load warning",
        )
        assert previous.call_count >= 1
        args = previous.call_args.args
        assert args[0] == QtMsgType.QtWarningMsg
        assert args[2] == "plugin load warning"

    def test_non_clipboard_falls_back_to_stderr_when_no_previous(
        self, qapp, capsys,
    ):
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        capsys.readouterr()
        ctx = _FakeContext(category="qt.gui.window")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtWarningMsg, ctx, "window geometry warning",
        )
        assert "window geometry warning" in capsys.readouterr().err

    def test_empty_category_forwarded(self, qapp, capsys):
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        capsys.readouterr()
        ctx = _FakeContext(category="")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtInfoMsg, ctx, "generic info",
        )
        assert "generic info" in capsys.readouterr().err

    def test_bytes_category_decoded_and_matched(self, qapp, capsys):
        """Qt sometimes exposes category as bytes; still redacted."""
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        capsys.readouterr()
        ctx = _FakeContext(category=b"qt.gui.clipboard")
        qt_clipboard._qt_message_handler(
            QtMsgType.QtDebugMsg, ctx, f"bytes-cat leak {_TOKEN}",
        )
        assert _TOKEN not in capsys.readouterr().err

    def test_env_var_logging_rules_does_not_leak_token(
        self, qapp, monkeypatch, capsys,
    ):
        monkeypatch.setenv(
            "QT_LOGGING_RULES", "qt.gui.clipboard.debug=true",
        )
        qt_clipboard.copy_token_to_clipboard(_TOKEN)
        captured = capsys.readouterr()
        assert _TOKEN not in captured.err
        assert _TOKEN not in captured.out


class TestNoQApplication:

    def test_returns_false_when_no_qapp(self, mocker):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = None
        assert qt_clipboard.copy_token_to_clipboard(_TOKEN) is False

    def test_no_qapp_logs_warning_without_token(self, mocker, caplog):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = None
        with caplog.at_level(logging.WARNING, logger=qt_clipboard.logger.name):
            qt_clipboard.copy_token_to_clipboard(_TOKEN)
        joined = _joined(caplog)
        assert _TOKEN not in joined
        assert f"token_len={len(_TOKEN)}" in joined

    def test_no_qapp_does_not_raise(self, mocker):
        fake = mocker.patch.object(qt_clipboard, "QGuiApplication")
        fake.instance.return_value = None
        try:
            qt_clipboard.copy_token_to_clipboard(_TOKEN)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"copy_token_to_clipboard raised {exc!r}")
