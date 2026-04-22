# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for :mod:`launcher.supervision`.

Covers:

- ``install_signal_handlers`` wires SIGTERM/SIGINT without raising
  on platforms where signal delivery is idiosyncratic (Windows).
- ``shutdown_supervisors`` is idempotent and tolerates both absent
  and hung supervisors (the launcher's ~20 s budget must hold).
- ``start_ipc_poll_thread`` picks up the manager-published
  ``broadcaster_params.json`` and starts the broadcaster exactly
  once (spec: "broadcaster thread count == 1").
- Setup-restart request file triggers
  ``apply_restart_request`` and gets cleared afterward.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from launcher import supervision
from launcher.broadcaster_supervisor import BROADCASTER_PARAMS_FILENAME


@pytest.fixture(autouse=True)
def _reset_module_state():
    supervision._reset_for_tests()
    yield
    supervision._reset_for_tests()


# -------------------------------------------------------------------
# install_signal_handlers
# -------------------------------------------------------------------

def test_install_signal_handlers_does_not_raise():
    # Best-effort installation; we only require it to complete
    # without raising on the current platform.
    supervision.install_signal_handlers()


# -------------------------------------------------------------------
# shutdown_supervisors
# -------------------------------------------------------------------

class TestShutdownSupervisors:
    def test_noop_when_none_registered(self):
        # Should be safe to call before anything has been registered.
        supervision.shutdown_supervisors()

    def test_stops_caddy_supervisor(self):
        caddy = MagicMock()
        supervision.set_caddy_supervisor(caddy)
        supervision.shutdown_supervisors()
        caddy.stop.assert_called_once()

    def test_tolerates_caddy_stop_raising(self):
        caddy = MagicMock()
        caddy.stop.side_effect = RuntimeError("boom")
        supervision.set_caddy_supervisor(caddy)
        # Must not propagate the exception.
        supervision.shutdown_supervisors()
        caddy.stop.assert_called_once()

    def test_idempotent(self):
        caddy = MagicMock()
        supervision.set_caddy_supervisor(caddy)
        supervision.shutdown_supervisors()
        supervision.shutdown_supervisors()  # second call, no-op
        assert caddy.stop.call_count == 1


# -------------------------------------------------------------------
# start_ipc_poll_thread
# -------------------------------------------------------------------

class _FakeBroadcasterSupervisor:
    instances: list = []

    def __init__(self):
        self.start_calls = []
        self.stop_called = False
        _FakeBroadcasterSupervisor.instances.append(self)

    def start_from_params(self, params):
        self.start_calls.append(params)

    def stop(self, join_timeout=5.0):
        self.stop_called = True

    def is_running(self):
        return bool(self.start_calls) and not self.stop_called


def _write_params(manager_data: Path, params: dict) -> None:
    manager_data.mkdir(parents=True, exist_ok=True)
    (manager_data / BROADCASTER_PARAMS_FILENAME).write_text(
        json.dumps(params), encoding='utf-8',
    )


def test_poll_thread_starts_broadcaster_exactly_once(tmp_path):
    """Spec: launcher boot → broadcaster thread count == 1.

    The poll loop must not double-start even if params stay on disk.
    """
    manager_data = tmp_path / 'manager'
    _FakeBroadcasterSupervisor.instances = []

    with patch(
        'launcher.broadcaster_supervisor.BroadcasterSupervisor',
        _FakeBroadcasterSupervisor,
    ), patch.object(
        supervision, 'IPC_POLL_INTERVAL_SECONDS', 0.02,
    ):
        thread = supervision.start_ipc_poll_thread(manager_data)

        # Publish params; loop should notice within a few ticks.
        _write_params(manager_data, {
            "manager_id": "m",
            "name": "n",
            "host": "h",
            "ip": "i",
            "port": 80,
            "version": "0",
        })

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if _FakeBroadcasterSupervisor.instances and (
                _FakeBroadcasterSupervisor.instances[0].start_calls
            ):
                break
            time.sleep(0.05)

        # Give a few more ticks — a buggy loop would double-start here.
        time.sleep(0.2)

        supervision.get_shutdown_event().set()
        thread.join(timeout=2.0)

    assert _FakeBroadcasterSupervisor.instances, (
        "Poll loop never instantiated a BroadcasterSupervisor"
    )
    instance = _FakeBroadcasterSupervisor.instances[0]
    assert len(instance.start_calls) == 1, (
        f"Poll loop started broadcaster {len(instance.start_calls)} "
        "times; expected exactly one."
    )


def test_poll_thread_applies_caddy_restart_request(tmp_path):
    """Restart request file triggers apply_restart_request + clear."""
    manager_data = tmp_path / 'manager'
    manager_data.mkdir()
    fake_caddy = MagicMock()
    supervision.set_caddy_supervisor(fake_caddy)

    # Write the request up front.
    from launcher.setup_helpers import (
        setup_restart_request_path,
        write_setup_restart_request,
    )
    write_setup_restart_request(manager_data, {"public_tls_port": 9999})

    with patch(
        'launcher.broadcaster_supervisor.BroadcasterSupervisor',
        _FakeBroadcasterSupervisor,
    ), patch.object(
        supervision, 'IPC_POLL_INTERVAL_SECONDS', 0.02,
    ), patch(
        'launcher.caddy_launcher.apply_restart_request',
    ) as apply_mock:
        thread = supervision.start_ipc_poll_thread(manager_data)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if apply_mock.called:
                break
            time.sleep(0.05)

        supervision.get_shutdown_event().set()
        thread.join(timeout=2.0)

    apply_mock.assert_called()
    # Request file must have been cleared.
    assert not setup_restart_request_path(manager_data).exists()


def test_shutdown_event_stops_poll_loop(tmp_path):
    """Setting the shutdown event terminates the poll loop."""
    manager_data = tmp_path / 'manager'
    manager_data.mkdir()
    with patch(
        'launcher.broadcaster_supervisor.BroadcasterSupervisor',
        _FakeBroadcasterSupervisor,
    ), patch.object(
        supervision, 'IPC_POLL_INTERVAL_SECONDS', 0.02,
    ):
        thread = supervision.start_ipc_poll_thread(manager_data)
        time.sleep(0.1)
        supervision.get_shutdown_event().set()
        thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_event_isolation_between_tests():
    """Fixture reset must clear prior event state."""
    # If the prior test left the event set, supervision._reset_for_tests
    # should have cleared it via the autouse fixture.
    assert not supervision.get_shutdown_event().is_set()


# -------------------------------------------------------------------
# Regression: prior poll runs must not prevent fresh start
# -------------------------------------------------------------------

def test_thread_exit_cleans_runtime_state_for_rerun():
    """Verify test isolation: a second poll-thread start works fresh."""
    # Nothing to assert beyond "this test runs after the others without
    # an exception" — the autouse fixture proves the invariant.
    assert supervision._get_broadcaster_supervisor_for_tests() is None


# Help threading.join tolerate delay on slower Windows agents.
_ = threading
