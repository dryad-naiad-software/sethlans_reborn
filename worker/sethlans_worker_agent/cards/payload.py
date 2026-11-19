# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The ``cards`` object of registration and every heartbeat (FR-CR-1, D21).

``build()`` reads the saved choice once (``store.snapshot()``) and takes the
card list from the probe cache of the worker's card pages
(``web_ui.cards_cache.report_probe``): a probe at most one heartbeat interval
old, so the heartbeat runs the OS probe at most once per interval, and none
while an Unreal render holds the machine (FR-CK-1; with nothing cached then,
the ``cards`` key is left out). The states, alerts,
notices, problem and warnings are the card GET's (``web_ui.cards_view.view``),
reshaped. Like the GET it is display data only (S1): it raises no alert,
stops no job, requests no rebuild and never starts Blender.

When something failed, only what is safe is sent:

- ``config.json`` could not be read (C-20): no ``cards`` key, so the manager
  keeps the last report; never defaults that look like a real choice.
- The OS probe failed: the cards Blender reports at a PCI address (no vendor
  or device ids; a ticked card Blender does not list is ``unknown``), plus
  the warning ``os_probe_failed``.
- Anything raised: no ``cards`` key; logged once.

Every string is printable ASCII (the manager turns anything else into ``?``)
within the manager's caps, and every list holds at most 16 entries, so the
manager drops nothing for its length. Holds no lock of its own.
"""
import logging
from typing import List, Optional

from sethlans_worker_agent import config
from sethlans_worker_agent.cards import store, store_requests
from sethlans_worker_agent.cards.alert_records import ALERT_KINDS
from sethlans_worker_agent.cards.identity import is_card_key, key_sort_value
from sethlans_worker_agent.cards.os_probe import log_once

logger = logging.getLogger(__name__)

SCHEMA = 1
MAX_ENTRIES = 16
MAX_NAME = 128
MAX_TEXT = 200  # note, detail, warning text and problem (the manager's MAX_REASON_LENGTH)
MAX_RAISED_AT = 40
BACKENDS = frozenset({'CUDA', 'OPTIX', 'HIP', 'ONEAPI', 'METAL'})
STATES = frozenset({'ok', 'lost', 'changed', 'blender_lost', 'held', 'unticked', 'unknown'})
OS_PROBE_CODE = 'os_probe_failed'
OS_PROBE_TEXT = ("The worker could not list its graphics cards through the operating system; the cards shown are "
                 "the ones Blender reports, without vendor and device ids.")
MISSING_NOTES = {'lost': 'missing from the card list',
                 'unknown': 'the operating system could not list the graphics cards'}
_READ_FAILED_LOG = ("The worker's settings file could not be read; heartbeats leave out the graphics card report "
                    "until it can be read.")


def text(value, limit: int) -> str:
    """``value`` as printable ASCII (anything else becomes ``?``), at most ``limit`` characters."""
    return ''.join(ch if ' ' <= ch <= '~' else '?' for ch in (value if isinstance(value, str) else ''))[:limit]


def _pci_id(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFF else None


def _mark(value) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _card(entry: dict, ticked: bool, state, backend=None, note='', screen=None, primary_screen=None) -> dict:
    key = entry['key']
    if state not in STATES:
        state = 'unknown' if ticked else 'unticked'
    return {
        'key': key, 'name': text(entry.get('name'), MAX_NAME) or key,
        'vendor_id': _pci_id(entry.get('vendor_id')), 'device_id': _pci_id(entry.get('device_id')),
        'ticked': ticked, 'state': state, 'blender': backend is not None,
        'backend': backend if backend in BACKENDS else None, 'note': text(note, MAX_TEXT),
        'screen': _mark(screen), 'primary_screen': _mark(primary_screen),
    }


def cards_of(answer: dict) -> List[dict]:
    """Inventory cards and the ticked cards it lacks (from their saved records), in PCI address order.

    Over 16 entries, the ticked ones are kept first.
    """
    entries = [_card(entry, entry.get('ticked') is True, entry.get('state'), entry.get('backend'), entry.get('note'),
                     entry.get('screen'), entry.get('primary_screen')) for entry in answer['cards']]
    entries += [_card(entry, True, entry.get('state'), note=MISSING_NOTES.get(entry.get('state'), ''))
                for entry in answer['missing']]
    entries = [entry for entry in entries if is_card_key(entry['key'])]
    if len(entries) > MAX_ENTRIES:
        ticked = [entry for entry in entries if entry['ticked']][:MAX_ENTRIES]
        entries = ticked + [entry for entry in entries if not entry['ticked']][:MAX_ENTRIES - len(ticked)]
    return sorted(entries, key=lambda entry: key_sort_value(entry['key']))


def alerts_of(answer: dict) -> List[dict]:
    """The open alerts (FR-CK-5 records), newest 16."""
    found = []
    for alert in answer['alerts']:
        if alert.get('kind') not in ALERT_KINDS or not is_card_key(alert.get('key')):
            continue
        raised_at = alert.get('raised_at')
        found.append({'id': text(alert.get('id'), 32), 'kind': alert['kind'], 'key': alert['key'],
                      'name': text(alert.get('name'), MAX_NAME) or alert['key'],
                      'detail': text(alert.get('detail'), MAX_TEXT),
                      'raised_at': text(raised_at, MAX_RAISED_AT) if isinstance(raised_at, str) else None})
    return found[-MAX_ENTRIES:]


def notices_of(answer: dict) -> List[dict]:
    return [{'kind': notice['kind'], 'key': notice['key'], 'name': text(notice.get('name'), MAX_NAME) or notice['key']}
            for notice in answer['notices']][:MAX_ENTRIES]


def warnings_of(answer: dict, os_probe_failed: bool) -> List[dict]:
    found = [{'code': OS_PROBE_CODE, 'key': None, 'text': OS_PROBE_TEXT}] if os_probe_failed else []
    found += [{'code': text(warning.get('code'), 32), 'key': warning.get('key') if is_card_key(warning.get('key'))
               else None, 'text': text(warning.get('text'), MAX_TEXT)} for warning in answer['warnings']]
    return found[:MAX_ENTRIES]


def shape(answer: dict, section, os_probe_failed: bool) -> dict:
    """The FR-CR-1 object from one card GET answer (``cards_view.view``) and the section it was built from."""
    return {
        'schema': SCHEMA,
        'method': answer['method'],
        'gpu_mode': answer['gpu_mode'],
        'env_override': section.env_override is True,
        'revision': answer['revision'],
        'manager_changes_allowed': section.allow_manager_changes is True and not section.env_override,  # FR-CM-1
        'rebuild_waiting': answer['rebuild_waiting'] is True,
        'problem': text(answer['problem'], MAX_TEXT),
        'cards': cards_of(answer),
        'alerts': alerts_of(answer),
        'notices': notices_of(answer),
        'warnings': warnings_of(answer, os_probe_failed),
        'request_outcome': store_requests.outcome_for_report(section),  # FR-CM-7, without requested_by/_at
    }


def _probe_for_report():
    """The cached card probe, at most one heartbeat interval old; none is run during an Unreal render."""
    from sethlans_worker_agent import unreal_bridge
    from sethlans_worker_agent.web_ui import cards_cache
    max_age = max(cards_cache.CACHE_SECONDS, float(config.HEARTBEAT_INTERVAL_SECONDS))
    return cards_cache.report_probe(max_age, probe_allowed=unreal_bridge.drift_check_allowed())


def build() -> Optional[dict]:
    """The ``cards`` object; ``None`` when the saved choice could not be read (C-20) or no probe may run yet."""
    section = store.snapshot()
    if section.read_failed:
        log_once(logger, logging.WARNING, _READ_FAILED_LOG)
        return None
    from sethlans_worker_agent.web_ui import cards_view
    probe = _probe_for_report()
    if probe is None:
        logger.debug("No card probe yet and an Unreal render holds the machine; the card report is left out.")
        return None
    return shape(cards_view.view(probe, section), section, probe.inventory.os_probe_failed)


def add_to(payload: dict) -> None:
    """Add ``cards`` to a registration or heartbeat payload; left out when it cannot be built. Never raises."""
    try:
        report = build()
    except Exception:  # noqa: BLE001 - the heartbeat is sent without the report
        log_once(logger, logging.ERROR, "The graphics card report could not be built; heartbeats are sent "
                 "without it.", exc_info=True)
        return
    if report is not None:
        payload['cards'] = report
