# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_accounts.py``.

Covers admin user creation and worker password setup with the unified
error envelope (``setup_error``) — see setup-auth-unification spec.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from workers.views.setup_accounts import (
    setup_admin_user_view,
    setup_worker_password_view,
)


def _setup_phase_post(api_rf, path, data):
    request = api_rf.post(path, data, format="json")
    session = {"setup_phase": True, "setup_session_id": "sid-1"}
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    request._setup_snapshot = {
        "complete": False, "phase": "admin", "session_id": None,
    }
    return request


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch("workers.views.setup_accounts.is_frozen", return_value=False)


@pytest.fixture
def _patch_topology_read(mocker):
    """Sentinel topology is needed by _try_auto_enroll_local_worker."""
    mocker.patch(
        "workers.views.setup_accounts.read_sentinel",
        return_value={
            "version": 1, "completed_at": None,
            "topology": "manager", "checkpoints": [],
        },
    )


class TestAdminUserCreation:

    @pytest.mark.usefixtures("_patch_topology_read")
    def test_happy_path(self, api_rf, mocker):
        mock_user = MagicMock(username="admin")
        mocker.patch(
            "workers.views.setup_accounts.create_admin_user",
            return_value=mock_user,
        )
        mocker.patch(
            "workers.views.setup_accounts.generate_enrollment_key",
        )
        mocker.patch("workers.views.setup_accounts.append_checkpoint")
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {
                "username": "admin", "email": "admin@test.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "ok"
        assert resp.data["username"] == "admin"

    def test_password_mismatch(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {
                "username": "admin", "email": "a@b.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "different",
            },
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    @pytest.mark.usefixtures("_patch_topology_read")
    def test_duplicate_username_returns_409(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_accounts.create_admin_user",
            side_effect=ValidationError("Username already taken."),
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {
                "username": "admin", "email": "a@b.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "precondition_unmet"
        assert resp.data["error"]["details"]["username"] == "admin"

    @pytest.mark.usefixtures("_patch_topology_read")
    def test_weak_password_rejected(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_accounts.create_admin_user",
            side_effect=ValidationError(["This password is too short."]),
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {
                "username": "admin", "email": "a@b.com",
                "password": "ab", "password_confirm": "ab",
            },
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_missing_username(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {
                "username": "", "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_missing_password(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/admin-user/",
            {"username": "admin", "password": "", "password_confirm": ""},
        )
        resp = setup_admin_user_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"


class TestWorkerPassword:

    def test_happy_path(self, api_rf, mocker):
        mocker.patch("workers.views.setup_accounts.set_worker_ui_password")
        mocker.patch("workers.views.setup_accounts.append_checkpoint")
        req = _setup_phase_post(
            api_rf, "/api/setup/worker-password/",
            {"password": "LongEnough1!"},
        )
        resp = setup_worker_password_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "ok"

    def test_short_password_rejected(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/worker-password/",
            {"password": "short"},
        )
        resp = setup_worker_password_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_empty_password_rejected(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/worker-password/", {"password": ""},
        )
        resp = setup_worker_password_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_service_error_returns_500(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_accounts.set_worker_ui_password",
            side_effect=OSError("permission denied"),
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/worker-password/",
            {"password": "LongEnough1!"},
        )
        resp = setup_worker_password_view(req)
        assert resp.status_code == 500
        assert resp.data["error"]["code"] == "internal_error"
