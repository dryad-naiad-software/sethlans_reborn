# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_database.py``.

SQLite path (apply migrations), external DB path (validate + write
config), invalid engine rejection, connection failure handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from workers.views.setup_database import setup_database_view


def _setup_phase_post(api_rf, path, data):
    request = api_rf.post(path, data, format="json")
    session = {"setup_phase": True, "setup_session_id": "sid-1"}
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    request._setup_snapshot = {
        "complete": False, "phase": "database", "session_id": None,
    }
    return request


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        "workers.views.setup_database.is_frozen", return_value=False,
    )


class TestDatabaseSQLite:

    def test_sqlite_applies_migrations(self, api_rf, mocker):
        mock_migrate = mocker.patch(
            "workers.views.setup_database.apply_migrations",
        )
        mocker.patch("workers.views.setup_database.append_checkpoint")
        req = _setup_phase_post(
            api_rf, "/api/setup/database/", {"engine": "sqlite"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "ok"
        mock_migrate.assert_called_once()

    def test_sqlite_is_default(self, api_rf, mocker):
        mocker.patch("workers.views.setup_database.apply_migrations")
        mocker.patch("workers.views.setup_database.append_checkpoint")
        req = _setup_phase_post(api_rf, "/api/setup/database/", {})
        resp = setup_database_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "ok"

    def test_sqlite_migration_failure_returns_500(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_database.apply_migrations",
            side_effect=RuntimeError("migration failed"),
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/database/", {"engine": "sqlite"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 500
        assert resp.data["error"]["code"] == "internal_error"

    def test_sqlite_records_checkpoint(self, api_rf, mocker):
        mocker.patch("workers.views.setup_database.apply_migrations")
        mock_cp = mocker.patch(
            "workers.views.setup_database.append_checkpoint",
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/database/", {"engine": "sqlite"},
        )
        setup_database_view(req)
        mock_cp.assert_called_once()
        assert mock_cp.call_args[0][1] == "database_configured"


class TestDatabaseExternal:

    def test_postgresql_restart_required(self, api_rf, mocker):
        mocker.patch("workers.views.setup_database.validate_db_connection")
        mocker.patch("workers.views.setup_database.write_manager_ini")
        mocker.patch("workers.views.setup_database.append_checkpoint")
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {
                "engine": "postgresql", "host": "db.local", "port": "5432",
                "name": "sethlans", "user": "admin", "password": "s",
            },
        )
        resp = setup_database_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "restart_required"

    def test_connection_failure(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_database.validate_db_connection",
            side_effect=ConnectionError("Connection refused"),
        )
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {"engine": "postgresql", "host": "bad", "name": "db"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_missing_name(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {"engine": "postgresql", "host": "db.local"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"


class TestDatabaseInvalidEngine:

    def test_unknown_engine(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {"engine": "oracle", "name": "mydb"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_custom_engine_requires_path(self, api_rf):
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {"engine": "custom"},
        )
        resp = setup_database_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_custom_engine_with_path_accepted(self, api_rf, mocker):
        mocker.patch("workers.views.setup_database.validate_db_connection")
        mocker.patch("workers.views.setup_database.write_manager_ini")
        mocker.patch("workers.views.setup_database.append_checkpoint")
        req = _setup_phase_post(
            api_rf, "/api/setup/database/",
            {
                "engine": "custom",
                "engine_path": "my.custom.backend",
                "name": "mydb",
            },
        )
        resp = setup_database_view(req)
        assert resp.status_code == 200
        assert resp.data["status"] == "restart_required"
