# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics cards in use (FR-CL-1) and the one-family rule (FR-CC-4).

A ticked card is in use for Blender when its state (``cards.matching``,
FR-CK-2) is ``ok`` and Blender offers it; for Unreal when its state is ``ok``
or ``blender_lost`` and it has a DXGI index. With method ``cpu`` no card is in
use for Blender (B6); the Unreal list does not depend on the method (C-23).
``unreal_card_addresses`` gives the addresses of the Unreal cards (FR-CU-5).
The probing itself is done by ``capacity.card_check``. The cards in use are in PCI address order (D4);
the Blender ones are frozen into the capacity profile, and ``ignored`` are the
Blender ids of the detected cards that are not in use, so the render script
switches them off without a mismatch line (FR-CB-2).

``SETHLANS_COMPUTE_CARDS`` names keys only: each key's model is taken from the
first inventory that lists it with ids and kept for the process, so a later
model change is still seen (FR-CC-2). An unusable ticked card is logged at
ERROR once per process, except a card that only waits for Blender to be
installed: that one is logged at INFO once (#305), it gets no slot either,
and the periodic card check (``capacity.card_check.check``) finds it once
Blender is in place and requests the rebuild that gives it its slot.
"""
import logging
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, NamedTuple, Optional, Tuple

from sethlans_worker_agent.cards import matching
from sethlans_worker_agent.cards.identity import has_ids, key_sort_value
from sethlans_worker_agent.cards.inventory import NOTE_NO_BLENDER
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

FAMILIES = {'CUDA': 'CUDA', 'OPTIX': 'CUDA', 'HIP': 'HIP', 'ONEAPI': 'ONEAPI', 'METAL': 'METAL'}

NO_TICK_TEXT = ("GPU rendering is chosen, but no graphics card is ticked. This worker takes no GPU jobs. "
                "Tick a card in the worker's settings.")
NONE_USABLE_TEXT = ("None of the ticked graphics cards can be used (missing, changed or waiting for a person to "
                    "check them). This worker takes no GPU jobs until one can.")
WAITING_FOR_BLENDER_TEXT = ("Waiting for Blender to be installed before the ticked graphics cards can be used. "
                            "This worker takes no GPU jobs until then.")
COMBINE_TEXT = ("Combine GPUs needs two or more cards that Blender drives the same way (CUDA/OptiX, HIP, "
                "oneAPI or Metal).")
COMBINE_PROBLEM_TEXT = COMBINE_TEXT + " This worker takes no GPU jobs until the choice is changed."
WAITING_FOR_BLENDER_LOG = ("Ticked graphics card %s (%s) is waiting for Blender: Blender is not installed yet. "
                           "The card is checked again at the first card check after Blender is installed.")

_env_lock = threading.Lock()
_env_records: Dict[str, dict] = {}


class CardInUse(NamedTuple):
    key: str
    name: str
    blender_id: str
    backend: str


@dataclass(frozen=True)
class Selection:
    cards: Tuple[CardInUse, ...] = ()
    ignored: Tuple[str, ...] = ()
    problem: str = ''


def one_family(backends: Iterable[Optional[str]]) -> bool:
    """FR-CC-4: at least two non-null backends, all of one family (CUDA and OptiX are one)."""
    counted = [backend for backend in backends if backend is not None]
    return len(counted) >= 2 and len({FAMILIES.get(backend, backend) for backend in counted}) == 1


def record_of(card) -> dict:
    """The saved record of an inventory card (D3 plus ``revision_id``, C-17)."""
    return {'key': card.key, 'vendor_id': card.vendor_id, 'device_id': card.device_id,
            'revision_id': card.revision_id, 'name': card.name}


def env_records(keys, inv) -> Tuple[dict, ...]:
    """Records for keys from ``SETHLANS_COMPUTE_CARDS``, frozen from the first inventory listing each with ids.

    A card without vendor and device ids (the Blender fallback) is not frozen:
    its current record is used until an inventory gives the ids.
    """
    by_key = {card.key: card for card in inv.cards}
    with _env_lock:
        for key in keys:
            if key not in _env_records and key in by_key and has_ids(by_key[key]):
                _env_records[key] = record_of(by_key[key])
        current = {key: record_of(by_key[key]) for key in keys if key in by_key}
        return tuple(dict(_env_records.get(key) or current.get(key) or {'key': key, 'name': key}) for key in keys)


def reset_env_records_for_tests() -> None:
    """Forget the records and the brand finding (``env_brands``) of ``SETHLANS_COMPUTE_CARDS``. Test-only hook."""
    from sethlans_worker_agent.cards import env_brands
    with _env_lock:
        _env_records.clear()
    env_brands.reset_for_tests()


_NOT_USED = {
    matching.LOST: 'missing from the card list',
    matching.HELD: 'held by an open alert until a person dismisses it',
    matching.BLENDER_LOST: 'Blender no longer offers it; held from Blender until a person dismisses the alert',
    matching.UNKNOWN: 'the operating system could not list the graphics cards',
}


def _why_not_used(state: str, saved: dict, card) -> str:
    if state == matching.CHANGED:
        return f'now {ascii(card.name)} instead of {ascii(saved.get("name", ""))}'
    if state in _NOT_USED:
        return _NOT_USED[state]
    return f'not offered by Blender ({card.note or "no matching Blender device"})'


def _states_of(inv, section, alerts, states):
    return matching.card_states(section, inv, alerts) if states is None else states


def blender_cards_in_use(inv, section, alerts=None, states=None) -> Tuple[CardInUse, ...]:
    """The ticked cards Blender may render on, in PCI address order (FR-CL-1).

    ``states`` from ``matching.card_states``; computed here from ``alerts``
    (default: the section's) when not given.
    """
    if section.method == 'cpu':
        return ()
    states = _states_of(inv, section, alerts, states)
    by_key = {card.key: card for card in inv.cards}
    cards = []
    for saved in sorted(section.cards, key=lambda record: key_sort_value(record['key'])):
        card, state = by_key.get(saved['key']), states.get(saved['key'])
        if state == matching.OK and card.blender is not None:
            cards.append(CardInUse(card.key, card.name, card.blender[0], card.blender[1]))
        elif state == matching.OK and card.note == NOTE_NO_BLENDER:  # not a fault: no Blender to ask yet (#305)
            log_once(logger, logging.INFO, WAITING_FOR_BLENDER_LOG, ascii(saved.get('name') or saved['key']),
                     saved['key'])
        else:
            log_once(logger, logging.ERROR, "Ticked graphics card %s (%s) is not used by Blender: %s.",
                     ascii(saved.get('name') or saved['key']), saved['key'], _why_not_used(state, saved, card))
    return tuple(cards)


def unreal_cards_in_use(inv, section, alerts=None, states=None) -> tuple:
    """The ticked inventory cards Unreal may render on: state ``ok`` or ``blender_lost`` and a DXGI index (FR-CL-1)."""
    states = _states_of(inv, section, alerts, states)
    ticked = set(section.ticked_keys)
    return tuple(sorted((card for card in inv.cards if card.key in ticked and card.dxgi_index is not None
                         and states.get(card.key) in (matching.OK, matching.BLENDER_LOST)),
                        key=lambda card: key_sort_value(card.key)))


def unreal_card_addresses(inv=None, section=None) -> frozenset:
    """FR-CU-5: the ``dddd:bb:dd.f`` addresses of the Unreal cards in use (the GPU memory reader's filter).

    From ``inv`` and ``section`` when both are given, else from the saved
    choice and the heartbeat's card probe (``unreal_cards.current``). Empty
    when no card is ticked or nothing can be decided; never a card without
    an address (O6).
    """
    from sethlans_worker_agent.cards import unreal_cards
    found = unreal_cards.evaluate(section, inv) if inv is not None and section is not None else unreal_cards.current()
    return unreal_cards.addresses(found[0]) if found is not None else frozenset()


def waiting_for_blender(inv, section, states) -> bool:
    """Every ticked card is ``ok`` and only waits for Blender to be installed (#305); False with no ticked card."""
    by_key = {card.key: card for card in inv.cards}
    return bool(section.cards) and all(
        states.get(saved['key']) == matching.OK and saved['key'] in by_key
        and by_key[saved['key']].note == NOTE_NO_BLENDER for saved in section.cards)


def problem_message(section, cards: Tuple[CardInUse, ...], waiting: bool = False) -> str:
    """FR-CK-6: why a worker set to render on graphics cards takes no GPU jobs ('' when it does).

    ``waiting`` (``waiting_for_blender``): the ticked cards are fine but no Blender is installed yet.
    """
    if section.method == 'cpu' or cards:
        return ''
    if not section.cards:
        return NO_TICK_TEXT
    return WAITING_FOR_BLENDER_TEXT if waiting else NONE_USABLE_TEXT
