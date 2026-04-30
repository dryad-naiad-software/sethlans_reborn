# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checkpoint name constants and resume-route map (FR-CHK2 / FR-CHK3).

The wizard's per-step state lives in ``<data_dir>/.setup_progress.json``
under the schema declared by FR-CHK1 (``schema_version``, ``topology``,
``checkpoints``). This module pins the canonical names so handlers and
the resume logic agree on a single vocabulary.

* ``CHECKPOINT_NAMES`` — ordered tuple of every checkpoint a wizard
  flow can emit; used by the resume target endpoint to walk forward
  through the persisted ``checkpoints`` list and find the first
  unfinished step.
* ``RESUME_NEXT_ROUTE`` — checkpoint-name → next-route mapping. A user
  whose latest checkpoint is ``network_configured`` resumes at
  ``/database`` etc. ``topology_chosen`` and ``worker_password_set``
  branch by topology — the wizard frontend reads ``topology.json`` to
  pick the right successor.
* ``MANAGER_WORKER_ONLY_CHECKPOINTS`` — checkpoints emitted only when
  topology is ``manager_worker``; resume logic skips them under the
  ``manager`` topology.
"""

from __future__ import annotations

# FR-CHK2 — ordered checkpoint names. Used by resume logic and by the
# verify handler's "all required checkpoints present" guard.
WELCOME_SEEN = "welcome_seen"
TOPOLOGY_CHOSEN = "topology_chosen"
NETWORK_CONFIGURED = "network_configured"
DATABASE_CONFIGURED = "database_configured"
ADMIN_VALIDATED = "admin_validated"
WORKER_PASSWORD_SET = "worker_password_set"
VERIFIED = "verified"

CHECKPOINT_NAMES: tuple[str, ...] = (
    WELCOME_SEEN,
    TOPOLOGY_CHOSEN,
    NETWORK_CONFIGURED,
    DATABASE_CONFIGURED,
    ADMIN_VALIDATED,
    WORKER_PASSWORD_SET,
    VERIFIED,
)

# Checkpoints that exist only on the manager_worker topology branch.
MANAGER_WORKER_ONLY_CHECKPOINTS = frozenset({WORKER_PASSWORD_SET})

# FR-CHK3 — resume-route map (checkpoint name → next route to navigate
# to once that checkpoint is the latest one observed).
RESUME_NEXT_ROUTE: dict[str, str] = {
    WELCOME_SEEN: "/topology",
    TOPOLOGY_CHOSEN: "/network",
    NETWORK_CONFIGURED: "/database",
    DATABASE_CONFIGURED: "/admin-user",
    # admin → worker-password (manager_worker) or verify (manager); the
    # frontend resolves the branch from topology.json.
    ADMIN_VALIDATED: "/worker-password",
    WORKER_PASSWORD_SET: "/verify",
    VERIFIED: "/done",
}


__all__ = [
    "WELCOME_SEEN",
    "TOPOLOGY_CHOSEN",
    "NETWORK_CONFIGURED",
    "DATABASE_CONFIGURED",
    "ADMIN_VALIDATED",
    "WORKER_PASSWORD_SET",
    "VERIFIED",
    "CHECKPOINT_NAMES",
    "MANAGER_WORKER_ONLY_CHECKPOINTS",
    "RESUME_NEXT_ROUTE",
]
