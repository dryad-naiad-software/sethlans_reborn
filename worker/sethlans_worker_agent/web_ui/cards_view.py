# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What the worker's two graphics card pages share (FR-CW-2, FR-CW-4, FR-CW-5).

- ``probe_now()``: a fresh OS probe and screen read (``cards.screens``)
  joined with the *cached* Blender device list. It never starts Blender.
- ``view(probe, section)``: the JSON answer of both card GETs, with each
  card's state, the open alerts, the notices, the problem text and the
  warnings (``cards.matching``, ``cards.warnings``). It is display data only;
  it raises no alert, stops no job and requests no rebuild (S1).
- ``parse_body(body)``: the FR-CW-5 shape checks of a save request; a failure
  raises ``ValueError`` with one plain sentence.
- ``save(fields, probe, source)``: saves through ``choice.save_choice`` and
  maps every refusal to an HTTP status and a plain sentence.

Card addresses travel over HTTP as the text ``normalize_pci`` accepts
(``0000:23:00.0``, the ``address`` of each card in the GET answer) or, on
macOS, as ``apple-gpu``. This module holds no lock and keeps no state.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from sethlans_worker_agent import config_compute, unreal_switch
from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice, inventory, matching, os_probe, screens, store, store_requests
from sethlans_worker_agent.cards import warnings as card_warnings
from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY, KEY_PREFIX, card_key, clean_name, key_address
from sethlans_worker_agent.cards.in_use import FAMILIES
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError

logger = logging.getLogger(__name__)

READ_FAILED_TEXT = "The worker's settings file could not be read just now. Reload the page."
NOTE_NOT_CHECKED = 'Blender not checked yet'
ENV_OVERRIDE_TEXT = "The graphics card choice is set by environment variables on this worker."
STALE_TEXT = "The card choice was changed meanwhile (on this page or from the manager). Reload the page."
HELD_TEXT = "Dismiss the alert for {name} first."
NOT_SAVED_TEXT = "The card choice could not be saved; see the worker log."
MAX_ADDRESS_TEXT = 32
MAX_RAISED_AT = 40  # an ISO 8601 UTC time is 25 characters

# The fields of a save request; the wizard's may also give the manager-changes switch (FR-CW-2).
_FIELDS = ('method', 'gpu_mode', 'cards', 'shown', 'revision')
SWITCH_FIELD = 'allow_manager_changes'


@dataclass(frozen=True)
class Probe:
    """One fresh inventory and the Blender list it was joined with."""
    inventory: inventory.Inventory
    blender_cards: object  # None (not checked), inventory.BLENDER_CHECK_FAILED, or the cached list


def probe_now() -> Probe:
    """A fresh OS probe and screen read joined with the cached Blender list (never starts Blender)."""
    from sethlans_worker_agent.cards.startup_log import cached_blender_list
    blender_cards, version = cached_blender_list()
    return Probe(screens.marked(inventory.build(os_probe.probe(), blender_cards, version)), blender_cards)


def address_of(key: str) -> str:
    """The text a page sends for a card: the PCI address without ``pci:``, or ``apple-gpu``."""
    return key[len(KEY_PREFIX):] if key.startswith(KEY_PREFIX) else key


def short_address(key: str) -> Optional[str]:
    """``bus:device.function`` for the card label, or ``None`` for a card without a PCI address."""
    address = key_address(key)
    return f'{address[1]:02x}:{address[2]:02x}.{address[3]:x}' if address is not None else None


def blender_check_state(blender_cards, checking: bool = False, check_failed: bool = False) -> str:
    """``done``, ``failed``, ``running`` or ``not_checked`` for the cached Blender device list.

    ``checking`` and ``check_failed`` describe the wizard's own device check
    (``setup.blender_devices``); they count only while nothing is cached.
    """
    if blender_cards is None:
        if checking:
            return 'running'
        return 'failed' if check_failed else 'not_checked'
    return 'failed' if blender_cards is inventory.BLENDER_CHECK_FAILED else 'done'


def _uncached_note(check_failed: bool) -> str:
    return inventory.NOTE_BLENDER_FAILED if check_failed else NOTE_NOT_CHECKED


def _card_view(card: inventory.Card, ticked, uncached_note: Optional[str], state: Optional[str]) -> dict:
    backend = card.blender[1] if card.blender is not None else None
    note = uncached_note if uncached_note and card.note == inventory.NOTE_NO_BLENDER else card.note
    return {
        'key': card.key, 'address': address_of(card.key), 'pci': short_address(card.key),
        'name': card.name, 'vendor_id': card.vendor_id, 'device_id': card.device_id,
        'backend': backend, 'family': FAMILIES.get(backend, backend) if backend else None,
        'note': note, 'ticked': card.key in ticked, 'state': state,
        'screen': card.screen, 'primary_screen': card.primary_screen,
    }


def _missing_view(record: dict, state: Optional[str]) -> dict:
    """A ticked card the fresh inventory does not list (``lost``, or ``unknown`` after a failed OS probe)."""
    key = record['key']
    return {'key': key, 'address': address_of(key), 'pci': short_address(key),
            'name': clean_name(str(record.get('name') or '')) or key, 'state': state,
            'vendor_id': record.get('vendor_id'), 'device_id': record.get('device_id')}  # the saved ids (FR-CR-1)


def _alert_view(alert: dict) -> dict:
    """An open alert for the pages; ``name``, ``detail`` and ``raised_at`` are normalised (a hand-edited file)."""
    raised_at = alert.get('raised_at')
    return {
        'id': alert.get('id'), 'kind': alert.get('kind'), 'key': alert.get('key'),
        'name': clean_name(str(alert.get('name') or '')) or alert.get('key'),
        'detail': store.printable_detail(alert.get('detail')),
        'raised_at': raised_at if isinstance(raised_at, str) and len(raised_at) <= MAX_RAISED_AT else None,
    }


def _checked(probe: Probe, section: config_compute.ComputeSection) -> dict:
    """States, alerts, notices, problem and warnings for the answer; all empty when the read failed."""
    if section.read_failed:
        return {'states': {}, 'missing': [], 'alerts': [], 'notices': [], 'problem': '', 'warnings': []}
    result = matching.evaluate(section, probe.inventory)  # display only: nothing is raised, stopped or rebuilt
    present = {card.key for card in probe.inventory.cards}
    return {
        'states': result.states,
        'missing': [_missing_view(record, result.states.get(record['key']))
                    for record in result.section.cards if record['key'] not in present],
        'alerts': [_alert_view(alert) for alert in section.alerts],
        'notices': [dict(notice) for notice in result.notices],
        'problem': result.problem,
        'warnings': card_warnings.build(probe.inventory, result),
    }


def view(probe: Probe, section: config_compute.ComputeSection, checking: bool = False,
         check_failed: bool = False) -> dict:
    """The GET answer: the saved choice, the fresh cards, their states and the warnings (display only, S1).

    When ``config.json`` could not be read (C-20) the answer says so with
    ``config_read_failed: true`` and ``error``, and gives no saved choice
    (``method``, ``gpu_mode`` and ``revision`` are ``null``) instead of defaults.
    """
    failed = section.read_failed
    ticked = set() if failed else set(section.ticked_keys)
    uncached = _uncached_note(check_failed and not checking) if probe.blender_cards is None else None
    checked = _checked(probe, section)
    answer = {
        'config_read_failed': failed,
        'method': None if failed else section.method,
        'gpu_mode': None if failed else section.gpu_mode,
        'ticked': [] if failed else list(section.ticked_keys),
        'revision': None if failed else section.revision,
        'env_override': section.env_override,
        'os_probe_failed': probe.inventory.os_probe_failed,
        'blender_version': probe.inventory.blender_version,
        'blender_check': blender_check_state(probe.blender_cards, checking, check_failed),
        'cards': [_card_view(card, ticked, uncached, checked['states'].get(card.key))
                  for card in probe.inventory.cards],
        'rebuild_waiting': rebuild.rebuild_waiting(),  # a new card list waits for running GPU jobs (FR-CW-3)
    }
    answer.update(unreal_switch.state())  # the Unreal switch (O14): unreal_enabled, _env, _restart_needed
    answer.update(store_requests.view_fields(section))  # the manager-changes switch and last_request (FR-CW-3)
    answer.update((name, checked[name]) for name in ('missing', 'alerts', 'notices', 'problem', 'warnings'))
    if failed:
        answer['error'] = READ_FAILED_TEXT
    return answer


def _choice_text(data: dict, name: str, allowed, sentence: str) -> str:
    value = data[name]
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(sentence)
    return value


def _key_of(text) -> Optional[str]:
    if not isinstance(text, str) or len(text) > MAX_ADDRESS_TEXT:
        return None
    return APPLE_GPU_KEY if text == APPLE_GPU_KEY else card_key(text)


def _keys(data: dict, name: str) -> Tuple[str, ...]:
    value = data[name]
    if not isinstance(value, list) or len(value) > config_compute.MAX_ENTRIES:
        raise ValueError(f"The {name} list must be a list of at most {config_compute.MAX_ENTRIES} PCI addresses.")
    keys = tuple(_key_of(text) for text in value)
    if None in keys:
        raise ValueError(f"Each entry in the {name} list must be a PCI address such as 0000:01:00.0.")
    if len(set(keys)) != len(keys):
        raise ValueError(f"The {name} list names a graphics card more than once.")
    return keys


def _revision(data: dict) -> int:
    value = data['revision']
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= config_compute.MAX_REVISION:
        raise ValueError(f"The revision must be a whole number from 0 to {config_compute.MAX_REVISION}.")
    return value


def parse_body(body: bytes, with_switch: bool = False) -> dict:
    """The fields of a save request (FR-CW-5); ``ValueError`` with one plain sentence when it is not valid.

    With ``with_switch`` (the wizard) the optional ``allow_manager_changes`` (a JSON boolean) is accepted too.
    """
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):  # RecursionError: deeply nested arrays or objects
        data = None
    if not isinstance(data, dict):
        raise ValueError("The request body must be a JSON object.")
    if set(data) - set(_FIELDS) - ({SWITCH_FIELD} if with_switch else set()):
        raise ValueError("The request has a field this page does not accept.")
    if SWITCH_FIELD in data and not isinstance(data[SWITCH_FIELD], bool):
        raise ValueError("The allow_manager_changes switch must be true or false.")
    if set(_FIELDS) - set(data):
        raise ValueError("The request must give method, gpu_mode, cards, shown and revision.")
    fields = {
        'method': _choice_text(data, 'method', config_compute.METHODS,
                               "The compute method must be cpu, gpu or cpu_gpu."),
        'gpu_mode': _choice_text(data, 'gpu_mode', config_compute.GPU_MODES,
                                 "The GPU mode must be split or combined."),
        'cards': _keys(data, 'cards'),
        'shown': _keys(data, 'shown'),
        'revision': _revision(data),
    }
    if not set(fields['cards']) <= set(fields['shown']):
        raise ValueError("Every ticked graphics card must also be in the shown list.")
    if SWITCH_FIELD in data:
        fields[SWITCH_FIELD] = data[SWITCH_FIELD]
    return fields


def save(fields: dict, probe: Probe, source: str) -> Tuple[int, dict]:
    """Save through ``choice.save_choice``; ``(HTTP status, JSON body)``."""
    try:
        revision = choice.save_choice(fields['method'], fields['gpu_mode'], fields['cards'], fields['shown'],
                                      fields['revision'], probe.inventory)
    except store.EnvOverride:
        return 409, {'error': ENV_OVERRIDE_TEXT}
    except store.StaleRevision:
        return 409, {'error': STALE_TEXT}
    except store.CardHeld as held:
        return 400, {'error': HELD_TEXT.format(name=held.name)}
    except ValueError as refused:
        return 400, {'error': str(refused)}
    except (ConfigLockTimeoutError, OSError):
        logger.error("The graphics card choice from the %s could not be saved.", source, exc_info=True)
        return 503, {'error': NOT_SAVED_TEXT}
    mode = fields['gpu_mode'] if len(fields['cards']) >= 2 else 'split'  # as store.save keeps it
    logger.info("Graphics card choice saved from the %s: method %s, GPU mode %s, cards %s; revision %d.",
                source, fields['method'], mode, ', '.join(fields['cards']) or 'none', revision)
    return 200, {'status': 'ok', 'revision': revision}
