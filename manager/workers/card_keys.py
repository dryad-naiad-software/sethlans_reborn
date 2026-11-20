# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The graphics card key, as the worker reports it (worker card selection D3;
gpu-memory-reader FR-GH-3).

A leaf module: it imports only ``re``, so both heartbeat view modules
(``heartbeat_cards`` and ``heartbeat_unreal``) can use it without one
importing the other.
"""
import re

APPLE_GPU_KEY = 'apple-gpu'
_CARD_KEY = re.compile(r'pci:[0-9a-f]{4,8}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', re.ASCII)


def is_pci_card_key(value) -> bool:
    """True for a normalised ``pci:dddd:bb:dd.f`` key only (not ``apple-gpu``)."""
    return isinstance(value, str) and _CARD_KEY.fullmatch(value) is not None


def is_card_key(value) -> bool:
    """True for ``apple-gpu`` and a normalised ``pci:dddd:bb:dd.f`` key."""
    return value == APPLE_GPU_KEY or is_pci_card_key(value)
