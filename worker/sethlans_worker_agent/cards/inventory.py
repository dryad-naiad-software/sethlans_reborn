# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card inventory: the OS card list joined with Blender's (FR-CI-6).

``build(os_cards, blender_cards)`` takes the OS probe result (``None`` when it
failed) and the cached physical Blender list (``None`` when no Blender is
installed yet, ``BLENDER_CHECK_FAILED`` when Blender's device check failed,
which is not an empty list). Blender devices join OS cards by domain:bus:device, compared
as integers; the function digit is ignored. A Blender device that cannot be
tied to an OS card is not a card. When the OS probe failed on Windows or
Linux, the cards come from Blender devices whose id carries an address, so the
identity is still the address. On macOS the one GPU is the card ``apple-gpu``
when Blender lists exactly one ``METAL`` device (D9).

``build`` is pure apart from once-only log lines; it holds no lock.
"""
import logging
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sethlans_worker_agent.cards.identity import (
    APPLE_GPU_KEY, blender_pci, card_key, clean_name, key_address, key_sort_value,
)
from sethlans_worker_agent.cards.os_probe import MAX_CARDS, log_once

logger = logging.getLogger(__name__)

BLENDER_CHECK_FAILED = type('BlenderCheckFailed', (), {'__repr__': lambda self: 'BLENDER_CHECK_FAILED'})()
NOTE_NO_BLENDER = 'Blender not installed yet'
NOTE_BLENDER_FAILED = 'Blender could not list its devices; see the log'
NOTE_SHARED_DEVICE = 'shares its PCI device with another card; Blender cannot tell them apart'
NOTE_LISTED_TWICE = 'Blender lists this card twice; it cannot tell which entry is which'
METAL = 'METAL'


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    vendor_id: Optional[int] = None
    device_id: Optional[int] = None
    dxgi_index: Optional[int] = None          # Windows only (FR-CI-2)
    dedicated_mib: Optional[int] = None       # Windows only
    revision_id: Optional[int] = None         # PCI revision; recorded, not used for any decision yet
    blender: Optional[Tuple[str, str]] = None  # (Blender id, backend) or None
    note: str = ''
    screen: Optional[bool] = None             # screen marks: filled by a later chunk (FR-CS)
    primary_screen: Optional[bool] = None


@dataclass(frozen=True)
class Inventory:
    cards: Tuple[Card, ...] = ()
    os_probe_failed: bool = False
    blender_version: Optional[str] = None


def not_offered_note(blender_version: Optional[str]) -> str:
    return f'not offered by Blender {blender_version}' if blender_version else 'not offered by Blender'


def _blender_devices(blender_cards) -> List[Tuple[str, str, str]]:
    """``(id, backend, name)`` of each well-formed Blender record."""
    devices = []
    for record in blender_cards if isinstance(blender_cards, (list, tuple)) else ():
        if isinstance(record, dict) and isinstance(record.get('id'), str) and isinstance(record.get('type'), str):
            devices.append((record['id'], record['type'], clean_name(record.get('name') or '')))
    return devices


def _untied(device_id: str) -> None:
    log_once(logger, logging.WARNING, "Blender device %s cannot be tied to a graphics card; it is not used.",
             ascii(device_id))


def _by_device(os_cards) -> Dict[Tuple[int, int, int], List[str]]:
    """(domain, bus, device) -> keys of the OS cards at that PCI device."""
    by_device: Dict[Tuple[int, int, int], List[str]] = {}
    for card in os_cards:
        address = key_address(card.key)
        if address is not None:
            by_device.setdefault(address[:3], []).append(card.key)
    return by_device


def _join(by_device, devices) -> Dict[str, Optional[Tuple[str, str]]]:
    """Card key -> (Blender id, backend) per unambiguous match; ``None`` for a card Blender lists twice."""
    matches: Dict[str, List[Tuple[str, str]]] = {}
    for device_id, backend, _name in devices:
        keys = by_device.get(blender_pci(device_id))
        if not keys:
            _untied(device_id)
        elif len(keys) == 1:
            matches.setdefault(keys[0], []).append((device_id, backend))
    for key, found in matches.items():
        if len(found) > 1:
            log_once(logger, logging.ERROR, "Blender lists %d devices for the graphics card %s; none of them is used.",
                     len(found), key)
    return {key: found[0] if len(found) == 1 else None for key, found in matches.items()}


def _shared_keys(by_device) -> set:
    """Keys of OS cards that share a PCI device with another card (ERROR, once)."""
    shared = set()
    for keys in by_device.values():
        if len(keys) > 1:
            log_once(logger, logging.ERROR, "Graphics cards %s share one PCI device; Blender cannot be tied to "
                     "either of them.", ', '.join(keys))
            shared.update(keys)
    return shared


def _from_os(os_cards, blender_cards, blender_version) -> Inventory:
    devices = _blender_devices(blender_cards)
    by_device = _by_device(os_cards)
    shared = _shared_keys(by_device)
    matches = _join(by_device, devices) if blender_cards is not None else {}
    names = {device_id: name for device_id, _backend, name in devices}
    cards = []
    for os_card in os_cards:
        match = matches.get(os_card.key)
        if blender_cards is None:
            note = NOTE_NO_BLENDER
        elif blender_cards is BLENDER_CHECK_FAILED:
            note = NOTE_BLENDER_FAILED
        elif os_card.key in shared:
            note = NOTE_SHARED_DEVICE
        elif os_card.key in matches and match is None:
            note = NOTE_LISTED_TWICE
        else:
            note = '' if match else not_offered_note(blender_version)
        name = os_card.name or (names.get(match[0], '') if match else '') or os_card.key
        cards.append(Card(key=os_card.key, name=name, vendor_id=os_card.vendor_id, device_id=os_card.device_id,
                          dxgi_index=os_card.dxgi_index, dedicated_mib=os_card.dedicated_mib,
                          revision_id=os_card.revision_id, blender=match, note=note))
    return Inventory(cards=tuple(cards), blender_version=blender_version)


def _from_blender(blender_cards, blender_version) -> Inventory:
    """OS probe failed (Windows, Linux): Blender devices whose id carries an address."""
    log_once(logger, logging.WARNING, "Could not list the graphics cards through the operating system; using "
             "the cards Blender reports at a PCI address.")
    by_key: Dict[str, List[Tuple[str, str, str]]] = {}
    for device_id, backend, name in _blender_devices(blender_cards):
        address = blender_pci(device_id)
        key = card_key(f'{address[0]:04x}:{address[1]:02x}:{address[2]:02x}.0') if address else None
        if key is None:
            _untied(device_id)
        else:
            by_key.setdefault(key, []).append((device_id, backend, name))
    cards = []
    for key, found in by_key.items():
        if len(found) > 1:
            log_once(logger, logging.ERROR, "Blender lists %d devices at %s; none of them is used.", len(found), key)
            continue
        device_id, backend, name = found[0]
        cards.append(Card(key=key, name=name or key, blender=(device_id, backend)))
    cards.sort(key=lambda card: key_sort_value(card.key))
    return Inventory(cards=tuple(cards[:MAX_CARDS]), os_probe_failed=True, blender_version=blender_version)


def _macos(os_cards, blender_cards, blender_version) -> Inventory:
    """D9: one GPU and exactly one Metal device form ``apple-gpu``; any other count, no card."""
    if os_cards is None:
        log_once(logger, logging.WARNING, "Could not list the graphics cards through system_profiler; no card "
                 "is offered.")
        return Inventory(os_probe_failed=True, blender_version=blender_version)
    if len(os_cards) != 1:
        log_once(logger, logging.WARNING, "macOS lists %d GPUs; a card is offered only when there is exactly one.",
                 len(os_cards))
        return Inventory(blender_version=blender_version)
    gpu = os_cards[0]
    if blender_cards is None or blender_cards is BLENDER_CHECK_FAILED:
        note = NOTE_NO_BLENDER if blender_cards is None else NOTE_BLENDER_FAILED
        return Inventory(cards=(Card(key=APPLE_GPU_KEY, name=gpu.name, note=note),), blender_version=blender_version)
    metal = [device for device in _blender_devices(blender_cards) if device[1] == METAL]
    if len(metal) != 1:
        log_once(logger, logging.WARNING, "Blender lists %d Metal devices; a card is offered only when there is "
                 "exactly one.", len(metal))
        return Inventory(blender_version=blender_version)
    device_id, backend, name = metal[0]
    card = Card(key=APPLE_GPU_KEY, name=gpu.name or name, blender=(device_id, backend))
    return Inventory(cards=(card,), blender_version=blender_version)


def build(os_cards, blender_cards, blender_version: Optional[str] = None,
          platform_name: Optional[str] = None) -> Inventory:
    """Join the OS card list with Blender's physical device list (FR-CI-6)."""
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == 'darwin':
        return _macos(os_cards, blender_cards, blender_version)
    if os_cards is None:
        return _from_blender(blender_cards, blender_version)
    keyed = tuple(card for card in os_cards if card.key is not None)
    return _from_os(keyed, blender_cards, blender_version)
