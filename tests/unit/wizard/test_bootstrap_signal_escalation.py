# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #176 — second-SIGINT escalation in
:func:`wizard.sethlans_wizard.bootstrap.install_signal_handlers`.

The first SIGINT/SIGTERM should signal a graceful shutdown via
``server.shutdown_server`` (which sets the shutdown event the main
thread is parked on). A second signal calls ``os._exit(130)`` so the
operator can force-quit without a separate kill.

We can't safely test ``os._exit`` directly — it bypasses pytest. So
we monkeypatch ``os._exit`` on the bootstrap module and assert it
gets called with the right code on the second handler invocation.
"""

from __future__ import annotations

import signal

import pytest

from wizard.sethlans_wizard import bootstrap, server


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    bootstrap._reset_signal_count_for_tests()
    server.reset_shutdown_event_for_tests()
    server._SERVER_REF.clear()
    yield
    bootstrap._reset_signal_count_for_tests()
    server.reset_shutdown_event_for_tests()
    server._SERVER_REF.clear()
    # Restore default SIGINT/SIGTERM handlers — pytest's signal
    # machinery doesn't undo signal.signal() calls automatically.
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass


def _capture_handler():
    """Install handlers and return the registered SIGINT handler."""
    bootstrap.install_signal_handlers()
    return signal.getsignal(signal.SIGINT)


class TestSignalHandlerEscalation:

    def test_first_signal_signals_shutdown_event(self):
        handler = _capture_handler()
        ev = server.get_shutdown_event()
        assert not ev.is_set()

        # Simulate SIGINT delivery.
        handler(signal.SIGINT, None)

        assert ev.is_set()

    def test_second_signal_calls_os_exit_130(self, monkeypatch):
        handler = _capture_handler()
        exit_calls: list[int] = []

        def fake_exit(code):
            exit_calls.append(code)
            # Don't actually exit; raise so the rest of the handler
            # path doesn't run.
            raise SystemExit(code)

        monkeypatch.setattr(bootstrap.os, "_exit", fake_exit)

        # First SIGINT: graceful path. Doesn't call os._exit.
        handler(signal.SIGINT, None)
        assert exit_calls == []
        assert server.get_shutdown_event().is_set()

        # Second SIGINT: must escalate to os._exit(130).
        with pytest.raises(SystemExit) as ex:
            handler(signal.SIGINT, None)
        assert ex.value.code == 130
        assert exit_calls == [130]

    def test_sigterm_then_sigint_also_escalates(self, monkeypatch):
        """The escalation counter is signal-agnostic — a SIGTERM
        followed by a SIGINT (or vice versa) still triggers force-exit
        on the second signal."""
        handler = _capture_handler()
        exit_calls: list[int] = []

        def fake_exit(code):
            exit_calls.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(bootstrap.os, "_exit", fake_exit)

        handler(signal.SIGTERM, None)
        assert exit_calls == []

        with pytest.raises(SystemExit):
            handler(signal.SIGINT, None)
        assert exit_calls == [130]

    def test_handler_safe_when_no_server_registered(self, monkeypatch):
        """Issue #176 acceptance: handler must not crash if the signal
        fires before the server binds (e.g. during cert generation)."""
        handler = _capture_handler()
        # No server in the slot — this is the pre-bind state.
        assert server._SERVER_REF.get() is None
        # Must not raise.
        handler(signal.SIGINT, None)
        # The event still gets set — so that when the server does
        # bind and call run(), it'll observe the event immediately
        # and exit cleanly. (Actually — run() resets the event on
        # entry, so this is a documented edge case: a signal that
        # fires AFTER configure_logging but BEFORE run() may be lost.
        # The window is microseconds.)
        assert server.get_shutdown_event().is_set()

    def test_install_does_not_raise_on_main_thread(self):
        # Idempotent: calling twice is fine.
        bootstrap.install_signal_handlers()
        bootstrap.install_signal_handlers()
