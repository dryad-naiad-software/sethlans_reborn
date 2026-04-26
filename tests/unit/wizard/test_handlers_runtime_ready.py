# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/runtime_ready.py``.

Spec 1 / A4. Covers FR-W14 (booting/ready/failed status envelope, 1s
TTL probe cache, ack flag on first ready) + FR-W14a (hardcoded URL per
topology, query-string ?url= rejection / SEC-MED-12) + the
CONC-v23-MED-1 lock-ordering invariant (8-thread stress).
"""

from __future__ import annotations

import io
import json
import threading
import time

import pytest

from wizard.sethlans_wizard import auth_state, ipc
from wizard.sethlans_wizard.handlers import runtime_ready
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic


_VALID_SESSION = "valid-session-token-for-rr"
_IPC_SECRET = b"ipc-hmac-secret-bytes-rr-xxx"


def _build_environ(*, method="GET", path="/api/wizard/runtime-ready/",
                   remote_addr="127.0.0.1", query_string="", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
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
    runtime_ready.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()
    runtime_ready.reset_state_for_tests()


def _auth_env(**kw):
    headers = kw.pop("headers", {}) or {}
    headers["X-Wizard-Session"] = _VALID_SESSION
    return _build_environ(headers=headers, **kw)


def _ok_payload():
    return {"boot_id": "x", "worker_id": "y", "version": "1"}


def _make_handler(data_dir, probe_fn):
    return runtime_ready.make_runtime_ready_handler(
        data_dir, _IPC_SECRET, probe_runtime_health=probe_fn,
    )


class TestBooting:

    def test_no_topology_returns_booting(self, tmp_path):
        probe_calls: list[str] = []
        h = _make_handler(tmp_path, lambda u: (probe_calls.append(u), None)[1])
        status, _, body = _call(h, _auth_env())
        assert status.startswith("200")
        assert body == {"status": "booting", "url": None}
        assert probe_calls == []

    def test_probe_returns_none_yields_booting(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = _make_handler(tmp_path, lambda u: None)
        status, _, body = _call(h, _auth_env())
        assert status.startswith("200")
        assert body == {"status": "booting", "url": None}


class TestReady:

    def test_probe_ok_yields_ready(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = _make_handler(tmp_path, lambda u: _ok_payload())
        status, _, body = _call(h, _auth_env())
        assert status.startswith("200")
        assert body["status"] == "ready"
        assert body["url"] == "https://localhost:8080/api/health/"

    def test_first_ready_sets_browser_ack_flag(self, tmp_path):
        write_topology_atomic(tmp_path, "manager")
        h = _make_handler(tmp_path, lambda u: _ok_payload())
        assert auth_state.was_browser_redirect_acknowledged() is False
        _call(h, _auth_env())
        assert auth_state.was_browser_redirect_acknowledged() is True

    @pytest.mark.parametrize("topo,expected_url", [
        ("manager", "https://localhost:8080/api/health/"),
        ("manager_worker", "https://localhost:8080/api/health/"),
        ("worker_only", "https://localhost:8081/api/health/"),
    ])
    def test_url_per_topology(self, tmp_path, topo, expected_url):
        runtime_ready.reset_state_for_tests()
        write_topology_atomic(tmp_path, topo)
        h = _make_handler(tmp_path, lambda u: _ok_payload())
        _, _, body = _call(h, _auth_env())
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
            _IPC_SECRET,
            payload={"reason": "exited 1"},
        )
        (wizard_dir / ".launcher_log_path").write_text(
            "/var/log/sethlans/launcher.log",
        )
        probe_calls: list[str] = []
        h = _make_handler(
            tmp_path, lambda u: (probe_calls.append(u), None)[1],
        )
        status, _, body = _call(h, _auth_env())
        assert status.startswith("200")
        assert body["status"] == "failed"
        assert body["url"] is None
        assert body["log_path"] == "/var/log/sethlans/launcher.log"
        assert probe_calls == []  # short-circuit


class TestProbeCache:

    def test_concurrent_polls_within_ttl_share_one_probe(self, tmp_path):
        # CONC-v22-MED-2: 5 concurrent polls within the 1s TTL window
        # MUST result in exactly 1 outbound probe.
        write_topology_atomic(tmp_path, "manager")
        call_count = {"n": 0}
        call_lock = threading.Lock()

        def fake_probe(url):
            with call_lock:
                call_count["n"] += 1
            time.sleep(0.05)  # widen overlap window
            return _ok_payload()

        h = _make_handler(tmp_path, fake_probe)
        results: list[dict] = []
        rlock = threading.Lock()
        barrier = threading.Barrier(5)

        def fire():
            barrier.wait()
            _, _, body = _call(h, _auth_env())
            with rlock:
                results.append(body)

        ts = [threading.Thread(target=fire) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=5)

        assert call_count["n"] == 1, call_count
        assert all(r["status"] == "ready" for r in results)

    def test_cache_uses_monotonic_time(self, tmp_path, monkeypatch):
        # CONC-v23-LOW-1: cache TTL must use time.monotonic.
        write_topology_atomic(tmp_path, "manager")
        clock = {"t": 1000.0}
        monkeypatch.setattr(
            runtime_ready.time, "monotonic", lambda: clock["t"],
        )
        call_count = {"n": 0}

        def fake_probe(url):
            call_count["n"] += 1
            return _ok_payload()

        h = _make_handler(tmp_path, fake_probe)
        _call(h, _auth_env())  # probes
        assert call_count["n"] == 1
        clock["t"] = 1000.5  # within TTL
        _call(h, _auth_env())  # cached
        assert call_count["n"] == 1
        clock["t"] = 1002.0  # past TTL
        _call(h, _auth_env())  # re-probes
        assert call_count["n"] == 2


class TestLockOrdering:

    def test_ab_ba_stress_does_not_deadlock(self, tmp_path):
        # AC-W12 — 8-thread stress over the canonical lock order.
        # Verify no thread blocked past the 10s budget.
        write_topology_atomic(tmp_path, "manager")

        def fake_probe(url):
            time.sleep(0.02)
            return _ok_payload()

        h = _make_handler(tmp_path, fake_probe)

        def fire_runtime():
            for _ in range(20):
                _call(h, _auth_env())

        def fire_handoff():
            for _ in range(50):
                auth_state.was_browser_redirect_acknowledged()
                auth_state.is_done_sent()

        threads = [threading.Thread(target=fire_runtime) for _ in range(4)]
        threads += [threading.Thread(target=fire_handoff) for _ in range(4)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        elapsed = time.monotonic() - start
        assert elapsed < 10, f"deadlock suspected; elapsed={elapsed:.2f}s"
        for t in threads:
            assert not t.is_alive(), "thread still alive past 10s budget"


class TestAuthGate:

    def test_missing_session_returns_401(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, _IPC_SECRET)
        status, _, _ = _call(h, _build_environ())
        assert status.startswith("401")

    def test_wrong_session_returns_401(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, _IPC_SECRET)
        status, _, _ = _call(
            h, _build_environ(headers={"X-Wizard-Session": "wrong"}),
        )
        assert status.startswith("401")

    def test_post_returns_405(self, tmp_path):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, _IPC_SECRET)
        status, headers, _ = _call(h, _auth_env(method="POST"))
        assert status.startswith("405")
        assert headers.get("Allow") == "GET"


class TestNoUrlInQueryString:
    # SEC-MED-12 — any token/url-shaped query key MUST 400 before probe.
    @pytest.mark.parametrize(
        "qs_key", ["url", "session_token", "session", "token"],
    )
    def test_qs_url_rejected(self, tmp_path, qs_key):
        h = runtime_ready.make_runtime_ready_handler(tmp_path, _IPC_SECRET)
        env = _auth_env(query_string=f"{qs_key}=https://attacker/")
        status, _, body = _call(h, env)
        assert status.startswith("400"), body
        assert "url" in body["error"].lower()


class TestFactoryGuards:

    def test_rejects_empty_secret(self, tmp_path):
        with pytest.raises(ValueError):
            runtime_ready.make_runtime_ready_handler(tmp_path, b"")

    def test_rejects_non_bytes_secret(self, tmp_path):
        with pytest.raises(ValueError):
            runtime_ready.make_runtime_ready_handler(
                tmp_path, "no")  # type: ignore[arg-type]
