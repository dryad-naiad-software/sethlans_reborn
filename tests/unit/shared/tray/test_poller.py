# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/poller.py`` (FR-15..FR-17a, FR-26)."""

from __future__ import annotations

import queue
import threading

import pytest

from shared.tray import poller as poller_mod
from shared.tray.notifications import NotificationEvent
from shared.tray.poller import StatePoller


@pytest.fixture(autouse=True)
def _always_launcher_alive(mocker):
    # Avoid self-exit branch during unit tests.
    mocker.patch.object(
        poller_mod.launcher_watch, "is_launcher_alive", return_value=True,
    )


def _make_poller(mocker, script=None):
    """Create a StatePoller with ``_fetch`` scripted by side_effect.

    ``script`` is an ordered list mixing payload dicts (success) and
    Exception instances (failure) representing the sequence of ticks.
    """
    q: queue.Queue = queue.Queue()
    stop = threading.Event()
    quit_flag = threading.Event()
    p = StatePoller(
        "http://127.0.0.1:8088/api/status/public/",
        stop, q, quit_flag,
    )
    if script:
        mocker.patch.object(p, "_fetch", side_effect=list(script))
    return p, q, stop, quit_flag


def _drain(q: queue.Queue):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ------------------------------------------------------------------
# Happy-path transitions
# ------------------------------------------------------------------

class TestStartingToRunning:

    def test_first_200_transitions_to_running(self, mocker):
        payload = {
            "setup_mode": False,
            "workers_online": 3,
            "jobs_queued": 12,
            "jobs_rendering": 2,
            "version": "0.1.0",
            "boot_id": "boot-1",
        }
        p, q, _stop, _flag = _make_poller(mocker, [payload])
        p._tick()
        events = _drain(q)
        kinds = [e[0] for e in events]
        assert "snapshot" in kinds
        assert "state_change" in kinds
        state_change = [e for e in events if e[0] == "state_change"][0]
        assert state_change[1] == "starting"
        assert state_change[2] == "running"

    def test_starting_to_running_fires_notification(self, mocker):
        # setup_mode=True suppresses the setup-complete notification so
        # only the Starting->Running edge notification is observed.
        payload = {"boot_id": "b1", "version": "x", "setup_mode": True}
        p, q, _stop, _flag = _make_poller(mocker, [payload])
        p._tick()
        events = _drain(q)
        notif = [e for e in events if e[0] == "notification"]
        assert len(notif) == 1
        assert isinstance(notif[0][1], NotificationEvent)
        assert "running" in notif[0][1].message.lower()

    def test_snapshot_fields_populated(self, mocker):
        payload = {
            "setup_mode": True,
            "workers_online": 5,
            "jobs_queued": 7,
            "jobs_rendering": 1,
            "version": "9.9.9",
            "boot_id": "bx",
        }
        p, _q, _, _ = _make_poller(mocker, [payload])
        p._tick()
        s = p.snapshot
        assert s.state == "running"
        assert s.setup_mode is True
        assert s.workers_online == 5
        assert s.jobs_queued == 7
        assert s.jobs_rendering == 1
        assert s.version == "9.9.9"
        assert s.boot_id == "bx"


# ------------------------------------------------------------------
# Failure transitions
# ------------------------------------------------------------------

class TestRunningToError:

    def test_three_consecutive_failures_triggers_error(self, mocker):
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1"},
                RuntimeError("a"), RuntimeError("b"), RuntimeError("c"),
            ],
        )
        p._tick()  # running
        _drain(q)
        p._tick()  # fail 1
        p._tick()  # fail 2
        assert p.snapshot.state == "running"
        p._tick()  # fail 3 -> error
        assert p.snapshot.state == "error"

    def test_error_notification_fires_on_edge_only(self, mocker):
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1", "setup_mode": True},
                *([RuntimeError("e")] * 5),
            ],
        )
        p._tick()  # running
        for _ in range(3):
            p._tick()
        # 4 ticks -> edges: starting->running, running->error.
        events = _drain(q)
        state_changes = [e for e in events if e[0] == "state_change"]
        assert len(state_changes) == 2
        notif_msgs = [
            e[1].message for e in events if e[0] == "notification"
        ]
        # Expect manager running + manager error notifications.
        assert any("running" in m.lower() for m in notif_msgs)
        assert any("error state" in m.lower() for m in notif_msgs)

        # Additional failures: already in Error — no further events.
        p._tick()
        p._tick()
        events2 = _drain(q)
        assert not any(e[0] == "state_change" for e in events2)
        assert not any(e[0] == "notification" for e in events2)

    def test_error_to_running_on_single_200(self, mocker):
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1"},              # -> running
                RuntimeError("e"),
                RuntimeError("e"),
                RuntimeError("e"),              # -> error
                {"boot_id": "b1"},              # recovery (same boot_id)
            ],
        )
        p._tick()  # running
        for _ in range(3):
            p._tick()  # -> error
        assert p.snapshot.state == "error"
        _drain(q)
        p._tick()  # recovery 200
        assert p.snapshot.state == "running"
        # Recovery notification is emitted.
        events = _drain(q)
        notif = [e for e in events if e[0] == "notification"]
        assert any(
            "recovered" in n[1].message.lower() for n in notif
        )


# ------------------------------------------------------------------
# Quit flag behavior (FR-17a)
# ------------------------------------------------------------------

class TestQuitFlag:

    def test_failures_go_to_stopped_when_quit_flag_set(self, mocker):
        p, _q, _stop, flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1"},
                *([RuntimeError("e")] * 3),
            ],
        )
        p._tick()  # running
        flag.set()
        for _ in range(3):
            p._tick()
        assert p.snapshot.state == "stopped"

    def test_200_after_quit_clears_flag(self, mocker):
        p, _q, _stop, flag = _make_poller(
            mocker,
            script=[{"boot_id": "b1"}, {"boot_id": "b1"}],
        )
        p._tick()  # running
        flag.set()
        p._tick()  # 200 -> clears flag
        assert not flag.is_set()


# ------------------------------------------------------------------
# Boot id change (FR-17)
# ------------------------------------------------------------------

class TestBootIdChange:

    def test_boot_id_change_forces_through_starting(self, mocker):
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1"},
                {"boot_id": "b2"},
            ],
        )
        p._tick()
        _drain(q)
        p._tick()
        events = _drain(q)
        state_changes = [e for e in events if e[0] == "state_change"]
        # Expect running -> starting -> running
        seq = [(sc[1], sc[2]) for sc in state_changes]
        assert ("running", "starting") in seq
        assert ("starting", "running") in seq


# ------------------------------------------------------------------
# Launcher-dead self-termination
# ------------------------------------------------------------------

class TestLauncherDead:

    def test_sets_stop_event_when_launcher_gone(self, mocker):
        mocker.patch.object(
            poller_mod.launcher_watch,
            "is_launcher_alive", return_value=False,
        )
        p, _q, stop, _flag = _make_poller(mocker)
        p._tick()
        assert stop.is_set()


# See test_poller_snapshot.py for snapshot semantics (frozen dataclass,
# dataclasses.replace, thread-safe read).


# ------------------------------------------------------------------
# Setup-complete edge (FR-26) — Fix B / issue #79
# ------------------------------------------------------------------

def _setup_notifs(events):
    return [
        e for e in events
        if e[0] == "notification"
        and "setup complete" in e[1].message.lower()
    ]


class TestSetupCompleteEdge:

    def test_no_notif_when_tray_starts_after_setup_done(self, mocker):
        # Tray boots into a manager that is already out of setup mode.
        # First probe: setup_mode=False.  We must NOT fire the
        # "Setup complete" notification — the True->False edge never
        # happened during this tray session.
        payload = {"boot_id": "b1", "setup_mode": False}
        p, q, _stop, _flag = _make_poller(
            mocker, [payload, payload, payload],
        )
        p._tick()
        p._tick()
        p._tick()
        events = _drain(q)
        assert _setup_notifs(events) == []

    def test_fires_once_on_true_to_false_edge(self, mocker):
        # Tray boots mid-setup: first probe setup_mode=True, second
        # probe setup_mode=False.  Exactly one "Setup complete"
        # notification must fire.
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": False},
            ],
        )
        p._tick()
        mid = _drain(q)
        assert _setup_notifs(mid) == []
        p._tick()
        after = _drain(q)
        assert len(_setup_notifs(after)) == 1

    def test_does_not_refire_on_subsequent_false_ticks(self, mocker):
        p, q, _stop, _flag = _make_poller(
            mocker,
            script=[
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": False},
                {"boot_id": "b1", "setup_mode": False},
                {"boot_id": "b1", "setup_mode": False},
            ],
        )
        for _ in range(4):
            p._tick()
        events = _drain(q)
        assert len(_setup_notifs(events)) == 1
