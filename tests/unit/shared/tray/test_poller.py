# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/poller.py`` (FR-15..FR-17a).

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

from tests.unit.shared.tray.conftest import snapshots  # noqa: E402


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
            "workers_online": 3, "jobs_queued": 12,
            "jobs_rendering": 2, "version": "0.1.0", "boot_id": "boot-1",
        }
        p, _stop, _flag = poller_factory(script=[payload])
        snap_spy = QSignalSpy(p.snapshot_changed)
        p._tick()
        assert snap_spy.count() == 1
        emitted = snapshots(snap_spy)[0]
        assert isinstance(emitted, ManagerSnapshot)
        assert emitted.state == "running"
        assert emitted.workers_online == 3
        assert emitted.jobs_queued == 12
        assert emitted.version == "0.1.0"

    def test_snapshot_property_updates(self, poller_factory):
        payload = {
            "workers_online": 5, "jobs_queued": 7,
            "jobs_rendering": 1, "version": "9.9.9", "boot_id": "bx",
        }
        p, _stop, _flag = poller_factory(script=[payload])
        p._tick()
        s = p.snapshot
        assert s.state == "running"
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
        p._tick()  # fail 1
        p._tick()  # fail 2
        assert p.snapshot.state == "running"
        assert snap_spy.count() == 0
        p._tick()  # fail 3 -> error
        assert p.snapshot.state == "error"
        assert snap_spy.count() == 1

    def test_further_failures_emit_nothing_new(self, poller_factory):
        """Edge-triggered: staying in error sends no new snapshot."""
        p, _stop, _flag = poller_factory(
            script=[{"boot_id": "b1"}, *([RuntimeError("e")] * 5)],
        )
        p._tick()  # running
        for _ in range(3):
            p._tick()
        snap_spy = QSignalSpy(p.snapshot_changed)
        p._tick()
        p._tick()
        assert snap_spy.count() == 0


# Boot id change (FR-17).
class TestBootIdTransitions:

    def test_boot_id_change_forces_through_starting(self, poller_factory):
        p, _stop, _flag = poller_factory(
            script=[{"boot_id": "b1"}, {"boot_id": "b2"}],
        )
        p._tick()
        snap_spy = QSignalSpy(p.snapshot_changed)
        p._tick()
        assert [snap.state for snap in snapshots(snap_spy)] == ["starting", "running"]


# No setup state in the poller (issue #304).
class TestNoSetupState:

    def test_snapshot_has_no_setup_mode_field(self):
        names = {f.name for f in dataclasses.fields(ManagerSnapshot)}
        assert "setup_mode" not in names

    def test_stale_setup_mode_key_is_ignored(self, poller_factory):
        """A reply still carrying ``setup_mode`` (an older manager) is
        read as a plain status; no setup state is kept."""
        p, _stop, _flag = poller_factory(
            script=[
                {"boot_id": "old", "setup_mode": True},
                {"boot_id": "old", "setup_mode": False},
            ],
        )
        p._tick()
        p._tick()
        assert p.snapshot.state == "running"
        assert not hasattr(p.snapshot, "setup_mode")
        assert not hasattr(p, "_setup_notified")
        assert not hasattr(p, "_maybe_notify_setup_complete")


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
