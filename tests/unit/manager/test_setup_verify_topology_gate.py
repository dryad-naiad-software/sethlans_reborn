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

The PRIMARY assertion in this module drives the production code
path through ``POST /api/setup/verify/`` (``setup_verify_view`` →
``_setup_verify_locked`` → ``setup_verify._run_verification_checks``)
and asserts the response payload contains no entry with
``name == 'ffmpeg'``.  This is what the api-reviewer flagged as
missing in the original fix: gating the unused
``setup_verify_checks.run_verification_checks`` helper left the
live endpoint unchanged.

The lower-level tests against the dead helper
(``setup_verify_checks.run_verification_checks``) are retained as
documentation of intent, but the helper is not currently called by
production code.  See follow-up note in
``.tmp/fix_127_progress.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from workers.views.setup_verify import setup_verify_view
from workers.views.setup_verify_checks import run_verification_checks


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
    callee (``setup_verify._run_verification_checks``).  These tests
    are what the api-reviewer asked for: they will FAIL if the gate
    on the production function is reverted, regardless of whether
    the unused ``setup_verify_checks`` helper remains gated."""

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


@pytest.mark.django_db
class TestRunVerificationChecksTopologyGateHelper:
    """Lower-level coverage of ``setup_verify_checks.run_verification_checks``.

    This helper is not currently wired into the production verify
    endpoint (the live call goes through
    ``setup_verify._run_verification_checks``), but the gate exists
    here for symmetry and is kept under test as documentation.  If
    the helper is ever collapsed with the duplicate (option (b) in
    the followup brief), these tests can move with it or be deleted."""

    def test_worker_only_topology_omits_ffmpeg_check(self, tmp_path):
        """worker_only must not append the ffmpeg check (#127)."""
        checks = run_verification_checks(tmp_path, "worker_only")

        names = [c["name"] for c in checks]
        assert "ffmpeg" not in names, (
            "worker_only topology must not include ffmpeg in the "
            "verify checklist (issue #127)."
        )
        # blender is also manager_worker-only — confirm the
        # worker_only path stays minimal.
        assert "blender" not in names

    @pytest.mark.parametrize(
        "topology", ["manager", "manager_worker"],
    )
    def test_non_worker_only_topologies_keep_ffmpeg_check(
        self, tmp_path, topology,
    ):
        """manager and manager_worker must still include ffmpeg."""
        checks = run_verification_checks(tmp_path, topology)

        names = [c["name"] for c in checks]
        assert names.count("ffmpeg") == 1, (
            f"{topology} topology must include exactly one ffmpeg "
            f"check; got names={names}"
        )

    def test_manager_worker_topology_keeps_blender_check(self, tmp_path):
        """Lock the existing manager_worker contract: blender entry
        is still produced alongside ffmpeg so the #127 patch did not
        accidentally drop unrelated checks."""
        checks = run_verification_checks(tmp_path, "manager_worker")

        names = [c["name"] for c in checks]
        assert "ffmpeg" in names
        assert "blender" in names
