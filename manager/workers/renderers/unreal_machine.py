# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-machine Unreal memory results (Unreal per-machine memory test D1, D5,
FR-MT-K-3).

``worker_kinds(worker)`` gives the card kinds a worker is eligible for:
the first ``MAX_KINDS_PER_WORKER`` sorted distinct kinds of the cards in
its stored ``card_status.cards`` that are ``ticked`` true, in state
``ok`` and have a kind (a vendor and a device id).  The stored report is
self-reported and already reduced by ``views.heartbeat_cards``; anything
not in that shape counts as no card here.
"""

from shared.unreal.card_kind import card_kind

# D5: at most this many kinds per worker, so a changing revision id cannot
# grow the number of pilots a worker gets.
MAX_KINDS_PER_WORKER = 4
_OK = 'ok'


def _card_kind_of(card):
    """The D1 kind of a stored card that is ticked and ``ok``, else ``None``."""
    if not isinstance(card, dict) or card.get('ticked') is not True or card.get('state') != _OK:
        return None
    return card_kind(card.get('vendor_id'), card.get('device_id'), card.get('revision_id'))


def worker_kinds(worker) -> tuple:
    """The first four sorted distinct kinds of the worker's ticked ``ok`` cards (D5)."""
    status = worker.card_status
    cards = status.get('cards') if isinstance(status, dict) else None
    if not isinstance(cards, list):
        return ()
    kinds = {_card_kind_of(card) for card in cards} - {None}
    return tuple(sorted(kinds)[:MAX_KINDS_PER_WORKER])
