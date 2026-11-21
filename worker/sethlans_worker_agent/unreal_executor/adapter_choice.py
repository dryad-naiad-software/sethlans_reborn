# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics card an Unreal launch renders on (card selection FR-CU-1, D8, B4).

``choose(job_id)`` runs on the job thread before each warm-up and render
launch: a fresh OS probe (``cards.os_probe``; it never starts Blender), the
cached Blender list, the saved choice (``store.snapshot``) and the Unreal
cards in use (``cards.unreal_cards.evaluate``, read-only: no alert is raised
here). D8 (``card_order``) picks the only card, else the one with the most
dedicated memory (DXGI ``DedicatedVideoMemory``); among equal sizes a card
without a screen before one with a screen (an unknown mark counts as no
screen), then the lowest PCI address. The screen marks
(``cards.screens.marked``) are read only when no card key is handed over
(the ``SETHLANS_UNREAL_VRAM_MB`` override); with a key the gate already
chose by the same order and one card is left. Its
``index`` is the card's raw position in the
``EnumAdapterByGpuPreference(HIGH_PERFORMANCE)`` list (``EnumAdapters1``
without ``IDXGIFactory6``), counting the software and virtual adapters: the
index UE 5.8 reads ``-graphicsadapter`` by. No card: ``LocalConflict
('local_conflict', <FR-CU-6 reason>)`` before any launch.

The latest choice of each running Unreal claim is kept, keyed by the
reservation's ``claim_id`` (render per card D16, FR-PC-SEL-3), so the card
check can stop the job on a card that is lost, changed or held while it
renders, and only that job (FR-CK-3, C-24 m, FR-PC-SEL-4): ``all_current()``
lists them; ``forget(claim_id)`` drops one when its job ends. Two claims of
one job id (a retained claim and a re-claim) never overwrite each other.
``_lock`` is a leaf lock.
"""
import logging
import threading
from typing import Dict, NamedTuple, Optional, Tuple

from sethlans_worker_agent.cards import inventory, os_probe, screens, store, unreal_cards
from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor import card_order
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict

logger = logging.getLogger(__name__)

READ_FAILED_REASON = "the worker's graphics card settings could not be read"
KEPT_CARD_REASON = "the graphics card this job started on can no longer be used"


class Choice(NamedTuple):
    job_id: int
    key: str            # card key, ``pci:dddd:bb:dd.f``
    name: str
    index: int          # DXGI index: -graphicsadapter=<index>
    vendor_id: Optional[int] = None     # checked against UE's "Found D3D12 adapter" line (adapter_log)
    device_id: Optional[int] = None
    revision_id: Optional[int] = None


_lock = threading.Lock()
_current: Dict[str, Choice] = {}   # claim_id -> the claim's latest choice


def pick(cards):
    """D8 (``card_order``): the only card, else the most dedicated memory; ties to a card without a screen,
    then to the lowest PCI address."""
    return min(cards, key=lambda card: card_order.sort_key(card.dedicated_mib, card.screen, card.key))


def _cards_now(section, with_screens: bool):
    from sethlans_worker_agent.cards.startup_log import cached_blender_list
    if not section.cards:
        return unreal_cards.evaluate(section, None)
    blender_cards, version = cached_blender_list()
    inv = inventory.build(os_probe.probe(), blender_cards, version)
    return unreal_cards.evaluate(section, screens.marked(inv) if with_screens else inv)


def choose(job_id, key: Optional[str] = None, *, claim_id: str) -> Choice:
    """The card for this launch and its DXGI index; ``LocalConflict`` when Unreal has no card now.

    ``key``: the card of the job's first launch (the held card). D8 gives a
    job one card, so later launches only re-resolve that card's index, and
    fail when it can no longer be used (warm-up and render never land on two
    cards). ``claim_id``: the reservation's (D16), the key the choice is kept by.
    """
    if not claim_id:
        raise ValueError("adapter_choice.choose needs the reservation's claim_id (D16).")
    section = store.snapshot()
    if section.read_failed:
        raise LocalConflict('local_conflict', READ_FAILED_REASON)
    cards, reason = _cards_now(section, with_screens=key is None)  # with a key there is one card: no tie
    if key is not None:  # by address, as the gate and the reserver compare; an unreadable key matches nothing
        address = key_address(key)
        cards = tuple(card for card in cards if address is not None and key_address(card.key) == address)
        reason = KEPT_CARD_REASON
    if not cards:
        logger.warning("Unreal job %s: no graphics card to render on (%s).", job_id, reason)
        raise LocalConflict('local_conflict', reason)
    card = pick(cards)
    choice = Choice(job_id, card.key, card.name, card.dxgi_index, card.vendor_id, card.device_id, card.revision_id)
    with _lock:
        _current[claim_id] = choice
    logger.info("Unreal job %s renders on %s (%s), DXGI adapter %d.", job_id, ascii(card.name), card.key,
                card.dxgi_index)
    return choice


def d8_key() -> Optional[str]:
    """D8's card among the Unreal cards in use now, screen marks read (the reading override, render per card D14).

    The claim holds it and the first launch is asked for it (``choose(key=)``),
    so the held card is the card the job renders on. ``None`` when the saved
    choice cannot be read or there is no Unreal card. No alert is raised.
    """
    section = store.snapshot()
    if section.read_failed:
        return None
    cards, _reason = _cards_now(section, with_screens=True)
    return pick(cards).key if cards else None


def all_current() -> Tuple[Choice, ...]:
    """The latest choice of every running Unreal claim (FR-PC-SEL-3), in no particular order."""
    with _lock:
        return tuple(_current.values())


def forget(claim_id: str) -> None:
    """The claim's job ended: its choice no longer names a running render. Other claims keep theirs."""
    with _lock:
        _current.pop(claim_id, None)


def reset_for_tests() -> None:
    with _lock:
        _current.clear()
