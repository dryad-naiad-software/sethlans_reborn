# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Phase computation + menu rebuild helpers for the PySide6 tray.

Extracted from ``shared/tray/app.py`` to keep that module under the
300-line ceiling (NFR-1). Implements:

* :func:`compute_phase` — thin wrapper that calls
  :func:`shared.tray.phase.detect_phase` against the current
  ``_TrayContext``'s data dirs.
* :func:`build_section_for_phase` — return a fresh ``WizardSection``
  / ``ManagerSection`` instance for the manager slot of the menu,
  appropriate to the supplied phase. ``None`` for worker-only
  topologies.
* :func:`swap_menu_for_phase` — install a freshly-built top-level
  ``QMenu`` on the tray for the supplied phase. Worker section is
  rebuilt for QAction hygiene (FR-13) even though its observable
  behaviour is identical across phases.
* :func:`build_root_menu` — combine a manager-slot section + worker
  section into a single ``QMenu``, mirroring the legacy ``_build_menu``
  layout.

The helpers take a duck-typed ``ctx`` so unit tests can stub
``_TrayContext`` with a ``MagicMock`` whose attributes parallel
``app._TrayContext``.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from shared.tray import notifications
from shared.tray import phase as phase_mod
from shared.tray.menu_manager import ManagerSection
from shared.tray.menu_wizard import WizardSection
from shared.tray.menu_worker import WorkerSection
from shared.tray.notifications import NotificationEvent

logger = logging.getLogger(__name__)


def compute_phase(ctx) -> str:
    """Return the phase string ("wizard" / "runtime") for *ctx*."""
    return phase_mod.detect_phase(ctx.data_dir, ctx.manager_data_dir)


def build_section_for_phase(
    ctx, tray: QSystemTrayIcon, current_phase: str,
):
    """Return a section for the manager slot of the tray menu.

    During ``"wizard"`` phase a :class:`WizardSection` is returned;
    during ``"runtime"`` phase the existing :class:`ManagerSection` is
    returned. Returns ``None`` when the topology has no manager slot
    (``ctx.wants_manager`` is False — i.e. worker-only deployments).
    """
    if not ctx.wants_manager:
        return None

    def _notify(event: NotificationEvent) -> None:
        notifications.dispatch(tray, event)

    if current_phase == "wizard":
        return WizardSection(
            data_dir=ctx.data_dir,
            quit_requested_flag=ctx.quit_flag,
            get_version=lambda: ctx.current_snapshot().version or "?",
            notify=_notify,
        )
    return ManagerSection(
        data_dir=ctx.data_dir,
        manager_data_dir=ctx.manager_data_dir,
        manager_host=ctx.host,
        manager_port=ctx.main_port,
        quit_requested_flag=ctx.quit_flag,
        get_snapshot=ctx.current_snapshot,
        notify=_notify,
    )


def build_worker_section(ctx) -> Optional[WorkerSection]:
    """Construct a fresh WorkerSection or return None.

    A new section is created on phase swap for QAction hygiene
    (FR-12 / FR-13) even though the worker section's behaviour is
    identical across phases.
    """
    if not ctx.wants_worker:
        return None
    worker_only = ctx.wants_worker and not ctx.wants_manager
    return WorkerSection(
        data_dir=ctx.worker_data_dir.parent,
        quit_requested_flag=ctx.quit_flag,
        include_about=worker_only,
    )


def build_root_menu(ctx) -> QMenu:
    """Combine the manager-slot section + worker section into one QMenu.

    Mirrors the legacy ``app._build_menu`` layout: manager section's
    actions, optional separator, worker section's actions. Returns a
    fresh ``QMenu`` each call.
    """
    root = QMenu()
    if ctx.manager_section is not None:
        mgr_menu = ctx.manager_section.build_qmenu(parent=root)
        for action in mgr_menu.actions():
            root.addAction(action)
    if (
        ctx.manager_section is not None
        and ctx.worker_section is not None
    ):
        root.addSeparator()
    if ctx.worker_section is not None:
        worker_menu = ctx.worker_section.build_qmenu(parent=root)
        for action in worker_menu.actions():
            root.addAction(action)
    return root


def swap_menu_for_phase(
    ctx, tray: QSystemTrayIcon, next_phase: str,
) -> None:
    """Build a fresh top-level menu and install via ``setContextMenu``.

    Mutates ``ctx.current_phase`` / ``ctx.manager_section`` /
    ``ctx.worker_section`` in place, then issues exactly one
    ``tray.setContextMenu`` call. The previous menu and its QActions
    are dropped on the floor — Qt parents QActions to the QMenu, so
    destruction collects them. No QAction is reused across phase
    swaps; this is what makes the swap safe (no leftover triggered
    slots pointing at stale state).
    """
    ctx.current_phase = next_phase
    ctx.manager_section = build_section_for_phase(ctx, tray, next_phase)
    ctx.worker_section = build_worker_section(ctx)
    tray.setContextMenu(build_root_menu(ctx))
