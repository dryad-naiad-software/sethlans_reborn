# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Structural unit tests for ``shared/tray/menu_worker.py`` (FR-2, NFR-1).

Covers construction + QMenu shape: ``WorkerSection`` fields after
``__init__``, the action list returned by ``build_qmenu``, the order
of those actions, the about-action gating by ``include_about``, and
the header-text mapping from worker state.

Behavioral coverage (refresh, click handlers, quit-gate predicate,
QAction.triggered wiring) lives in ``test_menu_worker_behavior.py``.
This split keeps each file under the 300-line Python ceiling
(CLAUDE.md).  Shared fixtures are in ``conftest.py``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for menu_worker")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray.menu_worker import WorkerSection  # noqa: E402


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

    def test_returns_qmenu(self, worker_section):
        assert isinstance(worker_section.build_qmenu(), QMenu)

    def test_action_order_and_separator(self, worker_section):
        menu = worker_section.build_qmenu()
        actions = menu.actions()
        assert len(actions) == 4
        assert actions[0] is worker_section._act_header
        assert not actions[0].isSeparator()
        assert actions[1].isSeparator()
        assert actions[2] is worker_section._act_open_status
        assert actions[2].text() == "Open Sethlans Status"
        assert actions[3] is worker_section._act_quit
        assert actions[3].text() == "Quit Sethlans"

    def test_about_action_not_created(self, worker_section):
        worker_section.build_qmenu()
        assert worker_section._act_about is None

    def test_header_is_disabled(self, worker_section):
        worker_section.build_qmenu()
        assert worker_section._act_header.isEnabled() is False

    def test_menu_reference_stored(self, worker_section):
        menu = worker_section.build_qmenu()
        assert worker_section._menu is menu

    def test_build_qmenu_idempotent_rebuild(self, worker_section):
        first = worker_section.build_qmenu()
        second = worker_section.build_qmenu()
        assert first is not second
        assert worker_section._menu is second


class TestBuildQMenuWithAbout:

    def test_about_added_after_quit(self, worker_section_with_about):
        menu = worker_section_with_about.build_qmenu()
        actions = menu.actions()
        assert len(actions) == 5
        assert actions[3] is worker_section_with_about._act_quit
        assert actions[4] is worker_section_with_about._act_about
        assert actions[4].text() == "About Sethlans"
        assert actions[4].isEnabled() is True


# ------------------------------------------------------------------ #
# header_text
# ------------------------------------------------------------------ #

class TestHeaderText:

    @pytest.mark.parametrize("state, expected", [
        ("rendering", "[Worker] Rendering"),
        ("yielding", "[Worker] Yielding (finishing current frame)"),
        ("yielded", "[Worker] Yielded (waiting for idle)"),
        ("idle", "[Worker] Idle"),
        (None, "[Worker] None"),
    ])
    def test_state_mapping(
        self, worker_section, worker_state_holder, state, expected,
    ):
        worker_state_holder["state"] = state
        assert worker_section.header_text() == expected
