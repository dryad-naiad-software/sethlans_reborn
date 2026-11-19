# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Blender device list the card checks join with the OS list (FR-CK-1, FR-CK-2).

``known()`` is the list of the last trusted fresh detection, else the startup
cache: the periodic check (when it does not detect), the start check and the
rebuild all use it, so a check and the rebuild it requests agree.

``from_detection(fresh, os_cards, profile)`` turns a fresh detection into the
list a check uses. A detection that ran with another installed Blender
because the remembered one is gone (``drift.fallback_detection_executable``)
and that does not offer a card of the profile is not trusted: another
version can name or group devices differently, so this is not read as
``blender_lost``. The known list is kept and one WARNING per card says so.

State is read and written by the agent-loop thread only.
"""
import logging

from sethlans_worker_agent.capacity.drift import fallback_detection_executable, installed_version_of
from sethlans_worker_agent.cards import inventory, startup_log
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

_last_fresh = None  # Blender records of the last trusted fresh detection


def known():
    """``(blender_cards, version)``: the last trusted fresh detection, else the startup cache."""
    cached, version = startup_log.cached_blender_list()
    return (list(_last_fresh), version) if _last_fresh is not None else (cached, version)


def from_detection(fresh, os_cards, profile):
    """``(blender_cards, version, trusted)`` for the pairs a fresh detection returned."""
    global _last_fresh
    records = [{'id': device_id, 'type': backend} for device_id, backend in fresh]
    fallback = fallback_detection_executable()
    version = installed_version_of(fallback) if fallback else startup_log.cached_blender_list()[1]
    if fallback:
        offered = {card.key for card in inventory.build(os_cards, records, version).cards if card.blender is not None}
        missing = [card for card in profile.cards if card.key not in offered]
        if missing:
            for card in missing:
                log_once(logger, logging.WARNING, "Blender %s does not offer %s (%s); the device list in use is kept.",
                         version or '(version unknown)', ascii(card.name), card.key)
            return (*known(), False)
    _last_fresh = records
    return records, version, True


def reset_for_tests() -> None:
    """Forget the last fresh list. Test-only hook."""
    global _last_fresh
    _last_fresh = None
