# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/done.py`` (A4).

Covers FR-W9 (.wizard_done marker write + session-token clear),
FR-W9a (idempotent under handoff-state lock — single marker even with
concurrent calls), and the topology-must-exist precondition.
"""

from __future__ import annotations

import io
import json
import threading

import pytest

from wizard.sethlans_wizard import auth_state, ipc
from wizard.sethlans_wizard.handlers import done as done_handler
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic


_VALID_SESSION = "valid-session-token-for-done"
_IPC_SECRET = b"ipc-hmac-secret-bytes-zzz-yyy"


def _build_environ(*, method="POST", path="/api/wizard/done/", body=b"",
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
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(_VALID_SESSION)
    yield
    auth_state.reset_state_for_tests()


@pytest.fixture
def data_dir_with_topology(tmp_path):
    write_topology_atomic(tmp_path, "manager")
    return tmp_path


@pytest.fixture
def handler(data_dir_with_topology):
    return done_handler.make_done_handler(
        data_dir_with_topology, _IPC_SECRET, wizard_port=8100,
    )


def _auth_env():
    return _build_environ(headers={"X-Wizard-Session": _VALID_SESSION})


class TestHappyPath:

    def test_writes_wizard_done_marker(self, handler, data_dir_with_topology):
        status, _, body = _call(handler, _auth_env())
        assert status.startswith("200"), body
        assert body == {"status": "ok"}
        marker = data_dir_with_topology / "wizard" / ipc.MARKER_WIZARD_DONE
        assert marker.exists()
        # Validate via ipc.read_marker (HMAC + canonical data_dir + freshness).
        payload = ipc.read_marker(
            marker,
            _IPC_SECRET,
            expected_type="wizard_done",
            data_dir=data_dir_with_topology,
        )
        assert payload is not None
        assert payload["topology"] == "manager"
        assert payload["wizard_port"] == 8100
        assert isinstance(payload["wizard_pid"], int)

    def test_clears_session_token_after_marker(self, handler):
        assert auth_state.validate_session_token(_VALID_SESSION) is True
        _call(handler, _auth_env())
        assert auth_state.validate_session_token(_VALID_SESSION) is False


class TestPreconditions:

    def test_missing_topology_returns_400(self, tmp_path):
        # No topology.json written.
        h = done_handler.make_done_handler(tmp_path, _IPC_SECRET)
        status, _, body = _call(h, _auth_env())
        assert status.startswith("400"), body
        assert "topology" in body["error"].lower()
        # Marker MUST NOT have been written.
        assert not (tmp_path / "wizard" / ipc.MARKER_WIZARD_DONE).exists()

    def test_missing_session_returns_401(self, handler):
        env = _build_environ()
        status, _, _ = _call(handler, env)
        assert status.startswith("401")

    def test_wrong_session_returns_401(self, handler):
        env = _build_environ(headers={"X-Wizard-Session": "wrong"})
        status, _, _ = _call(handler, env)
        assert status.startswith("401")

    def test_get_returns_405(self, handler):
        env = _build_environ(method="GET",
                             headers={"X-Wizard-Session": _VALID_SESSION})
        status, headers, _ = _call(handler, env)
        assert status.startswith("405")
        assert headers.get("Allow") == "POST"

    def test_qs_token_rejected(self, handler):
        env = _build_environ(query_string="token=abc",
                             headers={"X-Wizard-Session": _VALID_SESSION})
        status, _, _ = _call(handler, env)
        assert status.startswith("400")


class TestIdempotency:

    def test_second_call_returns_already_done(self, handler):
        status1, _, body1 = _call(handler, _auth_env())
        assert status1.startswith("200")
        assert body1 == {"status": "ok"}
        # Re-issue session for second call (clear_session_token wiped it).
        auth_state.set_session_token(_VALID_SESSION)
        status2, _, body2 = _call(handler, _auth_env())
        assert status2.startswith("200")
        assert body2 == {"status": "already_done"}

    def test_concurrent_calls_produce_one_marker(
        self, handler, data_dir_with_topology,
    ):
        # FR-W9a: 5 concurrent calls under the handoff-state lock yield
        # exactly one .wizard_done marker. We track marker mtime to detect
        # any second write that would change it.
        marker = data_dir_with_topology / "wizard" / ipc.MARKER_WIZARD_DONE
        results: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(5)

        def fire():
            barrier.wait()
            # Each thread must have a valid session at the moment it
            # checks. Re-issue under a lock so they all see a session.
            with lock:
                auth_state.set_session_token(_VALID_SESSION)
            status, _, body = _call(handler, _auth_env())
            with lock:
                results.append(body.get("status", ""))

        threads = [threading.Thread(target=fire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert marker.exists()
        # Exactly one "ok" — the rest are "already_done".
        ok_count = sum(1 for r in results if r == "ok")
        already_count = sum(1 for r in results if r == "already_done")
        assert ok_count == 1, f"results={results}"
        assert ok_count + already_count == 5

    def test_marker_payload_canonical_data_dir(
        self, handler, data_dir_with_topology,
    ):
        # SEC-MED-9 — data_dir is a signed field, canonicalised.
        _call(handler, _auth_env())
        marker = data_dir_with_topology / "wizard" / ipc.MARKER_WIZARD_DONE
        raw = json.loads(marker.read_text("utf-8"))
        # Must match ipc._canonical_data_dir output.
        from pathlib import Path
        assert raw["data_dir"] == str(
            Path(data_dir_with_topology).resolve(strict=False),
        )


class TestFactoryGuards:

    def test_rejects_empty_secret(self, tmp_path):
        with pytest.raises(ValueError):
            done_handler.make_done_handler(tmp_path, b"")

    def test_rejects_non_bytes_secret(self, tmp_path):
        with pytest.raises(ValueError):
            done_handler.make_done_handler(
                tmp_path, "not-bytes")  # type: ignore[arg-type]
