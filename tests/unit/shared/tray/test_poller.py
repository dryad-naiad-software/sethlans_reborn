# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/poller.py`` (FR-15..FR-17a, FR-26).

Covers the PySide6-based ``QtStatePoller`` via ``QSignalSpy`` counts
and payload inspection.  Shared fixtures live in ``conftest.py``;
thread-lifecycle + cross-thread delivery tests live in
``test_poller_lifecycle.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for poller")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtTest import QSignalSpy  # noqa: E402

from shared.tray.poller import ManagerSnapshot  # noqa: E402

from tests.unit.shared.tray.conftest import (  # noqa: E402
    notification_msgs, setup_msgs, snapshots, state_changes,
)


# Snapshot dataclass semantics (mirror test_poller_snapshot.py).
class TestManagerSnapshot:

    def test_snapshot_is_frozen_dataclass(self):
        s = ManagerSnapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.state = "other"  # type: ignore[misc]

    def test_replace_produces_new_instance(self):
        s = ManagerSnapshot(state="starting")
        s2 = dataclasses.replace(s, state="running")
        assert s is not s2
        assert s.state == "starting"
        assert s2.state == "running"

    def test_default_fields(self):
        s = ManagerSnapshot()
        assert s.state == "starting"
        assert s.setup_mode is False
        assert s.workers_online == 0
        assert s.jobs_queued == 0
        assert s.jobs_rendering == 0
        assert s.version == ""
        assert s.boot_id == ""
        assert s.last_error == ""


# Happy-path: starting -> running.
class TestHappyPath:

    def test_first_tick_emits_snapshot_changed(self, poller_factory):
        payload = {
            "setup_mode": False, "workers_online": 3, "jobs_queued": 12,
            "jobs_rendering": 2, "version": "0.1.0", "boot_id": "boot-1",
        }
        p, _stop, _flag = poller_factory(script=[payload])
        snap_spy = QSignalSpy(p.snapshot_changed)
        state_spy = QSignalSpy(p.state_changed)
        p._tick()
        assert snap_spy.count() == 1
        emitted = snapshots(snap_spy)[0]
        assert isinstance(emitted, ManagerSnapshot)
        assert emitted.state == "running"
        assert emitted.workers_online == 3
        assert emitted.jobs_queued == 12
        assert emitted.version == "0.1.0"
        assert state_changes(state_spy) == [("starting", "running")]

    def test_first_tick_fires_running_notification(self, poller_factory):
        # setup_mode=True suppresses the setup-complete notification so
        # only the Starting->Running edge notification is observed.
        payload = {"boot_id": "b1", "version": "x", "setup_mode": True}
        p, _stop, _flag = poller_factory(script=[payload])
        notif_spy = QSignalSpy(p.notification)
        p._tick()
        msgs = notification_msgs(notif_spy)
        assert len(msgs) == 1
        assert "running" in msgs[0].lower()

    def test_snapshot_property_updates(self, poller_factory):
        payload = {
            "setup_mode": True, "workers_online": 5, "jobs_queued": 7,
            "jobs_rendering": 1, "version": "9.9.9", "boot_id": "bx",
        }
        p, _stop, _flag = poller_factory(script=[payload])
        p._tick()
        s = p.snapshot
        assert s.state == "running"
        assert s.setup_mode is True
        assert s.workers_online == 5
        assert s.jobs_queued == 7
        assert s.jobs_rendering == 1
        assert s.version == "9.9.9"
        assert s.boot_id == "bx"


# Failure threshold (FR-17).
class TestFailureThreshold:

    def test_three_consecutive_failures_triggers_error(
        self, poller_factory,
    ):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1"}, RuntimeError("a"),
                RuntimeError("b"), RuntimeError("c"),
            ],
        )
        p._tick()  # running
        snap_spy = QSignalSpy(p.snapshot_changed)
        state_spy = QSignalSpy(p.state_changed)
        p._tick()  # fail 1
        p._tick()  # fail 2
        assert p.snapshot.state == "running"
        assert snap_spy.count() == 0
        assert state_spy.count() == 0
        p._tick()  # fail 3 -> error
        assert p.snapshot.state == "error"
        assert snap_spy.count() == 1
        assert state_changes(state_spy) == [("running", "error")]

    def test_error_notification_fires_on_edge_only(self, poller_factory):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                *([RuntimeError("e")] * 5),
            ],
        )
        notif_spy = QSignalSpy(p.notification)
        state_spy = QSignalSpy(p.state_changed)
        p._tick()  # running
        for _ in range(3):
            p._tick()
        assert state_spy.count() == 2
        msgs = notification_msgs(notif_spy)
        assert any("running" in m.lower() for m in msgs)
        assert any("error state" in m.lower() for m in msgs)
        pre_state = state_spy.count()
        pre_notif = notif_spy.count()
        p._tick()
        p._tick()
        assert state_spy.count() == pre_state
        assert notif_spy.count() == pre_notif

    def test_error_to_running_recovery_notification(
        self, poller_factory,
    ):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1"},
                RuntimeError("e"), RuntimeError("e"), RuntimeError("e"),
                {"boot_id": "b1"},
            ],
        )
        p._tick()
        for _ in range(3):
            p._tick()
        assert p.snapshot.state == "error"
        notif_spy = QSignalSpy(p.notification)
        p._tick()
        assert p.snapshot.state == "running"
        msgs = notification_msgs(notif_spy)
        assert any("recovered" in m.lower() for m in msgs)


# Boot id change (FR-17).
class TestBootIdTransitions:

    def test_boot_id_change_forces_through_starting(self, poller_factory):
        p, _stop, _flag = poller_factory(
            script=[{"boot_id": "b1"}, {"boot_id": "b2"}],
        )
        p._tick()
        state_spy = QSignalSpy(p.state_changed)
        p._tick()
        seq = state_changes(state_spy)
        assert ("running", "starting") in seq
        assert ("starting", "running") in seq


# Setup-complete edge (FR-26).
class TestSetupCompleteEdge:

    def test_no_notif_when_tray_starts_after_setup_done(
        self, poller_factory,
    ):
        payload = {"boot_id": "b1", "setup_mode": False}
        p, _stop, _flag = poller_factory(script=[payload] * 3)
        notif_spy = QSignalSpy(p.notification)
        for _ in range(3):
            p._tick()
        assert setup_msgs(notif_spy) == []

    def test_fires_once_on_true_to_false_edge(self, poller_factory):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": False},
            ],
        )
        notif_spy = QSignalSpy(p.notification)
        p._tick()
        assert setup_msgs(notif_spy) == []
        p._tick()
        assert len(setup_msgs(notif_spy)) == 1

    def test_does_not_refire_on_subsequent_false_ticks(
        self, poller_factory,
    ):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                {"boot_id": "b1", "setup_mode": False},
                {"boot_id": "b1", "setup_mode": False},
                {"boot_id": "b1", "setup_mode": False},
            ],
        )
        notif_spy = QSignalSpy(p.notification)
        for _ in range(4):
            p._tick()
        assert len(setup_msgs(notif_spy)) == 1

    def test_setup_complete_refires_on_new_boot_id(self, poller_factory):
        # Regression: new manager boot re-arms the one-shot so the next
        # setup_mode True->False edge fires again in the same session.
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "boot-A", "setup_mode": True},
                {"boot_id": "boot-A", "setup_mode": False},
                {"boot_id": "boot-B", "setup_mode": True},
                {"boot_id": "boot-B", "setup_mode": False},
            ],
        )
        notif_spy = QSignalSpy(p.notification)
        p._tick()  # boot-A, setup in progress
        assert setup_msgs(notif_spy) == []
        p._tick()  # boot-A, setup complete -> fire #1
        assert len(setup_msgs(notif_spy)) == 1 and p._setup_notified
        p._tick()  # boot-B, back in setup (flag reset on boot change)
        assert len(setup_msgs(notif_spy)) == 1
        assert p._setup_notified is False
        p._tick()  # boot-B, setup complete -> fire #2
        assert len(setup_msgs(notif_spy)) == 2


# Notification edges — all three mapped edges in one sequence.
class TestNotificationEdges:

    def test_running_to_error_to_running_sequence(self, poller_factory):
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "b1", "setup_mode": True},
                RuntimeError("e"), RuntimeError("e"), RuntimeError("e"),
                {"boot_id": "b1", "setup_mode": True},
            ],
        )
        notif_spy = QSignalSpy(p.notification)
        p._tick()
        for _ in range(3):
            p._tick()
        p._tick()
        lowered = [m.lower() for m in notification_msgs(notif_spy)]
        assert any("manager is running" in m for m in lowered)
        assert any("error state" in m for m in lowered)
        assert any("recovered" in m for m in lowered)


# Quit flag (FR-17a).
class TestQuitFlagSemantics:

    def test_failures_go_to_stopped_when_quit_flag_set(
        self, poller_factory,
    ):
        p, _stop, flag = poller_factory(
            script=[{"boot_id": "b1"}, *([RuntimeError("e")] * 3)],
        )
        p._tick()
        flag.set()
        for _ in range(3):
            p._tick()
        assert p.snapshot.state == "stopped"

    def test_200_after_quit_clears_flag(self, poller_factory):
        p, _stop, flag = poller_factory(
            script=[{"boot_id": "b1"}, {"boot_id": "b1"}],
        )
        p._tick()
        flag.set()
        p._tick()
        assert not flag.is_set()
