# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/worker_password.py``
(FR-M2-6)."""

from __future__ import annotations

import hashlib
import json

import pytest

from wizard.sethlans_wizard import auth_state, wizard_state
from wizard.sethlans_wizard.handlers import (
    worker_password as worker_password_handler,
)

from ._phase1_helpers import VALID_SESSION, auth_env, call_handler


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

    def test_valid_password_hashed_and_stashed(self, handler):
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

    def test_hash_matches_pbkdf2_parameters(self):
        hash_hex, salt_hex = (
            worker_password_handler.hash_worker_password("hello-world")
        )
        # Recompute and compare — verifies the canonical parameters
        # (sha256 / 100_000 iters / 16-byte salt).
        salt = bytes.fromhex(salt_hex)
        derived = hashlib.pbkdf2_hmac(
            "sha256", b"hello-world", salt, iterations=100_000,
        )
        assert derived.hex() == hash_hex


class TestErrorPaths:

    def test_short_password_rejected(self, handler):
        env = auth_env(
            json.dumps({"password": "short"}).encode("utf-8"),
        )
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "password_too_short"
