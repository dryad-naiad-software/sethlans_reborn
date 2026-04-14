# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``manager/workers/views/setup_verify.py``."""

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


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        'workers.views.setup_verify.is_frozen', return_value=False,
    )


@pytest.fixture
def _mock_incomplete(mocker):
    mocker.patch(
        'workers.views.setup_verify.read_sentinel',
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
        '_check_db_reachable', '_check_admin_exists',
        '_check_ffmpeg', '_check_enrollment_key',
    ):
        mocker.patch(
            f'workers.views.setup_verify.{fn}',
            return_value={"name": fn, **_CHECK_OK},
        )


# ---- POST /api/setup/verify/ -----------------------------------------------

class TestVerifyView:

    @pytest.mark.usefixtures("_mock_incomplete")
    def test_all_checks_pass_writes_sentinel(
        self, api_rf, mocker,
    ):
        _mock_all_checks_pass(mocker)
        mock_create = mocker.patch(
            'workers.views.setup_verify.create_sentinel',
        )
        request = api_rf.post('/api/setup/verify/')
        response = setup_verify_view(request)
        assert response.status_code == 200
        assert response.data["all_passed"] is True
        assert len(response.data["checks"]) == 4
        mock_create.assert_called_once()

    @pytest.mark.usefixtures("_mock_incomplete")
    def test_check_failure_no_sentinel_written(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_verify._check_db_reachable',
            return_value={
                "name": "database", "passed": False,
                "error": "DB down",
            },
        )
        mocker.patch(
            'workers.views.setup_verify._check_admin_exists',
            return_value={"name": "admin_user", **_CHECK_OK},
        )
        mocker.patch(
            'workers.views.setup_verify._check_ffmpeg',
            return_value={"name": "ffmpeg", **_CHECK_OK},
        )
        mocker.patch(
            'workers.views.setup_verify._check_enrollment_key',
            return_value={"name": "enrollment_key", **_CHECK_OK},
        )
        mock_create = mocker.patch(
            'workers.views.setup_verify.create_sentinel',
        )
        request = api_rf.post('/api/setup/verify/')
        response = setup_verify_view(request)
        assert response.status_code == 200
        assert response.data["all_passed"] is False
        mock_create.assert_not_called()

    @pytest.mark.usefixtures("_mock_incomplete")
    def test_sentinel_write_failure_returns_error(
        self, api_rf, mocker,
    ):
        _mock_all_checks_pass(mocker)
        mocker.patch(
            'workers.views.setup_verify.create_sentinel',
            side_effect=OSError("disk full"),
        )
        request = api_rf.post('/api/setup/verify/')
        response = setup_verify_view(request)
        assert response.status_code == 200
        assert response.data["all_passed"] is False
        assert "sentinel" in response.data["error"].lower()

    def test_returns_cached_if_already_complete(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_verify.read_sentinel',
            return_value={
                "version": 1,
                "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager", "checkpoints": [],
            },
        )
        request = api_rf.post('/api/setup/verify/')
        response = setup_verify_view(request)
        assert response.status_code == 200
        assert response.data["all_passed"] is True


# ---- GET /api/setup/summary/ -----------------------------------------------

class TestSummaryView:

    def test_returns_summary_fields(
        self, api_rf, mocker, tmp_path,
    ):
        mocker.patch(
            'workers.views.setup_verify.read_sentinel',
            return_value={
                "version": 1,
                "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager_worker",
                "checkpoints": [],
            },
        )
        mocker.patch(
            'workers.views.setup_verify._get_data_dir',
            return_value=tmp_path,
        )
        # Write a mock manager.ini
        ini = tmp_path / "manager.ini"
        config = configparser.ConfigParser()
        config.add_section("server")
        config.set("server", "host", "0.0.0.0")
        config.set("server", "port", "8080")
        with open(ini, "w") as f:
            config.write(f)
        # Mock admin user
        mock_user = MagicMock(username="myadmin")
        mock_qs = MagicMock()
        mock_qs.first.return_value = mock_user
        mock_model = MagicMock()
        mock_model.objects.filter.return_value = mock_qs
        mocker.patch(
            'django.contrib.auth.get_user_model',
            return_value=mock_model,
        )
        # Mock ManagerSettings
        mock_ms = MagicMock(enrollment_key="TESTKEY123456789")
        ms_model = MagicMock()
        ms_model.objects.get.return_value = mock_ms
        mocker.patch(
            'workers.models.ManagerSettings', ms_model,
        )
        # Mock cert fingerprint
        orig = runtime_state.cert_fingerprint
        runtime_state.cert_fingerprint = "abcdef1234567890"
        try:
            request = api_rf.get('/api/setup/summary/')
            response = setup_summary_view(request)
        finally:
            runtime_state.cert_fingerprint = orig
        assert response.status_code == 200
        d = response.data
        assert d["manager_url"] == "https://localhost:8080"
        assert d["admin_username"] == "myadmin"
        assert d["enrollment_key"] == "TESTKEY123456789"
        assert d["cert_fingerprint"] == "abcdef1234567890"
        assert d["topology"] == "manager_worker"

    def test_returns_400_when_not_verified(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_verify.read_sentinel',
            return_value=None,
        )
        request = api_rf.get('/api/setup/summary/')
        response = setup_summary_view(request)
        assert response.status_code == 400
        assert "not yet verified" in response.data["error"].lower()

    def test_returns_400_when_no_completed_at(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_verify.read_sentinel',
            return_value={
                "version": 1, "completed_at": None,
                "topology": "manager", "checkpoints": [],
            },
        )
        request = api_rf.get('/api/setup/summary/')
        response = setup_summary_view(request)
        assert response.status_code == 400
