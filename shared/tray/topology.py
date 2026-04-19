# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read + validate ``<data_dir>/topology.json``.

Hardened per tray spec FR-5:

* 1 KB size cap.
* Whitelist validation of the ``topology`` key.
* Graceful default (``manager+worker``) on any error, with a WARNING log.
* Never raises — the tray must always start.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TOPOLOGY_MANAGER = "manager"
TOPOLOGY_WORKER = "worker"
TOPOLOGY_BOTH = "manager+worker"

_VALID = {TOPOLOGY_MANAGER, TOPOLOGY_WORKER, TOPOLOGY_BOTH}
_DEFAULT = TOPOLOGY_BOTH
_MAX_BYTES = 1024

# Some repos use the "manager_worker" spelling for filesystem-safety.
# Accept it but normalize to the canonical "manager+worker".
_LEGACY_ALIASES = {
    "manager_worker": TOPOLOGY_BOTH,
}


def _load_raw(path: Path) -> str | None:
    if not path.exists():
        logger.warning("topology.json missing at %s", path)
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning("stat failed on %s: %s", path, exc)
        return None
    if size > _MAX_BYTES:
        logger.warning(
            "topology.json is %d bytes (>%d)", size, _MAX_BYTES,
        )
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("read failed on %s: %s", path, exc)
        return None


def read_topology(data_dir: Path) -> str:
    """Return the tray topology string.

    One of ``"manager"`` / ``"worker"`` / ``"manager+worker"``.
    Falls back to the default on any error.
    """
    raw = _load_raw(data_dir / "topology.json")
    if raw is None:
        return _DEFAULT
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("topology.json malformed: %s", exc)
        return _DEFAULT
    value = data.get("topology") if isinstance(data, dict) else None
    if isinstance(value, str) and value in _LEGACY_ALIASES:
        value = _LEGACY_ALIASES[value]
    if value not in _VALID:
        logger.warning(
            "topology.json has unknown topology=%r; defaulting to %s",
            value, _DEFAULT,
        )
        return _DEFAULT
    return value
