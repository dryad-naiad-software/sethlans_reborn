# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration regression for issue #125: ``POST /api/setup/verify/`` must
return promptly when the ``ffmpeg_installed`` sentinel checkpoint is
absent — i.e. when the ffmpeg binary is mid-download / half-extracted.

Without the sentinel-checkpoint gate in ``_check_ffmpeg``, the verify
endpoint would subprocess a partial binary and wedge the Waitress
worker thread.  This test drives the real HTTP endpoint end-to-end
through the DRF router and asserts both the response shape and a
generous wall-clock bound that would be blown by the regression.
"""

from __future__ import annotations

import time

import pytest

from workers.services.sentinel import write_sentinel

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    reset_rate_limiter,
)


@pytest.fixture
def setup_env(mocker, tmp_path):
    """Setup-mode session bootstrapped against a tmp data dir."""
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


def _write_mid_wizard_sentinel(data_dir, checkpoints):
    """Write a sentinel with no ``completed_at`` and the given checkpoints."""
    write_sentinel(
        data_dir,
        {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": list(checkpoints),
        },
    )


@pytest.mark.django_db
class TestVerifyEndpointFfmpegCheckpointGate:
    """``POST /api/setup/verify/`` must NOT hang on a missing checkpoint."""

    def test_verify_returns_quickly_when_ffmpeg_checkpoint_missing(
        self, setup_env, client,
    ):
        # 1) Bootstrap a setup-phase session so the verify view passes
        #    SetupPhaseAuthentication / IsSetupPhaseUser.
        assert bootstrap(client).status_code == 204

        # 2) Sentinel exists (mid-wizard) but ffmpeg_installed is absent.
        _write_mid_wizard_sentinel(setup_env, checkpoints=["topology_chosen"])

        # 3) Wall-clock the actual HTTP call.
        start = time.monotonic()
        response = client.post("/api/setup/verify/")
        elapsed = time.monotonic() - start

        # 4) Response must be 200 and arrive well under the old 30s
        #    timeout — the gate makes it near-instant.
        assert response.status_code == 200, response.content
        assert elapsed < 3.0, (
            f"verify took {elapsed:.2f}s — sentinel-checkpoint gate "
            "regression suspected (issue #125)."
        )

        body = response.json()
        assert body["all_passed"] is False

        # 5) The ffmpeg check entry must be present, failed, and carry
        #    the gate's diagnostic message.
        ffmpeg_check = next(
            (c for c in body["checks"] if c["name"] == "ffmpeg"), None,
        )
        assert ffmpeg_check is not None, body
        assert ffmpeg_check["passed"] is False
        assert "FFmpeg not yet installed" in (ffmpeg_check["error"] or "")
