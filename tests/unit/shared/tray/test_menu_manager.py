# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/menu_manager.py``: the RUNNING manager section (FR-LN-4, FR-RS-1)."""

from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from shared.tray import ipc, menu_manager  # noqa: E402
from shared.tray.menu_manager import ManagerSection  # noqa: E402
from shared.tray.poller import ManagerSnapshot  # noqa: E402
from tests.unit.shared.tray.conftest import action, texts  # noqa: E402

RUNNING = ManagerSnapshot(state="running", workers_online=3, jobs_queued=7, jobs_rendering=2, version="1.2.3")


def build(settings_dir, snap=RUNNING, port=8080, notify=None):
    holder = {"snap": snap}
    section = ManagerSection(settings_dir, port, threading.Event(), lambda: holder["snap"], notify)
    return section, section.build_qmenu(), holder


def test_manager_menu_items(qapp, settings_dir):
    """FR-LN-4 RUNNING: today's manager items plus Reset setup...; no setup-only items."""
    section, menu, _ = build(settings_dir)
    assert texts(menu) == [
        "[Manager] Running", "Workers online: 3", "Jobs queued: 7  |  Rendering: 2", "---",
        "Open Dashboard", "Restart Manager", "View Manager Logs", "Reset setup...", "About Sethlans",
        "---", "Quit Manager", "---", "v1.2.3 -- :8080",
    ]


@pytest.mark.parametrize(("snap", "header", "counts"), [
    (ManagerSnapshot(state="starting"), "[Manager] Starting...", False),
    (ManagerSnapshot(state="error", last_error="urlerror: refused"), "[Manager] Error: urlerror: refused", False),
    (RUNNING, "[Manager] Running", True),
])
def test_refresh_follows_the_poller(qapp, settings_dir, snap, header, counts):
    """FR-LN-4: header and counts follow the manager's loopback status; counts only while it answers."""
    section, menu, holder = build(settings_dir, snap=ManagerSnapshot())
    holder["snap"] = snap
    section.refresh()
    assert menu.actions()[0].text() == header
    assert menu.actions()[1].isVisible() is counts


def test_dashboard_uses_the_launchers_port(qapp, settings_dir, mocker):
    """FR-LN-3: the dashboard address uses the port from launcher_state.json."""
    browser = mocker.patch.object(menu_manager.webbrowser, "open")
    section, menu, _ = build(settings_dir, port=8085)
    action(menu, "Open Dashboard").trigger()
    browser.assert_called_once_with("https://localhost:8085/")


@pytest.mark.parametrize(("marker", "label"), [
    (ipc.MARKER_RESTART, "Restart Manager"),
    (ipc.MARKER_RESET, "Reset setup..."),
    (ipc.MARKER_QUIT, "Quit Manager"),
])
def test_items_greyed_while_their_request_is_in_flight(qapp, settings_dir, marker, label):
    """FR-RS-1 / tray IPC: an item is greyed while its marker waits for the launcher."""
    section, menu, _ = build(settings_dir)
    assert action(menu, label).isEnabled()
    (settings_dir / marker).write_text("{}", encoding="utf-8")
    section.refresh()
    assert not action(menu, label).isEnabled()


def test_reset_opens_the_confirm_dialog(qapp, settings_dir, mocker):
    """FR-RS-1: Reset setup... asks first."""
    ask = mocker.patch.object(menu_manager.reset_confirm, "ask_reset")
    notify = []
    section, menu, _ = build(settings_dir, notify=notify.append)
    action(menu, "Reset setup...").trigger()
    ask.assert_called_once_with(settings_dir, notify.append)


def test_quit_manager_targets_the_manager(qapp, settings_dir, monkeypatch):
    """Quit Manager stops only the manager while a worker runs (launcher.running)."""
    monkeypatch.setenv(ipc.ENV_SECRET, "s")
    section, menu, _ = build(settings_dir)
    action(menu, "Quit Manager").trigger()
    assert json.loads((settings_dir / ipc.MARKER_QUIT).read_text(encoding="utf-8"))["target"] == "manager"


def test_view_manager_logs_opens_the_manager_log(qapp, settings_dir, mocker, monkeypatch, tmp_path):
    """View Manager Logs opens manager.log in the manager's data folder (not <settings>/logs)."""
    monkeypatch.setenv("SETHLANS_MANAGER_DATA_DIR", str(tmp_path / "mgr"))
    opened = mocker.patch("shared.tray.menu_manager_helpers.open_path", return_value=True)
    section, menu, _ = build(settings_dir)
    action(menu, "View Manager Logs").trigger()
    opened.assert_called_once_with(tmp_path / "mgr" / "logs" / "manager.log")
