# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
GPU list identity and comparison for the hardware change check (#239).

A worker's GPU list is the ordered list of physical cards that
``hardware_detection._filter_preferred_gpus`` returns: one record per
card, carrying Blender's device ``id`` and the card's preferred backend
``type``. Two lists are the same only when they hold the same
``(id, type)`` pairs in the same order; a matching count is not enough.

To name what changed, cards are matched by their physical id: the device
id without a trailing ``_OptiX`` (the rule ``_filter_preferred_gpus``
uses to group the entries of one card), so a card whose preferred backend
moved between CUDA and OptiX reads as "backend changed", not as one card
gone and another new. Every id is formatted with ``ascii()``, so a device
name with non-ASCII characters cannot break a log handler.
"""
from typing import List, Optional, Sequence, Tuple

GpuIdentity = Tuple[object, object]  # (Blender device id, backend type)


def gpu_identity(records: Sequence) -> List[GpuIdentity]:
    """Return the ordered (id, type) pairs of GPU records (dicts)."""
    return [
        (r.get('id'), r.get('type')) if isinstance(r, dict) else (r, None)
        for r in (records or [])
    ]


def _physical_id(dev_id) -> str:
    text = str(dev_id)
    return text[:-6] if text.endswith('_OptiX') else text


def _label(card: GpuIdentity) -> str:
    dev_id, dev_type = card
    return f"{dev_type} {ascii(dev_id)}"


def _labels(cards: Sequence[GpuIdentity]) -> str:
    return '[' + ', '.join(_label(c) for c in cards) + ']'


def describe_gpu_list_change(
    in_use: Sequence[GpuIdentity], now: Sequence[GpuIdentity],
) -> Optional[str]:
    """Return a plain description of how ``now`` differs, or None if equal.

    ``in_use`` is the list the worker uses (cached at startup), ``now``
    the list a fresh detection found. Any difference counts: a card gone,
    a card new, a card whose backend or id changed, or the same cards in
    a different order.
    """
    in_use, now = list(in_use), list(now)
    if in_use == now:
        return None
    by_card_in_use = {_physical_id(c[0]): c for c in in_use}
    by_card_now = {_physical_id(c[0]): c for c in now}
    parts = []
    gone = [c for key, c in by_card_in_use.items() if key not in by_card_now]
    if gone:
        parts.append(f"cards gone: {_labels(gone)}")
    new = [c for key, c in by_card_now.items() if key not in by_card_in_use]
    if new:
        parts.append(f"cards new: {_labels(new)}")
    changed = [
        f"{_label(c)} -> {_label(by_card_now[key])}"
        for key, c in by_card_in_use.items()
        if key in by_card_now and by_card_now[key] != c
    ]
    if changed:
        parts.append(f"backend or id changed: [{', '.join(changed)}]")
    order_in_use = [k for k in by_card_in_use if k in by_card_now]
    order_now = [k for k in by_card_now if k in by_card_in_use]
    if order_in_use != order_now:
        parts.append("order changed")
    if not parts:
        parts.append("list differs")
    return f"{'; '.join(parts)} (in use {_labels(in_use)}, detected now {_labels(now)})"
