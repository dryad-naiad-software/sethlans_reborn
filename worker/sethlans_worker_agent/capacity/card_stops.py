# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The alerts and per-job stops of one card evaluation (FR-CK-3, FR-CL-5, C5; C-24 e, m).

- ``raise_alerts(result)``: ``add_alert`` for every unusable ticked card;
  CRITICAL only for a newly added one. A failed write is kept in memory by
  the store and does not stop the caller (C5).
- ``jobs_on_unusable_cards(capacity, states)``: a Blender job holding a card
  that is ``lost``, ``changed``, ``held`` or ``blender_lost``; the Unreal job
  when the card it chose at its latest launch (``unreal_bridge.
  unreal_card_in_use``) is ``lost``, ``changed`` or ``held`` (D19: Unreal may
  keep a ``blender_lost`` card). The Blender loop skips every owner in
  ``_unreal_jobs``, read in the same ``_lock`` snapshot as the assignments
  (render per card FR-PC-SEL-4), so an Unreal job's held card never stops it
  as if it were Blender's. Retained (negative) owners and unticked or
  ``unknown`` cards stop nothing.
- ``stop_jobs``: each one through ``blender_stop.request_card_stop`` (its own
  event; the Unreal executor joins it with the shutdown event and releases
  the job, ``worker_hardware_changed``). The worker's shutdown event is not set.

Called on the agent-loop thread; takes ``WorkerCapacity._lock`` only to copy
the assignments, and the store's lock only inside ``add_alert``, never both.
"""
import logging

from sethlans_worker_agent.cards import matching, store
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError

logger = logging.getLogger(__name__)

STOPPED_FOR_BOTH = frozenset({matching.LOST, matching.CHANGED, matching.HELD})
STOPPED_FOR_BLENDER = STOPPED_FOR_BOTH | {matching.BLENDER_LOST}


def raise_alerts(result) -> None:
    """``add_alert`` for every unusable ticked card (FR-CK-3); CRITICAL only for a new one."""
    for card in result.unusable:
        try:
            added = store.add_alert(card.kind, card.key, card.name, card.detail)
        except (ConfigLockTimeoutError, OSError):
            added = True
        if added:
            logger.critical("Graphics card %s (%s) is %s (%s).", ascii(card.name), card.key, card.state,
                            ascii(card.detail))


def jobs_on_unusable_cards(capacity, states) -> list:
    """Job ids rendering on a card that is not usable for their renderer, sorted."""
    from sethlans_worker_agent import unreal_bridge
    with capacity._lock:
        held = list(capacity._gpu_assignments.items())
        unreal_owners = frozenset(capacity._unreal_jobs)
    stopped = {owner for key, owner in held
               if owner >= 0 and owner not in unreal_owners and states.get(key) in STOPPED_FOR_BLENDER}
    unreal = unreal_bridge.unreal_card_in_use()
    if unreal is not None and unreal[0] >= 0 and states.get(unreal[1]) in STOPPED_FOR_BOTH:
        stopped.add(unreal[0])
    return sorted(stopped)


def stop_jobs(capacity, states) -> None:
    from sethlans_worker_agent import blender_stop
    for job_id in capacity.jobs_on_unusable_cards(states):
        blender_stop.request_card_stop(job_id)
