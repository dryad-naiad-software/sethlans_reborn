# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Linux card probe (FR-CI-3).

Lists ``/sys/bus/pci/devices/*`` entries whose ``class`` file has base class
``0x03`` (display controller), so a card whose driver has no DRM device (for
example NVIDIA's own driver without ``nvidia-drm``) is still found. Each entry
must resolve under ``/sys/devices/`` to a directory named by a PCI address;
``vendor`` and ``device`` give the ids; every file is read with a 4 KiB cap;
one card per address; ``revision`` gives the PCI revision id (kept, unused).
The name is ``product_name`` when present and not empty, else
``"<vendor> GPU <vendor_id>:<device_id>"``. Linux cards have no adapter index
(Linux Unreal is on hold).

``probe(sys_root)`` takes the sysfs root so tests can point it at a fake tree.
"""
import logging
import os
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import card_key, clean_name
from sethlans_worker_agent.cards.os_probe import OsCard

logger = logging.getLogger(__name__)

READ_CAP = 4096
DISPLAY_BASE_CLASS = 0x03
VENDOR_NAMES = {0x10DE: 'NVIDIA', 0x1002: 'AMD', 0x8086: 'Intel'}


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, 'rb') as handle:
            data = handle.read(READ_CAP)
    except OSError:
        return None
    return data.decode('utf-8', errors='replace').strip()


def _read_hex(path: str) -> Optional[int]:
    text = read_text(path)
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _display_name(device_dir: str, vendor_id: int, device_id: int) -> str:
    product = clean_name(read_text(os.path.join(device_dir, 'product_name')) or '')
    if product:
        return product
    return f'{VENDOR_NAMES.get(vendor_id, "PCI")} GPU {vendor_id:04x}:{device_id:04x}'


def _card(entry_path: str, devices_root: str) -> Optional[OsCard]:
    device_dir = os.path.realpath(entry_path)
    if not device_dir.startswith(devices_root):
        return None
    key = card_key(os.path.basename(device_dir))
    if key is None:
        return None
    pci_class = _read_hex(os.path.join(device_dir, 'class'))
    if pci_class is None or (pci_class >> 16) != DISPLAY_BASE_CLASS:
        return None
    vendor_id = _read_hex(os.path.join(device_dir, 'vendor'))
    device_id = _read_hex(os.path.join(device_dir, 'device'))
    if vendor_id is None or device_id is None or vendor_id > 0xFFFF or device_id > 0xFFFF:
        logger.debug("PCI display device %s has unreadable ids; skipped.", ascii(device_dir))
        return None
    revision_id = _read_hex(os.path.join(device_dir, 'revision'))
    return OsCard(key=key, name=_display_name(device_dir, vendor_id, device_id), vendor_id=vendor_id,
                  device_id=device_id, revision_id=revision_id if revision_id is not None and revision_id <= 0xFF else None)


def probe(sys_root: str = '/sys') -> Optional[Tuple[OsCard, ...]]:
    """The display-class PCI devices, one per address; ``None`` when the PCI list cannot be read."""
    base = os.path.join(sys_root, 'bus', 'pci', 'devices')
    devices_root = os.path.join(os.path.realpath(os.path.join(sys_root, 'devices')), '')
    try:
        entries = sorted(os.listdir(base))
    except OSError as exc:
        logger.debug("Cannot list %s: %s", ascii(base), exc)
        return None
    cards = {}
    for entry in entries:
        card = _card(os.path.join(base, entry), devices_root)
        if card is not None and card.key not in cards:
            cards[card.key] = card
    return tuple(cards.values())
