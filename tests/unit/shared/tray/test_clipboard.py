# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``shared/tray/clipboard.py`` (FR-15 / tray FR-10).

Security invariants under test:

* Token is passed via stdin — never argv, never environment.
* ``shell=False`` is guaranteed (default) and never overridden.
* Command arg is a fixed literal list (``["clip"]`` / ``["pbcopy"]``).
* Logs record only ``token_len=<N>``; the token string is NEVER logged.
* ``result.stdout``/``result.stderr`` are NEVER logged.
* All exception paths return False without raising.
"""

from __future__ import annotations

import logging
import re
import subprocess

import pytest

from shared.tray import clipboard


_TOKEN = "Bb4BMMMGqXqAPv9M-sY8Y6q2VPl0kjsqQAQYcH30Qoc"


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="STDOUT_SECRET", stderr="STDERR_SECRET"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ------------------------------------------------------------------
# Platform-specific command selection
# ------------------------------------------------------------------

class TestPlatformDispatch:

    def test_windows_uses_clip_command(self, mocker):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        run = mocker.patch.object(
            clipboard.subprocess, "run", return_value=_FakeCompleted(),
        )
        assert clipboard.copy_token_to_clipboard(_TOKEN) is True
        args, kwargs = run.call_args
        assert args == (["clip"],)
        assert kwargs["input"] == _TOKEN
        assert kwargs.get("shell") in (None, False)
        # shell kwarg must never be True
        assert kwargs.get("shell") is not True

    def test_macos_uses_pbcopy_command(self, mocker):
        mocker.patch.object(clipboard.sys, "platform", "darwin")
        run = mocker.patch.object(
            clipboard.subprocess, "run", return_value=_FakeCompleted(),
        )
        assert clipboard.copy_token_to_clipboard(_TOKEN) is True
        args, kwargs = run.call_args
        assert args == (["pbcopy"],)
        assert kwargs["input"] == _TOKEN
        assert kwargs.get("shell") is not True

    def test_linux_returns_false_without_subprocess(self, mocker):
        mocker.patch.object(clipboard.sys, "platform", "linux")
        run = mocker.patch.object(clipboard.subprocess, "run")
        assert clipboard.copy_token_to_clipboard(_TOKEN) is False
        run.assert_not_called()

    def test_linux_logs_skip_hint(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "linux")
        mocker.patch.object(clipboard.subprocess, "run")
        with caplog.at_level(logging.INFO, logger=clipboard.logger.name):
            clipboard.copy_token_to_clipboard(_TOKEN)
        assert any(
            "Clipboard copy skipped on Linux" in rec.message
            for rec in caplog.records
        )


# ------------------------------------------------------------------
# Security invariants: no token / no stdout / no stderr leakage
# ------------------------------------------------------------------

class TestSecurityInvariants:

    def _assert_no_token_leak(self, caplog):
        joined = " ".join(rec.message for rec in caplog.records)
        assert _TOKEN not in joined, "Token appeared in log output"

    def _assert_token_len_in_log(self, caplog):
        joined = " ".join(rec.message for rec in caplog.records)
        # Regex: token_len=<digits>
        assert re.search(r"token_len=\d+", joined) is not None

    def _assert_no_stdout_stderr_leak(self, caplog):
        joined = " ".join(rec.message for rec in caplog.records)
        assert "STDOUT_SECRET" not in joined
        assert "STDERR_SECRET" not in joined

    def test_file_not_found_logs_token_len_only(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        mocker.patch.object(
            clipboard.subprocess, "run",
            side_effect=FileNotFoundError("clip missing"),
        )
        with caplog.at_level(logging.WARNING, logger=clipboard.logger.name):
            result = clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        self._assert_no_token_leak(caplog)
        self._assert_token_len_in_log(caplog)

    def test_timeout_logs_token_len_only(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        mocker.patch.object(
            clipboard.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="clip", timeout=5),
        )
        with caplog.at_level(logging.WARNING, logger=clipboard.logger.name):
            result = clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        self._assert_no_token_leak(caplog)
        self._assert_token_len_in_log(caplog)

    def test_oserror_returns_false_and_logs(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        mocker.patch.object(
            clipboard.subprocess, "run", side_effect=OSError("boom"),
        )
        with caplog.at_level(logging.WARNING, logger=clipboard.logger.name):
            result = clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        self._assert_no_token_leak(caplog)

    def test_subprocess_error_returns_false(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        mocker.patch.object(
            clipboard.subprocess, "run",
            side_effect=subprocess.SubprocessError("boom"),
        )
        with caplog.at_level(logging.WARNING, logger=clipboard.logger.name):
            result = clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        self._assert_no_token_leak(caplog)

    def test_nonzero_returncode_returns_false_no_stdout_leak(
        self, mocker, caplog,
    ):
        mocker.patch.object(clipboard.sys, "platform", "win32")
        mocker.patch.object(
            clipboard.subprocess, "run",
            return_value=_FakeCompleted(returncode=2),
        )
        with caplog.at_level(logging.WARNING, logger=clipboard.logger.name):
            result = clipboard.copy_token_to_clipboard(_TOKEN)
        assert result is False
        self._assert_no_token_leak(caplog)
        self._assert_no_stdout_stderr_leak(caplog)
        self._assert_token_len_in_log(caplog)

    def test_success_does_not_log_stdout_or_stderr(self, mocker, caplog):
        mocker.patch.object(clipboard.sys, "platform", "darwin")
        mocker.patch.object(
            clipboard.subprocess, "run",
            return_value=_FakeCompleted(returncode=0),
        )
        with caplog.at_level(logging.DEBUG, logger=clipboard.logger.name):
            clipboard.copy_token_to_clipboard(_TOKEN)
        self._assert_no_stdout_stderr_leak(caplog)
        self._assert_no_token_leak(caplog)


# ------------------------------------------------------------------
# Input validation
# ------------------------------------------------------------------

class TestInputValidation:

    def test_empty_token_returns_false_no_subprocess(self, mocker):
        run = mocker.patch.object(clipboard.subprocess, "run")
        assert clipboard.copy_token_to_clipboard("") is False
        run.assert_not_called()

    def test_non_string_token_returns_false(self, mocker):
        run = mocker.patch.object(clipboard.subprocess, "run")
        # Passing None — helper must not raise.
        assert clipboard.copy_token_to_clipboard(None) is False  # type: ignore[arg-type]
        run.assert_not_called()

    def test_never_raises_on_bad_input(self):
        # Contract: function never raises.
        try:
            clipboard.copy_token_to_clipboard(None)  # type: ignore[arg-type]
            clipboard.copy_token_to_clipboard("")
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"copy_token_to_clipboard raised {exc!r}")
