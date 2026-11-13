# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Normal-mode topology resolution for the launcher (issue #210).

Decides which runtime component(s) :func:`launcher.orchestration.run_normal_mode`
starts. The rule is *fail closed*: a ``topology.json`` value that is
present but not recognized must never widen a worker-only box into a
manager install (Caddy + an unconfigured manager + a browser tab).

Resolution order:

1. ``topology.json`` holds a recognized value: use it.
2. ``topology.json`` is missing, malformed, not a JSON object, or has no
   ``topology`` key: use the topology recorded in ``.setup_complete``
   if there is one, else the historical ``manager+worker`` default.
3. ``topology.json`` holds a value that is present but unrecognized
   (hand edit, future spelling, wrong type, ``null``): use the topology
   recorded in ``.setup_complete``; if that is also missing or
   unrecognized, return ``None``. The caller then refuses to start.

Split out of ``launcher/orchestration.py`` for the 300-line limit.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from launcher.cold_boot import safe_invoke
from launcher.setup_sentinel import recorded_topology
from shared.topology import TOPOLOGY_BOTH, normalize_topology

logger = logging.getLogger(__name__)

TOPOLOGY_KEY = "topology"
REASON_UNKNOWN_TOPOLOGY = (
    "topology.json names an unknown topology and .setup_complete "
    "records none; refusing to start"
)


def read_topology_json(data_dir: Path) -> dict:
    """Return ``<data_dir>/topology.json`` as a dict (``{}`` on any error).

    Valid JSON that is not an object (``[]``, ``"worker"``, ``3``) is
    treated like a malformed file instead of raising later on ``.get``.
    """
    path = Path(data_dir) / "topology.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_runtime_topology(data_dir: Path, payload: object) -> Optional[str]:
    """Return the canonical topology to run, or ``None`` to refuse.

    *payload* is the parsed ``topology.json`` (see
    :func:`read_topology_json`). See the module docstring for the order.
    """
    if not isinstance(payload, dict) or TOPOLOGY_KEY not in payload:
        recorded = recorded_topology(data_dir)
        if recorded is not None:
            logger.info(
                "topology.json has no topology; using %r from "
                ".setup_complete", recorded,
            )
            return recorded
        return TOPOLOGY_BOTH
    raw = payload[TOPOLOGY_KEY]
    canonical = normalize_topology(raw)
    if canonical is not None:
        return canonical
    recorded = recorded_topology(data_dir)
    if recorded is not None:
        logger.warning(
            "topology.json has unknown topology=%r; using %r recorded "
            "in .setup_complete", raw, recorded,
        )
        return recorded
    logger.error(
        "topology.json has unknown topology=%r and .setup_complete "
        "records none; refusing to start any component", raw,
    )
    return None


def fail_unknown_topology(
    on_startup_failed: Optional[Callable[[str, str], None]],
) -> int:
    """Surface the refusal on the splash error card; return rc=1."""
    safe_invoke(on_startup_failed, REASON_UNKNOWN_TOPOLOGY, "")
    return 1


__all__ = [
    "REASON_UNKNOWN_TOPOLOGY",
    "fail_unknown_topology",
    "read_topology_json",
    "resolve_runtime_topology",
]
