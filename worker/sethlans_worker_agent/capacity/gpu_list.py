# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU list identity for the card check (#239).

A worker's GPU list is the ordered list of physical cards that
``hardware_detection._filter_preferred_gpus`` returns: one record per
card, carrying Blender's device ``id`` and the card's preferred backend
``type``. The uncached detection (``drift.detect_physical_gpus_now``)
returns it as ``(id, type)`` pairs; the card check joins them with the OS
card list by PCI address (``cards.inventory``).
"""
from typing import List, Sequence, Tuple

GpuIdentity = Tuple[object, object]  # (Blender device id, backend type)


def gpu_identity(records: Sequence) -> List[GpuIdentity]:
    """Return the ordered (id, type) pairs of GPU records (dicts)."""
    return [
        (r.get('id'), r.get('type')) if isinstance(r, dict) else (r, None)
        for r in (records or [])
    ]
