# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/verify.py``
(FR-M2-8)."""

from __future__ import annotations

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import verify as verify_handler

from ._phase1_helpers import VALID_SESSION, auth_env, call_handler


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    verify_handler.reset_cache_for_tests()
    yield
    auth_state.reset_state_for_tests()
    verify_handler.reset_cache_for_tests()


@pytest.fixture
def handler(tmp_path):
    return verify_handler.make_verify_handler(tmp_path)


class TestVerifyChecklist:

    def test_returns_check_list_shape(self, handler):
        # Empty data dir — every check fails but the response is shaped
        # correctly with a list of dicts and an all_passed bool.
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert "checks" in body
        assert "all_passed" in body
        assert isinstance(body["checks"], list)
        assert isinstance(body["all_passed"], bool)
        names = {c["name"] for c in body["checks"]}
        # FR-M2-8 manager-only: 4 baseline checks.
        assert names == {
            "network_bindable",
            "database_reachable",
            "ffmpeg_runs",
            "pending_setup_writable",
        }

    def test_missing_session_returns_401(self, handler):
        from ._phase1_helpers import build_environ
        env = build_environ(method="POST", body=b"")
        status, _, _ = call_handler(handler, env)
        assert status.startswith("401")
