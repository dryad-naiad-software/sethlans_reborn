# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #129: ``_check_blender`` must short-circuit
on the ``blender_predownloaded`` sentinel checkpoint BEFORE touching
the binary.  Skipping this gate would let the verify endpoint
subprocess a half-extracted blender and wedge the Waitress worker
thread the same way #125 documented for ffmpeg.

These tests intentionally write a real sentinel file via
``write_sentinel`` (no mock of ``read_sentinel``) so the actual read
path is exercised end-to-end.
"""

from __future__ import annotations

import pytest

from workers.services.sentinel import write_sentinel
from workers.views.setup_verify_checks import _check_blender


def _write_sentinel_with_checkpoints(data_dir, checkpoints):
    """Write a mid-wizard sentinel with the supplied checkpoints."""
    write_sentinel(
        data_dir,
        {
            "version": 1,
            "completed_at": None,
            "topology": "manager_worker",
            "checkpoints": list(checkpoints),
        },
    )


class TestCheckBlenderCheckpointGate:
    """``_check_blender`` must NOT subprocess blender before the
    ``blender_predownloaded`` checkpoint is recorded (issue #129)."""

    def test_skips_subprocess_when_checkpoint_missing(
        self, tmp_path, mocker,
    ):
        # Sentinel exists but does NOT include blender_predownloaded.
        _write_sentinel_with_checkpoints(tmp_path, checkpoints=[])

        mock_already = mocker.patch(
            "workers.views.setup_verify_checks.blender_already_installed",
        )
        mock_find = mocker.patch(
            "workers.views.setup_verify_checks._find_blender_binary",
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        result = _check_blender(tmp_path)

        assert result == {
            "name": "blender",
            "passed": True,
            "error": None,
            "detail": "Blender not pre-downloaded (optional)",
        }
        mock_already.assert_not_called()
        mock_find.assert_not_called()
        mock_verify.assert_not_called()

    def test_skips_subprocess_when_no_sentinel_at_all(
        self, tmp_path, mocker,
    ):
        # No sentinel file written — read_sentinel returns None.
        mock_already = mocker.patch(
            "workers.views.setup_verify_checks.blender_already_installed",
        )
        mock_find = mocker.patch(
            "workers.views.setup_verify_checks._find_blender_binary",
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        result = _check_blender(tmp_path)

        assert result["name"] == "blender"
        assert result["passed"] is True
        assert result["detail"] == "Blender not pre-downloaded (optional)"
        mock_already.assert_not_called()
        mock_find.assert_not_called()
        mock_verify.assert_not_called()

    def test_skips_subprocess_when_unrelated_checkpoints_only(
        self, tmp_path, mocker,
    ):
        # Sentinel has other checkpoints but not blender_predownloaded.
        _write_sentinel_with_checkpoints(
            tmp_path,
            checkpoints=[
                "topology_chosen",
                "network_configured",
            ],
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        result = _check_blender(tmp_path)

        assert result["passed"] is True
        assert result["detail"] == "Blender not pre-downloaded (optional)"
        mock_verify.assert_not_called()

    def test_falls_through_when_checkpoint_present_happy_path(
        self, tmp_path, mocker,
    ):
        # Sentinel records the blender_predownloaded checkpoint, the
        # default Blender version is configured, the install dir
        # exists, and the binary verifies cleanly.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["blender_predownloaded"],
        )
        _patch_default_blender_version(mocker, version="4.5.0")

        mocker.patch(
            "workers.views.setup_verify_checks.blender_already_installed",
            return_value=True,
        )
        fake_binary = tmp_path / "blender.exe"
        mock_find = mocker.patch(
            "workers.views.setup_verify_checks._find_blender_binary",
            return_value=fake_binary,
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
            return_value="Blender 4.5.0",
        )

        result = _check_blender(tmp_path)

        assert result == {
            "name": "blender", "passed": True, "error": None,
        }
        mock_find.assert_called_once()
        mock_verify.assert_called_once_with(fake_binary)

    def test_checkpoint_present_but_no_default_version(
        self, tmp_path, mocker,
    ):
        # Sentinel says installed but no default Blender version row
        # exists — must report optional pass WITHOUT subprocessing.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["blender_predownloaded"],
        )
        _patch_default_blender_version(mocker, version=None)
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        result = _check_blender(tmp_path)

        assert result["name"] == "blender"
        assert result["passed"] is True
        assert result["detail"] == (
            "No default Blender version configured"
        )
        mock_verify.assert_not_called()

    def test_checkpoint_present_but_install_dir_missing(
        self, tmp_path, mocker,
    ):
        # Sentinel says installed but blender_already_installed reports
        # False — pass as optional without subprocessing.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["blender_predownloaded"],
        )
        _patch_default_blender_version(mocker, version="4.5.0")

        mocker.patch(
            "workers.views.setup_verify_checks.blender_already_installed",
            return_value=False,
        )
        mock_verify = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        result = _check_blender(tmp_path)

        assert result["passed"] is True
        assert result["detail"] == "Blender not pre-downloaded (optional)"
        mock_verify.assert_not_called()

    def test_checkpoint_present_verify_raises_returns_error(
        self, tmp_path, mocker,
    ):
        # Edge case: sentinel says installed, binary present, but the
        # subprocess call fails (e.g. real timeout from the bounded
        # verify subprocess timeout).  The error from
        # verify_blender_runs must propagate as a non-passing check
        # rather than crash.
        _write_sentinel_with_checkpoints(
            tmp_path, checkpoints=["blender_predownloaded"],
        )
        _patch_default_blender_version(mocker, version="4.5.0")

        mocker.patch(
            "workers.views.setup_verify_checks.blender_already_installed",
            return_value=True,
        )
        fake_binary = tmp_path / "blender.exe"
        mocker.patch(
            "workers.views.setup_verify_checks._find_blender_binary",
            return_value=fake_binary,
        )
        mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
            side_effect=RuntimeError(
                "Blender verification timed out after 5.0s.",
            ),
        )

        result = _check_blender(tmp_path)

        assert result["name"] == "blender"
        assert result["passed"] is False
        assert "timed out" in result["error"]


def _patch_default_blender_version(mocker, version):
    """Stub ``SupportedBlenderVersion.objects.filter(...).first()``.

    Pass ``version=None`` to simulate no default-version row.  The
    stub object exposes ``resolved_version`` to match the production
    code path (issue #136 — the gate reads ``resolved_version``, not
    ``version``).  ``series`` is also set so any incidental code that
    reads either attribute remains consistent with the model.
    """
    if version is None:
        obj = None
    else:
        obj = type(
            "V", (), {"resolved_version": version, "series": version},
        )()
    qs = mocker.MagicMock()
    qs.first.return_value = obj
    mocker.patch(
        "workers.models.SupportedBlenderVersion.objects.filter",
        return_value=qs,
    )


@pytest.mark.parametrize(
    "checkpoints,expected_passed,expected_detail",
    [
        (
            [],
            True,
            "Blender not pre-downloaded (optional)",
        ),
        (
            ["topology_chosen", "network_configured"],
            True,
            "Blender not pre-downloaded (optional)",
        ),
        (
            ["blender_predownloaded"],
            True,
            None,
        ),
    ],
)
def test_check_blender_gate_matrix(
    tmp_path, mocker, checkpoints, expected_passed, expected_detail,
):
    """Cross-check several checkpoint combinations against the gate."""
    _write_sentinel_with_checkpoints(tmp_path, checkpoints=checkpoints)
    _patch_default_blender_version(mocker, version="4.5.0")
    mocker.patch(
        "workers.views.setup_verify_checks.blender_already_installed",
        return_value=True,
    )
    mocker.patch(
        "workers.views.setup_verify_checks._find_blender_binary",
        return_value=tmp_path / "blender.exe",
    )
    mocker.patch(
        "workers.views.setup_verify_checks.verify_blender_runs",
        return_value="Blender 4.5.0",
    )

    result = _check_blender(tmp_path)

    assert result["passed"] is expected_passed
    if expected_detail is None:
        assert result.get("detail") is None
    else:
        assert result["detail"] == expected_detail
