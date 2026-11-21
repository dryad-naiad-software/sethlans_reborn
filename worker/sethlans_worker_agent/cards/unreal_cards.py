# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The cards Unreal may render on now, and why Unreal is off when there is none (FR-CL-1, FR-CU-5, FR-CU-6, O6).

``evaluate(section, inv)`` is pure: the Unreal cards in use (ticked, state
``ok`` or ``blender_lost``, a DXGI index; whatever the compute method, C-23)
in PCI address order, and when there is none the FR-CU-6 reason, checked in
this order: no ticked card; ``SETHLANS_COMPUTE_CARDS`` names cards of more
than one brand (``env_brands``, FR-PC-BR-3); the OS probe failed (the
inventory came from Blender, FR-CI-6); ticked cards, none usable.

``current()`` evaluates the saved choice against the probe of the heartbeat's
card report (``web_ui.cards_cache.report_probe``): at most one OS probe per
heartbeat interval and none while an Unreal render holds the machine (the
cached probe is used then). Like the card GET it is display data only: no
alert, no stop, no rebuild, never Blender (S1). ``None`` when the saved
choice cannot be read (C-20) or no probe may run yet.

``disabled_reason()`` feeds ``runtime.status``; when ``current()`` cannot
decide, the last decided reason stands ('' until one was decided).
``_last_lock`` is a leaf lock.
"""
import threading
from typing import Optional, Tuple

from sethlans_worker_agent.cards import env_brands, matching
from sethlans_worker_agent.cards.identity import key_address

NO_TICK_REASON = "no graphics card is ticked for rendering (worker settings)"
NONE_USABLE_REASON = "no ticked graphics card can be used (see the card alerts)"
PROBE_FAILED_REASON = "could not list the graphics cards through the operating system"

_last_lock = threading.Lock()
_last_reason = ''


def evaluate(section, inv) -> Tuple[tuple, str]:
    """``(Unreal cards in use, FR-CU-6 reason)``; the reason is '' when there is a card."""
    if not section.cards:
        return (), NO_TICK_REASON
    if env_brands.check(section, inv) is not None:
        return (), env_brands.MIXED_ENV_REASON
    if inv.os_probe_failed:
        return (), PROBE_FAILED_REASON
    cards = matching.evaluate(section, inv).unreal_cards
    return cards, '' if cards else NONE_USABLE_REASON


def current(cached_only: bool = False) -> Optional[Tuple[tuple, str]]:
    """``evaluate`` on the saved choice and the heartbeat's card probe; ``None`` when it cannot be decided.

    ``cached_only`` (the GPU memory sampler thread): the cached probe only, never a probe or a wait for one.
    """
    from sethlans_worker_agent import config, unreal_bridge
    from sethlans_worker_agent.cards import store
    from sethlans_worker_agent.web_ui import cards_cache
    section = store.snapshot()
    if section.read_failed:
        return None
    if not section.cards:
        return evaluate(section, None)
    max_age = max(cards_cache.CACHE_SECONDS, float(config.HEARTBEAT_INTERVAL_SECONDS))
    probe = (cards_cache.cached_only_probe() if cached_only else
             cards_cache.report_probe(max_age, probe_allowed=unreal_bridge.drift_check_allowed()))
    return None if probe is None else evaluate(section, probe.inventory)


def disabled_reason() -> str:
    """The FR-CU-6 reason Unreal is disabled for now ('' when a card can be used); never raises."""
    global _last_reason
    try:
        found = current()
    except Exception:  # noqa: BLE001 - keep the last decision; the launch-time choice checks again
        found = None
    with _last_lock:
        if found is not None:
            _last_reason = found[1]
        return _last_reason


def address_of(card) -> Optional[str]:
    """``dddd:bb:dd.f`` of a card key, or ``None`` for a card without a PCI address."""
    address = key_address(card.key)
    if address is None:
        return None
    return f'{address[0]:04x}:{address[1]:02x}:{address[2]:02x}.{address[3]:x}'


def addresses(cards) -> frozenset:
    """The addresses of ``cards``; a card without an address is left out (O6)."""
    return frozenset(address for address in (address_of(card) for card in cards) if address is not None)


def reset_for_tests() -> None:
    """Forget the last decided reason. Test-only hook."""
    global _last_reason
    with _last_lock:
        _last_reason = ''
