# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Refresh-driven dynamic state tests for ``shared/tray/menu_manager.py``.

Covers ``refresh()`` effects on dynamic text (header / setup / workers
/ jobs / footer), visibility predicates (copy_token / open_wizard /
counts), and enabled predicates (restart / quit).  Companion to
``test_menu_manager_core.py`` (structural / predicate tests) and
``test_menu_manager_handlers.py`` (click handler bodies).  Shared
fixtures come from ``conftest.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for menu_manager")
pytest.importorskip("pytestqt", reason="pytest-qt required for qapp fixture")


# ------------------------------------------------------------------ #
# refresh — dynamic text
# ------------------------------------------------------------------ #

class TestRefreshText:

    def test_header_reflects_running(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(state="running")
        section.refresh()
        assert section._act_header.text() == "[Manager] Running"

    def test_header_reflects_starting(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(state="starting")
        section.refresh()
        assert section._act_header.text() == "[Manager] Starting..."

    def test_header_reflects_stopped(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(state="stopped")
        section.refresh()
        assert section._act_header.text() == "[Manager] Stopped"

    def test_header_reflects_error_with_truncation(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(
            state="error", last_error="x" * 80,
        )
        section.refresh()
        # Only first 40 chars of last_error in the header.
        assert section._act_header.text().startswith("[Manager] Error: ")
        assert section._act_header.text().count("x") == 40

    def test_setup_line_ready_when_sentinel_exists(
        self, section, tmp_path,
    ):
        section.build_qmenu()
        (tmp_path / "manager" / "setup_complete.json").write_text(
            "{}", encoding="utf-8",
        )
        section.refresh()
        assert section._act_setup.text() == "Setup: Ready"

    def test_setup_line_in_progress(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(setup_mode=True)
        section.refresh()
        assert section._act_setup.text() == "Setup: In progress"

    def test_setup_line_needed(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(setup_mode=False)
        section.refresh()
        assert section._act_setup.text() == "Setup: Needed"

    def test_workers_line_reflects_snapshot(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(workers_online=42)
        section.refresh()
        assert section._act_workers.text() == "Workers online: 42"

    def test_jobs_line_reflects_snapshot(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(
            jobs_queued=11, jobs_rendering=4,
        )
        section.refresh()
        assert "Jobs queued: 11" in section._act_jobs.text()
        assert "Rendering: 4" in section._act_jobs.text()

    def test_footer_reflects_version_and_port(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(version="9.9.9")
        section.refresh()
        assert section._act_footer.text() == "v9.9.9 -- :8443"

    def test_footer_uses_question_mark_when_no_version(
        self, section, snapshot_holder, make_snapshot,
    ):
        section.build_qmenu()
        snapshot_holder["snap"] = make_snapshot(version="")
        section.refresh()
        assert section._act_footer.text() == "v? -- :8443"


# ------------------------------------------------------------------ #
# refresh — visibility predicates
# ------------------------------------------------------------------ #

class TestRefreshVisibility:

    def test_copy_token_visible_when_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "setup_token_available", return_value=True,
        )
        section.refresh()
        assert section._act_copy_token.isVisible() is True

    def test_copy_token_hidden_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "setup_token_available", return_value=False,
        )
        section.refresh()
        assert section._act_copy_token.isVisible() is False

    def test_open_wizard_visible_when_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(
            section, "wizard_visible", return_value=True,
        )
        section.refresh()
        assert section._act_open_wizard.isVisible() is True

    def test_open_wizard_hidden_when_predicate_false(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "wizard_visible", return_value=False,
        )
        section.refresh()
        assert section._act_open_wizard.isVisible() is False

    def test_workers_and_jobs_visible_when_counts_visible_true(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "counts_visible", return_value=True,
        )
        section.refresh()
        assert section._act_workers.isVisible() is True
        assert section._act_jobs.isVisible() is True

    def test_workers_and_jobs_hidden_when_counts_visible_false(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(
            section, "counts_visible", return_value=False,
        )
        section.refresh()
        assert section._act_workers.isVisible() is False
        assert section._act_jobs.isVisible() is False


# ------------------------------------------------------------------ #
# refresh — enabled predicates
# ------------------------------------------------------------------ #

class TestRefreshEnabled:

    def test_restart_enabled_reflects_predicate_true(
        self, section, mocker,
    ):
        section.build_qmenu()
        mocker.patch.object(section, "restart_enabled", return_value=True)
        section.refresh()
        assert section._act_restart.isEnabled() is True

    def test_restart_disabled_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "restart_enabled", return_value=False)
        section.refresh()
        assert section._act_restart.isEnabled() is False

    def test_quit_enabled_reflects_predicate_true(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "quit_enabled", return_value=True)
        section.refresh()
        assert section._act_quit.isEnabled() is True

    def test_quit_disabled_when_predicate_false(self, section, mocker):
        section.build_qmenu()
        mocker.patch.object(section, "quit_enabled", return_value=False)
        section.refresh()
        assert section._act_quit.isEnabled() is False
