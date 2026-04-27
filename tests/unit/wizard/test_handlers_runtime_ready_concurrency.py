# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``runtime_ready`` handler — cache + concurrency facets.

Spec 1 / A4. Covers the 1s TTL probe cache (CONC-v22-MED-2 single-flight,
CONC-v23-LOW-1 monotonic clock), the 8-thread lock-ordering stress
(CONC-v23-MED-1 / AC-W12), and the factory guards.

Response-shape and auth-gate tests live in
``test_handlers_runtime_ready_response.py``.
"""

from __future__ import annotations

import threading
import time

import pytest

from wizard.sethlans_wizard import auth_state, shutdown
from wizard.sethlans_wizard.handlers import _runtime_probe, runtime_ready
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

from ._runtime_ready_helpers import (
    VALID_SESSION, auth_env, call, make_handler, ok_payload,
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
            return ok_payload()

        h = make_handler(tmp_path, fake_probe)
        results: list[dict] = []
        rlock = threading.Lock()
        barrier = threading.Barrier(5)

        def fire():
            barrier.wait()
            _, _, body = call(h, auth_env())
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
            _runtime_probe.time, "monotonic", lambda: clock["t"],
        )
        call_count = {"n": 0}

        def fake_probe(url):
            call_count["n"] += 1
            return ok_payload()

        h = make_handler(tmp_path, fake_probe)
        call(h, auth_env())  # probes
        assert call_count["n"] == 1
        clock["t"] = 1000.5  # within TTL
        call(h, auth_env())  # cached
        assert call_count["n"] == 1
        clock["t"] = 1002.0  # past TTL
        call(h, auth_env())  # re-probes
        assert call_count["n"] == 2


class TestLockOrdering:

    def test_ab_ba_stress_does_not_deadlock(self, tmp_path):
        # AC-W12 — 8-thread stress over the canonical lock order.
        # Verify no thread blocked past the 10s budget.
        write_topology_atomic(tmp_path, "manager")

        def fake_probe(url):
            time.sleep(0.02)
            return ok_payload()

        h = make_handler(tmp_path, fake_probe)

        def fire_runtime():
            for _ in range(20):
                call(h, auth_env())

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


class TestFactoryGuards:

    def test_rejects_empty_secret(self, tmp_path):
        with pytest.raises(ValueError):
            runtime_ready.make_runtime_ready_handler(tmp_path, b"")

    def test_rejects_non_bytes_secret(self, tmp_path):
        with pytest.raises(ValueError):
            runtime_ready.make_runtime_ready_handler(
                tmp_path, "no")  # type: ignore[arg-type]
