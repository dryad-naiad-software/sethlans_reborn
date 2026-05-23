# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``_start_component`` stdout/stderr handling (issue #204).

Regression coverage for the deadlock where the launcher spawned long-running
manager / worker processes with ``stdout=subprocess.PIPE,
stderr=subprocess.PIPE`` and never drained them. The ~64 KB OS pipe buffer
filled within minutes, causing ``Handler.emit()`` inside the spawned
process to block while holding the logging handler's RLock — which then
deadlocked every Waitress worker thread that tried to log a 4xx response.

Contract enforced here:

* ``manager`` / ``worker``  → ``subprocess.DEVNULL`` on both streams.
* ``tray``                  → ``None`` on both streams (inherits parent).
* anything else             → ``subprocess.PIPE`` on both streams
  (short-lived components are drained elsewhere).
"""

from __future__ import annotations

import subprocess

import pytest

from launcher import run_launcher


@pytest.fixture
def stub_environment(mocker, tmp_path):
    """Stub the helpers ``_start_component`` calls so only the Popen
    arguments are exercised — no real subprocess is ever spawned.
    """
    mocker.patch.object(
        run_launcher, "_find_component_exe",
        return_value=tmp_path / "fake-exe",
    )
    mocker.patch.object(
        run_launcher, "popen_kwargs_for_component", return_value={},
    )
    # Force the "running from source" branch so the test does not depend
    # on whether the suite happens to be invoked under PyInstaller.
    # ``sys.frozen`` is absent under cpython; ``create=True`` adds it for
    # the duration of the test and restores the prior state on teardown.
    mocker.patch.object(
        run_launcher.sys, "frozen", new=False, create=True,
    )
    popen_mock = mocker.patch.object(
        run_launcher.subprocess, "Popen", autospec=True,
    )
    return popen_mock


def _popen_kwargs(popen_mock) -> dict:
    """Return the kwargs of the most recent Popen call."""
    assert popen_mock.call_args is not None, "Popen was never invoked"
    return popen_mock.call_args.kwargs


class TestStartComponentPipeHandling:
    """Each component must select the correct stdio strategy (#204)."""

    def test_manager_uses_devnull_on_both_streams(self, stub_environment):
        run_launcher._start_component("manager")
        kwargs = _popen_kwargs(stub_environment)
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL

    def test_worker_uses_devnull_on_both_streams(self, stub_environment):
        run_launcher._start_component("worker")
        kwargs = _popen_kwargs(stub_environment)
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL

    def test_tray_inherits_parent_stdio(self, stub_environment):
        run_launcher._start_component("tray")
        kwargs = _popen_kwargs(stub_environment)
        assert kwargs["stdout"] is None
        assert kwargs["stderr"] is None

    def test_other_components_use_pipe(self, stub_environment):
        # Short-lived components (e.g. wizard) are drained elsewhere; the
        # legacy PIPE behaviour must be preserved for them.
        run_launcher._start_component("wizard")
        kwargs = _popen_kwargs(stub_environment)
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
