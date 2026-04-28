# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #163 — quit-event basics + IPC consumption.

Covers the lower-level pieces of the spec:

* AC-EventExists — ``get_quit_requested_event`` returns a singleton
  :class:`threading.Event`; ``wait_or_quit`` returns False on plain
  timeout, True when the event is set.
* AC-IPCConsumed — the existing IPC poll thread, when given a tray
  secret + pid provider, consumes ``.quit_requested`` markers via
  :func:`launcher.tray_ipc.consume_pending_ipc` and sets the event
  within one ``IPC_POLL_INTERVAL_SECONDS``.
* AC-TestsIsolated — paired set/clear tests verify the autouse
  conftest fixture clears the event between tests.

Helpers live in ``_tray_quit_helpers`` so this file stays under the
300-line ceiling.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from launcher import supervision

from ._tray_quit_helpers import (
    FakeBroadcasterSupervisor,
    _TEST_TRAY_PID,
    _TEST_TRAY_SECRET,
    write_quit_marker,
)


# ----------------------------------------------------------------------
# AC-EventExists
# ----------------------------------------------------------------------

class TestQuitRequestedEvent:

    def test_returns_threading_event(self):
        evt = supervision.get_quit_requested_event()
        assert isinstance(evt, threading.Event)

    def test_singleton_across_calls(self):
        a = supervision.get_quit_requested_event()
        b = supervision.get_quit_requested_event()
        assert a is b

    def test_starts_clear(self):
        # The conftest autouse fixture guarantees this between tests.
        assert not supervision.get_quit_requested_event().is_set()

    def test_wait_or_quit_returns_false_on_timeout(self):
        assert supervision.wait_or_quit(0.05) is False

    def test_wait_or_quit_returns_true_when_event_set(self):
        supervision.get_quit_requested_event().set()
        assert supervision.wait_or_quit(5.0) is True


# ----------------------------------------------------------------------
# AC-IPCConsumed
# ----------------------------------------------------------------------

class TestIPCPollSetsQuitEvent:

    def test_quit_marker_sets_event_within_one_poll(self, tmp_path):
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        with patch(
            "launcher.broadcaster_supervisor.BroadcasterSupervisor",
            FakeBroadcasterSupervisor,
        ), patch.object(
            supervision, "IPC_POLL_INTERVAL_SECONDS", 0.02,
        ):
            thread = supervision.start_ipc_poll_thread(
                manager_data,
                secret=_TEST_TRAY_SECRET,
                tray_pid_provider=lambda: _TEST_TRAY_PID,
            )
            try:
                write_quit_marker(
                    tmp_path, _TEST_TRAY_SECRET, _TEST_TRAY_PID,
                )
                fired = supervision.get_quit_requested_event().wait(
                    timeout=2.0,
                )
            finally:
                supervision.get_shutdown_event().set()
                thread.join(timeout=2.0)
        assert fired

    def test_no_secret_skips_quit_consumption(self, tmp_path):
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        with patch(
            "launcher.broadcaster_supervisor.BroadcasterSupervisor",
            FakeBroadcasterSupervisor,
        ), patch.object(
            supervision, "IPC_POLL_INTERVAL_SECONDS", 0.02,
        ):
            thread = supervision.start_ipc_poll_thread(manager_data)
            write_quit_marker(
                tmp_path, _TEST_TRAY_SECRET, _TEST_TRAY_PID,
            )
            time.sleep(0.15)
            supervision.get_shutdown_event().set()
            thread.join(timeout=2.0)
        assert not supervision.get_quit_requested_event().is_set()

    def test_invalid_marker_does_not_set_event(self, tmp_path):
        manager_data = tmp_path / "manager"
        manager_data.mkdir()
        with patch(
            "launcher.broadcaster_supervisor.BroadcasterSupervisor",
            FakeBroadcasterSupervisor,
        ), patch.object(
            supervision, "IPC_POLL_INTERVAL_SECONDS", 0.02,
        ):
            thread = supervision.start_ipc_poll_thread(
                manager_data,
                secret=_TEST_TRAY_SECRET,
                tray_pid_provider=lambda: _TEST_TRAY_PID,
            )
            write_quit_marker(tmp_path, _TEST_TRAY_SECRET, pid=11111)
            time.sleep(0.15)
            supervision.get_shutdown_event().set()
            thread.join(timeout=2.0)
        assert not supervision.get_quit_requested_event().is_set()


# ----------------------------------------------------------------------
# AC-TestsIsolated
# ----------------------------------------------------------------------

class TestQuitEventIsolation:
    """Paired tests prove the conftest autouse fixture is wired."""

    def test_a_set_in_one_test(self):
        supervision.get_quit_requested_event().set()
        assert supervision.get_quit_requested_event().is_set()

    def test_b_clear_in_next_test(self):
        # If the autouse fixture failed, this would still be set.
        assert not supervision.get_quit_requested_event().is_set()
