# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/topology.py`` (A4).

Covers FR-W8 (atomic topology write, X-Wizard-Session header gate,
stale topology.json cleanup) and FR-CFG1 (schema_version + chosen_at).
"""

from __future__ import annotations

import io
import json
import os
import platform

import pytest

from wizard.sethlans_wizard import auth_state
from wizard.sethlans_wizard.handlers import topology as topology_handler


_VALID_SESSION = "valid-session-token"


def _build_environ(*, method="POST", path="/api/wizard/topology/", body=b"",
                   remote_addr="127.0.0.1", query_string="", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    for hk, hv in (headers or {}).items():
        env["HTTP_" + hk.upper().replace("-", "_")] = hv
    return env


def _call(handler, environ):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(handler(environ, start_response))
    parsed = json.loads(body.decode("utf-8")) if body else None
    return captured["status"], dict(captured["headers"]), parsed


@pytest.fixture(autouse=True)
def _reset_auth_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(_VALID_SESSION)
    yield
    auth_state.reset_state_for_tests()


@pytest.fixture
def handler(tmp_path):
    return topology_handler.make_topology_handler(tmp_path)


def _auth_env(body):
    return _build_environ(
        body=body,
        headers={"X-Wizard-Session": _VALID_SESSION},
    )


class TestHappyPaths:

    @pytest.mark.parametrize(
        "topo", ["manager", "manager_worker", "worker_only"],
    )
    def test_each_topology_writes_topology_json(self, handler, tmp_path, topo):
        env = _auth_env(json.dumps({"topology": topo}).encode("utf-8"))
        status, _, body = _call(handler, env)
        assert status.startswith("200"), body
        assert body == {"status": "ok"}
        path = tmp_path / "topology.json"
        assert path.exists()
        data = json.loads(path.read_text("utf-8"))
        assert data["topology"] == topo
        assert data["schema_version"] == 1
        # FR-CFG1 — ISO-8601 UTC with trailing Z.
        assert data["chosen_at"].endswith("Z")
        assert "T" in data["chosen_at"]

    def test_overwrites_existing_topology(self, handler, tmp_path):
        path = tmp_path / "topology.json"
        path.write_text(json.dumps({"topology": "manager"}))
        env = _auth_env(json.dumps(
            {"topology": "manager_worker"}).encode("utf-8"))
        _call(handler, env)
        data = json.loads(path.read_text("utf-8"))
        assert data["topology"] == "manager_worker"


class TestAuthGate:

    def test_missing_header_returns_401(self, handler):
        env = _build_environ(body=json.dumps(
            {"topology": "manager"}).encode("utf-8"))
        status, _, body = _call(handler, env)
        assert status.startswith("401"), body
        assert "session" in body["error"].lower()

    def test_wrong_header_returns_401(self, handler):
        env = _build_environ(
            body=json.dumps({"topology": "manager"}).encode("utf-8"),
            headers={"X-Wizard-Session": "wrong"},
        )
        status, _, _ = _call(handler, env)
        assert status.startswith("401")

    def test_no_session_issued_yet_returns_401(self, handler):
        auth_state.reset_state_for_tests()
        env = _build_environ(
            body=json.dumps({"topology": "manager"}).encode("utf-8"),
            headers={"X-Wizard-Session": _VALID_SESSION},
        )
        status, _, _ = _call(handler, env)
        assert status.startswith("401")


class TestValidation:

    def test_invalid_topology_returns_400(self, handler):
        env = _auth_env(json.dumps({"topology": "garbage"}).encode("utf-8"))
        status, _, body = _call(handler, env)
        assert status.startswith("400"), body
        assert "topology" in body["error"].lower()

    def test_missing_topology_field_returns_400(self, handler):
        env = _auth_env(json.dumps({"other": "x"}).encode("utf-8"))
        status, _, _ = _call(handler, env)
        assert status.startswith("400")

    def test_non_string_topology_returns_400(self, handler):
        env = _auth_env(json.dumps({"topology": 42}).encode("utf-8"))
        status, _, _ = _call(handler, env)
        assert status.startswith("400")

    def test_malformed_json_returns_400(self, handler):
        env = _auth_env(b"not-json{{")
        status, _, body = _call(handler, env)
        assert status.startswith("400")
        assert "json" in body["error"].lower()

    def test_get_returns_405(self, handler):
        env = _build_environ(method="GET",
                             headers={"X-Wizard-Session": _VALID_SESSION})
        status, headers, _ = _call(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_qs_token_rejected(self, handler):
        env = _build_environ(
            body=json.dumps({"topology": "manager"}).encode("utf-8"),
            query_string="session_token=abc",
            headers={"X-Wizard-Session": _VALID_SESSION},
        )
        status, _, _ = _call(handler, env)
        assert status.startswith("400")

    def test_oversized_body_returns_400(self, handler):
        env = _auth_env(b"\x00" * 100)
        env["CONTENT_LENGTH"] = "999999"
        status, _, body = _call(handler, env)
        assert status.startswith("400")
        assert "large" in body["error"].lower()


class TestStaleCleanup:

    def test_removes_topology_when_no_sentinel(self, tmp_path):
        topology_file = tmp_path / "topology.json"
        topology_file.write_text(json.dumps({"topology": "manager"}))
        sentinel = tmp_path / ".setup_complete"
        # Sentinel intentionally absent.
        removed = topology_handler.cleanup_stale_topology(tmp_path, sentinel)
        assert removed is True
        assert not topology_file.exists()

    def test_keeps_topology_when_sentinel_present(self, tmp_path):
        topology_file = tmp_path / "topology.json"
        topology_file.write_text(json.dumps({"topology": "manager"}))
        sentinel = tmp_path / ".setup_complete"
        sentinel.write_text("ok")
        removed = topology_handler.cleanup_stale_topology(tmp_path, sentinel)
        assert removed is False
        assert topology_file.exists()

    def test_noop_when_no_topology_file(self, tmp_path):
        sentinel = tmp_path / ".setup_complete"
        removed = topology_handler.cleanup_stale_topology(tmp_path, sentinel)
        assert removed is False


class TestAtomicWrite:

    def test_no_tmp_left_on_disk(self, handler, tmp_path):
        env = _auth_env(json.dumps({"topology": "manager"}).encode("utf-8"))
        _call(handler, env)
        # write_topology_atomic uses topology.json.tmp during the write.
        assert not (tmp_path / "topology.json.tmp").exists()
        assert (tmp_path / "topology.json").exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX chmod semantics only",
    )
    def test_chmod_600_on_posix(self, handler, tmp_path):
        env = _auth_env(json.dumps({"topology": "manager"}).encode("utf-8"))
        _call(handler, env)
        mode = os.stat(tmp_path / "topology.json").st_mode & 0o777
        assert mode == 0o600

    def test_write_topology_atomic_rejects_invalid(self, tmp_path):
        with pytest.raises(ValueError):
            topology_handler.write_topology_atomic(tmp_path, "invalid")

    def test_write_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nest"
        topology_handler.write_topology_atomic(nested, "manager")
        assert (nested / "topology.json").exists()


class TestExports:

    def test_valid_topologies_set(self):
        assert topology_handler.VALID_TOPOLOGIES == frozenset(
            {"manager", "manager_worker", "worker_only"},
        )

    def test_filename_constant(self):
        assert topology_handler.TOPOLOGY_FILENAME == "topology.json"


class TestChk4Checkpoint:
    """FR-CHK4: topology handler must also append topology_chosen to
    .setup_progress.json so resume logic sees it as a completed step."""

    def test_handler_appends_topology_chosen(self, handler, tmp_path):
        from wizard.sethlans_wizard import progress
        from wizard.sethlans_wizard.checkpoints import TOPOLOGY_CHOSEN
        env = _auth_env(json.dumps({"topology": "manager"}).encode("utf-8"))
        status, _, _ = _call(handler, env)
        assert status.startswith("200")
        payload = progress.read_checkpoints(tmp_path)
        assert TOPOLOGY_CHOSEN in (payload.get("checkpoints") or [])
        # And topology field is recorded.
        assert payload.get("topology") == "manager"

    def test_re_post_is_idempotent(self, handler, tmp_path):
        from wizard.sethlans_wizard import progress
        from wizard.sethlans_wizard.checkpoints import TOPOLOGY_CHOSEN
        for topo in ("manager", "manager_worker"):
            env = _auth_env(json.dumps({"topology": topo}).encode("utf-8"))
            _call(handler, env)
        payload = progress.read_checkpoints(tmp_path)
        # checkpoint appended only once even after multiple submits.
        assert (payload.get("checkpoints") or []).count(TOPOLOGY_CHOSEN) == 1
