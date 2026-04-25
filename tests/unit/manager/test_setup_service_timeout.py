# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #125: ``verify_ffmpeg_runs`` and
``verify_blender_runs`` must read ``VERIFY_SUBPROCESS_TIMEOUT_SECONDS``
at call time and pass it to ``subprocess.run`` so a wedged child cannot
hold the Waitress worker thread.

These tests prove two things at once:

* the module-level constant is the actual value used (not a stale value
  captured at import time), and
* a ``TimeoutExpired`` from ``subprocess.run`` is re-raised as a clear
  ``RuntimeError`` — the verify endpoint can keep responding instead of
  hanging.
"""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock

import pytest

from workers.services import setup as setup_service
from workers.services.setup import (
    verify_blender_runs,
    verify_ffmpeg_runs,
)


class TestVerifyFfmpegHonoursTimeoutConstant:
    """The function must dereference ``VERIFY_SUBPROCESS_TIMEOUT_SECONDS``
    each call so monkeypatching the constant in tests (or hot-reloading
    in production) takes effect immediately."""

    def test_subprocess_receives_patched_timeout(
        self, mocker, monkeypatch, tmp_path,
    ):
        # Drop the constant well below the default 5.0s — the value
        # actually forwarded to subprocess.run is what we will assert.
        monkeypatch.setattr(
            setup_service, "VERIFY_SUBPROCESS_TIMEOUT_SECONDS", 0.5,
        )

        fake_bin = tmp_path / "ffmpeg"
        fake_bin.write_text("binary")

        captured = {}

        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return MagicMock(
                returncode=0,
                stdout="ffmpeg version 6.0",
                stderr="",
            )

        mocker.patch(
            "workers.services.setup.subprocess.run",
            side_effect=fake_run,
        )

        verify_ffmpeg_runs(fake_bin)

        # Pin the regression: the timeout kwarg must equal the
        # CURRENT module-level constant, not a hard-coded number.
        assert captured["kwargs"]["timeout"] == 0.5
        assert captured["kwargs"]["shell"] is False

    def test_subprocess_receives_default_timeout_unpatched(
        self, mocker, tmp_path,
    ):
        # Sanity check: without patching, the default constant is used.
        fake_bin = tmp_path / "ffmpeg"
        fake_bin.write_text("binary")

        captured = {}

        def fake_run(*args, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock(returncode=0, stdout="x", stderr="")

        mocker.patch(
            "workers.services.setup.subprocess.run",
            side_effect=fake_run,
        )
        verify_ffmpeg_runs(fake_bin)
        assert (
            captured["kwargs"]["timeout"]
            == setup_service.VERIFY_SUBPROCESS_TIMEOUT_SECONDS
        )

    def test_timeout_expired_is_raised_quickly(
        self, mocker, monkeypatch, tmp_path,
    ):
        """When ``subprocess.run`` raises TimeoutExpired (which it
        does on its own after killing the child), our wrapper must
        re-raise as RuntimeError quickly — no extra polling."""
        monkeypatch.setattr(
            setup_service, "VERIFY_SUBPROCESS_TIMEOUT_SECONDS", 0.5,
        )
        fake_bin = tmp_path / "ffmpeg"
        fake_bin.write_text("binary")

        mocker.patch(
            "workers.services.setup.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd="ffmpeg", timeout=0.5,
            ),
        )

        start = time.monotonic()
        with pytest.raises(RuntimeError) as excinfo:
            verify_ffmpeg_runs(fake_bin)
        elapsed = time.monotonic() - start

        # Generous slack: the raise path is in-process and should
        # complete in well under 2 seconds.
        assert elapsed < 2.0
        assert "timed out" in str(excinfo.value).lower()


class TestVerifyBlenderHonoursTimeoutConstant:
    """Same regression guard for Blender — the wedge risk is symmetric
    (issue #125 surfaced via ffmpeg but the fix covers both)."""

    def test_subprocess_receives_patched_timeout(
        self, mocker, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(
            setup_service, "VERIFY_SUBPROCESS_TIMEOUT_SECONDS", 0.25,
        )
        fake_bin = tmp_path / "blender"
        fake_bin.write_text("binary")

        captured = {}

        def fake_run(*args, **kwargs):
            captured["kwargs"] = kwargs
            return MagicMock(
                returncode=0, stdout="Blender 4.3.0", stderr="",
            )

        mocker.patch(
            "workers.services.setup.subprocess.run",
            side_effect=fake_run,
        )

        verify_blender_runs(fake_bin)

        assert captured["kwargs"]["timeout"] == 0.25

    def test_timeout_expired_is_raised_quickly(
        self, mocker, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(
            setup_service, "VERIFY_SUBPROCESS_TIMEOUT_SECONDS", 0.25,
        )
        fake_bin = tmp_path / "blender"
        fake_bin.write_text("binary")

        mocker.patch(
            "workers.services.setup.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd="blender", timeout=0.25,
            ),
        )

        start = time.monotonic()
        with pytest.raises(RuntimeError) as excinfo:
            verify_blender_runs(fake_bin)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0
        assert "timed out" in str(excinfo.value).lower()
