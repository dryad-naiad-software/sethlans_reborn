# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Logs the joined card list once, when the worker builds its capacity.

Information only: nothing here changes which cards the worker renders on.
It runs the OS probe, joins it with the cached Blender list (it never starts
Blender) and writes one INFO line per card. It never raises into the caller.
"""
import logging
import threading
from typing import Optional, Tuple

from sethlans_worker_agent.cards import inventory, os_probe

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_done = False


def cached_blender_list() -> Tuple[object, Optional[str]]:
    """The cached physical Blender list and its Blender version.

    ``(None, None)`` before detection. A failed device check caches ``[]`` and
    leaves the executable unset (a success sets the executable first), so that
    case is ``(inventory.BLENDER_CHECK_FAILED, None)``, not an empty list.
    """
    from sethlans_worker_agent import hardware_detection
    from sethlans_worker_agent.capacity.drift import installed_version_of
    cards = hardware_detection._gpu_details_cache  # set after its executable, so both belong together
    executable = hardware_detection._gpu_details_blender_exe
    if cards is None:
        return None, None
    if executable is None:
        return inventory.BLENDER_CHECK_FAILED, None
    return list(cards), installed_version_of(executable)


def _describe(card: inventory.Card) -> str:
    parts = [f'{card.key} {ascii(card.name)}']
    if card.vendor_id is not None and card.device_id is not None:
        revision = f' rev {card.revision_id:02x}' if card.revision_id is not None else ''
        parts.append(f'ids {card.vendor_id:04x}:{card.device_id:04x}{revision}')
    if card.dxgi_index is not None:
        parts.append(f'DXGI adapter {card.dxgi_index}')
    if card.dedicated_mib is not None:
        parts.append(f'{card.dedicated_mib} MiB')
    if card.blender is not None:
        parts.append(f'Blender {ascii(card.blender[1])} {ascii(card.blender[0])}')
    if card.note:
        parts.append(card.note)
    return ', '.join(parts)


def _blender_text(inv: inventory.Inventory, blender_cards) -> str:
    if inv.blender_version:
        return f'Blender {inv.blender_version}'
    if blender_cards is None:
        return 'no Blender detection yet'
    if blender_cards is inventory.BLENDER_CHECK_FAILED:
        return 'the Blender device check failed'
    return 'Blender version unknown'  # detection Blender outside the managed blender-<ver>-<platform> folders


def _log(inv: inventory.Inventory, blender_cards=None) -> None:
    source = 'from Blender; the operating system list failed' if inv.os_probe_failed else 'operating system list'
    blender = _blender_text(inv, blender_cards)
    logger.info("Graphics cards (%s, %s): %d found. The worker does not use this list yet.",
                source, blender, len(inv.cards))
    for card in inv.cards:
        logger.info("  - %s", _describe(card))


def log_once_at_start() -> None:
    """Probe, join and log the card list, the first time only."""
    global _done
    with _lock:
        if _done:
            return
        _done = True
    try:
        blender_cards, version = cached_blender_list()
        _log(inventory.build(os_probe.probe(), blender_cards, version), blender_cards)
    except Exception:  # information only: never break the capacity build
        logger.warning("The graphics card list could not be logged.", exc_info=True)


def reset_for_tests() -> None:
    """Allow the next call to log again. Test-only hook."""
    global _done
    with _lock:
        _done = False
