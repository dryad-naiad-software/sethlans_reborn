# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for setup wizard account endpoints.

Exercises ``POST /api/setup/admin-user/`` and
``POST /api/setup/worker-password/`` via the Django test client.
Verifies real ORM user creation, Django password validation,
duplicate detection (409), and worker config file writes.
"""

import configparser

import pytest
from django.contrib.auth import authenticate, get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture()
def data_dir(tmp_path, settings):
    """Point ``settings.BASE_DIR`` at a temp directory.

    Writes a partial sentinel (no ``completed_at``) so the
    accounts views accept requests.
    """
    settings.BASE_DIR = tmp_path
    from workers.services.sentinel import write_sentinel
    write_sentinel(tmp_path, {
        "version": 1,
        "completed_at": None,
        "topology": "manager",
        "checkpoints": ["topology_chosen", "network_configured",
                        "database_configured"],
    })
    return tmp_path


# -------------------------------------------------------------------
# FR-A5: POST /api/setup/admin-user/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminUserCreation:

    def test_creates_superuser(self, data_dir):
        """Valid payload creates a real Django superuser."""
        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "username": "wizardadmin",
                "email": "admin@example.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["username"] == "wizardadmin"

        user = User.objects.get(username="wizardadmin")
        assert user.is_superuser is True
        assert user.is_staff is True

    def test_created_user_can_authenticate(self, data_dir):
        """The created superuser can log in via Django auth."""
        client = APIClient()
        client.post(
            "/api/setup/admin-user/",
            {
                "username": "authtest",
                "email": "auth@example.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format="json",
        )
        user = authenticate(username="authtest", password="Str0ng!Pass99")
        assert user is not None
        assert user.is_active is True

    def test_duplicate_username_returns_409(self, data_dir):
        """Second creation with the same username returns 409."""
        client = APIClient()
        payload = {
            "username": "dupeuser",
            "email": "dupe@example.com",
            "password": "Str0ng!Pass99",
            "password_confirm": "Str0ng!Pass99",
        }
        first = client.post(
            "/api/setup/admin-user/", payload, format="json",
        )
        assert first.status_code == 200

        second = client.post(
            "/api/setup/admin-user/", payload, format="json",
        )
        assert second.status_code == 409
        assert second.json()["error"] == "admin_exists"

    def test_password_mismatch_returns_400(self, data_dir):
        """Mismatched password fields are rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "username": "mismatch",
                "email": "m@example.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "DifferentPass!1",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "errors" in resp.json()

    def test_weak_password_returns_400(self, data_dir):
        """A common/short password fails Django validators."""
        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "username": "weakuser",
                "email": "w@example.com",
                "password": "password",
                "password_confirm": "password",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "errors" in resp.json()

    def test_missing_username_returns_400(self, data_dir):
        """Missing username field is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_missing_password_returns_400(self, data_dir):
        """Missing password field is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "username": "nopwd",
                "email": "n@example.com",
                "password_confirm": "abc",
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_admin_created_checkpoint_appended(self, data_dir):
        """Successful creation appends ``admin_created`` checkpoint."""
        client = APIClient()
        client.post(
            "/api/setup/admin-user/",
            {
                "username": "cptest",
                "email": "cp@example.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format="json",
        )
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(data_dir)
        assert "admin_created" in sentinel["checkpoints"]

    def test_returns_404_after_setup_complete(self, data_dir):
        """Endpoint returns 404 after ``completed_at`` is set."""
        from workers.services.sentinel import create_sentinel
        create_sentinel(data_dir, "manager", ["topology_chosen"])

        client = APIClient()
        resp = client.post(
            "/api/setup/admin-user/",
            {
                "username": "late",
                "email": "l@example.com",
                "password": "Str0ng!Pass99",
                "password_confirm": "Str0ng!Pass99",
            },
            format="json",
        )
        assert resp.status_code == 404


# -------------------------------------------------------------------
# FR-A6: POST /api/setup/worker-password/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestWorkerPassword:

    def test_sets_worker_password(self, data_dir):
        """Valid password writes hashed config to worker.ini."""
        client = APIClient()
        resp = client.post(
            "/api/setup/worker-password/",
            {"password": "WorkerPass!99"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        ini = configparser.ConfigParser()
        ini.read(data_dir / "worker.ini")
        assert ini.has_option("worker", "ui_password_hash")
        assert ini.has_option("worker", "ui_password_salt")

    def test_short_password_returns_400(self, data_dir):
        """Password shorter than 8 characters is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/worker-password/",
            {"password": "short"},
            format="json",
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_empty_password_returns_400(self, data_dir):
        """Empty password is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/worker-password/",
            {"password": ""},
            format="json",
        )
        assert resp.status_code == 400

    def test_worker_password_checkpoint_appended(self, data_dir):
        """Successful call appends ``worker_password_set``."""
        client = APIClient()
        client.post(
            "/api/setup/worker-password/",
            {"password": "WorkerPass!99"},
            format="json",
        )
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(data_dir)
        assert "worker_password_set" in sentinel["checkpoints"]
