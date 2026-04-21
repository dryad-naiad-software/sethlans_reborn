# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`launcher.broadcaster_supervisor`.

Covers:

- ``read_broadcaster_params`` returns None for missing / unparseable
  files, and the decoded dict when the file is a valid JSON object.
- ``BroadcasterSupervisor.start_from_params`` raises
  :class:`RuntimeError` when required keys are missing.
- ``BroadcasterSupervisor.start_from_params`` is idempotent-by-error
  (second call while a broadcaster is live raises) — matches spec
  "broadcaster thread count == 1" requirement.
- ``stop()`` tolerates a not-yet-started supervisor (no-op).
- ``stop()`` does not block launcher shutdown past the 5 s join
  timeout when the broadcaster thread is deliberately hung.

The :class:`MulticastBroadcaster` class import is mocked so the
tests never bind a real UDP socket.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from launcher import broadcaster_supervisor as bs_mod


class _FakeBroadcaster:
    """Minimal stand-in for ``MulticastBroadcaster``."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.join_called_with = None
        self._alive = False

    def start(self):
        self.started = True
        self._alive = True

    def stop(self):
        self.stopped = True
        self._alive = False

    def join(self, timeout=None):
        self.join_called_with = timeout

    def is_alive(self):
        return self._alive


class _HungBroadcaster(_FakeBroadcaster):
    """Broadcaster whose ``join`` blocks forever (stop hangs)."""

    def join(self, timeout=None):
        self.join_called_with = timeout
        # Simulate a hung thread — sleep for half the timeout so the
        # test doesn't wait too long but we still exercise the
        # timeout path. ``threading.Thread.join`` with a timeout
        # returns after the timeout regardless of state; our stand-in
        # approximates that by sleeping and leaving _alive True.
        if timeout is not None:
            time.sleep(min(timeout, 0.2))


# -------------------------------------------------------------------
# read_broadcaster_params
# -------------------------------------------------------------------

class TestReadBroadcasterParams:
    def test_missing_file_returns_none(self, tmp_path):
        assert bs_mod.read_broadcaster_params(tmp_path) is None

    def test_invalid_json_returns_none(self, tmp_path):
        (tmp_path / bs_mod.BROADCASTER_PARAMS_FILENAME).write_text(
            "not json {", encoding="utf-8",
        )
        assert bs_mod.read_broadcaster_params(tmp_path) is None

    def test_valid_json_returns_dict(self, tmp_path):
        payload = {
            "manager_id": "abc",
            "name": "Test",
            "host": "h",
            "ip": "1.2.3.4",
            "port": 8080,
            "version": "0.0.1",
        }
        (tmp_path / bs_mod.BROADCASTER_PARAMS_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8",
        )
        got = bs_mod.read_broadcaster_params(tmp_path)
        assert got == payload


# -------------------------------------------------------------------
# BroadcasterSupervisor
# -------------------------------------------------------------------

@pytest.fixture
def valid_params():
    return {
        "manager_id": "abc",
        "name": "Test",
        "host": "h",
        "ip": "1.2.3.4",
        "port": 8080,
        "version": "0.0.1",
    }


class TestBroadcasterSupervisorStart:
    def test_missing_required_keys_raises(self, valid_params):
        sv = bs_mod.BroadcasterSupervisor()
        incomplete = dict(valid_params)
        del incomplete["ip"]
        with patch.object(
            bs_mod, '_load_multicast_broadcaster_cls',
            return_value=_FakeBroadcaster,
        ):
            with pytest.raises(RuntimeError, match="missing keys"):
                sv.start_from_params(incomplete)

    def test_double_start_raises(self, valid_params):
        sv = bs_mod.BroadcasterSupervisor()
        with patch.object(
            bs_mod, '_load_multicast_broadcaster_cls',
            return_value=_FakeBroadcaster,
        ):
            sv.start_from_params(valid_params)
            assert sv.is_running() is True
            with pytest.raises(RuntimeError, match="already started"):
                sv.start_from_params(valid_params)
        sv.stop(join_timeout=0.1)

    def test_start_instantiates_and_starts_broadcaster(self, valid_params):
        sv = bs_mod.BroadcasterSupervisor()
        with patch.object(
            bs_mod, '_load_multicast_broadcaster_cls',
            return_value=_FakeBroadcaster,
        ):
            sv.start_from_params(valid_params)
        # ``_broadcaster`` is private; validate indirectly via stop.
        assert sv.is_running() is True
        sv.stop(join_timeout=0.1)
        assert sv.is_running() is False


class TestBroadcasterSupervisorStop:
    def test_stop_without_start_is_noop(self):
        sv = bs_mod.BroadcasterSupervisor()
        # Should not raise.
        sv.stop(join_timeout=0.1)

    def test_stop_happy_path_joins_with_timeout(self, valid_params):
        sv = bs_mod.BroadcasterSupervisor()
        with patch.object(
            bs_mod, '_load_multicast_broadcaster_cls',
            return_value=_FakeBroadcaster,
        ):
            sv.start_from_params(valid_params)
        sv.stop(join_timeout=2.5)
        assert sv.is_running() is False

    def test_hung_broadcaster_does_not_block_past_timeout(
        self, valid_params,
    ):
        """Unhappy path: deliberately-hung stop does not keep the
        launcher past its ~20 s shutdown budget."""
        sv = bs_mod.BroadcasterSupervisor()
        with patch.object(
            bs_mod, '_load_multicast_broadcaster_cls',
            return_value=_HungBroadcaster,
        ):
            sv.start_from_params(valid_params)

        start = threading.Event()

        def runner():
            start.set()
            sv.stop(join_timeout=0.2)

        t = threading.Thread(target=runner)
        t.start()
        start.wait(timeout=2.0)
        t.join(timeout=2.0)
        assert not t.is_alive(), (
            "Launcher shutdown blocked past the join timeout budget"
        )
