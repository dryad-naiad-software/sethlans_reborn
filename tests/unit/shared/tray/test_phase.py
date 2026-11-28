# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/phase.py``: the launcher state plus the setup mode (FR-LN-4, security L5)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")

from shared.tray import launcher_state as ls  # noqa: E402
from shared.tray import phase  # noqa: E402
from shared.tray.phase import Phase  # noqa: E402
from tests.unit.shared.tray.conftest import write_finished_setup, write_launcher_state  # noqa: E402


@pytest.mark.parametrize(("mode", "manager", "worker"), [
    ("manager", True, False),
    ("worker", False, True),
    ("manager_worker", True, True),
    (None, False, False),
])
def test_running_sections_follow_the_mode(settings_dir, mode, manager, worker):
    """FR-LN-4: RUNNING's sections come from setup_finished(), so worker-only gets them too."""
    write_launcher_state(settings_dir, "RUNNING", port=8080)
    if mode is not None:
        write_finished_setup(settings_dir, mode)
    got = phase.read_phase(settings_dir)
    assert (got.mode, got.manager, got.worker) == (mode, manager, worker)


def test_mode_is_ignored_outside_running(settings_dir):
    """Only RUNNING depends on the mode; SETUP after a failed start shows no running sections."""
    write_launcher_state(settings_dir, "SETUP", port=8080)
    write_finished_setup(settings_dir, "manager")
    got = phase.read_phase(settings_dir)
    assert (got.mode, got.manager, got.worker) == (None, False, False)


@pytest.mark.parametrize("state", sorted(ls.STATES))
def test_reset_offered_only_where_the_launcher_acts_on_it(state):
    """Security L5: Reset only in RUNNING and FAILED."""
    assert Phase(state).offers_reset is (state in {"RUNNING", "FAILED"})


@pytest.mark.parametrize(("prev", "nxt", "expected"), [
    pytest.param(Phase("STARTING"), Phase("FAILED", reason="DB down"),
                 ("Sethlans could not start", "DB down"), id="failed-with-reason"),
    pytest.param(Phase("STARTING"), Phase("FAILED"),
                 ("Sethlans could not start", "Open View Logs for details."), id="failed-no-reason"),
    pytest.param(Phase("FAILED", reason="a"), Phase("FAILED", reason="b"), None, id="still-failed"),
    pytest.param(Phase("STARTING"), Phase("RUNNING", 8080, mode="manager"),
                 ("Sethlans", "Sethlans is running."), id="running"),
    pytest.param(Phase("STARTING"), Phase("RUNNING", 8080, "The worker did not start", "manager_worker"),
                 ("Sethlans", "Sethlans is running. The worker did not start"), id="running-with-note"),
    pytest.param(Phase("RUNNING", mode="worker"), Phase("RUNNING", reason="The worker ended", mode="worker"),
                 ("Sethlans", "The worker ended"), id="child-ended"),
    pytest.param(Phase("RUNNING", reason="x", mode="worker"), Phase("RUNNING", mode="worker"), None,
                 id="reason-cleared"),
    pytest.param(Phase("STARTING"), Phase("SETUP", 8080), None, id="setup"),
])
def test_edge_notice(prev, nxt, expected):
    """The launcher's state edges give the tray's notifications (FR-LN-6 reason, FR-LN-1 child ended)."""
    notice = phase.edge_notice(prev, nxt)
    assert (notice if notice is None else (notice.title, notice.message)) == expected
