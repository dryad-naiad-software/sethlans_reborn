# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Record ``.setup_complete`` for topologies the apply pipeline skips.

Issue #210. For manager-bearing topologies the manager's
``apply_pending_setup`` command writes ``<shared data root>/.setup_complete``
(via ``workers.services.sentinel.create_sentinel``) and stays the sole
writer there. ``worker_only`` has no ``pending_setup.json`` and no
apply step, so after a successful wizard hand-off the launcher records
completion itself. It reuses the manager's sentinel writer verbatim
(version-1 JSON, temp file + ``fsync`` + ``os.replace``, 0600 on POSIX)
so there is exactly one on-disk format; readers are
``launcher.main_dispatch._is_setup_complete`` (existence),
``workers.services.sentinel.is_setup_complete`` (truthy
``completed_at``) and the tray's ``menu_manager_helpers``.

``workers.services.sentinel`` is pure stdlib (no Django). Source mode
puts the manager source dir on ``sys.path`` (same pattern as
:func:`launcher.broadcaster_supervisor._load_multicast_broadcaster_cls`);
the frozen launcher bundles the module as a hidden import
(``packaging/pyinstaller/launcher.spec``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from shared.frozen_paths import get_manager_dir, is_frozen
from shared.topology import has_manager, has_worker, normalize_topology

logger = logging.getLogger(__name__)


def _load_sentinel_module():
    """Import the manager's sentinel module with source/frozen tolerance."""
    if not is_frozen():
        manager_dir = get_manager_dir()
        if manager_dir.is_dir() and str(manager_dir) not in sys.path:
            sys.path.insert(0, str(manager_dir))
    from workers.services import sentinel
    return sentinel


def record_setup_complete(data_dir: Path, topology: str) -> bool:
    """Write ``.setup_complete`` for a manager-less topology.

    Call only after the hand-off succeeded: the wizard's ``.wizard_done``
    marker validated and the wizard was torn down. Returns ``True`` when
    a sentinel was written and ``False`` when nothing was written:

    * manager-bearing topologies: ``apply_pending_setup`` owns the
      sentinel; writing here would clobber its record (and mask a
      broken apply from the ``_run_orchestration`` defensive guard);
    * unrecognized topology strings: fail closed so the guard aborts
      rather than marking an unknown install complete;
    * an existing *complete* sentinel is preserved as-is.

    ``OSError`` from the atomic write propagates so the caller can fail
    the hand-off; no partial file is left behind (the writer unlinks
    its temp file).
    """
    if has_manager(topology) or not has_worker(topology):
        return False
    sentinel = _load_sentinel_module()
    data_dir = Path(data_dir)
    if sentinel.is_setup_complete(data_dir):
        logger.info(
            ".setup_complete already present under %s; leaving it", data_dir,
        )
        return False
    sentinel.create_sentinel(
        data_dir=data_dir, topology=topology, checkpoints=[],
    )
    logger.info(
        "Recorded .setup_complete for topology=%r (issue #210)", topology,
    )
    return True


def recorded_topology(data_dir: Path) -> Optional[str]:
    """Canonical topology recorded in ``.setup_complete``, or ``None``.

    ``None`` when the sentinel is absent, unreadable, not version 1,
    lacks a ``topology`` field, or records an unrecognized one. Used by
    normal mode as the fail-closed fallback when ``topology.json`` is
    missing or unrecognized (issue #210).
    """
    try:
        sentinel = _load_sentinel_module()
    except ImportError as exc:
        logger.error("sentinel reader unavailable: %s", exc)
        return None
    data = sentinel.read_sentinel(Path(data_dir))
    if not isinstance(data, dict):
        return None
    return normalize_topology(data.get("topology"))


__all__ = ["record_setup_complete", "recorded_topology"]
