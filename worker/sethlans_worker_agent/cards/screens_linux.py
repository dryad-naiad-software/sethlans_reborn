# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which card drives a screen, on Linux (FR-CS-1).

Reads the DRM entries under ``/sys/class/drm``: each ``card<N>`` is tied to a
card key through the PCI address its ``device`` link resolves to (under
``/sys/devices/``, as the card probe requires); each ``card<N>-<connector>``
has a ``status`` file (read with a 4 KiB cap). A card with a connector whose
status is ``connected`` drives a screen. Linux has no "main screen" for a
card, so ``primary_screen`` is always ``None``.

``read(sys_root)`` returns ``{key: (screen, None)}`` for every card with a DRM
entry: ``True`` with a connected connector, else ``None`` when a status could
not be read, else ``False`` (no connector or none connected). A card with no
DRM entry (for example NVIDIA's own driver without ``nvidia-drm``) is not in
the answer, so it reads as unknown. ``None`` when ``/sys/class/drm`` cannot
be listed. Holds no lock.
"""
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from sethlans_worker_agent.cards import os_probe_linux
from sethlans_worker_agent.cards.identity import card_key

logger = logging.getLogger(__name__)

CONNECTED = 'connected'
MAX_ENTRIES = 256
_CARD = re.compile(r'card(\d{1,4})', re.ASCII)
_CONNECTOR = re.compile(r'card(\d{1,4})-[A-Za-z0-9_.:-]{1,64}', re.ASCII)


def _read_status(path: str) -> Optional[str]:
    return os_probe_linux.read_text(path)  # 4 KiB cap, stripped; None when unreadable


def _card_key(drm: str, entry: str, devices_root: str) -> Optional[str]:
    device_dir = os.path.realpath(os.path.join(drm, entry, 'device'))
    if not device_dir.startswith(devices_root):
        return None
    return card_key(os.path.basename(device_dir))


def _screen(statuses: List[Optional[str]]) -> Optional[bool]:
    if CONNECTED in statuses:
        return True
    return None if None in statuses else False


def read(sys_root: str = '/sys') -> Optional[Dict[str, Tuple[Optional[bool], None]]]:
    """``{key: (screen, None)}`` for the cards with a DRM entry; ``None`` when the DRM list cannot be read."""
    drm = os.path.join(sys_root, 'class', 'drm')
    devices_root = os.path.join(os.path.realpath(os.path.join(sys_root, 'devices')), '')
    try:
        entries = sorted(os.listdir(drm))[:MAX_ENTRIES]
    except OSError as exc:
        logger.debug("Cannot list %s: %s", ascii(drm), exc)
        return None
    keys = {}  # DRM card number -> card key
    for entry in entries:
        match = _CARD.fullmatch(entry)
        key = _card_key(drm, entry, devices_root) if match else None
        if key is not None:
            keys[match.group(1)] = key
    statuses: Dict[str, List[Optional[str]]] = {key: [] for key in keys.values()}
    for entry in entries:
        match = _CONNECTOR.fullmatch(entry)
        if match and match.group(1) in keys:
            statuses[keys[match.group(1)]].append(_read_status(os.path.join(drm, entry, 'status')))
    return {key: (_screen(found), None) for key, found in statuses.items()}
