# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card identity: card keys, Blender device addresses, "same model".

- ``card_key(pci_text)``: ``'pci:' + normalize_pci(text)``, or ``None``.
- ``blender_pci(device_id)``: ``(domain, bus, device)`` as integers from a
  Blender device id such as ``HIP_AMD Radeon RX 7900 XT_0000:23:00`` or
  ``CUDA_NVIDIA GeForce RTX 5090_0000:01:00_OptiX``, or ``None``.
- ``same_model(saved, now)``: equal vendor and device ids when both sides have
  them, else equal normalised names.

Addresses are compared as parsed integers everywhere, never as text, so a
domain longer than four digits still matches.
"""
import re
from typing import Any, Optional, Tuple

from sethlans_worker_agent.gpu_memory.pci import normalize_pci

APPLE_GPU_KEY = 'apple-gpu'
KEY_PREFIX = 'pci:'
NAME_LIMIT = 128

_BLENDER_ADDRESS = re.compile(r'([0-9a-f]{4,8}):([0-9a-f]{2}):([0-9a-f]{2})(?:\.[0-7])?', re.IGNORECASE)
_KEY_ADDRESS = re.compile(r'pci:([0-9a-f]{4,8}):([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])')
_OPTIX_SUFFIX = '_OptiX'


def card_key(pci_text) -> Optional[str]:
    """The card key for a PCI address text, or ``None`` when it does not normalise."""
    normalized = normalize_pci(pci_text) if isinstance(pci_text, str) else None
    return KEY_PREFIX + normalized if normalized else None


def key_address(key) -> Optional[Tuple[int, int, int, int]]:
    """``(domain, bus, device, function)`` of a ``pci:`` key, else ``None`` (``apple-gpu`` too)."""
    if not isinstance(key, str):
        return None
    match = _KEY_ADDRESS.fullmatch(key)
    if match is None:
        return None
    domain, bus, device, function = (int(part, 16) for part in match.groups())
    return domain, bus, device, function


def key_sort_value(key) -> Tuple[int, ...]:
    """Sort value giving PCI address order (D4); keys without an address sort last."""
    address = key_address(key)
    return (0,) + address if address is not None else (1,)


def blender_pci(device_id) -> Optional[Tuple[int, int, int]]:
    """``(domain, bus, device)`` from the address at the end of a Blender device id."""
    if not isinstance(device_id, str):
        return None
    text = device_id.removesuffix(_OPTIX_SUFFIX)
    match = _BLENDER_ADDRESS.fullmatch(text.rsplit('_', 1)[-1])
    if match is None:
        return None
    domain, bus, device = (int(part, 16) for part in match.groups())
    return domain, bus, device


def _field(record: Any, name: str):
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _pci_id(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_name(name) -> str:
    """Name for comparison: case-folded, runs of whitespace collapsed to one space."""
    return ' '.join(str(name or '').split()).casefold()


def same_model(saved, now) -> bool:
    """D3: equal ``vendor_id`` and ``device_id`` when both sides have both, else equal names.

    ``saved`` and ``now`` are dicts or objects with ``vendor_id``, ``device_id``
    and ``name``.
    """
    ids = [_pci_id(_field(record, field)) for record in (saved, now) for field in ('vendor_id', 'device_id')]
    if None not in ids:
        return ids[0] == ids[2] and ids[1] == ids[3]
    return normalize_name(_field(saved, 'name')) == normalize_name(_field(now, 'name'))


def clean_name(text) -> str:
    """A probe-supplied name made safe to keep: control characters dropped, spaces collapsed, capped."""
    if not isinstance(text, str):
        return ''
    printable = ''.join(ch if ch.isprintable() else ' ' for ch in text)
    return ' '.join(printable.split())[:NAME_LIMIT]
