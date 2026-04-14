# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the setup wizard database endpoint.

Exercises ``POST /api/setup/database/`` via the Django test client.
Verifies the SQLite path (migrations applied, checkpoint recorded)
and the external DB path (INI written, ``restart_required`` returned).
"""

import configparser

import pytest
from rest_framework.test import APIClient


@pytest.fixture()
def data_dir(tmp_path, settings):
    """Point ``settings.BASE_DIR`` at a temp directory.

    No sentinel file is written so ``_setup_complete()`` returns
    ``False`` (sentinel absent → ``read_sentinel`` returns ``None``).
    """
    settings.BASE_DIR = tmp_path
    return tmp_path


# -------------------------------------------------------------------
# FR-A4: SQLite path — apply migrations directly
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestDatabaseSQLite:

    def test_sqlite_applies_migrations_and_returns_ok(self, data_dir):
        """SQLite engine applies migrations and returns ok.

        In the test environment the default DB is already migrated,
        so ``apply_migrations()`` is a no-op but must not error.
        """
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {"engine": "sqlite"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_sqlite_appends_checkpoint(self, data_dir):
        """SQLite path appends ``database_configured`` checkpoint."""
        client = APIClient()
        client.post(
            "/api/setup/database/",
            {"engine": "sqlite"},
            format="json",
        )
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(data_dir)
        assert sentinel is not None
        assert "database_configured" in sentinel["checkpoints"]

    def test_sqlite_default_engine(self, data_dir):
        """Omitting engine defaults to SQLite."""
        client = APIClient()
        resp = client.post(
            "/api/setup/database/", {}, format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# -------------------------------------------------------------------
# FR-A4: External DB path — write config, return restart_required
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestDatabaseExternal:

    def test_postgresql_missing_name_returns_400(self, data_dir):
        """External DB without a database name is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {
                "engine": "postgresql",
                "host": "localhost",
                "port": "5432",
                "user": "sethlans",
                "password": "secret",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["error"].lower()

    def test_unknown_engine_returns_400(self, data_dir):
        """Unrecognized engine string is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {"engine": "oracle", "name": "testdb"},
            format="json",
        )
        assert resp.status_code == 400
        assert "Unknown engine" in resp.json()["error"]

    def test_custom_engine_requires_engine_path(self, data_dir):
        """Custom engine without engine_path is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {"engine": "custom", "name": "testdb"},
            format="json",
        )
        assert resp.status_code == 400
        assert "engine_path" in resp.json()["error"]

    def test_external_db_connection_failure_returns_400(
        self, data_dir,
    ):
        """Unreachable external DB returns 400 with error message.

        Uses a PostgreSQL engine pointing at a non-existent host so
        ``validate_db_connection`` raises ``ConnectionError``.
        """
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {
                "engine": "postgresql",
                "host": "192.0.2.1",  # TEST-NET, unreachable
                "port": "5432",
                "name": "testdb",
                "user": "user",
                "password": "pass",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_external_db_writes_ini_and_returns_restart(
        self, data_dir, mocker,
    ):
        """Valid external DB writes config and returns restart_required.

        Mocks ``validate_db_connection`` to avoid needing a real
        external database server.
        """
        mocker.patch(
            "workers.views.setup_database.validate_db_connection",
        )
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {
                "engine": "postgresql",
                "host": "db.local",
                "port": "5432",
                "name": "sethlans_prod",
                "user": "sethlans",
                "password": "s3cret",
            },
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "restart_required"

        ini = configparser.ConfigParser()
        ini.read(data_dir / "manager.ini")
        assert ini.get("database", "engine") == "postgresql"
        assert ini.get("database", "name") == "sethlans_prod"
        assert ini.get("database", "host") == "db.local"
        assert ini.get("database", "user") == "sethlans"

    def test_external_db_appends_checkpoint(self, data_dir, mocker):
        """External DB path appends ``database_configured``."""
        mocker.patch(
            "workers.views.setup_database.validate_db_connection",
        )
        client = APIClient()
        client.post(
            "/api/setup/database/",
            {
                "engine": "mysql",
                "host": "db.local",
                "name": "sethlans",
                "user": "root",
                "password": "pass",
            },
            format="json",
        )
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(data_dir)
        assert "database_configured" in sentinel["checkpoints"]


# -------------------------------------------------------------------
# Post-completion guard
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestDatabasePostCompletion:

    def test_returns_404_when_setup_complete(self, data_dir):
        """Database endpoint returns 404 when setup is fully complete.

        The ``_setup_complete()`` checks ``completed_at is not None``,
        so only a fully completed sentinel triggers 404.
        """
        from workers.services.sentinel import write_sentinel
        write_sentinel(data_dir, {
            "version": 1,
            "completed_at": "2026-04-13T12:00:00Z",
            "topology": "manager",
            "checkpoints": ["topology_chosen"],
        })
        client = APIClient()
        resp = client.post(
            "/api/setup/database/",
            {"engine": "sqlite"},
            format="json",
        )
        assert resp.status_code == 404
