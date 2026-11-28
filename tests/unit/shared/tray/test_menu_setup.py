# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``shared/tray/menu_setup.py``: the tray menus outside RUNNING (FR-LN-4, FR-SEC-1,
owner decision 2026-11-28 (a) for FAILED, security L5)."""

from __future__ import annotations

import json
import logging
import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for the tray")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp")

from shared.tray import ipc, menu_setup  # noqa: E402
from shared.tray.menu_setup import SetupSection, StatusSection, read_setup_token  # noqa: E402
from shared.tray.phase import Phase  # noqa: E402
from tests.unit.shared.tray.conftest import action, texts, write_launcher_state  # noqa: E402

TOKEN = "Abc_def-0123456789abcdefghijklmnopqrstuvwxy"  # token_urlsafe(32) shape, 43 chars


def write_token(settings_dir, data: bytes):
    (settings_dir / "setup").mkdir(exist_ok=True)
    (settings_dir / "setup" / "setup_token").write_bytes(data)


@pytest.fixture
def notices():
    return []


def setup_menu(settings_dir, notices, port=8080, reason=None):
    section = SetupSection(settings_dir, Phase("SETUP", port, reason), threading.Event(), notices.append)
    return section, section.build_qmenu()


@pytest.mark.parametrize(("reason", "head"), [
    (None, ["Sethlans setup is open"]),
    ("The database could not be prepared", ["Sethlans setup is open", "The database could not be prepared"]),
])
def test_setup_menu_items(qapp, settings_dir, notices, reason, head):
    """FR-LN-4 SETUP: Open Setup, Copy Setup Token, View Logs, Quit (+ About); the
    last failed start's reason (FR-LN-6) as a line; no Reset (L5)."""
    _, menu = setup_menu(settings_dir, notices, reason=reason)
    assert texts(menu) == head + ["---", "Open Setup", "Copy Setup Token", "View Logs",
                                  "About Sethlans", "---", "Quit Sethlans"]


@pytest.mark.parametrize(("port", "opened"), [(8083, ["https://localhost:8083/setup/"]), (None, [])])
def test_open_setup_uses_the_launchers_port(qapp, settings_dir, notices, mocker, port, opened):
    """FR-LN-3: Open Setup goes to the port the launcher chose; greyed out without one."""
    browser = mocker.patch.object(menu_setup.webbrowser, "open")
    _, menu = setup_menu(settings_dir, notices, port=port)
    assert action(menu, "Open Setup").isEnabled() is (port is not None)
    action(menu, "Open Setup").trigger()
    assert [c.args[0] for c in browser.call_args_list] == opened


def test_copy_token_copies_on_click_without_logging_it(qapp, settings_dir, notices, mocker, caplog):
    """FR-SEC-1: Copy Setup Token reads setup/setup_token when clicked; never logged."""
    write_launcher_state(settings_dir, "SETUP", port=8080)
    write_token(settings_dir, TOKEN.encode() + b"\n")
    copy = mocker.patch.object(menu_setup, "copy_token_to_clipboard", return_value=True)
    clear_later = mocker.patch.object(menu_setup, "clear_token_later")
    _, menu = setup_menu(settings_dir, notices)
    assert copy.call_count == 0  # nothing is copied by building or showing the menu
    with caplog.at_level(logging.DEBUG):
        action(menu, "Copy Setup Token").trigger()
    copy.assert_called_once_with(TOKEN)
    clear_later.assert_called_once_with(TOKEN)  # security L2: it does not stay there
    assert [n.message for n in notices] == ["Setup token copied to clipboard."]
    assert all(TOKEN not in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize(("state", "token", "notice"), [
    pytest.param("STARTING", TOKEN.encode(), [], id="setup-closed-since"),
    pytest.param("SETUP", None, ["The setup token is not available."], id="no-token"),
    pytest.param("SETUP", b"short", ["The setup token is not available."], id="bad-shape"),
])
def test_copy_token_only_while_setup_is_open(qapp, settings_dir, notices, mocker, state, token, notice):
    """FR-SEC-1 / 6c: the token is copied only while the launcher is in SETUP and the file holds a token."""
    write_launcher_state(settings_dir, state, port=8080)
    if token is not None:
        write_token(settings_dir, token)
    copy = mocker.patch.object(menu_setup, "copy_token_to_clipboard", return_value=True)
    _, menu = setup_menu(settings_dir, notices)
    action(menu, "Copy Setup Token").trigger()
    assert copy.call_count == 0
    assert [n.message for n in notices] == notice


@pytest.mark.parametrize("data", [
    pytest.param(b"x" * 2000, id="oversized"),
    pytest.param("é".encode() * 20, id="not-ascii"),
    pytest.param(b"has space " * 4, id="not-token-shaped"),
])
def test_read_setup_token_refuses_anything_but_a_token(settings_dir, data):
    """FR-SEC-1: only a token-shaped value ever reaches the clipboard."""
    write_token(settings_dir, data)
    assert read_setup_token(settings_dir) == ""


def test_failed_menu(qapp, settings_dir, notices):
    """Owner decision 2026-11-28 (a): FAILED is the reason, View Logs, Reset setup... and Quit."""
    section = StatusSection(settings_dir, Phase("FAILED", reason="The database is down"),
                            threading.Event(), notices.append)
    menu = section.build_qmenu()
    assert texts(menu) == ["Sethlans could not start", "The database is down", "---", "View Logs",
                           "Reset setup...", "About Sethlans", "---", "Quit Sethlans"]


@pytest.mark.parametrize(("state", "has_reset"), [
    ("STARTING", False), ("STOPPING", False), ("RUNNING", True),
])
def test_status_menu_offers_reset_only_where_the_launcher_acts_on_it(qapp, settings_dir, state, has_reset):
    """Security L5: Reset in RUNNING (here with no readable mode) and FAILED only."""
    section = StatusSection(settings_dir, Phase(state), threading.Event())
    menu = section.build_qmenu()
    assert ("Reset setup..." in texts(menu)) is has_reset
    assert "Copy Setup Token" not in texts(menu)


def test_reset_opens_the_confirm_dialog(qapp, settings_dir, notices, mocker):
    """FR-RS-1: Reset setup... asks first (the dialog sends the request)."""
    ask = mocker.patch.object(menu_setup.reset_confirm, "ask_reset")
    section = StatusSection(settings_dir, Phase("FAILED"), threading.Event(), notices.append)
    menu = section.build_qmenu()
    action(menu, "Reset setup...").trigger()
    ask.assert_called_once_with(settings_dir, notices.append)


def test_quit_asks_the_launcher_to_quit_everything(qapp, settings_dir, monkeypatch):
    """FR-LN-4: Quit from the setup and status menus stops all of Sethlans (target all)."""
    monkeypatch.setenv(ipc.ENV_SECRET, "s")
    flag = threading.Event()
    section = StatusSection(settings_dir, Phase("STARTING"), flag)
    menu = section.build_qmenu()
    action(menu, "Quit Sethlans").trigger()
    payload = json.loads((settings_dir / ipc.MARKER_QUIT).read_text(encoding="utf-8"))
    assert (payload["action"], payload["target"], flag.is_set()) == ("quit", "all", True)
    section.refresh()
    assert action(menu, "Quit Sethlans").isEnabled() is False  # greyed while in flight


def test_reset_greyed_while_a_reset_is_in_flight(qapp, settings_dir):
    """FR-RS-1: a second Reset is not offered while the first marker waits."""
    section = StatusSection(settings_dir, Phase("RUNNING"), threading.Event())
    menu = section.build_qmenu()
    (settings_dir / ipc.MARKER_RESET).write_text("{}", encoding="utf-8")
    section.refresh()
    assert action(menu, "Reset setup...").isEnabled() is False


def test_view_logs_opens_the_launcher_log_folder(qapp, settings_dir, mocker):
    """FR-LN-4: View Logs opens <settings>/logs (launcher.log and the start logs)."""
    opened = mocker.patch("shared.tray.menu_manager_helpers.open_path", return_value=True)
    section = StatusSection(settings_dir, Phase("FAILED"), threading.Event())
    menu = section.build_qmenu()
    action(menu, "View Logs").trigger()
    opened.assert_called_once_with(settings_dir / "logs")
