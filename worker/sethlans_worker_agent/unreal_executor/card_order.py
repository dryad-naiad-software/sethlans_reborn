# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The order of the Unreal cards: the largest first; among equal-size cards,
prefer one without a screen, then the lowest key.

The most memory first. Among cards of the same size, one known to drive a
screen (``screen is True``) comes after one that does not; an unknown mark
(``None``, for example in session 0 under a Windows service) counts as no
screen, so without screen marks the order is exactly the lowest card key.
Then the lowest card key (``identity.key_sort_value``: address integers, a
keyless or unreadable key last).

The memory gate (``vram_gate_check.job_card``) and the launch's choice
(``adapter_choice.pick``) both sort by ``sort_key``, so the gated card is
the card the job renders on. Unreal only; Blender's device use does not
read it.
"""
from typing import Optional

from sethlans_worker_agent.cards.identity import key_sort_value


def sort_key(size_mib: Optional[int], screen: Optional[bool], key: Optional[str]) -> tuple:
    """Ascending order: the first card is the one Unreal renders on."""
    return -(size_mib or 0), screen is True, key_sort_value(key)
