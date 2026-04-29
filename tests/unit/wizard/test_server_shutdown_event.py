# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the issue #176 shutdown-event mechanics in
:mod:`wizard.sethlans_wizard.server`.

The bug: a SIGINT handler that called ``server.close()`` directly did
not reliably break waitress's ``server.run()`` loop — observed
30-minute hang in the wild. The fix moves the run loop onto a daemon
thread and parks the main thread on a :class:`threading.Event` that
the signal handler / FR-W17 paths set.

These tests cover the public surface (``request_shutdown``,
``shutdown_server``, ``get_shutdown_event``) WITHOUT actually binding
waitress — that's exercised in the integration suite.
"""

from __future__ import annotations

import threading

import pytest

from wizard.sethlans_wizard import server


@pytest.fixture(autouse=True)
def _reset_event_between_tests():
    server.reset_shutdown_event_for_tests()
    server._SERVER_REF.clear()
    yield
    server.reset_shutdown_event_for_tests()
    server._SERVER_REF.clear()


class TestShutdownEvent:

    def test_event_is_a_threading_event(self):
        ev = server.get_shutdown_event()
        # It must support .set / .clear / .wait / .is_set.
        assert isinstance(ev, threading.Event)

    def test_request_shutdown_sets_event(self):
        ev = server.get_shutdown_event()
        assert not ev.is_set()
        server.request_shutdown()
        assert ev.is_set()

    def test_request_shutdown_idempotent(self):
        ev = server.get_shutdown_event()
        server.request_shutdown()
        server.request_shutdown()
        server.request_shutdown()
        # Still set, no crash. Also: clear works post-multiple-set.
        assert ev.is_set()
        server.reset_shutdown_event_for_tests()
        assert not ev.is_set()

    def test_shutdown_server_sets_event_even_with_no_server(self):
        """The bare ``server.close()`` from the old handler did NOT set
        the event. Issue #176 demands that ``shutdown_server`` set the
        event regardless so the main-thread wait unblocks."""
        ev = server.get_shutdown_event()
        assert not ev.is_set()
        # No server registered — must still set event (no AttributeError,
        # no crash, just sets and returns).
        server.shutdown_server()
        assert ev.is_set()

    def test_shutdown_server_calls_close_when_registered(self):
        ev = server.get_shutdown_event()
        closed = {"count": 0}

        class FakeServer:
            def close(self):
                closed["count"] += 1

        srv = FakeServer()
        server._SERVER_REF.set(srv)
        try:
            server.shutdown_server()
            assert closed["count"] == 1
            assert ev.is_set()
            # Idempotent — second call is fine; still sets event,
            # still calls close (the slot wasn't cleared).
            server.shutdown_server()
            assert closed["count"] == 2
        finally:
            server._SERVER_REF.clear()

    def test_shutdown_server_swallows_close_errors(self):
        """A failing close() must not break the shutdown path."""
        ev = server.get_shutdown_event()

        class BoomServer:
            def close(self):
                raise RuntimeError("close races")

        server._SERVER_REF.set(BoomServer())
        try:
            # Must not raise.
            server.shutdown_server()
            assert ev.is_set()
        finally:
            server._SERVER_REF.clear()

    def test_run_join_timeout_constant_is_under_six_seconds(self):
        """Issue #176 acceptance: process must exit within ~5s of
        receiving SIGINT. The join timeout caps the worst case."""
        assert server._RUN_JOIN_TIMEOUT_SECONDS <= 6.0
        assert server._RUN_JOIN_TIMEOUT_SECONDS >= 1.0


class TestRunWaitsOnEvent:
    """Smoke-test the ``run()`` thread + event mechanics with a fake
    waitress server, without binding a real socket."""

    def test_run_returns_when_event_set(self, monkeypatch):
        run_called = threading.Event()
        close_called = threading.Event()

        class FakeServer:
            def run(self):
                # Block until close() is called — mimics waitress's
                # accept-loop behaviour.
                run_called.set()
                close_called.wait(timeout=10.0)

            def close(self):
                close_called.set()

        def fake_create_server(*args, **kwargs):
            return FakeServer()

        monkeypatch.setattr(
            server.waitress, "create_server", fake_create_server,
        )

        # Trigger the shutdown event from another thread shortly after
        # run() is called.
        def _trigger():
            run_called.wait(timeout=2.0)
            server.request_shutdown()

        trigger = threading.Thread(target=_trigger, daemon=True)
        trigger.start()

        # Should return cleanly within a couple of seconds.
        server.run(lambda env, sr: [b""], "127.0.0.1", 9)
        trigger.join(timeout=2.0)

        assert run_called.is_set()
        assert close_called.is_set()
        assert server._SERVER_REF.get() is None

    def test_run_returns_when_waitress_run_exits_unexpectedly(
        self, monkeypatch,
    ):
        """If waitress's run() raises, we must signal shutdown so the
        main thread doesn't park forever."""

        class CrashingServer:
            def run(self):
                raise RuntimeError("waitress crashed")

            def close(self):
                pass

        def fake_create_server(*args, **kwargs):
            return CrashingServer()

        monkeypatch.setattr(
            server.waitress, "create_server", fake_create_server,
        )

        # Should not hang — the daemon thread sets the event on
        # exception, run() unblocks.
        server.run(lambda env, sr: [b""], "127.0.0.1", 9)
        # Slot cleared on exit.
        assert server._SERVER_REF.get() is None
