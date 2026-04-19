# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_menu_manager_helpers.py``.

Pure helpers (no Qt) consumed by ``qt_menu_manager.ManagerSection``:
sentinel detection, token read from manager.ini, token shape
validation, and platform-specific log-opener dispatch.
"""

from __future__ import annotations

import logging
import sys

from shared.tray import qt_menu_manager_helpers as helpers
from shared.tray.qt_menu_manager_helpers import (
    SENTINEL_NAME,
    open_logs,
    read_token,
    sentinel_exists,
    validate_token,
)


class TestSentinelExists:

    def test_returns_false_when_no_sentinels_present(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        assert sentinel_exists(manager_dir) is False

    def test_returns_true_when_current_sentinel_present(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        (manager_dir / SENTINEL_NAME).write_text("{}", encoding="utf-8")
        assert sentinel_exists(manager_dir) is True

    def test_returns_true_when_legacy_sentinel_present(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        (tmp_path / ".setup_complete").write_text("", encoding="utf-8")
        assert sentinel_exists(manager_dir) is True

    def test_returns_true_when_both_present(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        (manager_dir / SENTINEL_NAME).write_text("{}", encoding="utf-8")
        (tmp_path / ".setup_complete").write_text("", encoding="utf-8")
        assert sentinel_exists(manager_dir) is True


class TestReadToken:

    def _write_ini(self, manager_dir, body):
        path = manager_dir / "manager.ini"
        path.write_text(body, encoding="utf-8")
        return path

    def test_returns_empty_when_ini_missing(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        assert read_token(manager_dir) == ""

    def test_returns_token_when_present(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        self._write_ini(
            manager_dir,
            "[setup]\ntoken = abc123XYZ_token-1234567890ABCDEF\n",
        )
        assert read_token(manager_dir) == "abc123XYZ_token-1234567890ABCDEF"

    def test_returns_empty_when_no_setup_section(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        self._write_ini(manager_dir, "[other]\nfoo = bar\n")
        assert read_token(manager_dir) == ""

    def test_returns_empty_when_setup_has_no_token(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        self._write_ini(manager_dir, "[setup]\nother = value\n")
        assert read_token(manager_dir) == ""

    def test_returns_empty_when_token_empty_string(self, tmp_path):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        self._write_ini(manager_dir, "[setup]\ntoken =\n")
        assert read_token(manager_dir) == ""

    def test_returns_empty_when_ini_oversized(self, tmp_path, caplog):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        # Fabricate an oversized (>1024 byte) ini body.
        body = "[setup]\ntoken = abc\n" + ("x = " + "y" * 40 + "\n") * 100
        assert len(body.encode("utf-8")) > helpers._MAX_INI_BYTES
        self._write_ini(manager_dir, body)
        with caplog.at_level(logging.WARNING, logger=helpers.logger.name):
            result = read_token(manager_dir)
        assert result == ""
        assert any(
            "exceeds" in rec.message for rec in caplog.records
        )

    def test_returns_empty_when_ini_malformed(self, tmp_path, caplog):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        # No section header — configparser raises MissingSectionHeaderError.
        self._write_ini(manager_dir, "token = oops_no_section\n")
        with caplog.at_level(logging.WARNING, logger=helpers.logger.name):
            result = read_token(manager_dir)
        assert result == ""
        assert any(
            "Could not parse manager.ini" in rec.message
            for rec in caplog.records
        )

    def test_returns_empty_when_stat_raises(self, tmp_path, mocker):
        manager_dir = tmp_path / "manager"
        manager_dir.mkdir()
        self._write_ini(manager_dir, "[setup]\ntoken = x\n")
        mocker.patch(
            "pathlib.Path.stat", side_effect=OSError("denied"),
        )
        assert read_token(manager_dir) == ""


class TestValidateToken:

    def test_rejects_empty_string(self):
        assert validate_token("") is False

    def test_rejects_none_like_input_empty(self):
        # Function signature is str, but defensive handling for ""/None-ish.
        assert validate_token("") is False

    def test_accepts_30_char_token(self):
        assert validate_token("a" * 30) is True

    def test_accepts_60_char_token(self):
        assert validate_token("a" * 60) is True

    def test_accepts_mixed_alnum_underscore_hyphen(self):
        assert validate_token("ABCdef123_-ABCdef123_-ABCdef1234") is True

    def test_rejects_29_char_token(self):
        assert validate_token("a" * 29) is False

    def test_rejects_61_char_token(self):
        assert validate_token("a" * 61) is False

    def test_rejects_token_with_space(self):
        assert validate_token("a" * 20 + " " + "a" * 20) is False

    def test_rejects_token_with_special_characters(self):
        assert validate_token("a" * 30 + "!") is False

    def test_rejects_token_with_plus(self):
        assert validate_token("aaaaaaaaaaaaaaaaaaaaaaaaaaaaa+") is False

    def test_rejects_token_with_slash(self):
        assert validate_token("aaaaaaaaaaaaaaaaaaaaaaaaaaaaa/") is False

    def test_rejects_token_with_internal_newline(self):
        # Internal newlines must be rejected (cannot appear in a valid
        # Crockford base32-ish token shape).
        assert validate_token("a" * 15 + "\n" + "a" * 15) is False

    def test_logs_warning_on_invalid_shape(self, caplog):
        with caplog.at_level(logging.WARNING, logger=helpers.logger.name):
            validate_token("bad!")
        assert any(
            "invalid shape" in rec.message for rec in caplog.records
        )

    def test_warning_does_not_include_token_value(self, caplog):
        with caplog.at_level(logging.WARNING, logger=helpers.logger.name):
            validate_token("SECRET_TOKEN_VALUE!")
        combined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "SECRET_TOKEN_VALUE" not in combined


class TestOpenLogs:

    def test_no_op_when_log_missing(self, tmp_path, mocker, caplog):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mocker.patch.object(sys, "platform", "win32")
        run_spy = mocker.patch("shared.tray.qt_menu_manager_helpers."
                               "subprocess.run")
        with caplog.at_level(logging.WARNING, logger=helpers.logger.name):
            open_logs(data_dir)
        run_spy.assert_not_called()
        assert any(
            "Manager log not found" in rec.message
            for rec in caplog.records
        )

    def _prepare_log(self, tmp_path):
        data_dir = tmp_path / "data"
        (data_dir / "logs").mkdir(parents=True)
        log_path = data_dir / "logs" / "manager.log"
        log_path.write_text("hello", encoding="utf-8")
        return data_dir, log_path

    def test_windows_uses_os_startfile(self, tmp_path, mocker):
        data_dir, log_path = self._prepare_log(tmp_path)
        mocker.patch.object(sys, "platform", "win32")
        run_spy = mocker.patch("shared.tray.qt_menu_manager_helpers."
                               "subprocess.run")
        # os.startfile only exists on Windows — patch via the os module
        # import that open_logs performs locally.
        import os as real_os
        startfile = mocker.patch.object(
            real_os, "startfile", create=True,
        )
        open_logs(data_dir)
        startfile.assert_called_once_with(str(log_path))
        run_spy.assert_not_called()

    def test_darwin_uses_open_command(self, tmp_path, mocker):
        data_dir, log_path = self._prepare_log(tmp_path)
        mocker.patch.object(sys, "platform", "darwin")
        run_spy = mocker.patch("shared.tray.qt_menu_manager_helpers."
                               "subprocess.run")
        open_logs(data_dir)
        run_spy.assert_called_once_with(
            ["open", str(log_path)], check=False,
        )

    def test_linux_uses_xdg_open(self, tmp_path, mocker):
        data_dir, log_path = self._prepare_log(tmp_path)
        mocker.patch.object(sys, "platform", "linux")
        run_spy = mocker.patch("shared.tray.qt_menu_manager_helpers."
                               "subprocess.run")
        open_logs(data_dir)
        run_spy.assert_called_once_with(
            ["xdg-open", str(log_path)], check=False,
        )
