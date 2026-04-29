# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/handlers/pending_setup.py``
(FR-PEND2 / FR-PEND1a)."""

from __future__ import annotations

import json
import time

import pytest

from wizard.sethlans_wizard import auth_state, wizard_state
from wizard.sethlans_wizard.handlers import pending_setup as pending_handler
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

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
    return pending_handler.make_pending_setup_handler(tmp_path)


def _populate_state():
    wizard_state.set_admin("alice", "alice@example.org", "Tr0ub4dor&3xp")
    wizard_state.set_ffmpeg("7.1", "/tmp/ffmpeg/7.1/ffmpeg")


class TestHappyPath:

    def test_manager_topology_writes_pending(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        _populate_state()

        before = int(time.time())
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body
        assert body == {"status": "ok"}

        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        assert target.exists()
        payload = json.loads(target.read_text("utf-8"))
        assert payload["schema_version"] == 1
        assert payload["topology"] == "manager"
        assert payload["created_at_unix"] >= before
        # FR-PEND1a: file must be a regular json object with the
        # expected schema fields.
        assert payload["admin_user"]["username"] == "alice"
        # auto_enroll_local_worker is False on manager topology.
        assert payload["auto_enroll_local_worker"] is False
        assert payload["worker_ui_password_hash"] is None

    def test_manager_worker_topology_marks_auto_enroll(
        self, handler, tmp_path,
    ):
        write_topology_atomic(tmp_path, "manager_worker")
        _populate_state()
        wizard_state.set_worker_password_hash("a" * 64, "b" * 32)

        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("200"), body

        target = tmp_path / pending_handler.PENDING_SETUP_FILENAME
        payload = json.loads(target.read_text("utf-8"))
        assert payload["topology"] == "manager_worker"
        assert payload["auto_enroll_local_worker"] is True
        assert payload["worker_ui_password_hash"] == "a" * 64
        assert payload["worker_ui_password_salt"] == "b" * 32


class TestErrorPaths:

    def test_missing_topology_returns_400(self, handler):
        # No topology.json on disk yet.
        _populate_state()
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "topology" in body["error"].lower()

    def test_missing_admin_state_returns_400(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        # Don't populate the admin tuple.
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert body["error"] == "wizard_state_incomplete"

    def test_worker_only_topology_rejected(self, handler, tmp_path):
        write_topology_atomic(tmp_path, "worker_only")
        env = auth_env(b"")
        status, _, body = call_handler(handler, env)
        assert status.startswith("400"), body
        assert "topology" in body["error"].lower() or "applicable" in body["error"].lower()
