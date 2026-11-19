# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Warnings about the card choice, as data for the pages and the manager (D21, FR-CB-5, FR-CS-3).

``build(inventory, result)`` gives ``[{"code", "key", "text"}]`` for one
``matching.evaluate`` result. ``engine_not_tied`` (key ``None``) holds when the
inventory is not empty and holds a card that is not a Blender card in use
(D22): with method ``cpu`` no card is in use, so any card counts. Eevee and
Workbench do not read the Cycles device preferences (#251), so the ticks do
not bind them. Then one ``ticked_drives_screen`` (key = the card) per ticked
inventory card whose ``screen`` mark is ``True``, in PCI address order,
naming the main screen when ``primary_screen`` is ``True``; none for an
unticked card or an unknown (``None``) mark. A text over 200 characters
names the card as ``PCI <address>`` instead of ``<name> (PCI <bus:dev.fn>)``.
``log_screen_warnings`` logs each such text once at WARNING when it first
appears at a check, again after it went away.

``log_engine_warning(job_id, engine)`` logs the FR-CB-5 line once per job
when an Eevee or Workbench job starts while that condition holds. It uses the
condition from the last ``remember`` (the start and the periodic card check
call it); before the first one it evaluates the cards once (OS probe, cached
Blender list, ``store.snapshot()``; it never starts Blender; no screen read,
the engine condition does not need it). It never raises. ``_lock`` is a leaf.
"""
import logging
import threading
from collections import deque
from typing import FrozenSet, List, Optional

from sethlans_worker_agent.cards.identity import KEY_PREFIX, key_address

logger = logging.getLogger(__name__)

ENGINE_CODE = 'engine_not_tied'
ENGINE_TEXT = ("Eevee and Workbench jobs may render on any graphics card of this machine, including unticked ones. "
               "The ticks are firm for Cycles and Unreal only until issue #251 is fixed.")
NOT_TIED_ENGINES = frozenset({'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'WORKBENCH', 'BLENDER_WORKBENCH'})
SCREEN_CODE = 'ticked_drives_screen'
SCREEN_TEXT = '{card} is ticked and drives a screen; rendering can slow it.'
MAIN_SCREEN_TEXT = '{card} is ticked and drives the main screen; rendering can slow it.'
MAX_TEXT = 200  # the manager's cap on a warning text
_LOG_TEXT = ("Job %s uses %s, which is not tied to the ticked graphics cards; it may render on an unticked card "
             "(#251).")

_lock = threading.Lock()
_condition: Optional[bool] = None          # engine_not_tied at the last remember(); under _lock
_warned: deque = deque(maxlen=64)          # job ids already warned (the thumbnail pass runs the script again)
_screen_logged: FrozenSet[str] = frozenset()  # screen warning texts logged at the last check; under _lock


def engine_not_tied(inventory, result) -> bool:
    """D22: a non-empty inventory holding a card that is not a Blender card in use."""
    if result.failed or not inventory.cards:
        return False
    in_use = {card.key for card in result.blender_cards}
    return any(card.key not in in_use for card in inventory.cards)


def screen_text(card) -> str:
    """FR-CS-3 text for a ticked card that drives a screen (the main screen when ``primary_screen`` is True)."""
    template = MAIN_SCREEN_TEXT if card.primary_screen is True else SCREEN_TEXT
    address = key_address(card.key)
    if address is None:  # apple-gpu: no PCI address
        return template.format(card=card.name or card.key)[:MAX_TEXT]
    text = template.format(card=f'{card.name} (PCI {address[1]:02x}:{address[2]:02x}.{address[3]:x})')
    return text if len(text) <= MAX_TEXT else template.format(card=f'PCI {card.key[len(KEY_PREFIX):]}')


def screen_warnings(inventory, result) -> List[dict]:
    """One ``ticked_drives_screen`` per ticked inventory card with ``screen is True``."""
    if result.failed or result.section is None:
        return []
    ticked = set(result.section.ticked_keys)
    return [{'code': SCREEN_CODE, 'key': card.key, 'text': screen_text(card)}
            for card in inventory.cards if card.key in ticked and card.screen is True]


def build(inventory, result) -> List[dict]:
    """The warnings for one evaluation (display data, no side effect)."""
    found = [{'code': ENGINE_CODE, 'key': None, 'text': ENGINE_TEXT}] if engine_not_tied(inventory, result) else []
    return found + screen_warnings(inventory, result)


def log_screen_warnings(inventory, result) -> None:
    """At a check: log each screen warning once while it lasts (a failed evaluation changes nothing)."""
    global _screen_logged
    if result.failed:
        return
    texts = [warning['text'] for warning in screen_warnings(inventory, result)]
    with _lock:
        new = [text for text in texts if text not in _screen_logged]
        _screen_logged = frozenset(texts)
    for text in new:
        logger.warning("%s", ascii(text)[1:-1])


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
    """Set the remembered condition and forget the warned jobs and logged screen warnings. Test-only hook."""
    global _condition, _screen_logged
    with _lock:
        _condition = condition
        _warned.clear()
        _screen_logged = frozenset()
