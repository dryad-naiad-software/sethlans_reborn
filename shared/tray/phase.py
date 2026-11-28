# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The tray's phase: the launcher's state plus the finished setup mode.

The launcher's ``launcher_state.json`` says what state Sethlans is in
(:mod:`shared.tray.launcher_state`); the tray never works the state out
itself. ``setup_finished()`` (FR-SF-4) gives the mode, which decides
the ``RUNNING`` menu's sections, so a worker-only machine gets phases
too. The mode is kept only in ``RUNNING``: no other menu depends on it.

* :func:`read_phase`: both reads; never raises.
* :class:`Phase`: what the menu is built from. A change of any field
  rebuilds the menu.
* :func:`edge_notice`: the notification for a change of phase, if any:
  reaching ``RUNNING``, a new ``RUNNING`` reason (a child that ended),
  and ``FAILED``.

Reset is offered only where the launcher acts on it, ``RUNNING`` and
``FAILED`` (security L5); Copy Setup Token only in ``SETUP``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared import settings_file
from shared.topology import has_manager, has_worker
from shared.tray import launcher_state as ls
from shared.tray.notifications import NotificationEvent

RESET_STATES = frozenset({ls.RUNNING, ls.FAILED})
NOTICE_TITLE = "Sethlans"


@dataclass(frozen=True)
class Phase:
    """The launcher state and, in ``RUNNING`` only, the setup mode."""

    state: str
    port: Optional[int] = None
    reason: Optional[str] = None
    mode: Optional[str] = None

    @property
    def manager(self) -> bool:
        """``RUNNING`` in a mode with the manager."""
        return self.state == ls.RUNNING and has_manager(self.mode)

    @property
    def worker(self) -> bool:
        """``RUNNING`` in a mode with the worker."""
        return self.state == ls.RUNNING and has_worker(self.mode)

    @property
    def offers_reset(self) -> bool:
        return self.state in RESET_STATES


def phase_from(state: ls.LauncherState, mode: Optional[str]) -> Phase:
    """The phase for a launcher state and a ``setup_finished()`` mode."""
    return Phase(state.state, state.port, state.reason, mode if state.state == ls.RUNNING else None)


def read_phase(settings_dir: Optional[Path] = None) -> Phase:
    """Read the launcher state and the setup mode; never raises."""
    return phase_from(ls.read_launcher_state(settings_dir), settings_file.setup_finished())


def edge_notice(prev: Phase, nxt: Phase) -> Optional[NotificationEvent]:
    """The notification for the change ``prev`` -> ``nxt``, or ``None``."""
    if nxt.state == ls.FAILED and prev.state != ls.FAILED:
        return NotificationEvent("Sethlans could not start", nxt.reason or "Open View Logs for details.")
    if nxt.state != ls.RUNNING:
        return None
    if prev.state != ls.RUNNING:
        message = "Sethlans is running." if nxt.reason is None else f"Sethlans is running. {nxt.reason}"
        return NotificationEvent(NOTICE_TITLE, message)
    if nxt.reason is not None and nxt.reason != prev.reason:
        return NotificationEvent(NOTICE_TITLE, nxt.reason)
    return None
