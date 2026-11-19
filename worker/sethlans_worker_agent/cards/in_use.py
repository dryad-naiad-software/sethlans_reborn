# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics cards in use for Blender (FR-CL-1) and the one-family rule (FR-CC-4).

A ticked card is in use for Blender when the fresh inventory lists its key,
its model is the saved one (D3, C-17), no open alert holds it (D19) and Blender
offers it. With method ``cpu`` no card is in use (B6) and nothing is probed.
The cards in use are in PCI address order (D4) and are frozen into the
capacity profile; ``ignored`` are the Blender ids of the detected cards that
are not in use, so the render script switches them off without a mismatch
line (FR-CB-2).

A saved record or a current card without vendor and device ids (the Blender
fallback when the OS probe failed, or macOS) cannot show a model change; it
is not treated as changed (C-18). ``SETHLANS_COMPUTE_CARDS`` names keys only:
each key's model is taken from the first inventory that lists it and kept for
the process, so a later model change is still seen (FR-CC-2).

The checks, card states and alerts of later chunks build on this module; for
now an unusable ticked card is logged at ERROR once per process.
"""
import dataclasses
import logging
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, NamedTuple, Optional, Tuple

from sethlans_worker_agent.cards import inventory as inventory_module
from sethlans_worker_agent.cards import os_probe
from sethlans_worker_agent.cards.identity import has_ids, key_sort_value, same_model
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

FAMILIES = {'CUDA': 'CUDA', 'OPTIX': 'CUDA', 'HIP': 'HIP', 'ONEAPI': 'ONEAPI', 'METAL': 'METAL'}
BLENDER_HOLDING_KINDS = frozenset({'card_lost', 'card_changed', 'card_lost_blender'})  # D19

NO_TICK_TEXT = ("GPU rendering is chosen, but no graphics card is ticked. This worker takes no GPU jobs. "
                "Tick a card in the worker's settings.")
NONE_USABLE_TEXT = ("None of the ticked graphics cards can be used (missing, changed or waiting for a person to "
                    "check them). This worker takes no GPU jobs until one can.")
COMBINE_TEXT = ("Combine GPUs needs two or more cards that Blender drives the same way (CUDA/OptiX, HIP, "
                "oneAPI or Metal).")
COMBINE_PROBLEM_TEXT = COMBINE_TEXT + " This worker takes no GPU jobs until the choice is changed."

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
    with _env_lock:
        _env_records.clear()


def _model_matches(saved: dict, card) -> bool:
    if not (has_ids(saved) and has_ids(card)):
        return True  # model unknown on one side: not a change (C-18)
    return same_model(saved, card)


def _why_not_used(saved: dict, card, held: set) -> Optional[str]:
    if card is None:
        return 'missing from the card list'
    if not _model_matches(saved, card):
        return f'now {ascii(card.name)} instead of {ascii(saved.get("name", ""))}'
    if saved['key'] in held:
        return 'held by an open alert until a person dismisses it'
    if card.blender is None:
        return f'not offered by Blender ({card.note or "no matching Blender device"})'
    return None


def blender_cards_in_use(inv, section, alerts=None) -> Tuple[CardInUse, ...]:
    """The ticked cards Blender may render on, in PCI address order (FR-CL-1)."""
    if section.method == 'cpu':
        return ()
    alerts = section.alerts if alerts is None else alerts
    held = {alert.get('key') for alert in alerts if alert.get('kind') in BLENDER_HOLDING_KINDS}
    by_key = {card.key: card for card in inv.cards}
    cards = []
    for saved in sorted(section.cards, key=lambda record: key_sort_value(record['key'])):
        card = by_key.get(saved['key'])
        reason = _why_not_used(saved, card, held)
        if reason is None:
            cards.append(CardInUse(card.key, card.name, card.blender[0], card.blender[1]))
        else:
            log_once(logger, logging.ERROR, "Ticked graphics card %s (%s) is not used: %s.",
                     ascii(saved.get('name') or saved['key']), saved['key'], reason)
    return tuple(cards)


def problem_message(section, cards: Tuple[CardInUse, ...]) -> str:
    """FR-CK-6: why a worker set to render on graphics cards takes no GPU jobs ('' when it does)."""
    if section.method == 'cpu' or cards:
        return ''
    return NO_TICK_TEXT if not section.cards else NONE_USABLE_TEXT


def select(section, inv, detected_ids=()) -> Selection:
    """The cards in use, the ignored Blender ids and the problem text for one section and inventory."""
    cards = blender_cards_in_use(inv, section)
    if section.gpu_mode == 'combined' and len(cards) > 1 and not one_family(card.backend for card in cards):
        log_once(logger, logging.ERROR, "Combine GPUs is set for cards Blender drives in different ways (%s); "
                 "this worker takes no GPU jobs until the choice is changed.",
                 ', '.join(sorted({card.backend for card in cards})))
        return Selection(problem=COMBINE_PROBLEM_TEXT)
    in_use = {card.blender_id for card in cards}
    ignored = tuple(device_id for device_id in detected_ids if device_id not in in_use)
    return Selection(cards=cards, ignored=ignored, problem=problem_message(section, cards))


def resolve(section) -> Selection:
    """Probe the cards and select those in use; with method ``cpu`` nothing is probed (B6)."""
    if section.method == 'cpu':
        return Selection()
    from sethlans_worker_agent.cards.startup_log import cached_blender_list
    blender_cards, version = cached_blender_list()
    inv = inventory_module.build(os_probe.probe(), blender_cards, version)
    if section.env_cards:
        section = dataclasses.replace(section, cards=env_records(section.ticked_keys, inv))
    detected = [record.get('id') for record in blender_cards if isinstance(record, dict)] \
        if isinstance(blender_cards, list) else []
    return select(section, inv, tuple(device_id for device_id in detected if isinstance(device_id, str)))
