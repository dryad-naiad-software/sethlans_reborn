# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card an Unreal claim holds (render per card D1, D3, D4, D14; FR-PC-CAP-3, FR-PC-SEL-1).

``choose(params, gate, card_keys, deps)`` runs in ``Reserver.try_reserve`` on
the agent-loop thread, after the fresh gate. It returns ``(key, '')`` or
``(None, reason)``; with a reason the reserver declines the job, so the claim
walk moves on to the next job instead of stopping on a card it cannot hold.

- A gated job holds the Unreal card in use at the gated card's address (the
  inventory's key, so every hold of one card has one spelling); it is
  declined when that card is held (chunk 4a: the gate still checks D8's card
  of all cards; choosing among free cards is chunk 4b).
- A ``shots`` job (no memory gate, D3) holds the first free Unreal card in use.
- The reading override (``SETHLANS_UNREAL_VRAM_MB``, no card key, D14) allows
  one Unreal job at a time: declined while any Unreal job holds a card;
  otherwise it holds D8's card of the Unreal cards in use
  (``ReserveDeps.d8_key``, ``adapter_choice.d8_key``), the card its first
  launch is then asked for, so the held card is the card it renders on.

``free_keys`` and ``has_unreal_job`` come from ``ReserveDeps.capacity`` (the
worker's ``WorkerCapacity``). The hold in ``job_claim`` stays the final check.
"""
import logging
import threading
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import key_address

logger = logging.getLogger(__name__)

NO_FREE_CARD = "no Unreal card in use is free."
OVERRIDE_ONE_JOB = "the GPU memory override allows one Unreal job at a time on this worker."
NO_D8_CARD = "no Unreal card to render on could be chosen."

_logged_lock = threading.Lock()  # leaf lock
_override_logged = False


def _log_override_once() -> None:
    global _override_logged
    with _logged_lock:
        if _override_logged:
            return
        _override_logged = True
    logger.info("The GPU memory override is set: this worker runs one Unreal job at a time (D14).")


def _inventory_key(gated: str, card_keys: tuple) -> str:
    address = key_address(gated)
    return next((key for key in card_keys if address is not None and key_address(key) == address), gated)


def choose(params, gate, card_keys: tuple, deps) -> Tuple[Optional[str], str]:
    """``(key to hold, '')`` or ``(None, why the job is declined)``."""
    capacity = deps.capacity() if deps.capacity is not None else None
    if capacity is None and not card_keys:
        return gate.card_key, ''  # nothing to check against (test deps without cards or capacity)
    if params.is_shots:
        free = capacity.free_keys(card_keys) if capacity is not None else tuple(card_keys)
        return (free[0], '') if free else (None, NO_FREE_CARD)
    if gate.card_key is None:
        if capacity is not None and capacity.has_unreal_job():
            return None, OVERRIDE_ONE_JOB
        _log_override_once()
        key = deps.d8_key() if deps.d8_key is not None else (card_keys[0] if card_keys else None)
        if key is None:
            return None, NO_D8_CARD
    else:
        key = _inventory_key(gate.card_key, card_keys)
    if capacity is not None and not capacity.free_keys((key,)):
        return None, f"its graphics card {key} is held by another job."
    return key, ''


def reset_for_tests() -> None:
    global _override_logged
    with _logged_lock:
        _override_logged = False
