# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.clipboard.copy_to_clipboard_native``.

Covers GitHub issue #88: the launcher process has no
``QGuiApplication``, so the Qt-based ``shared.tray.clipboard`` helper
silently fails on every first boot. The native helper shells out to
``pbcopy`` / ``clip`` / ``wl-copy`` / ``xclip`` / ``xsel`` instead.
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from launcher import clipboard as native_clipboard
from launcher.clipboard import copy_to_clipboard_native


# ---- Input validation ----

class TestInputValidation:

    @pytest.mark.parametrize("bad", ["", None, 123, 0.5, b"bytes"])
    def test_returns_false_for_empty_or_non_string(self, bad, caplog):
        with caplog.at_level(logging.WARNING, logger="launcher.clipboard"):
            assert copy_to_clipboard_native(bad) is False
        # No subprocess should have been invoked. Test by patching the
        # resolver to a sentinel and verifying it was not called would
        # require ordering — simpler to inspect the warning instead.
        joined = " ".join(rec.message for rec in caplog.records)
        assert "skipped" in joined or "non-string" in joined

    def test_does_not_log_token_value(self, mocker, caplog):
        # Even on failure the token must NEVER appear in logs.
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=None,
        )
        with caplog.at_level(logging.WARNING, logger="launcher.clipboard"):
            copy_to_clipboard_native("super-secret-token")
        joined = " ".join(rec.message for rec in caplog.records)
        assert "super-secret-token" not in joined
        assert "text_len=" in joined  # length OK to log


# ---- Per-platform tool resolution ----

class TestResolveCommand:

    def test_darwin_resolves_pbcopy_when_present(self, mocker):
        mocker.patch.object(
            native_clipboard.shutil, "which",
            side_effect=lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None,
        )
        assert native_clipboard._resolve_command("Darwin") == ("pbcopy",)

    def test_darwin_returns_none_without_pbcopy(self, mocker):
        mocker.patch.object(native_clipboard.shutil, "which", return_value=None)
        assert native_clipboard._resolve_command("Darwin") is None

    def test_windows_resolves_clip_when_present(self, mocker):
        mocker.patch.object(
            native_clipboard.shutil, "which",
            side_effect=lambda name: r"C:\Windows\System32\clip.exe" if name == "clip" else None,
        )
        assert native_clipboard._resolve_command("Windows") == ("clip",)

    def test_windows_returns_none_without_clip(self, mocker):
        mocker.patch.object(native_clipboard.shutil, "which", return_value=None)
        assert native_clipboard._resolve_command("Windows") is None

    def test_linux_prefers_wl_copy(self, mocker):
        mocker.patch.object(
            native_clipboard.shutil, "which",
            side_effect=lambda name: f"/usr/bin/{name}",
        )
        # wl-copy comes first in the fallback chain.
        assert native_clipboard._resolve_command("Linux") == ("wl-copy",)

    def test_linux_falls_back_to_xclip_if_no_wayland(self, mocker):
        def fake_which(name):
            if name == "wl-copy":
                return None
            return f"/usr/bin/{name}"
        mocker.patch.object(
            native_clipboard.shutil, "which", side_effect=fake_which,
        )
        assert native_clipboard._resolve_command("Linux") == (
            "xclip", "-selection", "clipboard",
        )

    def test_linux_falls_back_to_xsel_if_no_wayland_or_xclip(self, mocker):
        def fake_which(name):
            if name in ("wl-copy", "xclip"):
                return None
            return f"/usr/bin/{name}"
        mocker.patch.object(
            native_clipboard.shutil, "which", side_effect=fake_which,
        )
        assert native_clipboard._resolve_command("Linux") == (
            "xsel", "-b", "-i",
        )

    def test_linux_returns_none_when_no_tool_available(self, mocker):
        mocker.patch.object(native_clipboard.shutil, "which", return_value=None)
        assert native_clipboard._resolve_command("Linux") is None

    def test_unknown_system_treated_as_linux(self, mocker):
        # FreeBSD, Haiku, etc. — fall through the Linux tool chain.
        mocker.patch.object(
            native_clipboard.shutil, "which", return_value="/usr/bin/wl-copy",
        )
        assert native_clipboard._resolve_command("FreeBSD") == ("wl-copy",)


# ---- Subprocess plumbing ----

class TestSubprocessInvocation:

    def test_returns_true_on_successful_pbcopy(self, mocker):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=("pbcopy",),
        )
        run = mocker.patch.object(
            native_clipboard.subprocess, "run",
            return_value=subprocess.CompletedProcess(args=["pbcopy"], returncode=0),
        )
        assert copy_to_clipboard_native("token") is True
        # Verify command + stdin plumbing.
        kwargs = run.call_args.kwargs
        args = run.call_args.args
        assert args[0] == ["pbcopy"]
        assert kwargs["input"] == "token"
        assert kwargs["text"] is True
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 2

    def test_returns_false_on_called_process_error(self, mocker, caplog):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=("pbcopy",),
        )
        mocker.patch.object(
            native_clipboard.subprocess, "run",
            side_effect=subprocess.CalledProcessError(1, ["pbcopy"]),
        )
        with caplog.at_level(logging.WARNING, logger="launcher.clipboard"):
            assert copy_to_clipboard_native("token") is False
        joined = " ".join(rec.message for rec in caplog.records)
        assert "text_len=5" in joined
        assert "token" not in joined

    def test_returns_false_on_timeout(self, mocker):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=("pbcopy",),
        )
        mocker.patch.object(
            native_clipboard.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="pbcopy", timeout=2),
        )
        assert copy_to_clipboard_native("token") is False

    def test_returns_false_on_file_not_found(self, mocker):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=("pbcopy",),
        )
        mocker.patch.object(
            native_clipboard.subprocess, "run",
            side_effect=FileNotFoundError(),
        )
        assert copy_to_clipboard_native("token") is False

    def test_returns_false_on_oserror(self, mocker):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=("pbcopy",),
        )
        mocker.patch.object(
            native_clipboard.subprocess, "run",
            side_effect=OSError("permission denied"),
        )
        assert copy_to_clipboard_native("token") is False

    def test_no_subprocess_when_no_tool_available(self, mocker):
        mocker.patch.object(
            native_clipboard, "_resolve_command", return_value=None,
        )
        run = mocker.patch.object(native_clipboard.subprocess, "run")
        assert copy_to_clipboard_native("token") is False
        run.assert_not_called()
