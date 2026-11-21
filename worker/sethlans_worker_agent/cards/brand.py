# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The brand of a graphics card and the one-brand rule for ticked cards (unreal-render-per-card D7, FR-PC-BR-1).

``brand(card)`` is the PCI vendor id's brand (0x10DE NVIDIA, 0x1002 AMD, 0x8086 Intel, any other
``PCI <vvvv>``), or, when the inventory card has no vendor id (the inventory built from Blender after a failed
OS probe, macOS), the brand of its Blender backend family (CUDA/OptiX NVIDIA, HIP AMD, oneAPI Intel, Metal
Apple). ``None`` when neither is known.

``mixed_brands(cards)`` applies the rule to the ticked cards: they must all have one known brand. A card whose
brand is unknown may be ticked only on its own; beside any other card, known or unknown, the rule refuses, as it
does for two known brands. It covers both renderers (CS O2).

Pure functions; no lock, no state.
"""
from typing import Iterable, Optional, Tuple

VENDOR_BRANDS = {0x10DE: 'NVIDIA', 0x1002: 'AMD', 0x8086: 'Intel'}  # PCI-SIG vendor ids (CS FR-CI-3)
BACKEND_BRANDS = {'CUDA': 'NVIDIA', 'OPTIX': 'NVIDIA', 'HIP': 'AMD', 'ONEAPI': 'Intel', 'METAL': 'Apple'}
UNKNOWN_LABEL = 'unknown'

MIXED_BRANDS_TEXT = ("Graphics cards of more than one brand ({brands}) cannot be ticked together on one machine; "
                     "this is not supported. Tick the cards of one brand only.")


def brand(card) -> Optional[str]:
    """The brand of an inventory card (D7), or ``None`` when its vendor id and Blender backend are both unknown."""
    vendor_id = getattr(card, 'vendor_id', None)
    if isinstance(vendor_id, int) and not isinstance(vendor_id, bool) and 0 <= vendor_id <= 0xFFFF:
        return VENDOR_BRANDS.get(vendor_id, f'PCI {vendor_id:04x}')
    # no vendor id, or one that is not a PCI id (a bool, out of range): unknown, as the manager treats it
    blender = getattr(card, 'blender', None)
    backend = blender[1] if isinstance(blender, tuple) and len(blender) == 2 else None
    return BACKEND_BRANDS.get(backend) if isinstance(backend, str) else None


def mixed_brands(cards: Iterable) -> Optional[Tuple[str, ...]]:
    """The brand labels when ``cards`` break the one-brand rule, else ``None``.

    The labels are the known brands sorted, then ``unknown`` once when any card's brand is unknown (as the manager
    names them, so both sides print the same sentence).
    """
    brands = [brand(card) for card in cards]
    known = sorted({name for name in brands if name is not None})
    unknown = brands.count(None)
    if (unknown == 0 and len(known) <= 1) or (unknown == 1 and len(brands) == 1):
        return None  # one known brand, or one card of unknown brand on its own
    return tuple(known) + ((UNKNOWN_LABEL,) if unknown else ())


def mixed_text(labels: Iterable[str]) -> str:
    """``MIXED_BRANDS_TEXT`` naming ``labels``."""
    return MIXED_BRANDS_TEXT.format(brands=', '.join(labels))
