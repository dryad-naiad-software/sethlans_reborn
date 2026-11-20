# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Windows card probe (FR-CI-2).

DXGI lists the adapters in the order Unreal indexes ``-graphicsadapter`` by
(``gpu_memory.win_dxgi``); D3DKMT gives each adapter's PCI address by LUID
(``gpu_memory.win_adapter_info``). The DXGI index of a card is the raw index
in that list, counting every adapter. An adapter is a card only when it is
not flagged software, has dedicated memory, and its address gives a card key;
so the software adapter and a virtual display adapter (no PCI address) are not
cards. Two adapters with the same key are both dropped, with one ERROR line.

The LUID is used inside this call only and compared as integers; it is never
kept, saved or sent (it changes on reboot). ``cards_with_luids()`` returns it
with each card for the GPU memory reader, which keeps it in memory only.
"""
import logging
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key, clean_name
from sethlans_worker_agent.cards.os_probe import OsCard, log_once

logger = logging.getLogger(__name__)

MIB = 1024 * 1024


def _card(adapter, pci_address) -> Optional[OsCard]:
    from sethlans_worker_agent.gpu_memory.win_dxgi import DXGI_ADAPTER_FLAG_SOFTWARE
    if adapter.flags & DXGI_ADAPTER_FLAG_SOFTWARE or adapter.dedicated_bytes <= 0:
        return None
    key = card_key(pci_address(adapter.luid))
    if key is None:
        return None
    return OsCard(key=key, name=clean_name(adapter.name), vendor_id=adapter.vendor_id,
                  device_id=adapter.device_id, dxgi_index=adapter.index,
                  dedicated_mib=adapter.dedicated_bytes // MIB, revision_id=adapter.revision)


def _drop_duplicates(cards) -> Tuple[OsCard, ...]:
    counts = {}
    for card in cards:
        counts[card.key] = counts.get(card.key, 0) + 1
    for key, count in counts.items():
        if count > 1:
            log_once(logger, logging.ERROR, "%d graphics adapters report the PCI address %s; none of them is used.",
                     count, key)
    return tuple(card for card in cards if counts[card.key] == 1)


def cards_with_luids() -> Optional[Tuple[Tuple[OsCard, Tuple[int, int]], ...]]:
    """``probe()``'s cards, each with its ``(HighPart, LowPart)`` LUID; ``None`` when DXGI or D3DKMT is unavailable.

    For the GPU memory reader only (GPU memory reader FR-GW-4): the LUID stays in the result, in memory.
    """
    from sethlans_worker_agent.gpu_memory import win_adapter_info, win_dxgi
    adapters = win_dxgi.enumerate_adapters()
    if adapters is None:
        logger.debug("The DXGI adapter list is unavailable.")
        return None
    if not win_adapter_info.available():
        logger.debug("PCI addresses of the adapters cannot be read (D3DKMT unavailable).")
        return None
    pairs = [(_card(adapter, win_adapter_info.pci_address), adapter.luid) for adapter in adapters]
    pairs = [(card, luid) for card, luid in pairs if card]
    kept = {card.key for card in _drop_duplicates([card for card, _luid in pairs])}
    return tuple((card, luid) for card, luid in pairs if card.key in kept)


def probe() -> Optional[Tuple[OsCard, ...]]:
    """The cards DXGI and D3DKMT report, in DXGI order; ``None`` when either is unavailable."""
    pairs = cards_with_luids()
    return None if pairs is None else tuple(card for card, _luid in pairs)
