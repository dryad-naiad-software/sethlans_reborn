# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/admin_user.py``
(FR-M2-5)."""

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

from ._phase1_helpers import VALID_SESSION, auth_env, call_handler


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
        # Checkpoint recorded.
        progress_payload = progress.read_checkpoints(tmp_path)
        assert "admin_validated" in progress_payload["checkpoints"]


class TestErrorPaths:

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
