# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``runtime_ready`` handler — response-shape facets.

Spec 1 / A4. Covers FR-W14 (booting/ready/failed status envelopes,
ack-on-first-ready, hardcoded URL per topology) plus the auth and
query-string guards (FR-W14a / SEC-MED-12).

Cache-coordination, lock-ordering stress, and factory guards live in
``test_handlers_runtime_ready_concurrency.py`` so each file stays
under the 300-line limit.
"""

from __future__ import annotations

import pytest

from wizard.sethlans_wizard import auth_state, ipc, shutdown
from wizard.sethlans_wizard.handlers import runtime_ready
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

from ._runtime_ready_helpers import (
    IPC_SECRET, VALID_SESSION, auth_env, build_environ, call,
    make_handler, ok_payload,
)


@pytest.fixture(autouse=True)
def _reset_state():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)
    runtime_ready.reset_state_for_tests()
    shutdown.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()
    runtime_ready.reset_state_for_tests()
    shutdown.reset_state_for_tests()


class TestBooting:

    def test_no_topology_returns_booting(self, tmp_path):
        probe_calls: list[str] = []
        h = make_handler(
            tmp_path, lambda u: (probe_calls.append(u), None)[1],
        )
        status, _, body = call(h, auth_env())
        assert status.startswith("200")
        assert body == {"status": "booting", "url": None}
        assert probe_calls == []

    def test_probe_returns_none_yields_booting(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = make_handler(tmp_path, lambda u: None)
        status, _, body = call(h, auth_env())
        assert status.startswith("200")
        assert body == {"status": "booting", "url": None}


class TestReady:

    def test_probe_ok_yields_ready(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = make_handler(tmp_path, lambda u: ok_payload())
        status, _, body = call(h, auth_env())
        assert status.startswith("200")
        assert body["status"] == "ready"
        assert body["url"] == "https://localhost:8080/api/health/"

    def test_first_ready_sets_browser_ack_flag(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = make_handler(tmp_path, lambda u: ok_payload())
        assert auth_state.was_browser_redirect_acknowledged() is False
        call(h, auth_env())
        assert auth_state.was_browser_redirect_acknowledged() is True

    @pytest.mark.parametrize("topo,expected_url", [
        ("manager", "https://localhost:8080/api/health/"),
        ("manager_worker", "https://localhost:8080/api/health/"),
        ("worker_only", "https://localhost:8081/api/health/"),
    ])
    def test_url_per_topology(self, tmp_path, topo, expected_url):
        runtime_ready.reset_state_for_tests()
        write_topology_atomic(tmp_path, topo)
        h = make_handler(tmp_path, lambda u: ok_payload())
        _, _, body = call(h, auth_env())
        assert body["url"] == expected_url


class TestFailed:

    def test_runtime_failed_marker_short_circuits_probe(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir(exist_ok=True)
        ipc.write_marker(
            wizard_dir / ipc.MARKER_RUNTIME_FAILED,
            "runtime_failed",
            tmp_path,
            IPC_SECRET,
            payload={"reason": "exited 1"},
        )
        (wizard_dir / ".launcher_log_path").write_text(
            "/var/log/sethlans/launcher.log",
        )
        probe_calls: list[str] = []
        h = make_handler(
            tmp_path, lambda u: (probe_calls.append(u), None)[1],
        )
        status, _, body = call(h, auth_env())
        assert status.startswith("200")
        assert body["status"] == "failed"
        assert body["url"] is None
        assert body["log_path"] == "/var/log/sethlans/launcher.log"
        assert probe_calls == []  # short-circuit


class TestAuthGate:

    def test_missing_session_returns_401(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, IPC_SECRET)
        status, _, _ = call(h, build_environ())
        assert status.startswith("401")

    def test_wrong_session_returns_401(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, IPC_SECRET)
        status, _, _ = call(
            h, build_environ(headers={"X-Wizard-Session": "wrong"}),
        )
        assert status.startswith("401")

    def test_post_returns_405(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, IPC_SECRET)
        status, headers, _ = call(h, auth_env(method="POST"))
        assert status.startswith("405")
        assert headers.get("Allow") == "GET"


class TestNoUrlInQueryString:
    # SEC-MED-12 — any token/url-shaped query key MUST 400 before probe.
    @pytest.mark.parametrize(
        "qs_key", ["url", "session_token", "session", "token"],
    )
    def test_qs_url_rejected(self, tmp_path, qs_key):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, IPC_SECRET)
        env = auth_env(query_string=f"{qs_key}=https://attacker/")
        status, _, body = call(h, env)
        assert status.startswith("400"), body
        assert "url" in body["error"].lower()
