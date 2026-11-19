# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The refusals of ``cards.store.save`` and the checks behind them (FR-CC-4, D19, FR-CM-1).

Pure functions over a section read by ``store`` under its ``_section_lock``; this module holds no lock and no
state. ``store`` re-exports the exception classes, so callers keep naming them ``store.EnvOverride`` and so on.
"""
from typing import Iterable, List

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards.matching import BLENDER_HOLDING_KINDS


class EnvOverride(Exception):
    """The choice is set by ``SETHLANS_COMPUTE_*`` environment variables."""


class ManagerChangesOff(Exception):
    """A manager request met the manager-changes switch turned off (FR-CM-1), checked under the section lock."""


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


def distinct(keys: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(keys))


def newly_ticked(card, saved_cards) -> bool:
    """True unless a saved record has the same key, ``vendor_id`` and ``device_id``."""
    return not any(record.get('key') == card.key and record.get('vendor_id') == card.vendor_id
                   and record.get('device_id') == card.device_id for record in saved_cards)


def check_source(section, base_revision: int, manager_request: bool) -> None:
    """``EnvOverride``, then ``ManagerChangesOff`` for a manager request, then ``StaleRevision``."""
    if section.env_override:
        raise EnvOverride("the graphics card choice is set by environment variables on this worker")
    if manager_request and section.allow_manager_changes is not True:
        raise ManagerChangesOff("this worker does not allow the manager to change its graphics card choice")
    if base_revision != section.revision:
        raise StaleRevision(section.revision, base_revision)


def check_held(ticked, by_key, section) -> None:
    held = {alert['key'] for alert in section.alerts if alert.get('kind') in BLENDER_HOLDING_KINDS}
    for key in ticked:
        card = by_key.get(key)
        if card is not None and key in held and newly_ticked(card, section.cards):
            raise CardHeld(key, card.name)


def check_values(method, gpu_mode, ticked, shown, by_key) -> None:
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
