# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics card an Unreal launch renders on (card selection FR-CU-1, D8, B4).

``choose(job_id)`` runs on the job thread before each warm-up and render
launch: a fresh OS probe (``cards.os_probe``; it never starts Blender), the
cached Blender list, the saved choice (``store.snapshot``) and the Unreal
cards in use (``cards.unreal_cards.evaluate``, read-only: no alert is raised
here). D8 picks the only card, else the one with the most dedicated memory
(DXGI ``DedicatedVideoMemory``), ties to the lowest PCI address. Its
``index`` is the card's raw position in the
``EnumAdapterByGpuPreference(HIGH_PERFORMANCE)`` list (``EnumAdapters1``
without ``IDXGIFactory6``), counting the software and virtual adapters: the
index UE 5.8 reads ``-graphicsadapter`` by. No card: ``LocalConflict
('local_conflict', <FR-CU-6 reason>)`` before any launch.

The latest choice of the running Unreal job is kept (``current()``) so the
card check can stop that job when its card is lost, changed or held while it
renders (FR-CK-3, C-24 m); ``forget(job_id)`` clears it when the job ends.
``_lock`` is a leaf lock.
"""
import logging
import threading
from typing import NamedTuple, Optional

from sethlans_worker_agent.cards import inventory, os_probe, store, unreal_cards
from sethlans_worker_agent.cards.identity import key_sort_value
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
_current: Optional[Choice] = None


def pick(cards):
    """D8: the only card, else the most dedicated memory; ties to the lowest PCI address."""
    return min(cards, key=lambda card: (-(card.dedicated_mib or 0), key_sort_value(card.key)))


def _cards_now(section):
    from sethlans_worker_agent.cards.startup_log import cached_blender_list
    if not section.cards:
        return unreal_cards.evaluate(section, None)
    blender_cards, version = cached_blender_list()
    return unreal_cards.evaluate(section, inventory.build(os_probe.probe(), blender_cards, version))


def choose(job_id, key: Optional[str] = None) -> Choice:
    """The card for this launch and its DXGI index; ``LocalConflict`` when Unreal has no card now.

    ``key``: the card of the job's first launch. D8 gives a job one card,
    so later launches only re-resolve that card's index, and fail when it
    can no longer be used (warm-up and render never land on two cards).
    """
    global _current
    section = store.snapshot()
    if section.read_failed:
        raise LocalConflict('local_conflict', READ_FAILED_REASON)
    cards, reason = _cards_now(section)
    if key is not None:
        cards, reason = tuple(card for card in cards if card.key == key), KEPT_CARD_REASON
    if not cards:
        logger.warning("Unreal job %s: no graphics card to render on (%s).", job_id, reason)
        raise LocalConflict('local_conflict', reason)
    card = pick(cards)
    choice = Choice(job_id, card.key, card.name, card.dxgi_index, card.vendor_id, card.device_id, card.revision_id)
    with _lock:
        _current = choice
    logger.info("Unreal job %s renders on %s (%s), DXGI adapter %d.", job_id, ascii(card.name), card.key,
                card.dxgi_index)
    return choice


def current() -> Optional[Choice]:
    """The latest choice of the running Unreal job, or ``None``."""
    with _lock:
        return _current


def forget(job_id) -> None:
    """The job ended: its choice no longer names a running render."""
    global _current
    with _lock:
        if _current is not None and _current.job_id == job_id:
            _current = None


def reset_for_tests() -> None:
    global _current
    with _lock:
        _current = None
