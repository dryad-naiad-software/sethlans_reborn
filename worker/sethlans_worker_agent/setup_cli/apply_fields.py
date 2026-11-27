# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The checks of ``--setup-apply``'s input and the ``config.json`` pairs it builds (setup-rebuild FR-WK-1).

Every check runs before anything is sent or written, so a bad field never
costs an enrollment. A failure raises ``CliError``: ``bad_input`` for the
shape and the rules, ``card_held`` for a card an open alert holds,
``config_unreadable`` when ``config.json`` cannot be read.

The card choice is checked like the settings page's (``cards.store_rules``:
method, ticked cards on this machine, one brand, open alerts) against a
fresh OS card list. Blender may not be installed yet, so "Combine GPUs" is
saved for two or more cards of one brand without Blender's family check; the
card check at run time reports a choice Blender cannot combine.
"""
import logging
from typing import List, Optional

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import store_rules
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY, card_key, key_sort_value
from sethlans_worker_agent.cards.in_use import record_of
from sethlans_worker_agent.config_store import ConfigReadError
from sethlans_worker_agent.setup_cli.stdio import CliError
from sethlans_worker_agent.web_ui import password_rules

logger = logging.getLogger(__name__)

MAX_ADDRESS_TEXT = 32
MAX_NAME = 64


def _bool(request: dict, name: str) -> bool:
    value = request.get(name)
    if not isinstance(value, bool):
        raise CliError('bad_input', f"{name} must be true or false.")
    return value


def method(request: dict) -> str:
    value = request.get('compute')
    if not isinstance(value, str) or value not in config_compute.METHODS:
        raise CliError('bad_input', "compute must be cpu, gpu or cpu_gpu.")
    return value


def combine(request: dict) -> bool:
    return _bool(request, 'combine')


def unreal_enabled(request: dict) -> bool:
    return _bool(request, 'unreal_enabled')


def card_keys(request: dict) -> List[str]:
    value = request.get('cards')
    if not isinstance(value, list) or len(value) > config_compute.MAX_ENTRIES:
        raise CliError('bad_input', f"cards must be a list of at most {config_compute.MAX_ENTRIES} card addresses.")
    keys = []
    for text in value:
        key = None
        if isinstance(text, str) and len(text) <= MAX_ADDRESS_TEXT:
            key = APPLE_GPU_KEY if text == APPLE_GPU_KEY else card_key(text)
        if key is None:
            raise CliError('bad_input', "Each entry in cards must be a card address such as 0000:01:00.0.")
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise CliError('bad_input', "cards names a graphics card more than once.")
    return keys


def ui_password(request: dict, hostname: str) -> Optional[str]:
    """The new password, or ``None`` to keep the stored one (``ui_password`` null or left out)."""
    value = request.get('ui_password')
    if value is None:
        return None
    found = password_rules.problems(value, hostname)
    if found:
        raise CliError('bad_input', ' '.join(found))
    return value


def manager_name(request: dict) -> Optional[str]:
    value = request.get('manager_name')
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_NAME or not value.isprintable():
        raise CliError('bad_input', f"manager_name must be printable text of at most {MAX_NAME} characters.")
    return value


def compute_section(strict: bool = True):
    try:
        return config_compute.load(strict=strict)
    except ConfigReadError as error:
        raise CliError('config_unreadable', f"The worker's settings file could not be read: {error}")


def card_pairs(section, chosen_method: str, wants_combine: bool, ticked: List[str], found) -> list:
    """The ``compute`` pairs of one write, checked against the fresh card list ``found``."""
    by_key = {card.key: card for card in found.cards}
    shown = [card.key for card in found.cards]
    mode = 'combined' if wants_combine and len(ticked) >= 2 else 'split'
    try:
        store_rules.check_held(ticked, by_key, section)
        store_rules.check_values(chosen_method, mode, ticked, shown, by_key)
        store_rules.check_brands(ticked, by_key)
    except store_rules.CardHeld as held:
        raise CliError('card_held', f"{held.name} has an open alert; dismiss it on the worker page first.")
    except ValueError as refused:
        raise CliError('bad_input', str(refused))
    ordered = sorted(ticked, key=key_sort_value)
    return [
        ('compute.method', chosen_method),
        ('compute.gpu_mode', mode),
        ('compute.cards', [record_of(by_key[key]) for key in ordered]),
        ('compute.known', sorted(shown, key=key_sort_value)),
        ('compute.revision', section.revision + 1),
    ]
