# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build the tray menu for the phase, and follow the launcher (FR-LN-4).

:func:`tick` runs on the tray's state QTimer (GUI thread): it reads the
phase (``phase.read_phase``: ``launcher_state.json`` and
``setup_finished()``), and when it changed shows the change's
notification and installs a freshly built menu (leaving ``SETUP`` also
clears a copied setup token from the clipboard); then it refreshes the
sections in place (in-flight greying, the manager counts).

Sections per phase:

* ``SETUP``: :class:`~shared.tray.menu_setup.SetupSection`;
* ``RUNNING`` in a mode: the manager section, the worker section, or
  both (a separator between), with the launcher's reason on top;
* anything else (``STARTING``, ``STOPPING``, ``FAILED``, ``RUNNING``
  with no readable mode):
  :class:`~shared.tray.menu_setup.StatusSection`.

No QAction is reused across a rebuild. ``ctx`` is duck-typed (see
``app._TrayContext``) so tests can pass a simple stand-in.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from shared.tray import clipboard, icons
from shared.tray import launcher_state as ls
from shared.tray import phase as phase_mod
from shared.tray.menu_manager import ManagerSection
from shared.tray.menu_setup import SetupSection, StatusSection
from shared.tray.menu_universal import add_label, reason_line
from shared.tray.menu_worker import WorkerSection
from shared.tray.phase import Phase

logger = logging.getLogger(__name__)


def build_sections(ctx, phase: Phase) -> list:
    """The menu sections for ``phase`` (module doc), top to bottom."""
    common = dict(quit_requested_flag=ctx.quit_flag, notify=ctx.notify)
    if phase.state == ls.SETUP:
        return [SetupSection(ctx.settings_dir, phase, **common)]
    if not (phase.manager or phase.worker):
        return [StatusSection(ctx.settings_dir, phase, **common)]
    sections = []
    if phase.manager:
        sections.append(ManagerSection(ctx.settings_dir, phase.port, get_snapshot=ctx.current_snapshot, **common))
    if phase.worker:
        sections.append(WorkerSection(ctx.settings_dir, worker_only=not phase.manager, **common))
    return sections


def build_root_menu(sections: list, phase: Phase) -> QMenu:
    """One QMenu from the sections' actions; the ``RUNNING`` reason on top."""
    root = QMenu()
    if phase.state == ls.RUNNING and phase.reason and (phase.manager or phase.worker):
        add_label(root, reason_line(phase.reason))
        root.addSeparator()
    for index, section in enumerate(sections):
        if index:
            root.addSeparator()
        for action in section.build_qmenu(parent=root).actions():
            root.addAction(action)
    return root


def install(ctx, tray: QSystemTrayIcon, phase: Phase) -> None:
    """Build the sections and menu for ``phase`` and give the menu to the tray."""
    sections = build_sections(ctx, phase)
    menu = build_root_menu(sections, phase)
    tray.setContextMenu(menu)
    # The old menu is closed, and kept until the next rebuild, so it is
    # never deleted while it is open or still the tray's.
    old = getattr(ctx, "menu", None)
    if old is not None:
        old.hide()
    ctx.previous_menu = old
    ctx.phase, ctx.sections, ctx.menu = phase, sections, menu
    refresh_icon(ctx, tray)


def refresh_icon(ctx, tray: QSystemTrayIcon) -> None:
    try:
        manager = ctx.current_snapshot().state if ctx.phase.manager else None
        worker = "idle" if ctx.phase.worker else None
        tray.setIcon(QIcon(icons.get_icon(manager, worker)))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to refresh tray icon")


def refresh_sections(ctx) -> None:
    for section in ctx.sections:
        try:
            section.refresh()
        except Exception:  # noqa: BLE001
            logger.exception("%s.refresh failed", type(section).__name__)


def tick(ctx, tray: QSystemTrayIcon) -> None:
    """Follow the launcher: rebuild on a phase change, then refresh. Never raises."""
    try:
        nxt = phase_mod.read_phase(ctx.settings_dir)
        if nxt != ctx.phase:
            logger.info("Tray phase: %s -> %s", ctx.phase.state, nxt.state)
            notice = phase_mod.edge_notice(ctx.phase, nxt)
            if ctx.phase.state == ls.SETUP and nxt.state != ls.SETUP:
                clipboard.clear_copied_token()  # the token is no longer good
            install(ctx, tray, nxt)
            if notice is not None:
                ctx.notify(notice)
        refresh_sections(ctx)
    except Exception:  # noqa: BLE001 - keep the timer alive
        logger.exception("Tray state tick failed")
