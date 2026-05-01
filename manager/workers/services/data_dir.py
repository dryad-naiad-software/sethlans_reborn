# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager data-directory resolver.

Lifted out of the deleted ``sethlans_manager.middleware.setup_gate``
during Spec 2 cluster B1 (FR-DEL4).  ``heartbeat`` and
``status_public`` still need this resolver for sentinel reads, so it
lives here as a small shared helper.
"""

from __future__ import annotations

import os
from pathlib import Path

from shared.frozen_paths import get_data_dir, is_frozen


def get_manager_data_dir() -> Path:
    """Return the manager data directory.

    In frozen builds, the OS-appropriate data dir is used.  In non-frozen
    runs (dev + tests), ``settings.BASE_DIR`` is the default but can be
    overridden via ``SETHLANS_MANAGER_DATA_DIR`` so the E2E harness can
    anchor sentinel reads at the same per-test tmp tree it writes to
    (issue #137).
    """
    if is_frozen():
        return get_data_dir("manager")
    override = os.environ.get("SETHLANS_MANAGER_DATA_DIR")
    if override:
        return Path(override)
    from django.conf import settings
    return settings.BASE_DIR
