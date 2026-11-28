# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.restart_orchestrator` (tray Restart; 6a review gate)."""

from __future__ import annotations

import pytest

from launcher import restart_orchestrator
from tests.unit.launcher._loop_fakes import FakeProc


def test_restart_deletes_the_marker_and_respawns_after_the_old_one_ended(tmp_path):
    """Issue #345 / FR-20: the marker goes first, the old manager ends, the new one starts."""
    (tmp_path / ".restart_requested").write_text("{}", encoding="utf-8")
    old, new = FakeProc(1), FakeProc(2)

    assert restart_orchestrator.handle_restart_request(old, tmp_path, lambda: new) is new

    assert old.terminated and old.poll() is not None
    assert not (tmp_path / ".restart_requested").exists()


def test_no_new_manager_beside_one_that_would_not_end(tmp_path):
    """6a gate: a new manager starts only after the old one has exited (its start log
    cannot be replaced while held on Windows)."""
    old = FakeProc(1, obeys=False)
    respawn_called = []

    assert restart_orchestrator.handle_restart_request(old, tmp_path, lambda: respawn_called.append(1)) is None
    assert respawn_called == []


@pytest.mark.parametrize("error", [PermissionError("held"), OSError("missing exe")])
def test_a_failed_respawn_is_reported_not_raised(tmp_path, error):
    """6a gate: a held manager-start.log (PermissionError) or any start error gives None."""
    def respawn():
        raise error

    assert restart_orchestrator.handle_restart_request(FakeProc(1), tmp_path, respawn) is None
