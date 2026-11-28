# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for the ``launcher.splash_runner`` quit timer (issue #163, reopened).

A ``QTimer`` on the QApplication polls ``supervision.quit_requested()``
every 250 ms and routes dismissal through ``splash.close()``. Tests pin
timer presence, cadence, dismissal behaviour, and clean teardown. Qt is
mocked fully (``_splash_runner_stubs``).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for splash_runner")

from launcher import splash_runner  # noqa: E402
from tests.unit.launcher._splash_runner_stubs import (  # noqa: E402
    _patch_runner, _ScriptedThread, _StubSplash,
)


class TestQuitEventTimer:
    """Issue #163 (reopened) — a ``QTimer`` polls the cross-thread quit
    event from the Qt main thread so a tray Quit click during the
    error-card phase actually dismisses the splash. Without the timer,
    Qt's event loop has no way to observe a ``threading.Event`` set by
    the IPC daemon thread.
    """

    def test_quit_event_during_error_card_dismisses_splash(
        self, mocker, tmp_path,
    ):
        """AC-EventDuringErrorCard: timer tick during the error-card
        window dismisses the splash and reaches teardown_tray.
        """
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        timer = qapp._stub_timer
        teardown_calls = []

        # Force the supervision quit event to behave as "set" so that
        # when the timer's slot fires it observes the event and calls
        # splash.close().
        mocker.patch.object(
            splash_runner.supervision, "quit_requested", return_value=True,
        )

        def driver():
            # Failure path → error card visible → orchestration done.
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            assert splash.isVisible() is True
            assert qapp.quit.call_count == 0
            # Tray Quit marker fires → daemon sets event → timer ticks
            # → splash.close() called via the connected slot.
            timer.fire()
            return 0
        qapp.exec.side_effect = driver

        rc = splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda t: teardown_calls.append(t),
        )

        # AC-TimerCallsClose — the splash got closed by the timer slot.
        assert splash.isVisible() is False
        # AC-EventDuringErrorCard — teardown was reached, rc preserved.
        assert teardown_calls == [None]
        assert rc == 1

    def test_timer_polls_at_250ms_cadence(self, mocker, tmp_path):
        """AC-TimerExists / NFR-4: timer interval is exactly 250 ms."""
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        timer = qapp._stub_timer

        def driver():
            # Drive a normal failure + user dismiss so the runner
            # returns cleanly. We only care about timer interval here.
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            # While the timer is live, assert its cadence.
            assert timer.interval() == 250
            assert timer.isActive() is True
            splash.close()
            qapp.quit()
            return 0
        qapp.exec.side_effect = driver

        splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda _t: None,
        )

    def test_timer_stopped_after_runner_returns(self, mocker, tmp_path):
        """AC-TimerStopped / FR-4: the timer is explicitly stopped once
        ``run_with_splash`` returns.
        """
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        timer = qapp._stub_timer

        def driver():
            thread.startup_failed.emit("boom", "tb")
            thread.finished_with_code.emit(1)
            splash.close()
            qapp.quit()
            return 0
        qapp.exec.side_effect = driver

        splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 1,
            teardown_tray=lambda _t: None,
        )

        assert timer.isActive() is False

    def test_timer_does_not_fire_when_event_not_set(
        self, mocker, tmp_path,
    ):
        """AC-EventDuringColdBoot (negative half) / FR-5: ticks while
        the quit event is not set must NOT dismiss the splash. The
        cold-boot success path proceeds via ``_on_ready`` only.
        """
        thread = _ScriptedThread()
        splash = _StubSplash()
        qapp = _patch_runner(mocker, thread, splash)
        timer = qapp._stub_timer

        # Quit event stays unset throughout — timer ticks must be no-ops.
        quit_requested = mocker.patch.object(
            splash_runner.supervision, "quit_requested", return_value=False,
        )

        def driver():
            # Several harmless ticks while orchestration is "in progress".
            for _ in range(5):
                timer.fire()
            # No dismissal happened: splash is still visible, quit not
            # called, isVisible() unchanged.
            assert splash.isVisible() is True
            assert qapp.quit.call_count == 0
            # End the test cleanly via the success path.
            thread.cold_boot_ready.emit()
            return 0
        qapp.exec.side_effect = driver

        splash_runner.run_with_splash(
            args=mocker.MagicMock(),
            data_dir=tmp_path,
            version="9.9.9",
            pre_orchestration_setup=lambda _dd: (None, "secret"),
            run_orchestration=lambda *a, **kw: 0,
            teardown_tray=lambda _t: None,
        )

        # is_set was polled at least once per fire() — confirms the slot
        # actually ran and chose not to dismiss.
        assert quit_requested.call_count >= 5
