# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sentinel atomicity + per-request gate snapshot integration (C2).

Verifies that ``setup_state_snapshot(request)`` caches its result on the
request so multiple callers within a single request observe one
consistent view, even if a concurrent thread writes the sentinel mid-
flight.
"""

from __future__ import annotations

import threading

import pytest
from rest_framework.test import APIRequestFactory

from workers.services import setup_phase
from workers.services.setup_phase import setup_state_snapshot

from tests.integration.manager._setup_helpers import (
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    reset_rate_limiter,
    write_sentinel_complete,
)


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestGateSnapshot:

    def test_snapshot_cached_on_request(self, setup_env, mocker):
        rf = APIRequestFactory()
        request = rf.get("/api/setup/status/")
        spy = mocker.spy(setup_phase, "read_setup_progress")

        snap1 = setup_state_snapshot(request)
        snap2 = setup_state_snapshot(request)
        snap3 = setup_state_snapshot(request)

        assert snap1 is snap2 is snap3
        # Underlying phase lookup runs once regardless of how many
        # callers ask for the snapshot.
        assert spy.call_count <= 1

    def test_mid_flight_sentinel_write_does_not_flip_snapshot(
        self, setup_env,
    ):
        rf = APIRequestFactory()
        request = rf.get("/api/setup/status/")

        snap_before = setup_state_snapshot(request)
        assert snap_before["complete"] is False

        # Another thread writes the sentinel.  The in-flight request
        # must continue to see ``complete=False`` because its snapshot
        # was cached.
        def _writer():
            write_sentinel_complete(setup_env)

        t = threading.Thread(target=_writer)
        t.start()
        t.join()

        snap_after = setup_state_snapshot(request)
        assert snap_after is snap_before
        assert snap_after["complete"] is False

        # A *new* request, however, DOES see the sentinel.
        new_request = rf.get("/api/setup/status/")
        fresh = setup_state_snapshot(new_request)
        assert fresh["complete"] is True
