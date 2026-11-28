# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/menu_worker.py``: the RUNNING worker section (FR-LN-4)."""

from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from shared.tray import ipc, menu_worker  # noqa: E402
from shared.tray.menu_worker import WorkerSection  # noqa: E402
from tests.unit.shared.tray.conftest import action, texts  # noqa: E402


@pytest.mark.parametrize(("worker_only", "expected"), [
    (True, ["[Worker] Idle", "---", "Open Sethlans Status", "View Logs", "Reset setup...",
            "About Sethlans", "Quit Sethlans"]),
    (False, ["[Worker] Idle", "---", "Open Sethlans Status", "Quit Sethlans"]),
])
def test_worker_menu_items(qapp, settings_dir, worker_only, expected):
    """FR-LN-4: the worker-only menu gets View Logs and Reset setup...; next to the
    manager section they are the manager section's."""
    section = WorkerSection(settings_dir, threading.Event(), worker_only=worker_only)
    menu = section.build_qmenu()
    assert texts(menu) == expected


def test_status_page_is_https(qapp, settings_dir, mocker):
    """The worker page is served over HTTPS by the worker's Caddy on 8081 (never plain HTTP)."""
    browser = mocker.patch.object(menu_worker.webbrowser, "open")
    section = WorkerSection(settings_dir, threading.Event())
    menu = section.build_qmenu()
    action(menu, "Open Sethlans Status").trigger()
    browser.assert_called_once_with("https://localhost:8081/")


def test_quit_writes_to_the_settings_folder(qapp, settings_dir, monkeypatch):
    """Quit Sethlans stops everything (target all), with the marker in the settings
    folder the launcher reads, whatever the worker's data folder."""
    monkeypatch.setenv(ipc.ENV_SECRET, "s")
    flag = threading.Event()
    section = WorkerSection(settings_dir, flag, worker_only=True)
    menu = section.build_qmenu()
    action(menu, "Quit Sethlans").trigger()
    payload = json.loads((settings_dir / ipc.MARKER_QUIT).read_text(encoding="utf-8"))
    assert (payload["target"], flag.is_set()) == ("all", True)
    section.refresh()
    assert not action(menu, "Quit Sethlans").isEnabled()


def test_reset_opens_the_confirm_dialog(qapp, settings_dir, mocker):
    """FR-RS-1: the worker-only Reset setup... asks first."""
    ask = mocker.patch.object(menu_worker.reset_confirm, "ask_reset")
    section = WorkerSection(settings_dir, threading.Event(), worker_only=True)
    menu = section.build_qmenu()
    action(menu, "Reset setup...").trigger()
    ask.assert_called_once_with(settings_dir, None)
