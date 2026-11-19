# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card states, notices and the cards in use, from one section and one inventory (FR-CK-2, FR-CK-4, FR-CL-1).

The state of each ticked card; the first matching row wins (FR-CK-2):

- ``lost``: the key is not in the inventory (macOS: no single Metal device,
  so the inventory has no ``apple-gpu``);
- ``changed``: the model differs (D3 with ``revision_id``, C-17). A card whose
  saved or current side has no vendor and device ids (the Blender fallback
  when the OS probe failed, macOS) has an unknown model and is never
  ``changed`` (C-18);
- ``held``: an open ``card_lost`` or ``card_changed`` alert names it (D19);
- ``blender_lost``: an open ``card_lost_blender`` alert names it, or Blender
  offered it in the profile (``profile_keys``) and a fresh detection with the
  same Blender (``blender_fresh=True``) does not; Unreal may still use it;
- ``ok``. Unticked inventory cards are ``unticked``.

``unknown`` is the state of a ticked key missing from an inventory built
from Blender because the OS probe failed: there is no OS list to miss it from.
A failed probe or detection (``inventory`` or ``blender_fresh`` ``None``)
changes nothing: ``evaluate`` returns ``failed=True`` and the caller raises
no alert, stops no job and requests no rebuild (AC-11).

Pure apart from once-only log lines and the process-wide records of
``SETHLANS_COMPUTE_CARDS`` keys (``in_use.env_records``); holds no lock.
"""
import dataclasses
import logging
from typing import Dict, Iterable, NamedTuple, Optional, Tuple

from sethlans_worker_agent.cards.identity import has_ids, key_sort_value, same_model
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

LOST, CHANGED, HELD, BLENDER_LOST, OK = 'lost', 'changed', 'held', 'blender_lost', 'ok'
UNTICKED, UNKNOWN = 'unticked', 'unknown'
HOLDING_KINDS = frozenset({'card_lost', 'card_changed'})                      # held from both renderers (D19)
BLENDER_HOLDING_KINDS = HOLDING_KINDS | {'card_lost_blender'}                 # held from Blender
ALERT_KIND = {LOST: 'card_lost', CHANGED: 'card_changed', BLENDER_LOST: 'card_lost_blender'}


class Unusable(NamedTuple):
    """A ticked card to alert for (FR-CK-3): ``kind``, ``key``, ``name``, ``detail`` as ``store.add_alert`` takes them."""
    kind: str
    key: str
    name: str
    detail: str
    state: str


@dataclasses.dataclass(frozen=True)
class Evaluation:
    """One evaluation. ``failed``: nothing here may change a state, raise an alert or a rebuild."""
    failed: bool = False
    section: object = None                    # the section evaluated (env records filled in)
    states: Dict[str, str] = dataclasses.field(default_factory=dict)  # key -> state, ticked and inventory cards
    unusable: Tuple[Unusable, ...] = ()
    notices: Tuple[dict, ...] = ()            # {"kind": "card_new" | "card_gone", "key", "name"}
    problem: str = ''                         # FR-CK-6
    blender_cards: Tuple = ()                 # in_use.CardInUse, PCI address order
    unreal_cards: Tuple = ()                  # inventory.Card with a DXGI index, PCI address order


def _alert_kinds(alerts) -> Dict[str, set]:
    kinds: Dict[str, set] = {}
    for alert in alerts:
        kinds.setdefault(alert.get('key'), set()).add(alert.get('kind'))
    return kinds


def card_state(saved: dict, card, kinds: set, offered_before: bool, blender_fresh: bool,
               probe_failed: bool) -> str:
    """FR-CK-2 for one ticked card; ``card`` is the inventory card with the saved key, or ``None``."""
    if card is None:
        return UNKNOWN if probe_failed else LOST
    if has_ids(saved) and has_ids(card) and not same_model(saved, card):
        return CHANGED
    if kinds & HOLDING_KINDS:
        return HELD
    if 'card_lost_blender' in kinds or (blender_fresh and offered_before and card.blender is None):
        return BLENDER_LOST
    return OK


def card_states(section, inv, alerts=None, profile_keys: Iterable[str] = (),
                blender_fresh: bool = False) -> Dict[str, str]:
    """Key -> state for every ticked card and every inventory card."""
    kinds = _alert_kinds(section.alerts if alerts is None else alerts)
    by_key = {card.key: card for card in inv.cards}
    offered = set(profile_keys)
    states = {card.key: UNTICKED for card in inv.cards}
    for saved in section.cards:
        key = saved['key']
        states[key] = card_state(saved, by_key.get(key), kinds.get(key, set()), key in offered, blender_fresh,
                                 inv.os_probe_failed)
    return states


def _detail(state: str, saved: dict, card, inv) -> str:
    if state == LOST:
        return 'missing from the card list'
    if state == CHANGED:
        return f'was {saved.get("name") or saved["key"]}, now {card.name}'
    version = f' {inv.blender_version}' if inv.blender_version else ''
    return f'not offered by Blender{version} any more'


def unusable(section, inv, states: Dict[str, str]) -> Tuple[Unusable, ...]:
    """The ticked cards that are ``lost``, ``changed`` or ``blender_lost``, in PCI address order."""
    by_key = {card.key: card for card in inv.cards}
    found = []
    for saved in sorted(section.cards, key=lambda record: key_sort_value(record['key'])):
        key, state = saved['key'], states.get(saved['key'])
        if state in ALERT_KIND:
            card = by_key.get(key)
            name = saved.get('name') or (card.name if card is not None else '') or key
            found.append(Unusable(ALERT_KIND[state], key, name, _detail(state, saved, card, inv), state))
    return tuple(found)


def notices(section, inv) -> Tuple[dict, ...]:
    """FR-CK-4: ``card_new`` for an inventory key not in ``known``; ``card_gone`` for an unticked known key now absent.

    No ``card_gone`` from an inventory built from Blender after a failed OS
    probe: a card Blender does not offer is not gone from the machine.
    """
    known = set(section.known)
    present = {card.key for card in inv.cards}
    found = [{'kind': 'card_new', 'key': card.key, 'name': card.name} for card in inv.cards if card.key not in known]
    if not inv.os_probe_failed:
        ticked = set(section.ticked_keys)
        found += [{'kind': 'card_gone', 'key': key, 'name': key} for key in sorted(known, key=key_sort_value)
                  if key not in ticked and key not in present]
    return tuple(found)


def _blender_cards(section, inv, states, in_use) -> Tuple[Tuple, str]:
    """The Blender cards in use and the problem text, with the one-family rule for Combine GPUs."""
    cards = in_use.blender_cards_in_use(inv, section, states=states)
    if section.gpu_mode == 'combined' and len(cards) > 1 and not in_use.one_family(card.backend for card in cards):
        log_once(logger, logging.ERROR, "Combine GPUs is set for cards Blender drives in different ways (%s); "
                 "this worker takes no GPU jobs until the choice is changed.",
                 ', '.join(sorted({card.backend for card in cards})))
        return (), in_use.COMBINE_PROBLEM_TEXT
    return cards, in_use.problem_message(section, cards)


def evaluate(section, inventory, blender_fresh: Optional[bool] = False,
             profile_keys: Iterable[str] = ()) -> Evaluation:
    """States, alerts to raise, notices, problem text and cards in use for one section and one inventory.

    ``section``: ``store.snapshot()`` (its ``alerts`` include the in-memory
    ones). ``inventory``: built from a fresh OS probe, or ``None`` when the
    probe failed. ``blender_fresh``: ``True`` when the inventory was joined
    with a fresh detection by the profile's Blender, ``False`` for the cached
    list, ``None`` when that detection failed. ``profile_keys``: the keys of
    the profile's Blender cards. ``section.read_failed`` is not looked at
    here; callers that must not act on defaults check it first.
    """
    if inventory is None or blender_fresh is None:
        return Evaluation(failed=True, section=section)
    from sethlans_worker_agent.cards import in_use  # in_use imports this module
    if section.env_cards:
        section = dataclasses.replace(section, cards=in_use.env_records(section.ticked_keys, inventory))
    states = card_states(section, inventory, profile_keys=profile_keys, blender_fresh=blender_fresh)
    cards, problem = _blender_cards(section, inventory, states, in_use)
    return Evaluation(section=section, states=states, unusable=unusable(section, inventory, states),
                      notices=notices(section, inventory), problem=problem, blender_cards=cards,
                      unreal_cards=in_use.unreal_cards_in_use(inventory, section, states=states))
