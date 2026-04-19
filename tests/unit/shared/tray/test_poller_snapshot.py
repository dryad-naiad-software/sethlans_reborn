# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Snapshot-semantics tests for ``shared/tray/poller.py`` (FR-15).

Split from ``test_poller.py`` to keep both files under the 300-line cap.
"""

from __future__ import annotations

import dataclasses
import queue
import threading

import pytest

from shared.tray import poller as poller_mod
from shared.tray.poller import ManagerSnapshot, StatePoller


@pytest.fixture(autouse=True)
def _always_launcher_alive(mocker):
    mocker.patch.object(
        poller_mod.launcher_watch, "is_launcher_alive", return_value=True,
    )


class TestSnapshotSemantics:

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

    def test_thread_safe_snapshot_access(self, mocker):
        # Confirm snapshot can be read from another thread while poller
        # runs _tick() on the main thread.  No assertion on ordering —
        # the property read must not raise.
        q: queue.Queue = queue.Queue()
        stop = threading.Event()
        quit_flag = threading.Event()
        p = StatePoller(
            "http://127.0.0.1:8088/api/status/public/",
            stop, q, quit_flag,
        )
        mocker.patch.object(
            p, "_fetch",
            side_effect=[{"boot_id": "b1"}] * 10,
        )
        stop_reader = threading.Event()
        observed = []

        def _reader():
            while not stop_reader.is_set():
                observed.append(p.snapshot)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        try:
            for _ in range(5):
                p._tick()
        finally:
            stop_reader.set()
            t.join(timeout=1.0)
        assert len(observed) >= 1
