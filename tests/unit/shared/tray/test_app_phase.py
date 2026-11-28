# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/app_phase.py``: the menu follows the launcher's state (FR-LN-4, chunk 6c)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from shared.tray import app_phase  # noqa: E402
from shared.tray.menu_manager import ManagerSection  # noqa: E402
from shared.tray.menu_setup import SetupSection, StatusSection  # noqa: E402
from shared.tray.menu_worker import WorkerSection  # noqa: E402
from shared.tray.phase import Phase  # noqa: E402
from shared.tray.poller import ManagerSnapshot  # noqa: E402
from tests.unit.shared.tray.conftest import texts, write_finished_setup, write_launcher_state  # noqa: E402


@pytest.fixture
def ctx(settings_dir):
    notices = []
    return SimpleNamespace(
        settings_dir=settings_dir, quit_flag=threading.Event(), notify=notices.append, notices=notices,
        current_snapshot=ManagerSnapshot, phase=Phase("STARTING"), sections=[], menu=None,
    )


@pytest.mark.parametrize(("phase", "kinds"), [
    (Phase("SETUP", 8080), [SetupSection]),
    (Phase("STARTING"), [StatusSection]),
    (Phase("FAILED", reason="x"), [StatusSection]),
    (Phase("RUNNING", 8080), [StatusSection]),  # no readable mode
    (Phase("RUNNING", 8080, mode="manager"), [ManagerSection]),
    (Phase("RUNNING", mode="worker"), [WorkerSection]),
    (Phase("RUNNING", 8080, mode="manager_worker"), [ManagerSection, WorkerSection]),
])
def test_sections_per_phase(qapp, ctx, phase, kinds):
    """FR-LN-4: the launcher state picks the menu; in RUNNING the mode picks the sections."""
    assert [type(s) for s in app_phase.build_sections(ctx, phase)] == kinds


def test_manager_worker_menu_has_one_reset_and_the_reason_on_top(qapp, ctx):
    """FR-LN-4: Reset setup... once in every mode; the launcher's RUNNING reason is shown."""
    phase = Phase("RUNNING", 8080, "The worker ended (exit code 1)", "manager_worker")
    items = texts(app_phase.build_root_menu(app_phase.build_sections(ctx, phase), phase))
    assert items[:2] == ["The worker ended (exit code 1)", "---"]
    assert items.count("Reset setup...") == 1
    assert items.count("About Sethlans") == 1
    assert ["Quit Manager", "Quit Sethlans"] == [t for t in items if t.startswith("Quit")]


def test_tick_rebuilds_on_a_launcher_state_change(qapp, ctx, settings_dir):
    """6c: the tray follows launcher_state.json and setup_finished(); a change rebuilds the
    menu and gives the change's notification; no change keeps the menu."""
    tray = QSystemTrayIcon()
    app_phase.install(ctx, tray, Phase("STARTING"))
    first = ctx.menu
    app_phase.tick(ctx, tray)
    assert ctx.menu is first and ctx.notices == []

    write_launcher_state(settings_dir, "RUNNING")
    write_finished_setup(settings_dir, "worker")
    app_phase.tick(ctx, tray)
    assert ctx.phase == Phase("RUNNING", mode="worker")
    assert tray.contextMenu() is ctx.menu is not first
    assert "Open Sethlans Status" in texts(ctx.menu)
    assert [n.message for n in ctx.notices] == ["Sethlans is running."]


def test_tick_never_raises(qapp, ctx, mocker):
    """The state timer must keep running whatever a read does."""
    mocker.patch.object(app_phase.phase_mod, "read_phase", side_effect=RuntimeError("boom"))
    app_phase.tick(ctx, QSystemTrayIcon())


@pytest.mark.parametrize(("state", "cleared"), [("STARTING", True), ("SETUP", False)])
def test_leaving_setup_clears_a_copied_token(qapp, ctx, settings_dir, mocker, state, cleared):
    """Security L2: when setup closes, a setup token still on the clipboard is cleared."""
    clear = mocker.patch.object(app_phase.clipboard, "clear_copied_token")
    tray = QSystemTrayIcon()
    app_phase.install(ctx, tray, Phase("SETUP", 8080))
    write_launcher_state(settings_dir, state, port=None if state == "STARTING" else 8081)
    app_phase.tick(ctx, tray)
    assert clear.called is cleared
