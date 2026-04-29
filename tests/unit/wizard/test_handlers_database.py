# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/database.py`` (FR-M2-4).

Combines the dev agent's smoke pass with coverage expansion: every
error-category surfaces a documented user-facing message, raw driver
text NEVER reaches the response body, OPTIONS keys are not written to
manager.ini, and the request-method/auth/JSON gates all behave.
"""

from __future__ import annotations

import configparser
import json

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import database as database_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


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

    def test_sqlite_records_database_configured_checkpoint(
        self, handler, tmp_path,
    ):
        # Coverage expansion: signal gets dropped on success.
        from wizard.sethlans_wizard import progress
        env = auth_env(
            json.dumps({"engine": "sqlite", "name": "x.db"}).encode("utf-8"),
        )
        call_handler(handler, env)
        payload = progress.read_checkpoints(tmp_path)
        assert "database_configured" in payload["checkpoints"]

    def test_postgresql_happy_path_writes_ini(
        self, handler, tmp_path, mocker,
    ):
        # Coverage expansion: a successful live-connect populates host /
        # port / user / password into [database].
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(True, None),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "sethlans",
                 "host": "db.example", "port": 5432,
                 "user": "u", "password": "p"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        parser = configparser.ConfigParser()
        parser.read(str(tmp_path / "manager.ini"))
        assert parser.get("database", "engine") == "postgresql"
        assert parser.get("database", "host") == "db.example"
        assert parser.getint("database", "port") == 5432
        assert parser.get("database", "user") == "u"
        assert parser.get("database", "password") == "p"

    def test_custom_engine_records_engine_path(
        self, handler, tmp_path, mocker,
    ):
        # Coverage expansion: 'custom' engine carries an engine_path.
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(True, None),
        )
        env = auth_env(
            json.dumps(
                {"engine": "custom",
                 "engine_path": "django_my_backend",
                 "name": "x"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        parser = configparser.ConfigParser()
        parser.read(str(tmp_path / "manager.ini"))
        assert parser.get("database", "engine") == "custom"
        assert parser.get("database", "engine_path") == "django_my_backend"


class TestErrorCategoryAllowlist:
    """FR-M2-4 / security-reviewer MED-3 — every category surfaces a
    documented user-facing message and the raw driver text NEVER
    reaches the HTTP body."""

    @pytest.mark.parametrize(
        "category",
        [
            "auth_failed", "host_unreachable", "db_not_found",
            "permission_denied", "ssl_error", "timeout", "generic",
        ],
    )
    def test_each_category_returns_400_with_documented_message(
        self, handler, mocker, category,
    ):
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(False, category),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "n", "host": "h",
                 "port": 5432, "user": "u", "password": "supersecret"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == category
        # The "message" key MUST be present and human-readable.
        assert isinstance(body.get("message"), str)
        assert body["message"]

    def test_raw_password_never_in_response(self, handler, mocker):
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(False, "auth_failed"),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "n", "host": "h",
                 "port": 5432, "user": "u", "password": "leak-me-pls"},
            ).encode("utf-8"),
        )
        _, _, body = call_handler(handler, env)
        assert "leak-me-pls" not in json.dumps(body)

    def test_unknown_category_falls_back_to_generic(
        self, handler, mocker,
    ):
        # Coverage expansion: a category outside the documented set
        # should still produce a generic message rather than 500.
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(False, "made_up_category_xyz"),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "n", "host": "h",
                 "port": 5432, "user": "u", "password": "p"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        # The generic message should be picked up.
        assert "could not connect" in body["message"].lower()


class TestErrorPaths:

    def test_unknown_engine_rejected(self, handler):
        env = auth_env(
            json.dumps({"engine": "oracle", "name": "x"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "engine" in body["error"].lower()

    def test_missing_engine_rejected(self, handler):
        env = auth_env(json.dumps({"name": "x"}).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body

    def test_postgresql_unreachable_returns_category(
        self, handler, mocker,
    ):
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(False, "host_unreachable"),
        )
        env = auth_env(
            json.dumps(
                {"engine": "postgresql", "name": "x",
                 "host": "no.such.host", "port": 5432,
                 "user": "u", "password": "secret"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "host_unreachable"
        assert "secret" not in json.dumps(body)

    def test_get_returns_405_with_allow_header(self, handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, handler):
        env = build_environ(
            body=json.dumps({"engine": "sqlite"}).encode("utf-8"),
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")

    def test_malformed_json_returns_400(self, handler):
        env = auth_env(b"not-json{{")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        assert "json" in body["error"].lower()

    def test_oversized_body_returns_400(self, handler):
        env = auth_env(b"\x00" * 100)
        env["CONTENT_LENGTH"] = "999999"
        status, _, body = call_handler(handler, env)
        assert status.startswith("400")
        assert "large" in body["error"].lower()

    def test_qs_token_rejected(self, handler):
        env = build_environ(
            body=json.dumps({"engine": "sqlite"}).encode("utf-8"),
            headers={"X-Wizard-Session": VALID_SESSION},
            query_string="session_token=abc",
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("400")

    def test_manager_ini_write_failure_returns_500(
        self, handler, mocker,
    ):
        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            return_value=(True, None),
        )
        mocker.patch(
            "wizard.sethlans_wizard.handlers.database.update_manager_ini",
            side_effect=OSError("disk full"),
        )
        env = auth_env(
            json.dumps({"engine": "sqlite", "name": "x"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("500")
        assert "manager.ini" in body["error"].lower()


class TestRawDriverErrorNeverLeaks:
    """security-reviewer MED-3 — the actual exception text MUST live in
    the debug log only and NEVER reach the HTTP body."""

    def test_raw_exception_text_logged_not_returned(
        self, handler, mocker, caplog,
    ):
        # Stub live_connect to BEHAVE like the real driver — log a
        # detailed message and return only the category.
        import logging

        def fake_live_connect(engine, payload, dd):
            logging.getLogger("wizard.sethlans_wizard.db_validate").info(
                "real driver text: FATAL: remote-host returned %r",
                "weird-internal-info",
            )
            return False, "host_unreachable"

        mocker.patch(
            "wizard.sethlans_wizard.db_validate.live_connect",
            side_effect=fake_live_connect,
        )
        with caplog.at_level("INFO"):
            env = auth_env(
                json.dumps(
                    {"engine": "postgresql", "name": "n", "host": "h",
                     "port": 5432, "user": "u", "password": "p"},
                ).encode("utf-8"),
            )
            _, _, body = call_handler(handler, env)
        # Body MUST NOT carry the raw text.
        assert "weird-internal-info" not in json.dumps(body)
        # But the log MUST.
        msgs = [r.getMessage() for r in caplog.records]
        assert any("weird-internal-info" in m for m in msgs)


class TestSectionBuilder:
    """FR-M2-4 / django-api-reviewer LOW-8/9 — only the engine short
    name + name/host/port/user/password are written; OPTIONS-level keys
    must be absent."""

    def test_sqlite_section_omits_host_port_user_password(self):
        section = database_handler._build_section(
            "sqlite", {"name": "sethlans.db"},
        )
        assert section == {"engine": "sqlite", "name": "sethlans.db"}

    def test_postgresql_section_includes_credentials(self):
        section = database_handler._build_section(
            "postgresql",
            {"name": "n", "host": "h", "port": 5432,
             "user": "u", "password": "p"},
        )
        assert section == {
            "engine": "postgresql",
            "name": "n", "host": "h", "port": 5432,
            "user": "u", "password": "p",
        }

    def test_custom_engine_path_threaded_when_present(self):
        section = database_handler._build_section(
            "custom",
            {"name": "n", "host": "h", "port": 0,
             "user": "u", "password": "",
             "engine_path": "django_my_backend"},
        )
        assert section["engine"] == "custom"
        assert section["engine_path"] == "django_my_backend"

    def test_custom_engine_path_omitted_when_absent(self):
        section = database_handler._build_section(
            "custom", {"name": "n"},
        )
        assert "engine_path" not in section


class TestValidEnginesFrozenset:

    def test_valid_engines_pinned(self):
        assert database_handler.VALID_ENGINES == frozenset(
            {"sqlite", "postgresql", "mysql", "custom"},
        )
