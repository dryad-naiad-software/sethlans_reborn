# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The only writer of the ``compute`` section of ``config.json`` (FR-CC-4).

Every read-check-write holds the module-level ``_section_lock``, and every
write is one ``config_store.set_many`` call, so the choice, ``known``,
``revision`` and ``last_request`` land in one atomic file write. Lock order:
``_section_lock``, then ``config_store._config_lock``, then the config file
lock. ``WorkerCapacity._lock`` is never taken while ``_section_lock`` is held;
callers request a slot rebuild only after ``save`` returned (``cards.choice``).

``snapshot()`` reads the section once (no lock); each check, GET and request
evaluation works from one snapshot.
"""
import logging
import threading
from typing import Iterable, List, Optional

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards.identity import key_sort_value
from sethlans_worker_agent.cards.in_use import BLENDER_HOLDING_KINDS, COMBINE_TEXT, one_family, record_of

logger = logging.getLogger(__name__)

_section_lock = threading.Lock()


class EnvOverride(Exception):
    """The choice is set by ``SETHLANS_COMPUTE_*`` environment variables."""


class StaleRevision(Exception):
    """The save was based on another revision than the saved one."""

    def __init__(self, current: int, base: int):
        super().__init__(f'the saved revision is {current}, the change was based on {base}')
        self.current = current
        self.base = base


class CardHeld(Exception):
    """A newly ticked card is named by an alert that holds it (D19)."""

    def __init__(self, key: str, name: str):
        super().__init__(f'{name} has an open alert; dismiss it first')
        self.key = key
        self.name = name


def snapshot() -> config_compute.ComputeSection:
    """The effective section from one load (no lock)."""
    return config_compute.load()


def _distinct(keys: Iterable[str]) -> List[str]:
    seen: List[str] = []
    for key in keys:
        if key not in seen:
            seen.append(key)
    return seen


def _newly_ticked(card, saved_cards) -> bool:
    """True unless a saved record has the same key, ``vendor_id`` and ``device_id``."""
    return not any(record.get('key') == card.key and record.get('vendor_id') == card.vendor_id
                   and record.get('device_id') == card.device_id for record in saved_cards)


def _check_held(ticked, by_key, section) -> None:
    held = {alert['key'] for alert in section.alerts if alert.get('kind') in BLENDER_HOLDING_KINDS}
    for key in ticked:
        card = by_key.get(key)
        if card is not None and key in held and _newly_ticked(card, section.cards):
            raise CardHeld(key, card.name)


def _check_values(method, gpu_mode, ticked, shown, by_key) -> None:
    if method not in config_compute.METHODS:
        raise ValueError("The compute method must be CPU, GPU, or CPU and GPU.")
    if gpu_mode not in config_compute.GPU_MODES:
        raise ValueError("The GPU mode must be split or combined.")
    if len(ticked) > config_compute.MAX_ENTRIES or len(shown) > config_compute.MAX_ENTRIES:
        raise ValueError(f"At most {config_compute.MAX_ENTRIES} graphics cards can be listed.")
    for key in ticked:
        if key not in by_key:
            raise ValueError(f"The graphics card {key} is not on this machine.")
        if key not in shown:
            raise ValueError(f"The graphics card {key} was ticked but not shown.")
    if method in ('gpu', 'cpu_gpu') and not ticked:
        raise ValueError("GPU rendering needs at least one ticked graphics card.")


def save(method: str, gpu_mode: str, ticked_keys, shown_keys, inventory, base_revision: int,
         last_request: Optional[dict] = None) -> int:
    """Save the card choice in one write and return the new revision (FR-CC-4).

    Refuses, writing nothing: ``EnvOverride``, ``StaleRevision``, ``CardHeld``,
    then ``ValueError`` for a ticked key not in ``inventory`` or not shown, GPU
    rendering with no ticked card, or Combine GPUs against the one-family rule.
    ``combined`` with fewer than two ticked cards is saved as ``split``.
    """
    ticked = _distinct(ticked_keys)
    shown = _distinct(shown_keys)
    by_key = {card.key: card for card in inventory.cards}
    with _section_lock:
        section = config_compute.load()
        if section.env_override:
            raise EnvOverride("the graphics card choice is set by environment variables on this worker")
        if base_revision != section.revision:
            raise StaleRevision(section.revision, base_revision)
        _check_held(ticked, by_key, section)
        _check_values(method, gpu_mode, ticked, shown, by_key)
        mode = gpu_mode if len(ticked) >= 2 else 'split'
        if mode == 'combined' and not one_family(by_key[key].blender[1] if by_key[key].blender else None
                                                 for key in ticked):
            raise ValueError(COMBINE_TEXT)
        ordered = sorted(ticked, key=key_sort_value)
        revision = section.revision + 1
        pairs = [
            ('compute.method', method),
            ('compute.gpu_mode', mode),
            ('compute.cards', [record_of(by_key[key]) for key in ordered]),
            ('compute.known', sorted((key for key in shown if key in by_key), key=key_sort_value)),
            ('compute.revision', revision),
        ]
        if last_request is not None:
            pairs.append(('compute.last_request', last_request))
        config_store.set_many(pairs)
    return revision
