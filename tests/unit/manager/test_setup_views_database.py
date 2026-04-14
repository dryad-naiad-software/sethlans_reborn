# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/views/setup_database.py``.

Covers SQLite path (apply migrations), external DB path (validate
connection + write config), invalid engine rejection, and connection
failure handling.  All DB and filesystem calls are mocked.
"""

import pytest
from rest_framework.test import APIRequestFactory

from workers.views.setup_database import setup_database_view


@pytest.fixture
def api_rf():
    return APIRequestFactory()


@pytest.fixture
def _mock_sentinel_incomplete(mocker):
    """Sentinel returns None (setup not yet started)."""
    mocker.patch(
        'workers.views.setup_database.read_sentinel',
        return_value=None,
    )
    mocker.patch(
        'workers.views.setup_database.is_frozen',
        return_value=False,
    )


@pytest.fixture
def _mock_sentinel_complete(mocker):
    """Sentinel shows setup already complete."""
    mocker.patch(
        'workers.views.setup_database.read_sentinel',
        return_value={
            "version": 1,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "manager",
            "checkpoints": [],
        },
    )
    mocker.patch(
        'workers.views.setup_database.is_frozen',
        return_value=False,
    )


# ---- SQLite path -------------------------------------------------------------

class TestDatabaseSQLite:

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_sqlite_applies_migrations(self, api_rf, mocker):
        mock_migrate = mocker.patch(
            'workers.views.setup_database.apply_migrations',
        )
        mocker.patch(
            'workers.views.setup_database.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "sqlite"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "ok"
        mock_migrate.assert_called_once()

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_sqlite_is_default_engine(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_database.apply_migrations',
        )
        mocker.patch(
            'workers.views.setup_database.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/database/',
            {},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "ok"

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_sqlite_migration_failure_returns_500(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_database.apply_migrations',
            side_effect=RuntimeError("migration failed"),
        )
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "sqlite"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 500
        assert "error" in response.data

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_sqlite_records_checkpoint(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_database.apply_migrations',
        )
        mock_cp = mocker.patch(
            'workers.views.setup_database.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "sqlite"},
            format='json',
        )
        setup_database_view(request)
        mock_cp.assert_called_once()
        assert mock_cp.call_args[0][1] == "database_configured"


# ---- External DB path --------------------------------------------------------

class TestDatabaseExternal:

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_postgresql_returns_restart_required(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_database.validate_db_connection',
        )
        mocker.patch(
            'workers.views.setup_database.write_manager_ini',
        )
        mocker.patch(
            'workers.views.setup_database.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/database/',
            {
                "engine": "postgresql",
                "host": "db.local",
                "port": "5432",
                "name": "sethlans",
                "user": "admin",
                "password": "secret",
            },
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "restart_required"

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_connection_failure_returns_400(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_database.validate_db_connection',
            side_effect=ConnectionError("Connection refused"),
        )
        request = api_rf.post(
            '/api/setup/database/',
            {
                "engine": "postgresql",
                "host": "bad-host",
                "name": "db",
            },
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 400
        assert "Connection refused" in response.data["error"]

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_missing_name_returns_400(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "postgresql", "host": "db.local"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 400
        assert "name" in response.data["error"].lower()


# ---- Invalid engine ----------------------------------------------------------

class TestDatabaseInvalidEngine:

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_unknown_engine_rejected(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "oracle", "name": "mydb"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 400
        assert "Unknown engine" in response.data["error"]

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_custom_engine_requires_path(self, api_rf, mocker):
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "custom"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 400
        assert "engine_path" in response.data["error"]

    @pytest.mark.usefixtures("_mock_sentinel_incomplete")
    def test_custom_engine_with_path_accepted(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_database.validate_db_connection',
        )
        mocker.patch(
            'workers.views.setup_database.write_manager_ini',
        )
        mocker.patch(
            'workers.views.setup_database.append_checkpoint',
        )
        request = api_rf.post(
            '/api/setup/database/',
            {
                "engine": "custom",
                "engine_path": "my.custom.backend",
                "name": "mydb",
            },
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 200
        assert response.data["status"] == "restart_required"


# ---- Setup complete gate -----------------------------------------------------

class TestDatabaseSetupComplete:

    @pytest.mark.usefixtures("_mock_sentinel_complete")
    def test_returns_404_when_complete(self, api_rf):
        request = api_rf.post(
            '/api/setup/database/',
            {"engine": "sqlite"},
            format='json',
        )
        response = setup_database_view(request)
        assert response.status_code == 404
