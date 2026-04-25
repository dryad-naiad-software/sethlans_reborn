# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #127: the verify endpoint must omit the
ffmpeg check when the chosen topology is ``worker_only``.

The worker_only manager never renders, so ffmpeg is irrelevant —
including the check would force the operator to download ffmpeg
just to satisfy verify, and would also surface a misleading
"FFmpeg not yet installed" failure on the wizard summary.

These tests drive the production code path through
``POST /api/setup/verify/`` (``setup_verify_view`` →
``_setup_verify_locked`` → ``setup_verify_checks.run_verification_checks``)
and assert the response payload contains/omits the expected check
entries.  After issue #133 collapsed the duplicate verification
helper, the live endpoint and the previously-dead helper are now a
single function, so these tests cover both paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from workers.views.setup_verify import setup_verify_view


def _setup_phase_request(method, *args, **kwargs):
    request = method(*args, **kwargs)
    session = {"setup_phase": True, "setup_session_id": "sid-1"}
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    request._setup_snapshot = {
        "complete": False, "phase": "verify", "session_id": None,
    }
    return request


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        "workers.views.setup_verify.is_frozen", return_value=False,
    )


@pytest.mark.django_db
class TestVerifyEndpointTopologyGateProduction:
    """Drive ``POST /api/setup/verify/`` through the production
    callee (``setup_verify_checks.run_verification_checks``).  These
    tests will FAIL if the topology gate on the production function
    is reverted."""

    def test_verify_worker_only_response_omits_ffmpeg(
        self, api_rf, mocker,
    ):
        """worker_only verify response must contain no ffmpeg entry
        (#127)."""
        mocker.patch(
            "workers.views.setup_verify.read_sentinel",
            return_value={
                "version": 1, "completed_at": None,
                "topology": "worker_only",
                "checkpoints": [
                    "topology_chosen", "network_configured",
                    "database_configured", "admin_created",
                ],
            },
        )
        # Stop create_sentinel from touching the filesystem on a
        # potentially-passing run.
        mocker.patch(
            "workers.views.setup_verify.create_sentinel",
        )

        req = _setup_phase_request(
            api_rf.post, "/api/setup/verify/",
        )
        resp = setup_verify_view(req)

        assert resp.status_code == 200
        names = [c["name"] for c in resp.data["checks"]]
        assert "ffmpeg" not in names, (
            "worker_only verify response must omit the ffmpeg check "
            "(issue #127). Production gate was reverted."
        )
        assert "blender" not in names

    @pytest.mark.parametrize(
        "topology", ["manager", "manager_worker"],
    )
    def test_verify_non_worker_only_response_keeps_ffmpeg(
        self, api_rf, mocker, topology,
    ):
        """manager and manager_worker still get the ffmpeg check."""
        mocker.patch(
            "workers.views.setup_verify.read_sentinel",
            return_value={
                "version": 1, "completed_at": None,
                "topology": topology,
                "checkpoints": [
                    "topology_chosen", "network_configured",
                    "database_configured", "admin_created",
                ],
            },
        )
        mocker.patch(
            "workers.views.setup_verify.create_sentinel",
        )

        req = _setup_phase_request(
            api_rf.post, "/api/setup/verify/",
        )
        resp = setup_verify_view(req)

        assert resp.status_code == 200
        names = [c["name"] for c in resp.data["checks"]]
        assert names.count("ffmpeg") == 1, (
            f"{topology} verify response must include exactly one "
            f"ffmpeg check; got names={names}"
        )
        # Lock the existing manager_worker contract so the #133 cleanup
        # cannot accidentally drop unrelated checks: blender is still
        # produced for manager_worker but not for plain manager.
        if topology == "manager_worker":
            assert "blender" in names, (
                "manager_worker verify response must still include "
                "the blender check."
            )
        else:
            assert "blender" not in names
