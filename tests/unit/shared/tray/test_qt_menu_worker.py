# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/qt_menu_worker.py`` (FR-2, NFR-1).

Covers ``WorkerSection`` QMenu construction, dynamic header text,
quit-enabled marker gating, click-handler wiring, and
triggered-signal plumbing.  Collaborators (``webbrowser``, ``ipc``,
``show_about_dialog``) are mocked -- no real browser opens, no
filesystem markers are written.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for qt_menu_worker")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray import qt_menu_worker as qmw  # noqa: E402
from shared.tray.qt_menu_worker import WorkerSection  # noqa: E402


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture
def state_holder():
    holder = {"state": "idle"}
    holder["get"] = lambda: holder["state"]
    return holder


@pytest.fixture
def section(qapp, tmp_path, state_holder, mocker):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Default: no quit marker exists.
    mocker.patch.object(qmw.ipc, "marker_exists", return_value=False)
    return WorkerSection(
        data_dir=data_dir,
        quit_requested_flag=threading.Event(),
        get_worker_state=state_holder["get"],
    )


@pytest.fixture
def section_with_about(qapp, tmp_path, state_holder, mocker):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(qmw.ipc, "marker_exists", return_value=False)
    return WorkerSection(
        data_dir=data_dir,
        quit_requested_flag=threading.Event(),
        include_about=True,
        get_worker_state=state_holder["get"],
    )


# ------------------------------------------------------------------ #
# Constructor
# ------------------------------------------------------------------ #

class TestConstructor:

    def test_stores_all_args(self, qapp, tmp_path):
        flag = threading.Event()
        getter = lambda: "idle"  # noqa: E731
        sec = WorkerSection(
            data_dir=tmp_path / "d", quit_requested_flag=flag,
            include_about=True, worker_host="example", worker_port=9999,
            get_worker_state=getter,
        )
        assert sec.data_dir == tmp_path / "d"
        assert sec.quit_flag is flag
        assert sec.include_about is True
        assert sec.host == "example"
        assert sec.port == 9999
        assert sec._get_state is getter
        assert isinstance(sec.data_dir, Path)

    def test_defaults(self, qapp, tmp_path):
        sec = WorkerSection(
            data_dir=tmp_path / "d",
            quit_requested_flag=threading.Event(),
        )
        assert sec.include_about is False
        assert sec.host == "127.0.0.1"
        assert sec.port == 8081
        assert sec._get_state() == "idle"
        # Action slots start unset.
        for attr in ("_menu", "_act_header", "_act_open_status",
                     "_act_quit", "_act_about"):
            assert getattr(sec, attr) is None, attr


# ------------------------------------------------------------------ #
# build_qmenu — structure
# ------------------------------------------------------------------ #

class TestBuildQMenuWithoutAbout:

    def test_returns_qmenu(self, section):
        assert isinstance(section.build_qmenu(), QMenu)

    def test_action_order_and_separator(self, section):
        menu = section.build_qmenu()
        actions = menu.actions()
        assert len(actions) == 4
        assert actions[0] is section._act_header
        assert not actions[0].isSeparator()
        assert actions[1].isSeparator()
        assert actions[2] is section._act_open_status
        assert actions[2].text() == "Open Sethlans Status"
        assert actions[3] is section._act_quit
        assert actions[3].text() == "Quit Sethlans"

    def test_about_action_not_created(self, section):
        section.build_qmenu()
        assert section._act_about is None

    def test_header_is_disabled(self, section):
        section.build_qmenu()
        assert section._act_header.isEnabled() is False

    def test_menu_reference_stored(self, section):
        menu = section.build_qmenu()
        assert section._menu is menu

    def test_build_qmenu_idempotent_rebuild(self, section):
        first = section.build_qmenu()
        second = section.build_qmenu()
        assert first is not second
        assert section._menu is second


class TestBuildQMenuWithAbout:

    def test_about_added_after_quit(self, section_with_about):
        menu = section_with_about.build_qmenu()
        actions = menu.actions()
        assert len(actions) == 5
        assert actions[3] is section_with_about._act_quit
        assert actions[4] is section_with_about._act_about
        assert actions[4].text() == "About Sethlans"
        assert actions[4].isEnabled() is True


# ------------------------------------------------------------------ #
# header_text + quit_sethlans_enabled
# ------------------------------------------------------------------ #

class TestHeaderText:

    @pytest.mark.parametrize("state, expected", [
        ("rendering", "[Worker] Rendering"),
        ("yielding", "[Worker] Yielding (finishing current frame)"),
        ("yielded", "[Worker] Yielded (waiting for idle)"),
        ("idle", "[Worker] Idle"),
        (None, "[Worker] None"),
    ])
    def test_state_mapping(self, section, state_holder, state, expected):
        state_holder["state"] = state
        assert section.header_text() == expected


class TestQuitEnabledPredicate:

    def test_false_when_marker_exists(self, qapp, tmp_path, mocker):
        m = mocker.patch.object(
            qmw.ipc, "marker_exists", return_value=True,
        )
        sec = WorkerSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        assert sec.quit_sethlans_enabled() is False
        m.assert_called_once_with(tmp_path, qmw.ipc.MARKER_QUIT)

    def test_true_when_marker_absent(self, section):
        assert section.quit_sethlans_enabled() is True


# ------------------------------------------------------------------ #
# refresh
# ------------------------------------------------------------------ #

class TestRefresh:

    def test_refresh_before_build_is_noop(self, qapp, tmp_path):
        sec = WorkerSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        sec.refresh()  # must not raise

    def test_accepts_and_ignores_snapshot_arg(self, section):
        section.build_qmenu()
        section.refresh(object())
        section.refresh(snapshot=object())

    def test_toggles_quit_enabled_off(self, section, mocker):
        section.build_qmenu()
        assert section._act_quit.isEnabled() is True
        # Flip the global marker_exists mock to True.
        qmw.ipc.marker_exists.return_value = True
        section.refresh()
        assert section._act_quit.isEnabled() is False

    def test_toggles_quit_enabled_on(self, qapp, tmp_path, mocker):
        mocker.patch.object(qmw.ipc, "marker_exists", return_value=True)
        sec = WorkerSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
        )
        sec.build_qmenu()
        assert sec._act_quit.isEnabled() is False
        qmw.ipc.marker_exists.return_value = False
        sec.refresh()
        assert sec._act_quit.isEnabled() is True

    def test_reflects_header_state_change(self, section, state_holder):
        state_holder["state"] = "idle"
        section.build_qmenu()
        assert section._act_header.text() == "[Worker] Idle"
        state_holder["state"] = "rendering"
        section.refresh()
        assert section._act_header.text() == "[Worker] Rendering"

    def test_build_qmenu_invokes_refresh_once(self, section, state_holder):
        state_holder["state"] = "yielding"
        section.build_qmenu()
        assert section._act_header.text() == (
            "[Worker] Yielding (finishing current frame)"
        )


# ------------------------------------------------------------------ #
# Click handlers
# ------------------------------------------------------------------ #

class TestClickHandlers:

    def test_on_open_worker_status_default_host_port(self, section, mocker):
        open_spy = mocker.patch.object(qmw.webbrowser, "open")
        section.on_open_worker_status()
        open_spy.assert_called_once_with("http://127.0.0.1:8081/")

    def test_on_open_worker_status_custom_host_port(
        self, qapp, tmp_path, mocker,
    ):
        sec = WorkerSection(
            data_dir=tmp_path,
            quit_requested_flag=threading.Event(),
            worker_host="host.example",
            worker_port=9100,
        )
        open_spy = mocker.patch.object(qmw.webbrowser, "open")
        sec.on_open_worker_status()
        open_spy.assert_called_once_with("http://host.example:9100/")

    def test_on_quit_sethlans_calls_ipc_request_quit_all(
        self, section, mocker,
    ):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        section.on_quit_sethlans()
        spy.assert_called_once_with(section.data_dir, target="all")

    def test_on_quit_sethlans_tolerates_ipc_exception(self, section, mocker):
        mocker.patch.object(
            qmw.ipc, "request_quit", side_effect=RuntimeError("boom"),
        )
        section.on_quit_sethlans()  # must not raise

    def test_on_about_invokes_show_dialog_with_menu_parent(
        self, section_with_about, mocker,
    ):
        spy = mocker.patch.object(qmw, "show_about_dialog")
        section_with_about.build_qmenu()
        section_with_about.on_about()
        spy.assert_called_once()
        assert spy.call_args.args[0] is section_with_about._menu


# ------------------------------------------------------------------ #
# rebuild — alias for build_qmenu
# ------------------------------------------------------------------ #

class TestRebuildAlias:

    def test_rebuild_returns_qmenu(self, section):
        assert isinstance(section.rebuild(), QMenu)

    def test_rebuild_delegates_to_build_qmenu(self, section, mocker):
        spy = mocker.spy(section, "build_qmenu")
        section.rebuild()
        spy.assert_called_once()


# ------------------------------------------------------------------ #
# QAction.triggered wiring
# ------------------------------------------------------------------ #

class TestTriggeredWiring:

    def test_open_status_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(
            WorkerSection, "on_open_worker_status", autospec=True,
        )
        section.build_qmenu()
        section._act_open_status.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is section

    def test_quit_triggered_invokes_handler(self, section, mocker):
        spy = mocker.patch.object(
            WorkerSection, "on_quit_sethlans", autospec=True,
        )
        section.build_qmenu()
        section._act_quit.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is section

    def test_about_triggered_invokes_handler(
        self, section_with_about, mocker,
    ):
        spy = mocker.patch.object(
            WorkerSection, "on_about", autospec=True,
        )
        section_with_about.build_qmenu()
        section_with_about._act_about.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is section_with_about

    def test_open_status_triggered_reaches_webbrowser(
        self, section, mocker,
    ):
        open_spy = mocker.patch.object(qmw.webbrowser, "open")
        section.build_qmenu()
        section._act_open_status.trigger()
        open_spy.assert_called_once_with("http://127.0.0.1:8081/")

    def test_quit_triggered_reaches_ipc_request_quit(self, section, mocker):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        section.build_qmenu()
        section._act_quit.trigger()
        spy.assert_called_once_with(section.data_dir, target="all")
