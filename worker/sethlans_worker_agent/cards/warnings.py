# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Warnings about the card choice, as data for the pages and the manager (D21, FR-CB-5).

``build(inventory, result)`` gives ``[{"code", "key", "text"}]`` for one
``matching.evaluate`` result. ``engine_not_tied`` (key ``None``) holds when the
inventory is not empty and holds a card that is not a Blender card in use
(D22): with method ``cpu`` no card is in use, so any card counts. Eevee and
Workbench do not read the Cycles device preferences (#251), so the ticks do
not bind them.

``log_engine_warning(job_id, engine)`` logs the FR-CB-5 line once per job
when an Eevee or Workbench job starts while that condition holds. It uses the
condition from the last ``remember`` (the start and the periodic card check
call it); before the first one it evaluates the cards once (OS probe, cached
Blender list, ``store.snapshot()``; it never starts Blender). It never raises.
``_lock`` is a leaf.
"""
import logging
import threading
from collections import deque
from typing import List, Optional

logger = logging.getLogger(__name__)

ENGINE_CODE = 'engine_not_tied'
ENGINE_TEXT = ("Eevee and Workbench jobs may render on any graphics card of this machine, including unticked ones. "
               "The ticks are firm for Cycles and Unreal only until issue #251 is fixed.")
NOT_TIED_ENGINES = frozenset({'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'WORKBENCH', 'BLENDER_WORKBENCH'})
_LOG_TEXT = ("Job %s uses %s, which is not tied to the ticked graphics cards; it may render on an unticked card "
             "(#251).")

_lock = threading.Lock()
_condition: Optional[bool] = None          # engine_not_tied at the last remember(); under _lock
_warned: deque = deque(maxlen=64)          # job ids already warned (the thumbnail pass runs the script again)


def engine_not_tied(inventory, result) -> bool:
    """D22: a non-empty inventory holding a card that is not a Blender card in use."""
    if result.failed or not inventory.cards:
        return False
    in_use = {card.key for card in result.blender_cards}
    return any(card.key not in in_use for card in inventory.cards)


def build(inventory, result) -> List[dict]:
    """The warnings for one evaluation (display data, no side effect)."""
    if engine_not_tied(inventory, result):
        return [{'code': ENGINE_CODE, 'key': None, 'text': ENGINE_TEXT}]
    return []


def remember(inventory, result) -> None:
    """Keep the engine condition of an authoritative evaluation (start, periodic check); a failed one is ignored."""
    global _condition
    if result.failed:
        return
    value = engine_not_tied(inventory, result)
    with _lock:
        _condition = value


def forget() -> None:
    """Drop the remembered condition (a check that probed nothing); the next job start evaluates it once."""
    global _condition
    with _lock:
        _condition = None


def _evaluate_now() -> bool:
    from sethlans_worker_agent.cards import inventory as inventory_module
    from sethlans_worker_agent.cards import matching, os_probe, store
    from sethlans_worker_agent.cards.startup_log import cached_blender_list
    section = store.snapshot()
    if section.read_failed:
        return False
    blender_cards, version = cached_blender_list()
    inv = inventory_module.build(os_probe.probe(), blender_cards, version)
    result = matching.evaluate(section, inv)
    remember(inv, result)
    return engine_not_tied(inv, result)


def _holds() -> bool:
    with _lock:
        known = _condition
    if known is not None:
        return known
    try:
        return _evaluate_now()
    except Exception:  # information only: never break a render
        logger.debug("The Eevee/Workbench card condition could not be evaluated.", exc_info=True)
        return False


def log_engine_warning(job_id, engine) -> bool:
    """FR-CB-5: one WARNING per job start of an engine the ticks do not bind; ``True`` when logged."""
    if engine not in NOT_TIED_ENGINES or not _holds():
        return False
    with _lock:
        if job_id in _warned:
            return False
        _warned.append(job_id)
    logger.warning(_LOG_TEXT, job_id, engine)
    return True


def reset_for_tests(condition: Optional[bool] = None) -> None:
    """Set the remembered condition and forget the warned jobs. Test-only hook."""
    global _condition
    with _lock:
        _condition = condition
        _warned.clear()
