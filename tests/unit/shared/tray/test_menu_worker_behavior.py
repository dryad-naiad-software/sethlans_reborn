# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Behavioral unit tests for ``shared/tray/menu_worker.py`` (FR-2, NFR-1).

Covers dynamic behavior: the quit-enabled predicate + marker gating,
``refresh`` reacting to state + marker flips, click handlers
(``on_open_worker_status``, ``on_quit_sethlans``, ``on_about``), the
``rebuild`` alias, and QAction.triggered plumbing end-to-end.

Construction + QMenu-shape coverage lives in
``test_menu_worker_structure.py``.  This split keeps each file under
the 300-line Python ceiling (CLAUDE.md).  Shared fixtures are in
``conftest.py``.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for menu_worker")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from shared.tray import menu_worker as qmw  # noqa: E402
from shared.tray.menu_worker import WorkerSection  # noqa: E402


# ------------------------------------------------------------------ #
# quit_sethlans_enabled predicate
# ------------------------------------------------------------------ #

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

    def test_true_when_marker_absent(self, worker_section):
        assert worker_section.quit_sethlans_enabled() is True


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

    def test_accepts_and_ignores_snapshot_arg(self, worker_section):
        worker_section.build_qmenu()
        worker_section.refresh(object())
        worker_section.refresh(snapshot=object())

    def test_toggles_quit_enabled_off(self, worker_section, mocker):
        worker_section.build_qmenu()
        assert worker_section._act_quit.isEnabled() is True
        # Flip the global marker_exists mock to True.
        qmw.ipc.marker_exists.return_value = True
        worker_section.refresh()
        assert worker_section._act_quit.isEnabled() is False

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

    def test_reflects_header_state_change(
        self, worker_section, worker_state_holder,
    ):
        worker_state_holder["state"] = "idle"
        worker_section.build_qmenu()
        assert worker_section._act_header.text() == "[Worker] Idle"
        worker_state_holder["state"] = "rendering"
        worker_section.refresh()
        assert worker_section._act_header.text() == "[Worker] Rendering"

    def test_build_qmenu_invokes_refresh_once(
        self, worker_section, worker_state_holder,
    ):
        worker_state_holder["state"] = "yielding"
        worker_section.build_qmenu()
        assert worker_section._act_header.text() == (
            "[Worker] Yielding (finishing current frame)"
        )


# ------------------------------------------------------------------ #
# Click handlers
# ------------------------------------------------------------------ #

class TestClickHandlers:

    def test_on_open_worker_status_default_host_port(
        self, worker_section, mocker,
    ):
        open_spy = mocker.patch.object(qmw.webbrowser, "open")
        worker_section.on_open_worker_status()
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
        self, worker_section, mocker,
    ):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        worker_section.on_quit_sethlans()
        spy.assert_called_once_with(worker_section.data_dir, target="all")

    def test_on_quit_sethlans_tolerates_ipc_exception(
        self, worker_section, mocker,
    ):
        mocker.patch.object(
            qmw.ipc, "request_quit", side_effect=RuntimeError("boom"),
        )
        worker_section.on_quit_sethlans()  # must not raise

    def test_on_about_invokes_show_dialog_with_menu_parent(
        self, worker_section_with_about, mocker,
    ):
        spy = mocker.patch.object(qmw, "show_about_dialog")
        worker_section_with_about.build_qmenu()
        worker_section_with_about.on_about()
        spy.assert_called_once()
        assert spy.call_args.args[0] is worker_section_with_about._menu


# ------------------------------------------------------------------ #
# rebuild — alias for build_qmenu
# ------------------------------------------------------------------ #

class TestRebuildAlias:

    def test_rebuild_returns_qmenu(self, worker_section):
        from PySide6.QtWidgets import QMenu
        assert isinstance(worker_section.rebuild(), QMenu)

    def test_rebuild_delegates_to_build_qmenu(self, worker_section, mocker):
        spy = mocker.spy(worker_section, "build_qmenu")
        worker_section.rebuild()
        spy.assert_called_once()


# ------------------------------------------------------------------ #
# QAction.triggered wiring
# ------------------------------------------------------------------ #

class TestTriggeredWiring:

    def test_open_status_triggered_invokes_handler(
        self, worker_section, mocker,
    ):
        spy = mocker.patch.object(
            WorkerSection, "on_open_worker_status", autospec=True,
        )
        worker_section.build_qmenu()
        worker_section._act_open_status.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is worker_section

    def test_quit_triggered_invokes_handler(self, worker_section, mocker):
        spy = mocker.patch.object(
            WorkerSection, "on_quit_sethlans", autospec=True,
        )
        worker_section.build_qmenu()
        worker_section._act_quit.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is worker_section

    def test_about_triggered_invokes_handler(
        self, worker_section_with_about, mocker,
    ):
        spy = mocker.patch.object(
            WorkerSection, "on_about", autospec=True,
        )
        worker_section_with_about.build_qmenu()
        worker_section_with_about._act_about.trigger()
        assert spy.call_count == 1
        assert spy.call_args.args[0] is worker_section_with_about

    def test_open_status_triggered_reaches_webbrowser(
        self, worker_section, mocker,
    ):
        open_spy = mocker.patch.object(qmw.webbrowser, "open")
        worker_section.build_qmenu()
        worker_section._act_open_status.trigger()
        open_spy.assert_called_once_with("http://127.0.0.1:8081/")

    def test_quit_triggered_reaches_ipc_request_quit(
        self, worker_section, mocker,
    ):
        spy = mocker.patch.object(qmw.ipc, "request_quit")
        worker_section.build_qmenu()
        worker_section._act_quit.trigger()
        spy.assert_called_once_with(worker_section.data_dir, target="all")
