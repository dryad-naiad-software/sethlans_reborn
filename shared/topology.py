# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Canonical topology vocabulary shared by the launcher and the tray.

``topology.json`` is written by the setup wizard with the spellings in
``wizard.sethlans_wizard.handlers.topology.VALID_TOPOLOGIES``
(``manager`` / ``manager_worker`` / ``worker_only``). Older code paths
and tests use the tray's canonical forms (``manager`` / ``worker`` /
``manager+worker``). Launcher and tray code that branches on the
topology MUST go through :func:`normalize_topology` (or the
:func:`has_manager` / :func:`has_worker` predicates) instead of
matching raw strings, so a new spelling only has to be taught here
(issue #210). The wizard and worker setup handlers are deliberately
out of scope: they validate and write the wizard's own spellings
(``VALID_TOPOLOGIES``).

No fallback policy lives here: ``normalize_topology`` returns ``None``
for anything unknown and each caller decides how to fail (the launcher
fails closed, see :mod:`launcher.runtime_topology`).

Pure stdlib, no I/O: safe to import from the frozen launcher, the tray
helper and tests alike.
"""

from __future__ import annotations

from typing import Optional

TOPOLOGY_MANAGER = "manager"
TOPOLOGY_WORKER = "worker"
TOPOLOGY_BOTH = "manager+worker"

CANONICAL_TOPOLOGIES = frozenset(
    {TOPOLOGY_MANAGER, TOPOLOGY_WORKER, TOPOLOGY_BOTH},
)

# Wizard / filesystem-safe spellings -> canonical form.
LEGACY_ALIASES = {
    "manager_worker": TOPOLOGY_BOTH,
    "worker_only": TOPOLOGY_WORKER,
}

_MANAGER_BEARING = frozenset({TOPOLOGY_MANAGER, TOPOLOGY_BOTH})
_WORKER_BEARING = frozenset({TOPOLOGY_WORKER, TOPOLOGY_BOTH})


def normalize_topology(value: object) -> Optional[str]:
    """Return the canonical topology for *value*, or ``None`` if unknown.

    Accepts the canonical forms and every alias in :data:`LEGACY_ALIASES`.
    Non-string input (``None``, numbers, dicts from a malformed file)
    yields ``None``; callers decide their own fallback.
    """
    if not isinstance(value, str):
        return None
    value = LEGACY_ALIASES.get(value, value)
    return value if value in CANONICAL_TOPOLOGIES else None


def has_manager(value: object) -> bool:
    """True when *value* names a topology that runs the manager."""
    return normalize_topology(value) in _MANAGER_BEARING


def has_worker(value: object) -> bool:
    """True when *value* names a topology that runs the worker."""
    return normalize_topology(value) in _WORKER_BEARING


__all__ = [
    "CANONICAL_TOPOLOGIES",
    "LEGACY_ALIASES",
    "TOPOLOGY_BOTH",
    "TOPOLOGY_MANAGER",
    "TOPOLOGY_WORKER",
    "has_manager",
    "has_worker",
    "normalize_topology",
]
