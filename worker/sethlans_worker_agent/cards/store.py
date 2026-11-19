# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The only writer of the ``compute`` section of ``config.json`` (FR-CC-4).

Every read-check-write holds ``_section_lock`` and writes once through ``config_store.set_many``. Lock order:
``_section_lock``, then ``_memory_lock`` (a leaf) or ``config_store._config_lock``, then the file lock;
``WorkerCapacity._lock`` is never taken under ``_section_lock`` (callers request a rebuild after a write
returned). Writers read strictly and refuse on an unreadable file with ``config_store.ConfigReadError``, an
``OSError`` (C-20).

Alerts (FR-CK-5, C5, C-21): an alert whose write failed is kept in memory for
the process and the error is raised. Every read (``snapshot`` and each writer,
through ``_load``) merges the memory in, so ``save`` sees such an alert as
holding; the next successful write puts the memory in the file and empties it.
``snapshot`` takes no ``_section_lock``: it copies the memory first.

Manager requests (FR-CM-7, D16): an outcome whose write failed is kept in
memory (``remember_request``, read by ``cards.store_requests``); a later write of
``last_request`` empties it inside the same critical section. The refusal rules
and exception classes live in ``cards.store_rules``.
"""
import dataclasses
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import store_rules
from sethlans_worker_agent.cards.alert_records import ALERT_KINDS
from sethlans_worker_agent.cards.identity import clean_name, is_card_key, key_sort_value
from sethlans_worker_agent.cards.in_use import COMBINE_TEXT, one_family, record_of
from sethlans_worker_agent.cards.store_rules import CardHeld, EnvOverride, ManagerChangesOff, StaleRevision
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError

__all__ = ['CardHeld', 'EnvOverride', 'ManagerChangesOff', 'StaleRevision', 'add_alert', 'remove_alerts', 'save',
           'snapshot', 'printable_detail', 'request_pairs', 'remember_request', 'remembered_request']

logger = logging.getLogger(__name__)

MAX_ALERTS = config_compute.MAX_ENTRIES
MAX_DETAIL = 200

_section_lock = threading.Lock()
_memory_lock = threading.Lock()   # leaf; guards _memory_alerts
_memory_alerts: List[dict] = []   # alerts whose write failed; mutated only under _section_lock


MAX_REQUEST_IDS = 8
_memory_request: Optional[dict] = None  # a request outcome whose write failed; under _memory_lock
_memory_request_ids: List[str] = []     # ids evaluated whose write failed; under _memory_lock


def _merged(memory: List[dict], section):
    """``section`` with the alerts in ``memory`` that the file does not hold, newest 16 kept."""
    if not memory:
        return section
    saved = {(alert['kind'], alert['key']) for alert in section.alerts} | {alert['id'] for alert in section.alerts}
    extra = [alert for alert in memory if (alert['kind'], alert['key']) not in saved and alert['id'] not in saved]
    return dataclasses.replace(section, alerts=tuple(list(section.alerts) + extra)[-MAX_ALERTS:])


def _memory_copy() -> List[dict]:
    with _memory_lock:
        return [dict(alert) for alert in _memory_alerts]


def _load(strict: bool):
    """The section with the in-memory alerts merged in (the memory is copied before the file is read)."""
    return _merged(_memory_copy(), config_compute.load(strict=strict))


def snapshot() -> config_compute.ComputeSection:
    """The section and alerts from one load; ``read_failed`` (defaults) when unreadable, so a pass is skipped."""
    return _load(strict=False)


def _write(pairs: list, section) -> None:
    """One ``set_many``; caller holds ``_section_lock``. Alerts in memory go into the same write.

    A write holding ``compute.last_request`` (built by ``request_pairs``) also empties the request memory.
    """
    if _memory_copy() and not any(dotted == 'compute.alerts' for dotted, _value in pairs):
        pairs = pairs + [('compute.alerts', [dict(alert) for alert in section.alerts])]
    config_store.set_many(pairs)
    global _memory_request
    with _memory_lock:
        _memory_alerts.clear()  # every alert in memory was merged into ``section`` and is now in the file
        if any(dotted == 'compute.last_request' for dotted, _value in pairs):
            _memory_request = None
            _memory_request_ids.clear()  # merged into the write by request_pairs


def request_pairs(section, record: dict) -> list:
    """``last_request`` and the last 8 evaluated ids (file, memory, this one) for one write (D16, FR-CM-7)."""
    with _memory_lock:
        ids = [item for item in list(section.recent_request_ids) + _memory_request_ids if item != record['id']]
    return [('compute.last_request', dict(record)),
            ('compute.recent_request_ids', (list(dict.fromkeys(ids)) + [record['id']])[-MAX_REQUEST_IDS:])]


def remember_request(record: dict) -> None:
    """Keep an outcome whose write failed for the process (FR-CM-7); caller holds ``_section_lock``."""
    global _memory_request
    with _memory_lock:
        _memory_request = dict(record)
        _memory_request_ids[:] = [item for item in _memory_request_ids if item != record['id']][-(MAX_REQUEST_IDS - 1):]
        _memory_request_ids.append(record['id'])


def remembered_request():
    """``(outcome kept in memory or None, ids kept in memory)``."""
    with _memory_lock:
        return (dict(_memory_request) if _memory_request is not None else None), list(_memory_request_ids)


def printable_detail(detail) -> str:
    """An alert ``detail`` as saved and shown: printable ASCII, at most ``MAX_DETAIL`` characters (FR-CK-5)."""
    return ''.join(ch if ' ' <= ch <= '~' else '?' for ch in str(detail or ''))[:MAX_DETAIL]


def add_alert(kind: str, key: str, name: str, detail: str) -> bool:
    """Add an open alert (FR-CK-5); ``True`` only when newly added, ``False`` when one of its kind and key is open.

    ``ValueError`` for an unknown kind or key; a failed read or write keeps the alert in memory and raises.
    """
    if kind not in ALERT_KINDS or not is_card_key(key):
        raise ValueError(f'not a card alert: {kind!r} for {key!r}')
    alert = {'id': uuid.uuid4().hex, 'kind': kind, 'key': key, 'name': clean_name(name) or key,
             'detail': printable_detail(detail), 'raised_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    with _section_lock:
        try:
            section = _load(strict=True)
        except (ConfigLockTimeoutError, OSError):
            _keep_in_memory(alert, _memory_copy())
            raise
        if any(open_alert['kind'] == kind and open_alert['key'] == key for open_alert in section.alerts):
            _retry_memory(section)
            return False
        try:
            _write([('compute.alerts', _capped(list(section.alerts) + [alert]))], section)
        except (ConfigLockTimeoutError, OSError):
            _keep_in_memory(alert, section.alerts)
            raise
    return True


def _retry_memory(section) -> None:
    """Try again to write the alerts kept in memory; caller holds ``_section_lock``. A failure keeps them."""
    if not _memory_copy():
        return
    try:
        _write([('compute.alerts', [dict(alert) for alert in section.alerts])], section)
    except (ConfigLockTimeoutError, OSError):
        logger.debug("The graphics card alerts kept in memory could not be saved yet.", exc_info=True)


def _capped(alerts: List[dict]) -> List[dict]:
    while len(alerts) > MAX_ALERTS:
        dropped = alerts.pop(0)
        logger.warning("More than %d graphics card alerts are open; the oldest (%s for %s) is dropped.",
                       MAX_ALERTS, dropped['kind'], dropped['key'])
    return alerts


def _keep_in_memory(alert: dict, open_alerts) -> None:
    """Keep ``alert`` for the process unless one of its kind and key is open; caller holds ``_section_lock``."""
    if any(other['kind'] == alert['kind'] and other['key'] == alert['key'] for other in open_alerts):
        return
    with _memory_lock:
        _memory_alerts[:] = _capped(_memory_alerts + [alert])
    logger.error("The graphics card alert %s for %s could not be saved; it is kept until the worker stops.",
                 alert['kind'], alert['key'])


def remove_alerts(ids: Iterable[str]) -> List[dict]:
    """Remove the open alerts with these ids (file and memory); returns the removed records.

    Unknown ids are ignored; no open id, no write. An unreadable file or a failed write removes nothing
    (file or memory) and raises; a successful write empties the memory (``_write``).
    """
    wanted = set(ids)
    with _section_lock:
        section = _load(strict=True)
        removed = [dict(alert) for alert in section.alerts if alert['id'] in wanted]
        if not removed:
            return []
        _write([('compute.alerts', [dict(alert) for alert in section.alerts if alert['id'] not in wanted])], section)
    return removed


def save(method: str, gpu_mode: str, ticked_keys, shown_keys, inventory, base_revision: int,
         last_request: Optional[dict] = None, manager_request: bool = False) -> int:
    """Save the card choice in one write and return the new revision (FR-CC-4).

    Refuses, writing nothing: ``ConfigReadError``, ``EnvOverride``,
    ``ManagerChangesOff`` (``manager_request`` and the switch is not on, FR-CM-1),
    ``StaleRevision``, ``CardHeld`` (alerts in memory count, C-21), then
    ``ValueError`` (ticked key not in ``inventory`` or not shown, GPU with no
    tick, Combine GPUs against the one-family rule). ``combined`` with fewer
    than two ticked cards is saved as ``split``. ``last_request`` goes into the
    same write with the recent request ids (``request_pairs``).
    """
    ticked = store_rules.distinct(ticked_keys)
    shown = store_rules.distinct(shown_keys)
    by_key = {card.key: card for card in inventory.cards}
    with _section_lock:
        section = _load(strict=True)
        store_rules.check_source(section, base_revision, manager_request)
        store_rules.check_held(ticked, by_key, section)
        store_rules.check_values(method, gpu_mode, ticked, shown, by_key)
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
            pairs += request_pairs(section, last_request)
        _write(pairs, section)
    return revision


def reset_memory_for_tests() -> None:
    """Forget the alerts and the request outcome kept in memory. Test-only hook."""
    global _memory_request
    with _memory_lock:
        _memory_alerts.clear()
        _memory_request = None
        _memory_request_ids.clear()
