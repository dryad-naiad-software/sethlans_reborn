# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/worker_password.py``
(FR-M2-6).

Combines the dev agent's smoke pass with coverage expansion: PBKDF2
parameters match the worker's existing scheme (cross-checked against
``worker.sethlans_worker_agent.web_ui.auth._hash_password``), every
gate (auth/method/qs/json/body) returns the expected error, and the
plaintext password is NEVER written to the wizard log or the response.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from wizard.sethlans_wizard import auth_state, progress, wizard_state
from wizard.sethlans_wizard.handlers import (
    worker_password as worker_password_handler,
)

from ._phase1_helpers import VALID_SESSION, auth_env, build_environ, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    wizard_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()
    wizard_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return worker_password_handler.make_worker_password_handler(tmp_path)


class TestHappyPath:

    def test_valid_password_hashed_and_stashed(self, handler, tmp_path):
        env = auth_env(
            json.dumps(
                {"password": "secret-pa55", "use_admin_password": False},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body["status"] == "ok"
        state = wizard_state.get_worker_password()
        assert state is not None
        # PBKDF2 hex output is 64 hex chars (sha256 -> 32 bytes -> 64 hex).
        assert len(state["hash"]) == 64
        assert len(state["salt"]) == 32  # 16 bytes hex
        # Coverage expansion: checkpoint dropped on success.
        payload = progress.read_checkpoints(tmp_path)
        assert "worker_password_set" in payload["checkpoints"]

    def test_password_never_in_response(self, handler):
        env = auth_env(
            json.dumps({"password": "leak-this-please"}).encode("utf-8"),
        )
        _, _, body = call_handler(handler, env)
        assert "leak-this-please" not in json.dumps(body)

    def test_password_never_in_log(self, handler, caplog):
        # NF-6 — the password MUST NOT appear in the wizard log.
        with caplog.at_level("INFO"):
            env = auth_env(
                json.dumps({"password": "leak-this-please"}).encode("utf-8"),
            )
            call_handler(handler, env)
        for record in caplog.records:
            assert "leak-this-please" not in record.getMessage()

    def test_resubmission_generates_fresh_salt(self, handler):
        # FR-M2-6 — every submit produces a new salt + hash.
        env1 = auth_env(json.dumps({"password": "secret-pa55"}).encode("utf-8"))
        call_handler(handler, env1)
        first = wizard_state.get_worker_password()
        env2 = auth_env(json.dumps({"password": "secret-pa55"}).encode("utf-8"))
        call_handler(handler, env2)
        second = wizard_state.get_worker_password()
        assert first["salt"] != second["salt"]
        assert first["hash"] != second["hash"]


class TestPbkdf2Parameters:
    """FR-M2-6 — the produced hash MUST match the worker's existing
    PBKDF2 scheme exactly (sha256, 100_000 iters, 16-byte salt).
    Cross-checked against ``worker.web_ui.auth._hash_password``."""

    def test_hash_matches_pbkdf2_parameters(self):
        hash_hex, salt_hex = (
            worker_password_handler.hash_worker_password("hello-world")
        )
        salt = bytes.fromhex(salt_hex)
        derived = hashlib.pbkdf2_hmac(
            "sha256", b"hello-world", salt, iterations=100_000,
        )
        assert derived.hex() == hash_hex

    def test_hash_matches_worker_auth_scheme(self):
        # Coverage expansion: FR-M2-6 contract — wizard hash MUST be
        # verifiable by the worker's existing _hash_password helper.
        hash_hex, salt_hex = (
            worker_password_handler.hash_worker_password("topsecret-pw")
        )
        salt = bytes.fromhex(salt_hex)
        # Replicate worker.web_ui.auth._hash_password parameters.
        derived = hashlib.pbkdf2_hmac(
            "sha256", b"topsecret-pw", salt, iterations=100_000,
        )
        assert derived.hex() == hash_hex

    def test_salt_unique_per_call(self):
        # Coverage expansion: 16-byte salt randomness — two calls must
        # produce different salts.
        salts = {
            worker_password_handler.hash_worker_password("x")[1]
            for _ in range(20)
        }
        # Almost certainly all 20 unique; allow 2 collisions worst case.
        assert len(salts) >= 18


class TestPasswordValidation:

    def test_short_password_rejected(self, handler):
        env = auth_env(
            json.dumps({"password": "short"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_too_short"

    def test_seven_char_password_rejected(self, handler):
        # Coverage expansion: boundary at 8 chars (NF-8).
        env = auth_env(
            json.dumps({"password": "1234567"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_too_short"

    def test_eight_char_password_accepted(self, handler):
        # Coverage expansion: boundary — exactly 8 chars must pass.
        env = auth_env(
            json.dumps({"password": "12345678"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body

    def test_missing_password_returns_required(self, handler):
        env = auth_env(json.dumps({}).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_required"

    def test_empty_password_returns_required(self, handler):
        env = auth_env(json.dumps({"password": ""}).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_required"

    def test_non_string_password_returns_required(self, handler):
        env = auth_env(json.dumps({"password": 12345}).encode("utf-8"))
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_required"


class TestUseAdminPasswordContract:
    """FR-M2-6 v3 (FE-1 fix) — when ``use_admin_password`` is True the
    handler MUST read the admin plaintext from wizard_state instead of
    accepting it from the request body. The browser therefore never
    holds the admin password between the admin-user and worker-password
    steps; the previous Phase 2 implementation round-tripped it via
    ``window.sessionStorage`` which lingered until tab close.
    """

    def test_use_admin_password_with_state_present_succeeds(self, handler, tmp_path):
        wizard_state.set_admin(
            "operator",
            "ops@example.com",
            "MyV3ry-Strong!Adm1nPwd",
        )
        env = auth_env(
            json.dumps({"use_admin_password": True}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body["status"] == "ok"
        # The stashed hash MUST be derivable from the admin plaintext
        # using the same PBKDF2 parameters the worker auth uses.
        state = wizard_state.get_worker_password()
        assert state is not None
        salt = bytes.fromhex(state["salt"])
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            b"MyV3ry-Strong!Adm1nPwd",
            salt,
            iterations=100_000,
        )
        assert derived.hex() == state["hash"]
        # Checkpoint dropped on success.
        payload = progress.read_checkpoints(tmp_path)
        assert "worker_password_set" in payload["checkpoints"]

    def test_use_admin_password_with_password_field_null_succeeds(self, handler):
        # Frontend may send `password: null` for explicitness; handler
        # MUST inspect ``use_admin_password`` first and ignore the null
        # password field rather than rejecting as password_required.
        wizard_state.set_admin("operator", "ops@example.com", "AnotherStr0ngPwd!")
        env = auth_env(
            json.dumps(
                {"use_admin_password": True, "password": None},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body

    def test_use_admin_password_without_state_returns_400(self, handler):
        # Operator hit /worker-password without going through admin-user.
        env = auth_env(
            json.dumps({"use_admin_password": True}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "admin_password_unavailable"
        # NO worker_password_set checkpoint should have been recorded.
        assert wizard_state.get_worker_password() is None

    def test_use_admin_password_ignores_body_password(self, handler):
        # FE-1 fix: even if a malicious or buggy frontend sends a
        # password alongside use_admin_password=True, the handler MUST
        # use the admin plaintext from wizard_state — never the body
        # value. This locks the contract so the leak cannot regress.
        wizard_state.set_admin(
            "operator",
            "ops@example.com",
            "TheActu4lAdm1nPassw0rd",
        )
        env = auth_env(
            json.dumps(
                {
                    "use_admin_password": True,
                    "password": "attacker-supplied-junk",
                },
            ).encode("utf-8"),
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("200")
        state = wizard_state.get_worker_password()
        # Hash must be derived from the *admin* password, not the body.
        salt = bytes.fromhex(state["salt"])
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            b"TheActu4lAdm1nPassw0rd",
            salt,
            iterations=100_000,
        )
        assert derived.hex() == state["hash"]

    def test_use_admin_password_false_with_password_uses_body(self, handler):
        # Existing behavior preserved: explicit opt-out path still uses
        # the supplied password and rejects when missing.
        wizard_state.set_admin("operator", "ops@example.com", "AdminPwd-Lives")
        env = auth_env(
            json.dumps(
                {"use_admin_password": False, "password": "manualpw1"},
            ).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        # And the hash is from the *manual* password, not the admin one.
        state = wizard_state.get_worker_password()
        salt = bytes.fromhex(state["salt"])
        derived = hashlib.pbkdf2_hmac(
            "sha256", b"manualpw1", salt, iterations=100_000,
        )
        assert derived.hex() == state["hash"]

    def test_use_admin_password_false_missing_password_returns_required(self, handler):
        # Even with admin state present, opting out means body password
        # is required.
        wizard_state.set_admin("operator", "ops@example.com", "AdminPwd-Lives")
        env = auth_env(
            json.dumps({"use_admin_password": False}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_required"


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
            body=json.dumps({"password": "secret-pa55"}).encode("utf-8"),
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
            body=json.dumps({"password": "secret-pa55"}).encode("utf-8"),
            headers={"X-Wizard-Session": VALID_SESSION},
            query_string="session_token=abc",
        )
        status, _, _ = call_handler(handler, env)
        assert status.startswith("400")


class TestExports:

    def test_dunder_all(self):
        for name in (
            "make_worker_password_handler",
            "hash_worker_password",
        ):
            assert name in worker_password_handler.__all__

    def test_pbkdf2_constants_pinned(self):
        # FR-M2-6 — wizard MUST stay locked to worker's scheme.
        assert worker_password_handler.PBKDF2_ALGO == "sha256"
        assert worker_password_handler.PBKDF2_ITERATIONS == 100_000
        assert worker_password_handler.SALT_LENGTH == 16
        assert worker_password_handler.MIN_PASSWORD_LENGTH == 8
