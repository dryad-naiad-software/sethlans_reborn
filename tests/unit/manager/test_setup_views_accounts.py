# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/views/setup_accounts.py``.

Covers admin user creation (happy path, password mismatch, duplicate
username, weak password) and worker password setup (minimum length
check, happy path).  All service calls are mocked.
"""

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from workers.views.setup_accounts import (
    setup_admin_user_view,
    setup_worker_password_view,
)


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture
def _mock_sentinel_incomplete(mocker):
    mocker.patch(
        'workers.views.setup_accounts.read_sentinel',
        return_value={
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": [],
        },
    )
    mocker.patch(
        'workers.views.setup_accounts.is_frozen',
        return_value=False,
    )


@pytest.fixture
def _mock_sentinel_complete(mocker):
    mocker.patch(
        'workers.views.setup_accounts.read_sentinel',
        return_value={
            "version": 1,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "manager",
            "checkpoints": [],
        },
    )
    mocker.patch(
        'workers.views.setup_accounts.is_frozen',
        return_value=False,
    )


# ---- POST /api/setup/admin-user/ --------------------------------------------

class TestAdminUserCreation:

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_happy_path(self, api_rf, mocker):
        mock_user = MagicMock()
        mock_user.username = "admin"
        mocker.patch(
            'workers.views.setup_accounts.create_admin_user',
            return_value=mock_user,
        )
        mocker.patch(
            'workers.views.setup_accounts.generate_enrollment_key',
        )
        mocker.patch(
            'workers.views.setup_accounts.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "email": "admin@test.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert response.data["username"] == "admin"

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_password_mismatch_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "email": "a@b.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "different",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 400
        assert any(
            "match" in e.lower() for e in response.data["errors"]
        )

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_duplicate_username_returns_409(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_accounts.create_admin_user',
            side_effect=ValidationError("Username already taken."),
        )
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "email": "a@b.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 409
        assert response.data["error"] == "admin_exists"
        assert response.data["username"] == "admin"

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_weak_password_rejected(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_accounts.create_admin_user',
            side_effect=ValidationError(
                ["This password is too short."],
            ),
        )
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "email": "a@b.com",
                "password": "ab",
                "password_confirm": "ab",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 400
        assert "errors" in response.data

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_missing_username_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 400

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_missing_password_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "password": "",
                "password_confirm": "",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 400

    @pytest.mark.usefixtures("_mock_sentinel_complete")
    def test_returns_404_when_complete(self, api_rf):
        request = api_rf.post(
            '/api/setup/admin-user/',
            {
                "username": "admin",
                "password": "pass",
                "password_confirm": "pass",
            },
            format='json',
        )
        response = setup_admin_user_view(request)
        assert response.status_code == 404


# ---- POST /api/setup/worker-password/ ----------------------------------------

class TestWorkerPassword:

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_happy_path(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_accounts.set_worker_ui_password',
        )
        mocker.patch(
            'workers.views.setup_accounts.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/worker-password/',
            {"password": "LongEnough1!"},
            format='json',
        )
        response = setup_worker_password_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "ok"

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_short_password_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/worker-password/',
            {"password": "short"},
            format='json',
        )
        response = setup_worker_password_view(request)
        assert response.status_code == 400
        assert "8 characters" in response.data["error"]

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_empty_password_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/worker-password/',
            {"password": ""},
            format='json',
        )
        response = setup_worker_password_view(request)
        assert response.status_code == 400

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_service_error_returns_500(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_accounts.set_worker_ui_password',
            side_effect=OSError("permission denied"),
        )
        request = api_rf.post(
            '/api/setup/worker-password/',
            {"password": "LongEnough1!"},
            format='json',
        )
        response = setup_worker_password_view(request)
        assert response.status_code == 500
