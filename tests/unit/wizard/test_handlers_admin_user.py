# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/admin_user.py`` (FR-M2-5).

Combines the dev agent's smoke pass with coverage expansion: every
required-field check, the resource-fail-closed branch, weak-password
failure surfaces a list of failure codes, and the password tuple is
NEVER written to the wizard log.
"""

from __future__ import annotations

import json

import pytest

from wizard.sethlans_wizard import (
    auth_state,
    password_validators,
    progress,
    wizard_state,
)
from wizard.sethlans_wizard.handlers import admin_user as admin_handler

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    wizard_state.reset_state_for_tests()
    password_validators.reset_resource_cache_for_tests()
    yield
    auth_state.reset_state_for_tests()
    wizard_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return admin_handler.make_admin_user_handler(tmp_path)


class TestHappyPath:

    def test_strong_password_stashes_admin(self, handler, tmp_path):
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Tr0ub4dor&3xp1l@in",
                "password_confirm": "Tr0ub4dor&3xp1l@in",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body["status"] == "ok"
        assert body["username"] == "alice"
        admin = wizard_state.get_admin()
        assert admin is not None
        assert admin["username"] == "alice"
        progress_payload = progress.read_checkpoints(tmp_path)
        assert "admin_validated" in progress_payload["checkpoints"]

    def test_password_never_in_response(self, handler):
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Tr0ub4dor&3xp1l@in",
                "password_confirm": "Tr0ub4dor&3xp1l@in",
            }).encode("utf-8"),
        )
        _, _, body = call_handler(handler, env)
        # The successful response carries username only.
        assert "Tr0ub4dor" not in json.dumps(body)

    def test_password_never_in_log(self, handler, caplog):
        # NF-6 — the password MUST NOT appear in the wizard log.
        with caplog.at_level("INFO"):
            env = auth_env(
                json.dumps({
                    "username": "alice",
                    "email": "alice@example.org",
                    "password": "Tr0ub4dor&3xp1l@in",
                    "password_confirm": "Tr0ub4dor&3xp1l@in",
                }).encode("utf-8"),
            )
            call_handler(handler, env)
        for record in caplog.records:
            assert "Tr0ub4dor" not in record.getMessage()


class TestFieldShape:

    @pytest.mark.parametrize(
        "missing_field",
        ["username", "email", "password", "password_confirm"],
    )
    def test_missing_field_returns_required_code(
        self, handler, missing_field,
    ):
        # Coverage expansion: each required field gets its own code.
        payload = {
            "username": "alice",
            "email": "alice@example.org",
            "password": "Tr0ub4dor&3xp1l@in",
            "password_confirm": "Tr0ub4dor&3xp1l@in",
        }
        del payload[missing_field]
        env = auth_env(json.dumps(payload).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == f"{missing_field}_required"

    @pytest.mark.parametrize(
        "field", ["username", "email", "password", "password_confirm"],
    )
    def test_empty_field_returns_required_code(self, handler, field):
        # Coverage expansion: empty string also fails the required gate.
        payload = {
            "username": "alice",
            "email": "alice@example.org",
            "password": "Tr0ub4dor&3xp1l@in",
            "password_confirm": "Tr0ub4dor&3xp1l@in",
        }
        payload[field] = ""
        env = auth_env(json.dumps(payload).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == f"{field}_required"

    @pytest.mark.parametrize(
        "field", ["username", "email", "password", "password_confirm"],
    )
    def test_non_string_field_returns_required_code(self, handler, field):
        payload = {
            "username": "alice",
            "email": "alice@example.org",
            "password": "Tr0ub4dor&3xp1l@in",
            "password_confirm": "Tr0ub4dor&3xp1l@in",
        }
        payload[field] = 42
        env = auth_env(json.dumps(payload).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == f"{field}_required"


class TestPasswordValidation:

    def test_password_mismatch_returns_400(self, handler):
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Tr0ub4dor&3xp1l@in",
                "password_confirm": "different",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_mismatch"

    def test_weak_password_returns_failures(self, handler):
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "password",
                "password_confirm": "password",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_invalid"
        assert "password_too_common" in body["failures"]

    def test_short_password_returns_short_failure(self, handler):
        # Coverage expansion: short password surfaces the documented
        # failure code.
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Ab3!",
                "password_confirm": "Ab3!",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_invalid"
        assert "password_too_short" in body["failures"]

    def test_password_similar_to_username(self, handler):
        # Coverage expansion: similarity check fires when password
        # closely resembles the chosen username.
        env = auth_env(
            json.dumps({
                "username": "supersecret",
                "email": "alice@example.org",
                "password": "supersecret123",
                "password_confirm": "supersecret123",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "password_too_similar" in body["failures"]


class TestResourceFailClosed:
    """FR-M2-5 — resource-integrity failure must hard-fail the handler
    with a 500 instead of silently passing weak passwords."""

    def test_resource_invalid_returns_500_at_top(self, handler, mocker):
        mocker.patch.object(
            admin_handler, "verify_resource",
            return_value="common_passwords_resource_invalid",
        )
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Tr0ub4dor&3xp1l@in",
                "password_confirm": "Tr0ub4dor&3xp1l@in",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("500"), body
        assert body["error"] == "common_passwords_resource_invalid"

    def test_resource_invalid_inside_validator_returns_500(
        self, handler, mocker,
    ):
        # Coverage expansion: alternative path — resource looked OK at
        # the top but breaks while a per-validator check is running.
        mocker.patch.object(
            admin_handler, "verify_resource", return_value=None,
        )
        mocker.patch.object(
            admin_handler, "validate_password",
            return_value=["common_passwords_resource_invalid"],
        )
        env = auth_env(
            json.dumps({
                "username": "alice",
                "email": "alice@example.org",
                "password": "Tr0ub4dor&3xp1l@in",
                "password_confirm": "Tr0ub4dor&3xp1l@in",
            }).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("500"), body
        assert body["error"] == "common_passwords_resource_invalid"


class TestRequestGates:

    def test_get_returns_405(self, handler):
        env = build_environ(
            method="GET",
            headers={"X-Wizard-Session": VALID_SESSION},
        )
        status, headers, _ = call_handler(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_missing_session_returns_401(self, handler):
        env = build_environ(
            body=json.dumps(
                {"username": "x", "email": "x", "password": "x",
                 "password_confirm": "x"},
            ).encode("utf-8"),
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
            body=json.dumps(
                {"username": "x", "email": "x", "password": "x",
                 "password_confirm": "x"},
            ).encode("utf-8"),
            headers={"X-Wizard-Session": VALID_SESSION},
            query_string="session_token=abc",
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("400")
