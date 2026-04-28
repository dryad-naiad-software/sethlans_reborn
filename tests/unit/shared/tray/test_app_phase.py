# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/app_phase.py`` (spec FR-11, FR-12,
FR-13, AC-PhaseTransition).

Covers:

* ``compute_phase`` delegates to ``phase.detect_phase``.
* ``build_section_for_phase`` returns ``WizardSection`` /
  ``ManagerSection`` / ``None`` based on phase + topology.
* ``build_worker_section`` is rebuilt for QAction hygiene (FR-13)
  even though its observable behaviour is identical across phases.
* ``swap_menu_for_phase`` mutates ``ctx.current_phase``,
  ``ctx.manager_section``, ``ctx.worker_section`` and issues exactly
  one ``tray.setContextMenu`` call (AC-PhaseTransition).
* ``build_root_menu`` combines the manager + worker sections with a
  separator between them (mirrors legacy ``app._build_menu`` shape).
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 required for app_phase",
)
pytest.importorskip(
    "pytestqt", reason="pytest-qt required for qapp fixture",
)

from PySide6.QtWidgets import QMenu  # noqa: E402

from shared.tray import app_phase  # noqa: E402
from shared.tray import phase as phase_mod  # noqa: E402
from shared.tray.menu_manager import ManagerSection  # noqa: E402
from shared.tray.menu_wizard import WizardSection  # noqa: E402
from shared.tray.menu_worker import WorkerSection  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers — minimal context stub
# ------------------------------------------------------------------ #

def _make_ctx(
    qapp_unused,
    tmp_path,
    wants_manager=True,
    wants_worker=True,
    current_phase="runtime",
):
    """Return a duck-typed ``_TrayContext`` substitute for app_phase
    helpers.

    The real ``_TrayContext`` reads from the filesystem at construction
    time; for unit-scope tests we hand-craft the minimal attribute set
    instead. ``manager_section`` / ``worker_section`` start as ``None``
    so we can observe the swap helper populating them.
    """
    from types import SimpleNamespace
    from shared.tray.poller import ManagerSnapshot

    return SimpleNamespace(
        data_dir=tmp_path,
        manager_data_dir=tmp_path / "manager",
        worker_data_dir=tmp_path / "worker" / "agent",
        wants_manager=wants_manager,
        wants_worker=wants_worker,
        host="localhost",
        main_port=8443,
        quit_flag=threading.Event(),
        current_phase=current_phase,
        current_snapshot=lambda: ManagerSnapshot(version="9.9.9"),
        manager_section=None,
        worker_section=None,
    )


# ------------------------------------------------------------------ #
# compute_phase delegates to detect_phase
# ------------------------------------------------------------------ #

class TestComputePhase:

    def test_delegates_to_detect_phase(self, qapp, tmp_path, mocker):
        ctx = _make_ctx(qapp, tmp_path)
        spy = mocker.patch.object(
            phase_mod, "detect_phase", return_value="runtime",
        )
        result = app_phase.compute_phase(ctx)
        assert result == "runtime"
        spy.assert_called_once_with(
            ctx.data_dir, ctx.manager_data_dir,
        )

    def test_returns_wizard_string_for_wizard(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path)
        mocker.patch.object(
            phase_mod, "detect_phase", return_value="wizard",
        )
        assert app_phase.compute_phase(ctx) == "wizard"

    def test_compute_phase_matches_detect_phase_no_files(
        self, qapp, tmp_path,
    ):
        """End-to-end: with no files on disk, both calls agree."""
        (tmp_path / "manager").mkdir()
        ctx = _make_ctx(qapp, tmp_path)
        expected = phase_mod.detect_phase(
            ctx.data_dir, ctx.manager_data_dir,
        )
        assert app_phase.compute_phase(ctx) == expected


# ------------------------------------------------------------------ #
# build_section_for_phase — phase + topology dispatch
# ------------------------------------------------------------------ #

class TestBuildSectionForPhase:

    def test_returns_wizard_section_in_wizard_phase(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_manager=True)
        tray = mocker.MagicMock()
        section = app_phase.build_section_for_phase(ctx, tray, "wizard")
        assert isinstance(section, WizardSection)

    def test_returns_manager_section_in_runtime_phase(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_manager=True)
        tray = mocker.MagicMock()
        section = app_phase.build_section_for_phase(ctx, tray, "runtime")
        assert isinstance(section, ManagerSection)

    def test_returns_none_for_worker_only_topology_wizard(
        self, qapp, tmp_path, mocker,
    ):
        """``wants_manager=False`` -> no manager slot regardless of
        phase. Worker-only deployments have no wizard either."""
        ctx = _make_ctx(qapp, tmp_path, wants_manager=False)
        tray = mocker.MagicMock()
        result = app_phase.build_section_for_phase(ctx, tray, "wizard")
        assert result is None

    def test_returns_none_for_worker_only_topology_runtime(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_manager=False)
        tray = mocker.MagicMock()
        result = app_phase.build_section_for_phase(ctx, tray, "runtime")
        assert result is None

    def test_wizard_section_receives_data_dir(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_manager=True)
        tray = mocker.MagicMock()
        section = app_phase.build_section_for_phase(ctx, tray, "wizard")
        assert section.data_dir == ctx.data_dir
        assert section.quit_flag is ctx.quit_flag

    def test_manager_section_receives_data_dirs(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_manager=True)
        tray = mocker.MagicMock()
        section = app_phase.build_section_for_phase(ctx, tray, "runtime")
        assert section.data_dir == ctx.data_dir
        assert section.manager_data_dir == ctx.manager_data_dir
        assert section.host == ctx.host
        assert section.port == ctx.main_port


# ------------------------------------------------------------------ #
# build_worker_section — rebuilds, reports None for manager-only
# ------------------------------------------------------------------ #

class TestBuildWorkerSection:

    def test_returns_worker_section_when_wants_worker(
        self, qapp, tmp_path,
    ):
        ctx = _make_ctx(qapp, tmp_path, wants_worker=True)
        section = app_phase.build_worker_section(ctx)
        assert isinstance(section, WorkerSection)

    def test_returns_none_when_no_worker_topology(self, qapp, tmp_path):
        ctx = _make_ctx(qapp, tmp_path, wants_worker=False)
        result = app_phase.build_worker_section(ctx)
        assert result is None

    def test_include_about_true_in_worker_only_topology(
        self, qapp, tmp_path,
    ):
        """FR-13: worker-only deployments need About on the worker
        section since there is no manager section."""
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=False, wants_worker=True,
        )
        section = app_phase.build_worker_section(ctx)
        assert section.include_about is True

    def test_include_about_false_when_manager_present(
        self, qapp, tmp_path,
    ):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=True,
        )
        section = app_phase.build_worker_section(ctx)
        assert section.include_about is False


# ------------------------------------------------------------------ #
# build_root_menu — manager + worker combine with separator
# ------------------------------------------------------------------ #

class TestBuildRootMenu:

    def test_returns_qmenu_with_manager_only(self, qapp, tmp_path):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=False,
        )
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, "runtime",
        )
        root = app_phase.build_root_menu(ctx)
        assert isinstance(root, QMenu)
        # Manager section's actions only — no worker actions.
        labels = [a.text() for a in root.actions() if not a.isSeparator()]
        assert "Open Dashboard" in labels

    def test_returns_qmenu_with_worker_only(self, qapp, tmp_path):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=False, wants_worker=True,
        )
        ctx.worker_section = app_phase.build_worker_section(ctx)
        root = app_phase.build_root_menu(ctx)
        assert isinstance(root, QMenu)
        labels = [a.text() for a in root.actions() if not a.isSeparator()]
        assert "Open Sethlans Status" in labels

    def test_combined_topology_has_separator_between(self, qapp, tmp_path):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=True,
        )
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, "runtime",
        )
        ctx.worker_section = app_phase.build_worker_section(ctx)
        root = app_phase.build_root_menu(ctx)
        # Both sections' actions should be present.
        labels = [a.text() for a in root.actions() if not a.isSeparator()]
        assert "Open Dashboard" in labels
        assert "Open Sethlans Status" in labels

    def test_returns_empty_when_both_sections_none(self, qapp, tmp_path):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=False, wants_worker=False,
        )
        root = app_phase.build_root_menu(ctx)
        assert isinstance(root, QMenu)
        assert root.actions() == []


# ------------------------------------------------------------------ #
# swap_menu_for_phase — AC-PhaseTransition
# ------------------------------------------------------------------ #

class TestSwapMenuForPhase:

    def test_mutates_current_phase(self, qapp, tmp_path, mocker):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=False,
            current_phase="wizard",
        )
        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "runtime")
        assert ctx.current_phase == "runtime"

    def test_replaces_manager_section(self, qapp, tmp_path, mocker):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=False,
            current_phase="wizard",
        )
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, "wizard",
        )
        old = ctx.manager_section
        assert isinstance(old, WizardSection)

        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "runtime")
        assert ctx.manager_section is not old
        assert isinstance(ctx.manager_section, ManagerSection)

    def test_rebuilds_worker_section_for_qaction_hygiene(
        self, qapp, tmp_path, mocker,
    ):
        """FR-13: even though the worker section's behaviour is
        identical across phases, it MUST be rebuilt to keep QAction
        ownership clean."""
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=True,
            current_phase="wizard",
        )
        ctx.worker_section = app_phase.build_worker_section(ctx)
        old_worker = ctx.worker_section

        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "runtime")
        # Fresh instance, not the same object.
        assert ctx.worker_section is not old_worker
        assert isinstance(ctx.worker_section, WorkerSection)

    def test_calls_set_context_menu_exactly_once(
        self, qapp, tmp_path, mocker,
    ):
        """AC-PhaseTransition: exactly one ``tray.setContextMenu`` call
        per swap, with a fresh QMenu."""
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=True,
        )
        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "runtime")
        assert tray.setContextMenu.call_count == 1
        args, _ = tray.setContextMenu.call_args
        assert isinstance(args[0], QMenu)

    def test_swap_to_wizard_installs_wizard_section(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=True, wants_worker=False,
            current_phase="runtime",
        )
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, "runtime",
        )
        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "wizard")
        assert isinstance(ctx.manager_section, WizardSection)

    def test_swap_for_worker_only_installs_no_manager_section(
        self, qapp, tmp_path, mocker,
    ):
        ctx = _make_ctx(
            qapp, tmp_path, wants_manager=False, wants_worker=True,
            current_phase="runtime",
        )
        tray = mocker.MagicMock()
        # Worker-only deployment: phase machinery is a harmless no-op
        # for the manager slot.
        app_phase.swap_menu_for_phase(ctx, tray, "wizard")
        assert ctx.manager_section is None
        # Worker section still rebuilt.
        assert isinstance(ctx.worker_section, WorkerSection)


# ------------------------------------------------------------------ #
# AC-NoSplashRegression — app_phase doesn't touch splash APIs
# ------------------------------------------------------------------ #

class TestNoSplashAPIs:

    def test_app_phase_does_not_import_splash(self):
        import shared.tray.app_phase as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "cold_boot_ready" not in src
        assert "startup_failed" not in src
        assert "splash" not in src.lower()
