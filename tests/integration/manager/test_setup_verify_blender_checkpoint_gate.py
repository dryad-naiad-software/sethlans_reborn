# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration regression for issue #129: ``POST /api/setup/verify/`` must
return promptly when the ``blender_predownloaded`` sentinel checkpoint
is absent — i.e. when the blender binary is mid-download /
half-extracted.

Without the sentinel-checkpoint gate in ``_check_blender``, the verify
endpoint would subprocess a partial blender binary and wedge the
Waitress worker thread the same way #125 documented for ffmpeg.  This
test drives the real HTTP endpoint end-to-end through the DRF router
and asserts both the response shape and a generous wall-clock bound
that would be blown by the regression.
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


def _write_mid_wizard_sentinel(data_dir, topology, checkpoints):
    """Write a sentinel with no ``completed_at`` and the given checkpoints."""
    write_sentinel(
        data_dir,
        {
            "version": 1,
            "completed_at": None,
            "topology": topology,
            "checkpoints": list(checkpoints),
        },
    )


@pytest.mark.django_db
class TestVerifyEndpointBlenderCheckpointGate:
    """``POST /api/setup/verify/`` must NOT hang on a missing
    blender checkpoint (issue #129)."""

    def test_verify_returns_quickly_when_blender_checkpoint_missing(
        self, setup_env, client, mocker,
    ):
        # 1) Bootstrap a setup-phase session so the verify view passes
        #    SetupPhaseAuthentication / IsSetupPhaseUser.
        assert bootstrap(client).status_code == 204

        # 2) Sentinel exists (mid-wizard) on the manager_worker
        #    topology — which is the only topology that triggers the
        #    blender check — but blender_predownloaded is absent.
        _write_mid_wizard_sentinel(
            setup_env,
            topology="manager_worker",
            checkpoints=[
                "topology_chosen",
                "network_configured",
                "ffmpeg_installed",
            ],
        )

        # 3) Belt-and-suspenders: if the gate ever regresses, this mock
        #    ensures the test still fails fast instead of attempting a
        #    real subprocess call against a missing binary.
        verify_mock = mocker.patch(
            "workers.views.setup_verify_checks.verify_blender_runs",
        )

        # 4) Wall-clock the actual HTTP call.
        start = time.monotonic()
        response = client.post("/api/setup/verify/")
        elapsed = time.monotonic() - start

        # 5) Response must be 200 and arrive well under the old 30s
        #    timeout — the gate makes it near-instant.
        assert response.status_code == 200, response.content
        assert elapsed < 3.0, (
            f"verify took {elapsed:.2f}s — sentinel-checkpoint gate "
            "regression suspected (issue #129)."
        )

        body = response.json()

        # 6) The blender check entry must be present, optionally pass,
        #    and carry the gate's diagnostic detail string.
        blender_check = next(
            (c for c in body["checks"] if c["name"] == "blender"),
            None,
        )
        assert blender_check is not None, body
        assert blender_check["passed"] is True
        assert blender_check.get("detail") == (
            "Blender not pre-downloaded (optional)"
        )

        # 7) The subprocess gate must not have been invoked.
        verify_mock.assert_not_called()
