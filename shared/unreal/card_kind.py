# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card kind strings (Unreal per-machine memory test D1, FR-MT-K-1).

A card kind is ``vvvv:dddd:rrrr``: the PCI vendor, device and revision ids
as four lower-case hex digits each, the revision ``none`` when it is not
known. A card without a vendor or device id has no kind.

The ids are the integers both sides hold: the worker's card inventory
(``Card.vendor_id``, ``device_id``, ``revision_id``) and the manager's
stored ``Worker.card_status`` cards (the same names, each an integer in
``0..0xFFFF`` or ``null``). A ``bool`` is not an id.
"""
import re
from typing import Optional

# ``\Z``, not ``$``: ``$`` also matches just before a trailing newline.
KIND_RE = r'^[0-9a-f]{4}:[0-9a-f]{4}:([0-9a-f]{4}|none)\Z'
_KIND = re.compile(KIND_RE, re.ASCII)
MAX_PCI_ID = 0xFFFF
NO_REVISION = 'none'


def _pci_id(value) -> Optional[int]:
    """``value`` when it is an integer PCI id (``0..0xFFFF``, not a ``bool``), else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_PCI_ID:
        return value
    return None


def card_kind(vendor, device, revision) -> Optional[str]:
    """The card kind of a card's PCI ids, or ``None`` when the vendor or device id is missing or invalid.

    A missing or invalid revision gives ``none``.
    """
    vendor_id, device_id = _pci_id(vendor), _pci_id(device)
    if vendor_id is None or device_id is None:
        return None
    revision_id = _pci_id(revision)
    revision_text = NO_REVISION if revision_id is None else f'{revision_id:04x}'
    return f'{vendor_id:04x}:{device_id:04x}:{revision_text}'


def is_card_kind(value) -> bool:
    """True for a string in the card kind form (``KIND_RE``)."""
    return isinstance(value, str) and _KIND.match(value) is not None
