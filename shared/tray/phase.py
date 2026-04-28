# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tray-menu lifecycle-phase detection (spec FR-1, FR-2).

The tray's context menu must adapt to the launcher's current lifecycle
phase. Two phases are recognized:

* ``"wizard"`` — the standalone setup-wizard subprocess is (or could
  be) running. The tray surfaces wizard-relevant items only.
* ``"runtime"`` — the manager / worker runtime is up. The full
  manager / worker menu is appropriate.

Phase detection is intentionally cheap (two file-existence checks) so
it can run on every poller tick (cadence 2 s) without measurable cost.
The function NEVER raises; any ``OSError`` from a stat / exists call
falls through to the missing-file branch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from shared.tray.menu_manager_helpers import sentinel_exists

logger = logging.getLogger(__name__)


def detect_phase(
    data_dir: Path, manager_data_dir: Path,
) -> Literal["wizard", "runtime"]:
    """Return the current tray-menu phase.

    Returns ``"wizard"`` when either of the following holds:

    1. ``<data_dir>/wizard/.setup_token`` exists (the launcher only
       writes this file during wizard mode and removes it / the whole
       ``<data_dir>/wizard/`` subdir on handoff).
    2. No setup-complete sentinel exists at any accepted location (per
       :func:`shared.tray.menu_manager_helpers.sentinel_exists`).

    Otherwise returns ``"runtime"``.

    Both branches are wrapped so a transient ``OSError`` (permission
    denied, race with file unlink, etc.) cannot escape this function.
    """
    try:
        if (data_dir / "wizard" / ".setup_token").exists():
            return "wizard"
    except OSError:
        # Treat as missing; sentinel branch below decides outcome.
        pass
    try:
        if sentinel_exists(manager_data_dir):
            return "runtime"
    except OSError:
        pass
    return "wizard"
