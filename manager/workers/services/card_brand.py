# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The brand of a graphics card, from what the worker reported (Unreal render
per card D7, FR-PC-BR-1, FR-PC-BR-5).

The manager cannot import the worker's ``cards/brand.py``, so the same
rule lives here, applied to a stored report entry's ``vendor_id`` and
``backend`` (worker card selection FR-CR-1):

- brand = the PCI vendor id: 0x10DE NVIDIA, 0x1002 AMD, 0x8086 Intel, any
  other id ``PCI <vvvv>`` (four lower-case hex digits, as the worker's
  identity texts write ids);
- with no vendor id, the Blender backend family: CUDA and OptiX NVIDIA,
  HIP AMD, oneAPI Intel, Metal Apple;
- neither: unknown (``None``).

The ticked cards must all have one known brand (D7): they are refused
together when they have more than one known brand, and a card of unknown
brand may be ticked only on its own (beside any other card, known or
unknown, it is refused).  A machine may have two brands; only ticking
both is refused.  These brand
names are the only vendor names in the manager's card code (NF-5).
"""

from typing import Iterable, Optional

from ..strict_json import _int_in

MAX_PCI_ID = 0xFFFF
VENDOR_BRANDS = {0x10DE: 'NVIDIA', 0x1002: 'AMD', 0x8086: 'Intel'}
BACKEND_BRANDS = {'CUDA': 'NVIDIA', 'OPTIX': 'NVIDIA', 'HIP': 'AMD', 'ONEAPI': 'Intel', 'METAL': 'Apple'}
UNKNOWN = 'unknown'

MIXED_BRANDS_TEXT = (
    "Graphics cards of more than one brand ({brands}) cannot be ticked together on one machine; "
    "this is not supported. Tick the cards of one brand only."
)


def brand(vendor_id, backend) -> Optional[str]:
    """The card's brand per D7, or ``None`` when neither value names one."""
    if _int_in(vendor_id, 0, MAX_PCI_ID):
        return VENDOR_BRANDS.get(vendor_id, f'PCI {vendor_id:04x}')
    if isinstance(backend, str):
        return BACKEND_BRANDS.get(backend)
    return None


def mixed_brands(brands: Iterable[Optional[str]]) -> Optional[str]:
    """The refusal sentence for the ticked cards' brands, or ``None`` when they may be ticked together.

    The sentence names the known brands in sorted order, then ``unknown``
    when any card's brand is unknown.
    """
    brands = list(brands)
    known = sorted({name for name in brands if name is not None})
    unknown = sum(1 for name in brands if name is None)
    if len(known) <= 1 and (unknown == 0 or len(brands) == 1):
        return None
    names = known + ([UNKNOWN] if unknown else [])
    return MIXED_BRANDS_TEXT.format(brands=', '.join(names))
