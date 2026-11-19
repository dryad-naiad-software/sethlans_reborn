# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Screen marks: which card drives a screen, and the main screen (FR-CS-1, FR-CS-2, O10).

``read(inventory)`` gives ``{key: (screen, primary_screen)}`` for every card
of the inventory, each ``True``, ``False`` or ``None`` (unknown). It picks the
platform module (``screens_windows``: DXGI outputs; ``screens_linux``: DRM
connectors) and never raises; any failure gives ``(None, None)`` for every
card. On macOS the marks come from the card probe's own ``system_profiler``
output, which ``inventory.build`` already put on ``apple-gpu``
(``screen`` = a display is listed under the GPU, ``primary_screen`` =
``screen``); nothing is run a second time.

``marked(inventory)`` is the inventory with those marks on its cards. The
start check, the periodic check, the light check during an Unreal hold and
the card pages' probe call it; a manager request does not (FR-CK-1). Marks
are display data: they never change a tick, a slot or a state.

Holds no lock and keeps no state except the once-only log record.
"""
import dataclasses
import logging
import sys
from typing import Dict, Optional, Tuple

from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

Marks = Tuple[Optional[bool], Optional[bool]]
UNKNOWN: Marks = (None, None)


def _platform_marks(platform_name: str, inventory) -> Optional[dict]:
    """The platform's ``{key: marks}``, or ``None`` when they cannot be read."""
    if platform_name == 'win32':
        from sethlans_worker_agent.cards import screens_windows
        return screens_windows.read()
    if platform_name.startswith('linux'):
        from sethlans_worker_agent.cards import screens_linux
        return screens_linux.read()
    if platform_name == 'darwin':
        return {card.key: (card.screen, card.primary_screen) for card in inventory.cards}
    return None


def _flag(value) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _pair(found) -> Marks:
    """Well-formed marks; an unknown ``screen`` makes both unknown."""
    if not isinstance(found, tuple) or len(found) != 2 or _flag(found[0]) is None:
        return UNKNOWN
    return found[0], _flag(found[1])


def read(inventory, platform_name: Optional[str] = None) -> Dict[str, Marks]:
    """``{key: (screen, primary_screen)}`` for every inventory card; never raises."""
    platform_name = sys.platform if platform_name is None else platform_name
    keys = [card.key for card in inventory.cards]
    try:
        found = _platform_marks(platform_name, inventory)
    except Exception:  # FR-CS-1: the marks are unknown, never an error for the caller
        log_once(logger, logging.WARNING, "Which graphics card drives a screen could not be read.", exc_info=True)
        found = None
    if not isinstance(found, dict):
        logger.debug("Screen marks unavailable; every card reads 'screen: unknown'.")
        return {key: UNKNOWN for key in keys}
    return {key: _pair(found.get(key)) for key in keys}


def marked(inventory, platform_name: Optional[str] = None):
    """``inventory`` with fresh screen marks on its cards."""
    if not inventory.cards:
        return inventory
    marks = read(inventory, platform_name)
    cards = tuple(dataclasses.replace(card, screen=marks[card.key][0], primary_screen=marks[card.key][1])
                  for card in inventory.cards)
    return dataclasses.replace(inventory, cards=cards)
