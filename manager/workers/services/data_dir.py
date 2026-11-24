# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager data-directory resolver.

Lifted out of the deleted ``sethlans_manager.middleware.setup_gate``
during Spec 2 cluster B1 (FR-DEL4).  ``manager_summary`` reads
``manager.ini`` from this folder.
"""

from __future__ import annotations

from pathlib import Path

from shared.frozen_paths import get_data_dir, is_frozen


def get_manager_data_dir() -> Path:
    """Return the manager data directory.

    In frozen builds, the OS-appropriate data dir is used.  In non-frozen
    runs (dev + tests), ``settings.BASE_DIR`` is the default but can be
    overridden via ``SETHLANS_MANAGER_DATA_DIR`` so the E2E harness can
    anchor reads at the same per-test tmp tree it writes to (issue #137).
    """
    if is_frozen():
        return get_data_dir("manager")
    from django.conf import settings
    from sethlans_manager.state_dir import source_data_dir
    return source_data_dir(settings.BASE_DIR)
