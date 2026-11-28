# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`launcher.supervision`: the supervisors' shutdown,
the quit request, the poll thread's two jobs (broadcaster and tray quit)
and the Reset's broadcaster stop.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from launcher import supervision
from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME
from launcher.tray_ipc import MARKER_QUIT, MARKER_RESTART
from tests.unit.launcher._marker_helpers import SECRET, TRAY_PID, write_marker

PARAMS = {"manager_id": "m", "name": "n", "host": "h", "ip": "i", "port": 80, "version": "0"}


@pytest.fixture(autouse=True)
def _reset_module_state():
    supervision._reset_for_tests()
    yield
    supervision._reset_for_tests()


class _FakeBroadcaster:
    instances: list = []

    def __init__(self):
        self.start_calls = []
        self.stopped = False
        _FakeBroadcaster.instances.append(self)

    def start_from_params(self, params):
        self.start_calls.append(params)

    def stop(self, join_timeout=5.0):
        self.stopped = True

    def is_running(self):
        return bool(self.start_calls) and not self.stopped


@pytest.fixture
def fake_broadcaster(monkeypatch):
    _FakeBroadcaster.instances = []
    monkeypatch.setattr("launcher.broadcaster_supervisor.BroadcasterSupervisor", _FakeBroadcaster)
    return _FakeBroadcaster


def _write_params(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / BROADCASTER_PARAMS_FILENAME
    path.write_text(json.dumps(PARAMS), encoding="utf-8")
    return path


class TestShutdownSupervisors:
    def test_stops_each_supervisor_once_and_tolerates_errors(self):
        """FR-6 (launcher teardown): both supervisors stop once, an error in one does not
        skip the other, and a second call is a no-op."""
        caddy, broadcaster = MagicMock(), MagicMock()
        broadcaster.stop.side_effect = RuntimeError("boom")
        supervision.set_caddy_supervisor(caddy)
        supervision._broadcaster_supervisor = broadcaster

        supervision.shutdown_supervisors()
        supervision.shutdown_supervisors()

        broadcaster.stop.assert_called_once()
        caddy.stop.assert_called_once()


class TestQuitRequest:
    def test_a_manager_quit_is_cleared_once_handled(self):
        """Issue #163, FR-LN-1: a quit request carries its target (Quit Manager stops the
        manager only); once handled it is cleared."""
        assert supervision.quit_target() is None
        supervision.request_quit("manager")
        assert supervision.quit_target() == "manager"
        assert supervision.wait_or_quit(0) is True
        assert supervision.clear_manager_quit() is True
        assert supervision.quit_target() is None
        assert supervision.wait_or_quit(0) is False

    def test_all_is_never_downgraded_or_cleared(self):
        """Concurrency review #4: a Quit Sethlans is not undone by a later Quit Manager or
        by the clear of an earlier one."""
        supervision.request_quit("manager")
        supervision.request_quit("all")
        supervision.request_quit("manager")
        assert supervision.clear_manager_quit() is False
        assert supervision.quit_target() == "all"

    def test_a_signal_is_a_full_quit_that_no_clear_loses(self):
        """FR-21, concurrency review #4: SIGTERM/SIGINT set one flag (no lock, nothing
        stopped in the handler); it reads as "all", also during a Quit Manager."""
        caddy = MagicMock()
        supervision.set_caddy_supervisor(caddy)
        supervision.request_quit("manager")

        supervision._graceful_shutdown(15, None)

        assert supervision.clear_manager_quit() is False
        assert supervision.quit_target() == "all" and supervision.was_signalled()
        assert supervision.wait_or_quit(5.0) is True
        caddy.stop.assert_not_called()

    def test_the_event_is_never_set_without_a_target(self):
        """Concurrency review #4: request and clear change the target and the event together."""
        supervision.request_quit("manager")
        supervision.clear_manager_quit()
        event = supervision.get_quit_requested_event()
        assert (event.is_set(), supervision.quit_target()) == (False, None)
        supervision.request_quit("all")
        assert (event.is_set(), supervision.quit_target()) == (True, "all")


class TestBroadcaster:
    def test_starts_once_from_the_params(self, fake_broadcaster, tmp_path):
        """Broadcaster count == 1: the params staying on disk do not start it twice."""
        _write_params(tmp_path)
        supervision._maybe_start_broadcaster(tmp_path)
        supervision._maybe_start_broadcaster(tmp_path)
        assert len(fake_broadcaster.instances) == 1
        assert fake_broadcaster.instances[0].start_calls == [PARAMS]

    def test_none_starts_once_shutdown_has_begun(self, fake_broadcaster, tmp_path):
        """Concurrency review #5: after shutdown_supervisors the poll thread starts no broadcaster."""
        _write_params(tmp_path)
        supervision.shutdown_supervisors()
        supervision._maybe_start_broadcaster(tmp_path)
        assert fake_broadcaster.instances == []

    def test_no_params_starts_nothing(self, fake_broadcaster, tmp_path):
        """The broadcaster waits for the manager's params."""
        supervision._maybe_start_broadcaster(tmp_path)
        assert supervision._get_broadcaster_supervisor_for_tests() is None

    def test_reset_stops_it_and_it_returns_with_the_next_manager(self, fake_broadcaster, tmp_path):
        """FR-RS-1: Reset stops the broadcaster and deletes its params, so it cannot start
        from the stale file; the next manager's params start a new one."""
        params = _write_params(tmp_path)
        supervision._maybe_start_broadcaster(tmp_path)
        first = fake_broadcaster.instances[0]

        supervision.stop_broadcaster(tmp_path)
        supervision._maybe_start_broadcaster(tmp_path)

        assert first.stopped and not params.exists()
        assert supervision._get_broadcaster_supervisor_for_tests() is None
        _write_params(tmp_path)
        supervision._maybe_start_broadcaster(tmp_path)
        assert len(fake_broadcaster.instances) == 2 and fake_broadcaster.instances[1].is_running()


class TestTrayQuit:
    def test_reads_quit_only_and_leaves_restart_for_the_loop(self, tmp_path):
        """Issue #345: a restart marker survives the poll thread's read; a quit marker is
        observed with its target."""
        write_marker(tmp_path / MARKER_RESTART, target="manager")
        supervision._maybe_observe_tray_quit(tmp_path, SECRET, lambda: TRAY_PID)
        assert (tmp_path / MARKER_RESTART).exists()
        assert supervision.quit_target() is None

        write_marker(tmp_path / MARKER_QUIT, target="manager")
        supervision._maybe_observe_tray_quit(tmp_path, SECRET, lambda: TRAY_PID)
        assert supervision.quit_target() == "manager"
        assert (tmp_path / MARKER_RESTART).exists()

    @pytest.mark.parametrize("secret, pid", [
        pytest.param(None, TRAY_PID, id="no-secret"),
        pytest.param(SECRET, 11111, id="wrong-pid"),
    ])
    def test_unusable_marker_requests_nothing(self, tmp_path, secret, pid):
        """FR-20b: without the session secret, or from another pid, no quit is requested."""
        write_marker(tmp_path / MARKER_QUIT, pid=pid, target="all")
        supervision._maybe_observe_tray_quit(tmp_path, secret, lambda: TRAY_PID)
        assert supervision.quit_target() is None

    def test_second_click_stays_on_disk_for_the_cascade(self, tmp_path):
        """FR-21: while a quit is pending the poll thread leaves the next quit marker for
        the cascade's second-quit check (which kills at once)."""
        supervision.request_quit("all")
        write_marker(tmp_path / MARKER_QUIT, target="all")
        supervision._maybe_observe_tray_quit(tmp_path, SECRET, lambda: TRAY_PID)
        assert (tmp_path / MARKER_QUIT).exists()

    def test_a_failing_pass_does_not_end_the_poll_thread(self, mocker, tmp_path):
        """Concurrency review #7: an error in one poll is logged and polling goes on."""
        calls = []

        def boom(_folder):
            calls.append(1)
            if len(calls) == 2:
                supervision.get_shutdown_event().set()
            raise RuntimeError("boom")
        mocker.patch.object(supervision, "_maybe_start_broadcaster", side_effect=boom)
        mocker.patch.object(supervision, "IPC_POLL_INTERVAL_SECONDS", 0)

        supervision._ipc_poll_loop(tmp_path, tmp_path)

        assert len(calls) == 2

    def test_poll_thread_ends_on_the_shutdown_event(self, fake_broadcaster, tmp_path):
        """The poll thread is a daemon that ends with the launcher's shutdown event."""
        supervision.get_shutdown_event().set()
        thread = supervision.start_ipc_poll_thread(tmp_path, params_dir=tmp_path)
        thread.join(timeout=5.0)
        assert not thread.is_alive()
