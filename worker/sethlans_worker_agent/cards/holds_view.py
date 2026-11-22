# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which job holds each graphics card (render per card FR-PC-MGR-3, -4).

``snapshot()`` reads ``WorkerCapacity.holds_snapshot()`` once (one
``_lock`` acquisition) and returns a ``Holds``; ``Holds.fields(key)`` gives
the ``job_id`` and ``renderer`` of one card for the card GET answer and,
through it, for the heartbeat's ``cards`` report. Both are ``None`` when the
card is free.

- A card is matched by PCI address (``identity.key_address``), never by its
  text, so a held key spelled differently from the inventory key still
  matches. A key without an address (``apple-gpu``) is matched by its text.
- A retained reservation (a process that would not die, FR-UE-11) is held
  under a negative owner key; it is reported as the job it was retained
  from, since its card stays busy until the worker restarts.
- ``job_id`` is a positive int at most ``2^31-1`` and ``renderer`` is
  ``BLENDER`` or ``UNREAL``; anything else gives both as ``None``.
- No capacity yet (the worker is still starting): every card is free.

Display data only. Holds no lock of its own and keeps no state.
"""
from typing import Dict, Optional, Tuple

from sethlans_worker_agent.cards.identity import key_address

MAX_JOB_ID = 2 ** 31 - 1
RENDERERS = frozenset({'BLENDER', 'UNREAL'})


def checked(job_id, renderer) -> Dict[str, Optional[object]]:
    """``{'job_id', 'renderer'}``: both values when both are valid, else both ``None``."""
    valid = (isinstance(job_id, int) and not isinstance(job_id, bool) and 1 <= job_id <= MAX_JOB_ID
             and isinstance(renderer, str) and renderer in RENDERERS)
    return {'job_id': job_id, 'renderer': renderer} if valid else {'job_id': None, 'renderer': None}


def _job_of(owner) -> Optional[int]:
    """The job id behind an owner key; a retained owner (``-job_id - 1``) maps back to its job."""
    if not isinstance(owner, int) or isinstance(owner, bool):
        return None
    return owner if owner > 0 else -owner - 1


def _match_value(key):
    address = key_address(key)
    return address if address is not None else key


class Holds:
    """One snapshot of the held cards: match value -> ``(job_id, renderer)``."""

    def __init__(self, held: Dict[str, Tuple[int, str]]):
        self._by_card = {}
        for key, (owner, renderer) in sorted(held.items(), key=lambda item: str(item[0])):
            self._by_card.setdefault(_match_value(key), (_job_of(owner), renderer))

    def fields(self, key) -> Dict[str, Optional[object]]:
        """``job_id`` and ``renderer`` for the card ``key``; both ``None`` when it is free."""
        return checked(*self._by_card.get(_match_value(key), (None, None)))


def snapshot() -> Holds:
    """The held cards now, from one ``holds_snapshot()``; every card free before the capacity exists."""
    from sethlans_worker_agent import job_processor
    capacity = job_processor.get_worker_capacity()
    return Holds(capacity.holds_snapshot() if capacity is not None else {})
