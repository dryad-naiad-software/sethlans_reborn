# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/shutdown.py`` (FR-W17 / Phase F1).

Exercises the FR-W17 polite-shutdown machinery:

* (a) :func:`schedule_grace_timer` — single-arming idempotency, on-fire
  callback dispatch.
* (b) :func:`start_post_done_threads` — ``.wizard_reject`` polling
  thread observes a valid HMAC marker, fires the on-reject callback.
* (c) Same poller — 5-minute failsafe trip when no marker is ever
  written, fires the on-failsafe callback.
* CONC-v23-MED-3 — ``.runtime_failed`` short-circuit fires after
  :data:`RUNTIME_FAILED_MIN_DELAY` elapsed.
"""

from __future__ import annotations

import threading
import time

import pytest

from wizard.sethlans_wizard import ipc, shutdown


_IPC_SECRET = b"shutdown-test-secret-bytes-aa"


@pytest.fixture(autouse=True)
def _reset_state():
    shutdown.reset_state_for_tests()
    yield
    shutdown.reset_state_for_tests()


# ---------------------------------------------------------------------
# (a) Grace timer
# ---------------------------------------------------------------------

class TestScheduleGraceTimer:

    def test_first_call_arms_and_returns_true(self):
        fired = threading.Event()
        first = shutdown.schedule_grace_timer(on_fire=fired.set)
        assert first is True
        # The timer is 3s in production; we're not waiting for it.
        # We just want to know it was armed.
        # Cancel via reset_state_for_tests in fixture.

    def test_second_call_is_noop_returns_false(self):
        fired = threading.Event()
        first = shutdown.schedule_grace_timer(on_fire=fired.set)
        second = shutdown.schedule_grace_timer(on_fire=fired.set)
        assert first is True
        assert second is False, (
            "FR-W17(a) — only the FIRST close() may arm the grace timer"
        )

    def test_callback_fires_on_grace_elapse(self, monkeypatch):
        """Verify the timer's callback actually executes."""
        # Drop the grace to a few ms so the test stays fast.
        monkeypatch.setattr(shutdown, "GRACE_TIMER_SECONDS", 0.05)
        fired = threading.Event()
        shutdown.schedule_grace_timer(on_fire=fired.set)
        assert fired.wait(timeout=2.0), "timer never fired within 2s"


# ---------------------------------------------------------------------
# (b) .wizard_reject polling
# ---------------------------------------------------------------------

class TestRejectMarkerPolling:

    def test_valid_reject_marker_triggers_on_reject(self, tmp_path):
        captured: dict = {}
        fired = threading.Event()

        def on_reject(payload):
            captured["payload"] = payload
            fired.set()

        # Start the thread BEFORE writing the marker so we exercise
        # the real polling cadence (not a one-shot read).
        thread = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=10.0,
            poll_interval=0.05,
            on_reject=on_reject,
            on_failsafe=lambda: None,
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        # Emulate the done handler signalling the marker write.
        shutdown.mark_done_written()
        # Now write the .wizard_reject marker.
        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir(exist_ok=True)
        ipc.write_marker(
            wizard_dir / ipc.MARKER_WIZARD_REJECT,
            "wizard_reject", tmp_path, _IPC_SECRET,
            payload={"reason": "topology mismatch"},
        )

        assert fired.wait(timeout=3.0), "on_reject never fired"
        assert captured["payload"]["type"] == "wizard_reject"
        assert captured["payload"]["reason"] == "topology mismatch"
        # Allow the thread to terminate cleanly so the test doesn't
        # leak a daemon.
        thread.join(timeout=1.0)

    def test_invalid_marker_ignored(self, tmp_path):
        """An HMAC-signed marker for the wrong data_dir MUST be ignored."""
        on_reject_calls: list = []
        on_failsafe_calls: list = []

        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir(exist_ok=True)
        # Write a marker signed with a DIFFERENT secret — wizard
        # validation must reject it.
        ipc.write_marker(
            wizard_dir / ipc.MARKER_WIZARD_REJECT,
            "wizard_reject", tmp_path, b"wrong-secret-bytes-different",
            payload={"reason": "evil"},
        )

        thread = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=0.3,  # short failsafe so the test ends
            poll_interval=0.05,
            on_reject=lambda p: on_reject_calls.append(p),
            on_failsafe=lambda: on_failsafe_calls.append("failsafe"),
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        shutdown.mark_done_written()
        thread.join(timeout=2.0)

        assert on_reject_calls == [], (
            "wrong-HMAC reject marker MUST NOT trigger on_reject"
        )
        assert on_failsafe_calls == ["failsafe"], (
            "test should have ended via the failsafe path"
        )


# ---------------------------------------------------------------------
# (c) 5-minute failsafe
# ---------------------------------------------------------------------

class TestFailsafeTimer:

    def test_failsafe_fires_when_no_browser_polls(self, tmp_path):
        fired = threading.Event()
        thread = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=0.2,
            poll_interval=0.05,
            on_reject=lambda _p: None,
            on_failsafe=fired.set,
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        shutdown.mark_done_written()
        # Should fire ~0.2s in.
        assert fired.wait(timeout=2.0), "failsafe never fired"
        thread.join(timeout=1.0)

    def test_failsafe_does_not_fire_before_done_written(self, tmp_path):
        """No failsafe ticks before .wizard_done is on disk."""
        fired = threading.Event()
        thread = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=0.05,
            poll_interval=0.02,
            on_reject=lambda _p: None,
            on_failsafe=fired.set,
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        # Wait longer than the failsafe budget WITHOUT signalling done.
        time.sleep(0.3)
        assert not fired.is_set(), (
            "failsafe MUST NOT fire before mark_done_written()"
        )
        # Now signal — failsafe should fire promptly.
        shutdown.mark_done_written()
        assert fired.wait(timeout=2.0)
        thread.join(timeout=1.0)


# ---------------------------------------------------------------------
# .runtime_failed short-circuit (CONC-v23-MED-3)
# ---------------------------------------------------------------------

class TestRuntimeFailedShortCircuit:

    def test_short_circuit_fires_after_min_delay(self, tmp_path, monkeypatch):
        # Drop the min-delay so the test runs quickly.
        monkeypatch.setattr(
            shutdown, "RUNTIME_FAILED_MIN_DELAY", 0.05,
        )
        fired = threading.Event()
        captured: dict = {}

        def on_failed(payload):
            captured["payload"] = payload
            fired.set()

        wizard_dir = tmp_path / "wizard"
        wizard_dir.mkdir(exist_ok=True)
        # Pre-write the failed marker so the polling loop sees it on
        # the first iteration AFTER the min-delay window.
        ipc.write_marker(
            wizard_dir / ipc.MARKER_RUNTIME_FAILED,
            "runtime_failed", tmp_path, _IPC_SECRET,
            payload={"reason": "exit 1"},
        )

        thread = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=10.0,
            poll_interval=0.02,
            on_reject=lambda _p: None,
            on_failsafe=lambda: None,
            on_runtime_failed_short_circuit=on_failed,
        )
        shutdown.mark_done_written()

        assert fired.wait(timeout=3.0), "short-circuit never fired"
        assert captured["payload"]["type"] == "runtime_failed"
        thread.join(timeout=1.0)


class TestStartIsIdempotent:

    def test_double_start_returns_placeholder(self, tmp_path):
        # Use a tiny failsafe so the first thread terminates promptly
        # (we don't want a daemon thread outliving the test).
        fired = threading.Event()
        first = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=0.05,
            poll_interval=0.02,
            on_reject=lambda _p: None,
            on_failsafe=fired.set,
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        second = shutdown.start_post_done_threads(
            tmp_path, _IPC_SECRET,
            failsafe_seconds=0.05,
            poll_interval=0.02,
            on_reject=lambda _p: None,
            on_failsafe=lambda: None,
            on_runtime_failed_short_circuit=lambda _p: None,
        )
        assert first is not second, "second call must return a placeholder"
        # Drain the first thread cleanly.
        shutdown.mark_done_written()
        assert fired.wait(timeout=2.0)
        first.join(timeout=1.0)
