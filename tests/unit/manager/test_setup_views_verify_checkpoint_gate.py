# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #125: ``_check_ffmpeg`` must short-circuit
on the ``ffmpeg_installed`` sentinel checkpoint BEFORE touching the
binary.  Skipping this gate would let the verify endpoint subprocess
a half-extracted ffmpeg and wedge the Waitress worker thread.

These tests intentionally write a real sentinel file via
``write_sentinel`` (no mock of ``read_sentinel``) so the actual read
path is exercised end-to-end.
"""

from __future__ import annotations

import pytest

from workers.services.sentinel import write_sentinel
from workers.views.setup_verify_checks import _check_ffmpeg


def _write_sentinel_with_checkpoints(data_dir, checkpoints):
    """Write a mid-wizard sentinel with the supplied checkpoints."""
    write_sentinel(
        data_dir,
        {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": list(checkpoints),
        },
    )


class TestCheckFfmpegCheckpointGate:
    """``_check_ffmpeg`` must NOT subprocess ffmpeg before the
    ``ffmpeg_installed`` checkpoint is recorded (issue #125)."""

    def test_skips_subprocess_when_checkpoint_missing(
        self, tmp_path, mocker,
    ):
        # Sentinel exists but does NOT include ffmpeg_installed.
        _write_sentinel_with_checkpoints(tmp_path, checkpoints=[])

        mock_get_binary = mocker.patch(
            "workers.views.setup_verify_checks.get_ffmpeg_binary",
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_ffmpeg_runs",
        )

        result = _check_ffmpeg(tmp_path)

        assert result == {
            "name": "ffmpeg",
            "passed": False,
            "error": "FFmpeg not yet installed",
        }
        mock_get_binary.assert_not_called()
        mock_verify.assert_not_called()

    def test_skips_subprocess_when_no_sentinel_at_all(
        self, tmp_path, mocker,
    ):
        # No sentinel file written — read_sentinel returns None.
        mock_get_binary = mocker.patch(
            "workers.views.setup_verify_checks.get_ffmpeg_binary",
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_ffmpeg_runs",
        )

        result = _check_ffmpeg(tmp_path)

        assert result == {
            "name": "ffmpeg",
            "passed": False,
            "error": "FFmpeg not yet installed",
        }
        mock_get_binary.assert_not_called()
        mock_verify.assert_not_called()

    def test_falls_through_to_existing_logic_when_checkpoint_present(
        self, tmp_path, mocker,
    ):
        # Sentinel records the ffmpeg_installed checkpoint.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["ffmpeg_installed"],
        )

        fake_binary = tmp_path / "ffmpeg.exe"
        mock_get_binary = mocker.patch(
            "workers.views.setup_verify_checks.get_ffmpeg_binary",
            return_value=fake_binary,
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_ffmpeg_runs",
            return_value="ffmpeg version 6.0",
        )

        result = _check_ffmpeg(tmp_path)

        assert result == {
            "name": "ffmpeg", "passed": True, "error": None,
        }
        # The post-rewrite wrapper calls get_ffmpeg_binary with the
        # version-pinned install dir (not the data_dir).  Assert the
        # call landed on the locator at all rather than the exact path.
        assert mock_get_binary.called
        mock_verify.assert_called_once_with(fake_binary)

    def test_checkpoint_present_but_binary_missing_returns_error(
        self, tmp_path, mocker,
    ):
        # Edge case: sentinel says installed, but get_ffmpeg_binary
        # returns None — must report failure WITHOUT calling
        # verify_ffmpeg_runs.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["ffmpeg_installed"],
        )
        mocker.patch(
            "workers.views.setup_verify_checks.get_ffmpeg_binary",
            return_value=None,
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_ffmpeg_runs",
        )

        result = _check_ffmpeg(tmp_path)

        assert result["name"] == "ffmpeg"
        assert result["passed"] is False
        assert result["error"] == "FFmpeg binary not found"
        mock_verify.assert_not_called()

    def test_checkpoint_present_verify_raises_returns_error(
        self, tmp_path, mocker,
    ):
        # Edge case: sentinel says installed, binary present, but the
        # subprocess call fails (e.g. a real timeout from the new
        # VERIFY_SUBPROCESS_TIMEOUT_SECONDS guard).  The error from
        # verify_ffmpeg_runs must propagate as a non-passing check
        # rather than crash.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["ffmpeg_installed"],
        )
        fake_binary = tmp_path / "ffmpeg.exe"
        mocker.patch(
            "workers.views.setup_verify_checks.get_ffmpeg_binary",
            return_value=fake_binary,
        )
        mocker.patch(
            "workers.views.setup_verify_checks.verify_ffmpeg_runs",
            side_effect=RuntimeError(
                "FFmpeg verification timed out after 5.0s.",
            ),
        )

        result = _check_ffmpeg(tmp_path)

        assert result["name"] == "ffmpeg"
        assert result["passed"] is False
        assert "timed out" in result["error"]


@pytest.mark.parametrize(
    "checkpoints,expected_passed,expected_error",
    [
        (
            [],
            False,
            "FFmpeg not yet installed",
        ),
        (
            ["topology_chosen", "network_configured"],
            False,
            "FFmpeg not yet installed",
        ),
        (
            ["ffmpeg_installed"],
            True,
            None,
        ),
    ],
)
def test_check_ffmpeg_gate_matrix(
    tmp_path, mocker, checkpoints, expected_passed, expected_error,
):
    """Cross-check several checkpoint combinations against the gate."""
    _write_sentinel_with_checkpoints(tmp_path, checkpoints=checkpoints)
    mocker.patch(
        "workers.views.setup_verify_checks.get_ffmpeg_binary",
        return_value=tmp_path / "ffmpeg.exe",
    )
    mocker.patch(
        "workers.views.setup_verify_checks.verify_ffmpeg_runs",
        return_value="ffmpeg version 6.0",
    )

    result = _check_ffmpeg(tmp_path)

    assert result["passed"] is expected_passed
    assert result["error"] == expected_error
