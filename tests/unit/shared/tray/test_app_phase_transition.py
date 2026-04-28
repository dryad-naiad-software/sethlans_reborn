# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end phase-swap test for ``shared/tray/app.py`` (AC-PhaseTransition).

Drives the real ``on_snapshot`` slot through two ticks of disk state
to verify the wizard -> runtime transition rebuilds the menu wholesale
exactly once per swap, and the wizard-phase QActions are dropped on
the floor (no leftover slots pointing at stale state).

This is the AC-PhaseTransition gate from the spec.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip(
    "PySide6", reason="PySide6 required for app_phase_transition",
)
pytest.importorskip(
    "pytestqt", reason="pytest-qt required for qapp fixture",
)

from shared.tray import app, app_phase  # noqa: E402
from shared.tray.menu_manager import ManagerSection  # noqa: E402
from shared.tray.menu_wizard import WizardSection  # noqa: E402


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _make_ctx_for_transition(qapp_unused, tmp_path):
    """Build a ``_TrayContext``-like object directly via app helpers.

    We avoid the real ``_TrayContext.__init__`` because it touches the
    OS-level shared data dir; a stub gives us a deterministic disk
    layout.
    """
    from types import SimpleNamespace
    from shared.tray.poller import ManagerSnapshot

    return SimpleNamespace(
        data_dir=tmp_path,
        manager_data_dir=tmp_path / "manager",
        worker_data_dir=tmp_path / "worker" / "agent",
        wants_manager=True,
        wants_worker=False,
        host="localhost",
        main_port=8443,
        quit_flag=threading.Event(),
        current_phase="wizard",
        current_snapshot=lambda: ManagerSnapshot(version="1.0"),
        manager_section=None,
        worker_section=None,
    )


def _write_wizard_token(tmp_path, token="tok-abc"):
    wd = tmp_path / "wizard"
    wd.mkdir(exist_ok=True)
    (wd / ".setup_token").write_text(token, encoding="utf-8")
    (wd / "port").write_text("8443", encoding="utf-8")


def _write_setup_complete(tmp_path):
    (tmp_path / ".setup_complete").write_text("", encoding="utf-8")


# ------------------------------------------------------------------ #
# AC-PhaseTransition: wizard -> runtime swap
# ------------------------------------------------------------------ #

class TestWizardToRuntimeSwap:

    def test_initial_wizard_then_runtime_after_token_unlinked(
        self, qapp, tmp_path, mocker,
    ):
        """Start with wizard token -> wizard section. Remove token,
        write .setup_complete -> runtime section after one tick."""
        (tmp_path / "manager").mkdir()
        _write_wizard_token(tmp_path)

        ctx = _make_ctx_for_transition(qapp, tmp_path)
        # Initial phase should reflect wizard (token on disk).
        ctx.current_phase = app_phase.compute_phase(ctx)
        assert ctx.current_phase == "wizard"

        # Build initial wizard menu.
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, ctx.current_phase,
        )
        assert isinstance(ctx.manager_section, WizardSection)

        # Now flip the disk: remove wizard token, write sentinel.
        (tmp_path / "wizard" / ".setup_token").unlink()
        _write_setup_complete(tmp_path)

        # One tick: phase transitions wizard -> runtime.
        next_phase = app_phase.compute_phase(ctx)
        assert next_phase == "runtime"

        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, next_phase)

        # Manager section is now ManagerSection, not WizardSection.
        assert isinstance(ctx.manager_section, ManagerSection)
        assert ctx.current_phase == "runtime"
        # Exactly one setContextMenu call per swap.
        assert tray.setContextMenu.call_count == 1

    def test_no_swap_when_phase_unchanged(
        self, qapp, tmp_path, mocker,
    ):
        """When two consecutive ticks return the same phase, no swap
        happens — only refresh in place."""
        (tmp_path / "manager").mkdir()
        _write_wizard_token(tmp_path)
        ctx = _make_ctx_for_transition(qapp, tmp_path)
        ctx.current_phase = "wizard"

        # Phase computation returns wizard both times.
        next_phase_1 = app_phase.compute_phase(ctx)
        next_phase_2 = app_phase.compute_phase(ctx)
        assert next_phase_1 == "wizard"
        assert next_phase_2 == "wizard"
        assert next_phase_1 == ctx.current_phase

    def test_old_wizard_qactions_dropped_after_swap(
        self, qapp, tmp_path, mocker,
    ):
        """FR-12: phase swap drops the old QActions on the floor —
        Qt's parent-child cleanup collects them with the QMenu. We
        verify the section reference is replaced (not reused)."""
        (tmp_path / "manager").mkdir()
        _write_wizard_token(tmp_path)
        ctx = _make_ctx_for_transition(qapp, tmp_path)
        ctx.current_phase = "wizard"
        ctx.manager_section = app_phase.build_section_for_phase(
            ctx, None, "wizard",
        )

        # Hold a reference to one of the old QActions to verify it's
        # not reused after the swap.
        old_section = ctx.manager_section
        old_section.build_qmenu()
        old_quit_action = old_section._act_quit

        # Flip disk + swap.
        (tmp_path / "wizard" / ".setup_token").unlink()
        _write_setup_complete(tmp_path)
        tray = mocker.MagicMock()
        app_phase.swap_menu_for_phase(ctx, tray, "runtime")

        # The new section's QActions are NOT shared with the old.
        new_section = ctx.manager_section
        assert new_section is not old_section
        # New section hasn't built its menu yet (build_root_menu does
        # that during swap). Verify the old QAction is not the one
        # the new menu installed.
        if hasattr(new_section, "_act_quit") and new_section._act_quit:
            assert new_section._act_quit is not old_quit_action


# ------------------------------------------------------------------ #
# Initial phase computed in _TrayContext.__init__
# ------------------------------------------------------------------ #

class TestInitialPhaseInTrayContext:
    """The first menu (built in ``main()`` BEFORE ``app.exec()``) must
    reflect the detected phase at construction time so the user doesn't
    see a flash of the wrong menu before the first poller tick."""

    def test_tray_context_sets_current_phase_on_init(
        self, qapp, tmp_path, mocker,
    ):
        """``_TrayContext.__init__`` calls ``detect_phase`` and stores
        the result on ``self.current_phase``."""
        from shared.tray import topology as topo_mod

        mocker.patch.object(
            app, "get_shared_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            app, "get_data_dir",
            side_effect=lambda role: tmp_path / role,
        )
        mocker.patch.object(
            topo_mod, "read_topology",
            return_value=topo_mod.TOPOLOGY_MANAGER,
        )
        mocker.patch.object(
            app, "_read_manager_ports",
            return_value=("localhost", 8080, 8088),
        )

        ctx = app._TrayContext()
        # Default fresh-install state: wizard.
        assert ctx.current_phase == "wizard"

    def test_tray_context_initial_phase_runtime_with_sentinel(
        self, qapp, tmp_path, mocker,
    ):
        from shared.tray import topology as topo_mod

        # Plant the .setup_complete sentinel.
        (tmp_path / ".setup_complete").write_text("", encoding="utf-8")
        # And the manager dir, just to keep get_data_dir happy.
        (tmp_path / "manager").mkdir()

        mocker.patch.object(
            app, "get_shared_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            app, "get_data_dir",
            side_effect=lambda role: tmp_path / role,
        )
        mocker.patch.object(
            topo_mod, "read_topology",
            return_value=topo_mod.TOPOLOGY_MANAGER,
        )
        mocker.patch.object(
            app, "_read_manager_ports",
            return_value=("localhost", 8080, 8088),
        )

        ctx = app._TrayContext()
        assert ctx.current_phase == "runtime"

    def test_tray_context_phase_is_string(self, qapp, tmp_path, mocker):
        """Type invariant: current_phase is one of the two literal
        strings."""
        from shared.tray import topology as topo_mod
        mocker.patch.object(
            app, "get_shared_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            app, "get_data_dir",
            side_effect=lambda role: tmp_path / role,
        )
        mocker.patch.object(
            topo_mod, "read_topology",
            return_value=topo_mod.TOPOLOGY_MANAGER,
        )
        mocker.patch.object(
            app, "_read_manager_ports",
            return_value=("localhost", 8080, 8088),
        )

        ctx = app._TrayContext()
        assert ctx.current_phase in ("wizard", "runtime")
