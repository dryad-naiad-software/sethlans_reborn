# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup-phase helpers.

Owns:
 * :class:`SetupPhaseError` — raised by views to trigger a 409 envelope.
 * :func:`read_setup_progress` — derives the canonical wizard phase from
   the sentinel's ``checkpoints`` list.
 * :func:`require_setup_phase` — raises ``SetupPhaseError`` on mismatch.
 * :func:`setup_state_snapshot` — caches setup state on the request so a
   single HTTP request observes one consistent view (C2).

The canonical phase vocabulary is narrower than the sentinel checkpoint
history — phases map 1:1 to user-visible wizard steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from workers.services import checkpoints
from workers.services.sentinel import read_sentinel

# Canonical wizard phase slugs in linear order.
SETUP_PHASES: tuple[str, ...] = (
    "topology",
    "network",
    "database",
    "admin",
    "worker_password",
    "blender",
    "verify",
)

# Checkpoint -> phase completed by that checkpoint.  The "current" phase
# returned by :func:`read_setup_progress` is the next phase after the
# highest completed checkpoint.
_CHECKPOINT_TO_PHASE = {
    checkpoints.TOPOLOGY_CHOSEN: "topology",
    checkpoints.NETWORK_CONFIGURED: "network",
    checkpoints.DATABASE_CONFIGURED: "database",
    checkpoints.ADMIN_CREATED: "admin",
    checkpoints.WORKER_PASSWORD_SET: "worker_password",
    checkpoints.BLENDER_PREDOWNLOADED: "blender",
    checkpoints.VERIFIED: "verify",
}


class SetupPhaseError(Exception):
    """Raised from setup views when the wizard is in the wrong phase.

    The custom DRF exception handler (``workers.utils.errors``) maps
    this to a 409 envelope with the configured code.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 409,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def read_setup_progress(data_dir: Path) -> Optional[str]:
    """Return the current setup phase slug (next unfinished), or None.

    ``None`` means either no sentinel is present (wizard hasn't started)
    OR every phase is complete (``completed_at`` is set).
    """
    sentinel = read_sentinel(Path(data_dir))
    if sentinel is None:
        return None
    if sentinel.get("completed_at"):
        return None
    done_checkpoints = set(sentinel.get("checkpoints", []))

    # Find the first phase whose checkpoint is NOT present.
    for checkpoint, phase in _CHECKPOINT_TO_PHASE.items():
        if checkpoint not in done_checkpoints:
            return phase
    return None


def require_setup_phase(expected: str, data_dir: Path) -> None:
    """Raise :class:`SetupPhaseError` if the wizard isn't at ``expected``.

    Called manually inside function-based views because decorator
    ordering with ``@api_view`` is fragile (D-MED).
    """
    if expected not in SETUP_PHASES:
        raise ValueError(f"Unknown setup phase: {expected!r}")
    current = read_setup_progress(Path(data_dir))
    if current != expected:
        raise SetupPhaseError(
            code="precondition_unmet",
            message=(
                f"Expected setup phase {expected!r}, "
                f"current phase is {current!r}."
            ),
            status=409,
            details={"expected": expected, "current": current},
        )


def setup_state_snapshot(request) -> dict:
    """Return a cached ``{complete, phase, session_id}`` snapshot.

    Cached on ``request._setup_snapshot`` so gate middleware + permission
    class + view observe one consistent view of the world.
    """
    cached = getattr(request, "_setup_snapshot", None)
    if cached is not None:
        return cached

    from workers.services.ini_atomic import read_setup_session_id
    from sethlans_manager.middleware.setup_gate import _get_data_dir

    data_dir = _get_data_dir()
    sentinel = read_sentinel(data_dir)
    complete = sentinel is not None and bool(sentinel.get("completed_at"))
    phase = None if complete else read_setup_progress(data_dir)
    session_id = read_setup_session_id(data_dir)

    snapshot = {
        "complete": complete,
        "phase": phase,
        "session_id": session_id,
    }
    try:
        request._setup_snapshot = snapshot
    except Exception:
        pass
    return snapshot
