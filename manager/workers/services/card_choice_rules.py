# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The pure rules of the card-choice action (worker card selection FR-CM-10
steps 3-5, FR-CC-4, D19), each returning the one refusal sentence or
``None``.  ``services.card_choice`` applies them in order: the body's
shape (400) before the reported state (409), and the rules against the
stored report (400) last, since they need a usable report.
"""

from ..strict_json import _int_in
from ..views.heartbeat_cards import (
    GPU_MODES, MAX_ENTRIES, MAX_PCI_ID, MAX_REVISION, METHODS, is_card_key,
)

BODY_KEYS = ('method', 'gpu_mode', 'cards', 'shown', 'base_revision')
CARD_KEYS = ('key', 'vendor_id', 'device_id')
GPU_METHODS = frozenset({'gpu', 'cpu_gpu'})
# Stored states and alert kinds that hold a card from being newly ticked (D19).
HOLDING_STATES = frozenset({'lost', 'changed', 'held', 'blender_lost'})
HOLDING_ALERTS = frozenset({'card_lost', 'card_changed', 'card_lost_blender'})
# The one-family rule of FR-CC-4 (B1): CUDA and OptiX are one family.
FAMILIES = {'CUDA': 'CUDA', 'OPTIX': 'CUDA', 'HIP': 'HIP', 'ONEAPI': 'ONEAPI', 'METAL': 'METAL'}

BAD_BODY = "The body must be a JSON object with exactly the keys method, gpu_mode, cards, shown and base_revision."
BAD_METHOD = "method must be cpu, gpu or cpu_gpu."
BAD_GPU_MODE = "gpu_mode must be split or combined."
BAD_REVISION = "base_revision must be an integer from 0 to 2147483647."
BAD_CARDS = "cards must be a list of at most 16 objects with exactly the keys key, vendor_id and device_id."
BAD_KEY = "Each card key must have the form pci:dddd:bb:dd.f or be apple-gpu."
BAD_IDS = "vendor_id and device_id must be integers from 0 to 65535, or null."
DUPLICATE_CARD = "Each card may be named only once."
BAD_SHOWN = "shown must be a list of at most 16 different card keys."
NOT_SHOWN = "Every ticked card must be among the shown cards."
GPU_NEEDS_CARD = "A method that uses the GPU needs at least one ticked card."
COMBINED_NEEDS_TWO = "Combine GPUs needs at least two ticked cards."
NO_REPORT = "This worker has not reported its graphics cards yet."
ENV_OVERRIDE = "The graphics card choice on this worker is set by environment variables."
NOT_ALLOWED = "This worker does not allow the manager to change its graphics card choice."
STALE = "The worker's card choice changed since this page was loaded. Reload and try again."
NOT_REPORTED = "The worker has not reported the card {key}."
IDS_DIFFER = "The ids of {key} differ from the worker's report."
ONE_FAMILY = ("Combine GPUs needs two or more cards that Blender drives the same way "
              "(CUDA/OptiX, HIP, oneAPI or Metal).")
HELD = "{key} has an open alert; dismiss it first."


def _pci_id(value):
    return value is None or _int_in(value, 0, MAX_PCI_ID)


def _distinct_keys(values):
    return all(is_card_key(key) for key in values) and len(set(values)) == len(values)


def _card_list(cards):
    return isinstance(cards, list) and len(cards) <= MAX_ENTRIES and all(
        type(card) is dict and sorted(card) == sorted(CARD_KEYS) for card in cards)


def shape_error(data):
    """Step 3: the first broken shape rule's sentence, or ``None`` (checked lazily, in order)."""
    if type(data) is not dict or sorted(data) != sorted(BODY_KEYS):  # a form QueryDict is not a dict here
        return BAD_BODY
    cards, shown = data['cards'], data['shown']
    checks = (
        (BAD_METHOD, lambda: isinstance(data['method'], str) and data['method'] in METHODS),
        (BAD_GPU_MODE, lambda: isinstance(data['gpu_mode'], str) and data['gpu_mode'] in GPU_MODES),
        (BAD_REVISION, lambda: _int_in(data['base_revision'], 0, MAX_REVISION)),
        (BAD_CARDS, lambda: _card_list(cards)),
        (BAD_KEY, lambda: all(is_card_key(card['key']) for card in cards)),
        (BAD_IDS, lambda: all(_pci_id(card['vendor_id']) and _pci_id(card['device_id']) for card in cards)),
        (DUPLICATE_CARD, lambda: _distinct_keys([card['key'] for card in cards])),
        (BAD_SHOWN, lambda: isinstance(shown, list) and len(shown) <= MAX_ENTRIES and _distinct_keys(shown)),
        (NOT_SHOWN, lambda: {card['key'] for card in cards} <= set(shown)),
        (GPU_NEEDS_CARD, lambda: data['method'] not in GPU_METHODS or len(cards) >= 1),
        (COMBINED_NEEDS_TWO, lambda: data['gpu_mode'] != 'combined' or len(cards) >= 2),
    )
    return next((sentence for sentence, passes in checks if not passes()), None)


def clean_body(data):
    """A body that passed ``shape_error``, copied in the fixed key order."""
    body = {name: data[name] for name in BODY_KEYS}
    body['cards'] = [{name: card[name] for name in CARD_KEYS} for card in data['cards']]
    body['shown'] = list(data['shown'])
    return body


def state_error(card_status, base_revision):
    """Step 4: the 409 sentence for the worker's reported state, or ``None``."""
    if not isinstance(card_status, dict) or card_status.get('schema') != 1:
        return NO_REPORT
    if card_status.get('env_override') is True:
        return ENV_OVERRIDE
    if card_status.get('manager_changes_allowed') is not True:
        return NOT_ALLOWED
    return STALE if card_status.get('revision') != base_revision else None


def one_family(backends):
    """FR-CC-4: two or more Blender cards (null backends ignored), all of one family."""
    families = [FAMILIES[backend] for backend in backends if backend in FAMILIES]
    return len(families) >= 2 and len(set(families)) == 1


def _held_keys(card_status):
    alerts = card_status.get('alerts')
    return {alert.get('key') for alert in (alerts if isinstance(alerts, list) else [])
            if isinstance(alert, dict) and alert.get('kind') in HOLDING_ALERTS}


def _first(keys):
    return next(keys, None)


def report_error(body, card_status):
    """Step 5: the 400 sentence for a rule against the stored report, or ``None``.

    A card the worker reports as ticked with the same ids may stay ticked
    while held (AC-26); only a newly ticked one is refused (D19).
    """
    reported = card_status.get('cards')
    stored = {entry['key']: entry for entry in (reported if isinstance(reported, list) else [])
              if isinstance(entry, dict) and isinstance(entry.get('key'), str)}
    missing = _first(key for key in body['shown'] if key not in stored)
    if missing is not None:
        return NOT_REPORTED.format(key=missing)
    ticked = [(card, stored[card['key']]) for card in body['cards']]  # ticked keys are shown, so stored
    differ = _first(card['key'] for card, entry in ticked
                    if (entry.get('vendor_id'), entry.get('device_id')) != (card['vendor_id'], card['device_id']))
    if differ is not None:
        return IDS_DIFFER.format(key=differ)
    if body['gpu_mode'] == 'combined' and not one_family([entry.get('backend') for _card, entry in ticked]):
        return ONE_FAMILY
    held = _held_keys(card_status)
    newly_held = _first(card['key'] for card, entry in ticked if entry.get('ticked') is not True
                        and (entry.get('state') in HOLDING_STATES or card['key'] in held))
    return HELD.format(key=newly_held) if newly_held is not None else None
