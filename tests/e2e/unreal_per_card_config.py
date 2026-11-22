# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Gating, card choice and constants of the real render-per-card E2E
(Unreal render per card, chunk 8, AC-E1..E9).

The test needs everything the other real Unreal E2E tests need
(``unreal_e2e_config.unreal_skip_reason``) plus at least two graphics
cards of one brand that Unreal can use (a PCI address and a DXGI index):
only cards of one brand may be ticked together (D7). A machine with one
card of each of two brands is skipped with that reason. The checks run in
the module's ``skipif``, so a skipped machine never starts a manager, a
worker or Unreal.

``SETHLANS_COMPUTE_CARDS`` (PCI addresses, comma-separated) narrows the
choice; it must still name at least two cards of one brand. The worker's
Unreal cache is a dir of its own (``SETHLANS_UNREAL_E2E_PER_CARD_CACHE_DIR``,
default: the other tests' cache dir plus ``-percard``), kept apart from the
one-card tests' cache.
"""

import os

from tests.e2e import unreal_e2e_config as base
from tests.e2e.unreal_compute_env import CARDS_ENV, METHOD_ENV

PER_CARD_CACHE_ENV = "SETHLANS_UNREAL_E2E_PER_CARD_CACHE_DIR"
MIN_SAME_BRAND = 2
SIZE = (1280, 720)
# Hard cap, first submission to last check (about 50 min expected on a fresh cache).
GPU_LIMIT_S = 6000
BASE_ZEN_PORT = 8668
# U-4: memory a render's processes may hold on the other cards.
OTHER_CARDS_LIMIT_MB = 256
_KEY_PREFIX = "pci:"


def _probe():
    # Imported lazily: only an opted-in Windows node gets this far.
    from sethlans_worker_agent.cards import os_probe
    return os_probe.probe()


def _brand(card):
    from sethlans_worker_agent.cards.brand import brand
    return brand(card)


def _wanted(environ):
    """The addresses ``SETHLANS_COMPUTE_CARDS`` names, as integer tuples; ``None`` when unset."""
    from sethlans_worker_agent.cards.identity import key_address, parse_key_list
    text = environ.get(CARDS_ENV, "").strip()
    if not text:
        return None
    keys = parse_key_list(text, 16)
    return frozenset(key_address(key) for key in keys or ()) or frozenset({None})


def _found(groups):
    return ", ".join(f"{name or 'unknown brand'} x{len(cards)}" for name, cards in sorted(
        groups.items(), key=lambda item: str(item[0]))) or "none"


def choose_cards(environ=None, probe=_probe):
    """``(cards, None)``: the same-brand cards to tick, in PCI order; ``(None, reason)`` when there are none."""
    from sethlans_worker_agent.cards.identity import key_address
    environ = os.environ if environ is None else environ
    cards = probe()
    if cards is None:
        return None, "listing the graphics cards through the operating system failed"
    usable = [card for card in cards if isinstance(card.key, str) and card.key.startswith(_KEY_PREFIX)
              and card.dxgi_index is not None]
    wanted = _wanted(environ)
    if wanted is not None:
        usable = [card for card in usable if key_address(card.key) in wanted]
    groups = {}
    for card in usable:
        groups.setdefault(_brand(card), []).append(card)
    pairs = {name: group for name, group in groups.items() if name is not None and len(group) >= MIN_SAME_BRAND}
    if wanted is not None and (len(groups) != 1 or not pairs):
        return None, f"{CARDS_ENV} must name at least two graphics cards of one brand; it names {_found(groups)}"
    if not pairs:
        return None, f"needs at least two graphics cards of one brand for Unreal; found {_found(groups)}"
    if len(pairs) > 1:
        return None, f"cards of more than one brand come in pairs ({_found(groups)}); set {CARDS_ENV}"
    return tuple(next(iter(pairs.values()))), None


def card_env(cards):
    """The worker env that ticks exactly ``cards``."""
    return {METHOD_ENV: "cpu_gpu", CARDS_ENV: ",".join(card.key[len(_KEY_PREFIX):] for card in cards)}


def cache_dir():
    explicit = os.environ.get(PER_CARD_CACHE_ENV, "").strip()
    return explicit or (os.environ.get(base.CACHE_DIR_ENV) or base.DEFAULT_CACHE_DIR) + "-percard"


def skip_reason():
    """Why the render-per-card E2E cannot run here, or ``None``."""
    reason = base.unreal_skip_reason()
    if reason is not None:
        return reason
    _cards, reason = choose_cards()
    return reason
