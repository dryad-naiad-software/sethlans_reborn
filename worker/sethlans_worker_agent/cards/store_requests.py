# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The manager-changes switch and the last manager request in the ``compute`` section (FR-CM-1, FR-CM-7).

Both writers follow ``cards.store``'s rules (FR-CC-4): ``store._section_lock`` is held for the read-check-write,
the read is strict and the write is one ``config_store.set_many`` through ``store._write`` (which also saves the
alerts kept in memory). Neither changes ``revision`` (FR-CM-2).

- ``set_allow_manager_changes(allow, source)``: written only from the worker's own pages, never by a request
  (D18); ``SwitchEnvOverride`` when ``SETHLANS_COMPUTE_ALLOW_MANAGER_CHANGES`` sets it. One INFO line.
- ``set_last_request(record)``: the outcome of a refused request, written with the last 8 evaluated ids
  (``store.request_pairs``). When it cannot be written it is kept in memory for the process
  (``store.remember_request``, inside the section lock) and the error is raised (FR-CM-7); ``last_request(section)``
  returns it instead of the saved one until a later outcome is written. An applied request's outcome is written by
  ``store.save`` with the choice; any write of ``last_request`` empties the memory in the same critical section.
- ``already_evaluated(section, id)``: the id is among the last 8 evaluated, so it is never evaluated again (D16).
- ``outcome_for_report`` and ``view_fields`` shape the record for the heartbeat (FR-CR-1) and the card GETs.
"""
import logging
import re
from typing import Optional

from sethlans_worker_agent.cards import store

logger = logging.getLogger(__name__)

RESULTS = ('applied', 'refused')
CODES = ('applied', 'invalid', 'env_override', 'not_allowed', 'probe_failed', 'identity_mismatch',
         'stale_revision', 'card_held', 'save_failed')
MAX_REASON = 200
MAX_TIME_TEXT = 40
_HEX32 = re.compile(r'[0-9a-f]{32}')
_REQUESTED_BY = re.compile(r'user id=[0-9]{1,12}')
_SWITCH_KEY = 'compute.allow_manager_changes'


class SwitchEnvOverride(Exception):
    """The switch is set by ``SETHLANS_COMPUTE_ALLOW_MANAGER_CHANGES``."""


def set_allow_manager_changes(allow: bool, source: str) -> None:
    """Save the switch (FR-CM-1); ``SwitchEnvOverride``, ``ConfigLockTimeoutError`` or ``OSError`` when not saved."""
    if not isinstance(allow, bool):
        raise ValueError('the switch must be true or false')
    with store._section_lock:
        section = store._load(strict=True)
        if section.allow_from_env:
            raise SwitchEnvOverride()
        store._write([(_SWITCH_KEY, allow)], section)
    logger.info("Manager changes to the graphics card choice allowed: %s (set on the %s).", 'yes' if allow else 'no',
                source)


def set_last_request(record: dict) -> None:
    """Save a refused request's outcome (with the recent ids); on failure keep it in memory and raise."""
    with store._section_lock:
        try:
            section = store._load(strict=True)
            store._write(store.request_pairs(section, record), section)  # empties the request memory
        except Exception:
            store.remember_request(record)
            raise


def last_request(section) -> Optional[dict]:
    """The last request's outcome: the one kept in memory, else the saved one (``None`` when there is none)."""
    remembered, _ids = store.remembered_request()
    if remembered is not None:
        return remembered
    return dict(section.last_request) if isinstance(section.last_request, dict) else None


def already_evaluated(section, request_id: str) -> bool:
    """True for an id among the last 8 evaluated (saved or kept in memory) or the last outcome's (D16)."""
    remembered, ids = store.remembered_request()
    saved = section.last_request.get('id') if isinstance(section.last_request, dict) else None
    return request_id in set(section.recent_request_ids) | set(ids) | {saved} | (
        {remembered['id']} if remembered is not None else set())


def printable(value, limit: int) -> str:
    """``value`` as printable ASCII (anything else becomes ``?``), at most ``limit`` characters."""
    return ''.join(ch if ' ' <= ch <= '~' else '?' for ch in (value if isinstance(value, str) else ''))[:limit]


def _revision(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2 ** 31 - 1 else None


def clean_record(record) -> Optional[dict]:
    """The record with every field checked (a hand-edited file), or ``None`` when its id, result or code is not valid.

    ``requested_by`` is kept only in its ``user id=<pk>`` form (S2); anything else becomes ``None``.
    """
    if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not _HEX32.fullmatch(record['id']):
        return None
    if record.get('result') not in RESULTS or record.get('code') not in CODES \
            or (record['code'] == 'applied') != (record['result'] == 'applied'):
        return None
    requested_by = record.get('requested_by')
    times = {name: printable(record.get(name), MAX_TIME_TEXT) or None for name in ('requested_at', 'finished_at')}
    return {'id': record['id'], 'result': record['result'], 'code': record['code'],
            'reason': printable(record.get('reason'), MAX_REASON), 'revision': _revision(record.get('revision')),
            'requested_by': requested_by if isinstance(requested_by, str) and _REQUESTED_BY.fullmatch(requested_by)
            else None, 'requested_at': times['requested_at'], 'finished_at': times['finished_at']}


def outcome_for_report(section) -> Optional[dict]:
    """``cards.request_outcome`` (FR-CR-1): the last outcome without ``requested_by`` and ``requested_at``."""
    record = clean_record(last_request(section))
    if record is None:
        return None
    return {name: record[name] for name in ('id', 'result', 'code', 'reason', 'revision', 'finished_at')}


def view_fields(section) -> dict:
    """The switch and the last manager change for both card GETs (FR-CW-3, FR-CW-4)."""
    failed = section.read_failed
    return {
        'allow_manager_changes': None if failed else section.allow_manager_changes is True,
        'allow_manager_changes_env': section.allow_from_env is True,
        'manager_changes_allowed': not failed and section.allow_manager_changes is True and not section.env_override,
        'last_request': clean_record(last_request(section)),
    }


def reset_memory_for_tests() -> None:
    """Forget the outcome kept in memory. Test-only hook."""
    store.reset_memory_for_tests()
