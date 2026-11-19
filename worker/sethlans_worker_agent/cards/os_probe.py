# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The operating system's list of graphics cards (FR-CI-5).

``probe()`` picks the platform module (``os_probe_windows``,
``os_probe_linux``, ``os_probe_macos``), never raises, and returns ``None``
when the probe failed, as opposed to ``()`` when it worked and found no card.
At most ``MAX_CARDS`` cards are kept, in key order (PCI address order, D4).
The probe holds no lock and keeps no state except the once-only log record.

``log_once`` is shared by the card modules so a fault seen at every periodic
check is logged once per process, not once per check.
"""
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from sethlans_worker_agent.cards.identity import key_sort_value

logger = logging.getLogger(__name__)

MAX_CARDS = 16
_MAX_REMEMBERED = 256

_logged_lock = threading.Lock()
_logged: set = set()


@dataclass(frozen=True)
class OsCard:
    """One card as the operating system lists it.

    ``key`` is ``None`` only on macOS, where cards have no PCI address and the
    inventory decides the key (D9). ``dxgi_index`` is the Windows adapter index
    in Unreal's order (FR-CI-2), ``None`` elsewhere. ``display`` is macOS only:
    whether ``system_profiler`` lists a display under this GPU.
    """
    key: Optional[str]
    name: str
    vendor_id: Optional[int] = None
    device_id: Optional[int] = None
    dxgi_index: Optional[int] = None
    dedicated_mib: Optional[int] = None
    display: Optional[bool] = None
    revision_id: Optional[int] = None  # PCI revision (Windows, Linux); recorded, not used for any decision yet


def log_once(log: logging.Logger, level: int, message: str, *args, exc_info=False) -> None:
    """Log ``message % args`` the first time it is seen in this process."""
    text = message % args if args else message
    with _logged_lock:
        if text in _logged:
            return
        if len(_logged) < _MAX_REMEMBERED:
            _logged.add(text)
    log.log(level, text, exc_info=exc_info)


def reset_logged_for_tests() -> None:
    """Forget the once-only log record. Test-only hook."""
    with _logged_lock:
        _logged.clear()


def _platform_probe(platform_name: str):
    if platform_name == 'win32':
        from sethlans_worker_agent.cards import os_probe_windows
        return os_probe_windows.probe()
    if platform_name.startswith('linux'):
        from sethlans_worker_agent.cards import os_probe_linux
        return os_probe_linux.probe()
    if platform_name == 'darwin':
        from sethlans_worker_agent.cards import os_probe_macos
        return os_probe_macos.probe()
    log_once(logger, logging.WARNING, "Graphics cards cannot be listed on platform %s.", ascii(platform_name))
    return None


def _finish(cards) -> Tuple[OsCard, ...]:
    cards = tuple(cards)
    if any(card.key is not None for card in cards):
        cards = tuple(sorted(cards, key=lambda card: key_sort_value(card.key)))
    if len(cards) > MAX_CARDS:
        log_once(logger, logging.WARNING, "The operating system lists %d graphics cards; only the first %d are used.",
                 len(cards), MAX_CARDS)
        cards = cards[:MAX_CARDS]
    return cards


def probe(platform_name: Optional[str] = None) -> Optional[Tuple[OsCard, ...]]:
    """The cards the operating system lists, in key order; ``None`` when the probe failed."""
    platform_name = sys.platform if platform_name is None else platform_name
    try:
        cards = _platform_probe(platform_name)
        if cards is None:
            log_once(logger, logging.WARNING, "Could not list the graphics cards through the operating system.")
            return None
        return _finish(cards)
    except Exception:  # a probe must never raise into the caller (FR-CI-5)
        log_once(logger, logging.WARNING, "Listing the graphics cards through the operating system failed.",
                 exc_info=True)
        return None
