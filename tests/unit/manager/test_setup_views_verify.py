# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_verify.py``.

Covers the topology-aware verify endpoint and the setup-phase summary.
Post-completion access to ``/api/setup/summary/`` is handled by the
gate (404) — the view itself now raises 409 ``precondition_unmet``
when the sentinel is absent/incomplete.
"""

from __future__ import annotations

import configparser
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from sethlans_manager import runtime_state
from workers.views.setup_verify import (
    setup_summary_view,
    setup_verify_view,
)

_CHECK_OK = {"passed": True, "error": None}


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
    mocker.patch("workers.views.setup_verify.is_frozen", return_value=False)


@pytest.fixture
def _sentinel_incomplete(mocker):
    mocker.patch(
        "workers.views.setup_verify.read_sentinel",
        return_value={
            "version": 1, "completed_at": None,
            "topology": "manager",
            "checkpoints": [
                "topology_chosen", "network_configured",
                "database_configured", "admin_created",
                "ffmpeg_installed",
            ],
        },
    )


def _mock_all_checks_pass(mocker):
    for fn in (
        "_check_db_reachable", "_check_admin_exists",
        "_check_ffmpeg", "_check_enrollment_key",
    ):
        mocker.patch(
            f"workers.views.setup_verify.{fn}",
            return_value={"name": fn, **_CHECK_OK},
        )


class TestVerifyView:

    @pytest.mark.usefixtures("_sentinel_incomplete")
    def test_all_checks_pass_writes_sentinel(self, api_rf, mocker):
        _mock_all_checks_pass(mocker)
        mock_create = mocker.patch(
            "workers.views.setup_verify.create_sentinel",
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/verify/")
        resp = setup_verify_view(req)
        assert resp.status_code == 200
        assert resp.data["all_passed"] is True
        assert len(resp.data["checks"]) == 4
        mock_create.assert_called_once()

    @pytest.mark.usefixtures("_sentinel_incomplete")
    def test_check_failure_no_sentinel_written(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_verify._check_db_reachable",
            return_value={
                "name": "database", "passed": False, "error": "DB down",
            },
        )
        mocker.patch(
            "workers.views.setup_verify._check_admin_exists",
            return_value={"name": "admin_user", **_CHECK_OK},
        )
        mocker.patch(
            "workers.views.setup_verify._check_ffmpeg",
            return_value={"name": "ffmpeg", **_CHECK_OK},
        )
        mocker.patch(
            "workers.views.setup_verify._check_enrollment_key",
            return_value={"name": "enrollment_key", **_CHECK_OK},
        )
        mock_create = mocker.patch(
            "workers.views.setup_verify.create_sentinel",
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/verify/")
        resp = setup_verify_view(req)
        assert resp.status_code == 200
        assert resp.data["all_passed"] is False
        mock_create.assert_not_called()

    @pytest.mark.usefixtures("_sentinel_incomplete")
    def test_sentinel_write_failure(self, api_rf, mocker):
        _mock_all_checks_pass(mocker)
        mocker.patch(
            "workers.views.setup_verify.create_sentinel",
            side_effect=OSError("disk full"),
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/verify/")
        resp = setup_verify_view(req)
        assert resp.status_code == 200
        assert resp.data["all_passed"] is False
        assert "sentinel" in resp.data["error"].lower()

    def test_cached_if_already_complete(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_verify.read_sentinel",
            return_value={
                "version": 1, "completed_at": "2025-01-15T00:00:00Z",
                "topology": "manager", "checkpoints": [],
            },
        )
        req = _setup_phase_request(api_rf.post, "/api/setup/verify/")
        resp = setup_verify_view(req)
        assert resp.status_code == 200
        assert resp.data["all_passed"] is True


class TestSummaryView:
    """``setup_summary_view`` is session-phase protected and returns
    409 ``precondition_unmet`` when setup is not yet verified."""

    def test_returns_summary_fields(self, api_rf, mocker, tmp_path):
        mocker.patch(
            "workers.views.setup_verify.read_sentinel",
            return_value={
                "version": 1, "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager_worker", "checkpoints": [],
            },
        )
        mocker.patch(
            "workers.views.setup_verify._get_data_dir",
            return_value=tmp_path,
        )
        ini = tmp_path / "manager.ini"
        config = configparser.ConfigParser()
        config.add_section("server")
        config.set("server", "host", "0.0.0.0")
        config.set("server", "port", "8080")
        with open(ini, "w") as f:
            config.write(f)

        mock_user = MagicMock(username="myadmin")
        mock_qs = MagicMock()
        mock_qs.first.return_value = mock_user
        mock_model = MagicMock()
        mock_model.objects.filter.return_value = mock_qs
        mocker.patch(
            "django.contrib.auth.get_user_model", return_value=mock_model,
        )
        mock_ms = MagicMock(enrollment_key="TESTKEY123456789")
        ms_model = MagicMock()
        ms_model.objects.get.return_value = mock_ms
        mocker.patch("workers.models.ManagerSettings", ms_model)

        orig = runtime_state.cert_fingerprint
        runtime_state.cert_fingerprint = "abcdef1234567890"
        try:
            req = _setup_phase_request(api_rf.get, "/api/setup/summary/")
            resp = setup_summary_view(req)
        finally:
            runtime_state.cert_fingerprint = orig

        assert resp.status_code == 200
        d = resp.data
        assert d["manager_url"] == "https://localhost:8080"
        assert d["admin_username"] == "myadmin"
        assert d["enrollment_key"] == "TESTKEY123456789"
        assert d["cert_fingerprint"] == "abcdef1234567890"
        assert d["topology"] == "manager_worker"

    def test_returns_409_when_not_verified(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_verify.read_sentinel", return_value=None,
        )
        req = _setup_phase_request(api_rf.get, "/api/setup/summary/")
        resp = setup_summary_view(req)
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "precondition_unmet"

    def test_returns_409_when_no_completed_at(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_verify.read_sentinel",
            return_value={
                "version": 1, "completed_at": None,
                "topology": "manager", "checkpoints": [],
            },
        )
        req = _setup_phase_request(api_rf.get, "/api/setup/summary/")
        resp = setup_summary_view(req)
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "precondition_unmet"
