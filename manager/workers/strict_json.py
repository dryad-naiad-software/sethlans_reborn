# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Strict JSON type helpers shared by the self-reported worker payloads.

Used by the heartbeat's Unreal keys (``views/heartbeat_unreal.py``), the
``pilot-result/`` body and the measurement retry body (Unreal foundation
FR-PM-4, FR-PM-6).  Python's ``bool`` is an ``int`` subclass and the JSON
parser may produce ``float('nan')``; neither passes here.  No DRF import.
"""

import math


def _is_int(value):
    """``int`` but not ``bool`` (``True`` is an ``int`` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _int_in(value, low, high):
    return _is_int(value) and low <= value <= high


def is_number(value):
    """An ``int`` (not ``bool``) or a finite ``float``; never a string."""
    if _is_int(value):
        return True
    return isinstance(value, float) and math.isfinite(value)


def number_in(value, low, high):
    return is_number(value) and low <= value <= high


def _unknown_in(obj, allowed, prefix, found):
    if isinstance(obj, dict):
        found.extend(f"{prefix}{key}" for key in obj if key not in allowed)


def unknown_keys(data, allowed, nested=None, lists=None):
    """Return the sorted dotted names of keys outside the allowed sets.

    Args:
        data: The parsed body (anything; only dicts are inspected).
        allowed: Allowed top-level keys.
        nested: ``{key: allowed}`` for nested objects (``gpu.vendor``).
        lists: ``{key: allowed}`` for lists of objects (``per_shot[3].x``).
    """
    found = []
    if not isinstance(data, dict):
        return found
    _unknown_in(data, allowed, '', found)
    for key, inner in (nested or {}).items():
        _unknown_in(data.get(key), inner, f"{key}.", found)
    for key, inner in (lists or {}).items():
        items = data.get(key)
        if isinstance(items, list):
            for index, item in enumerate(items):
                _unknown_in(item, inner, f"{key}[{index}].", found)
    return sorted(found)
