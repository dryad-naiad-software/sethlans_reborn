# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/database.py``
(FR-M2-4)."""

from __future__ import annotations

import configparser
import json

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import database as database_handler

from ._phase1_helpers import VALID_SESSION, auth_env, call_handler


@pytest.fixture(autouse=True)
def _reset_auth():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    yield
    auth_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return database_handler.make_database_handler(tmp_path)


class TestHappyPath:

    def test_sqlite_engine_writes_ini(self, handler, tmp_path):
        env = auth_env(
            json.dumps(
                {"engine": "sqlite", "name": "sethlans.db"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body == {"status": "ok"}
        target = tmp_path / "manager.ini"
        parser = configparser.ConfigParser()
        parser.read(str(target))
        assert parser.get("database", "engine") == "sqlite"
        assert parser.get("database", "name") == "sethlans.db"


class TestErrorPaths:

    def test_unknown_engine_rejected(self, handler):
        env = auth_env(
            json.dumps(
                {"engine": "oracle", "name": "sethlans"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "engine" in body["error"].lower()

    def test_postgresql_unreachable_returns_category(
        self, handler, monkeypatch,
    ):
        # Force live_connect to return a 'host_unreachable' to exercise
        # the response shape without needing a real DB.
        from wizard.sethlans_wizard import db_validate

        monkeypatch.setattr(
            db_validate, "live_connect",
            lambda eng, payload, dd: (False, "host_unreachable"),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "x", "host": "no.such.host",
                 "port": 5432, "user": "u", "password": "secret"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "host_unreachable"
        # secret never appears in the response.
        assert "secret" not in json.dumps(body)
