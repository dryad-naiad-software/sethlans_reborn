# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The outcome of a card change, as a worker reports it in ``cards.request_outcome``
(worker card selection FR-CM-13, AC-29, AC-33).

Self-reported and untrusted.  ``reported_outcome`` keeps the outcome only
when every field passes FR-CM-13 (other keys are ignored and never
stored); anything else is dropped and changes nothing.  ``outcome_effect``
then decides, from the row's own values, what an accepted outcome changes:
it counts only for an id issued to that worker; the outcome for the
pending id clears the pending request; ``card_choice_outcome`` is replaced
only by an outcome for a later issued id than the stored one (so a repeat,
or a late outcome for an older request, changes nothing there).  Issue
order is the manager's own, never the worker's clock.
"""

import logging
import threading
from collections import deque
from collections.abc import Mapping
from datetime import datetime

from ..serializers.worker_card_schemas import CARD_OUTCOME_CODES, CARD_OUTCOME_RESULTS
from ..strict_json import _int_in
from .heartbeat_cards import MAX_REVISION, is_alert_id
from .heartbeat_unreal import MAX_REASON_LENGTH, _sanitize_reason

logger = logging.getLogger(__name__)

MAX_TIME_TEXT = 40
RESULTS, CODES = frozenset(CARD_OUTCOME_RESULTS), frozenset(CARD_OUTCOME_CODES)
FIELDS = ('id', 'result', 'code', 'reason', 'revision', 'finished_at')
# The last unknown outcome ids warned about, per worker pk: one WARNING per
# id, also when a worker alternates between a few of them.
WARNED_IDS_PER_WORKER = 8
_warned_unknown = {}
_warned_lock = threading.Lock()


def _finished_at(raw):
    """An aware ISO 8601 time of at most 40 characters, in ``isoformat()`` form, else ``None``."""
    if not isinstance(raw, str) or len(raw) > MAX_TIME_TEXT:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.utcoffset() is not None else None


_CHECKS = (
    ('id', is_alert_id),
    ('result', lambda raw: isinstance(raw, str) and raw in RESULTS),
    ('code', lambda raw: isinstance(raw, str) and raw in CODES),
    ('reason', lambda raw: isinstance(raw, str) and len(raw) <= MAX_REASON_LENGTH),
    ('revision', lambda raw: _int_in(raw, 0, MAX_REVISION)),
    ('finished_at', lambda raw: _finished_at(raw) is not None),
)


def reported_outcome(data):
    """The request body's ``cards.request_outcome`` in its checked form, else ``None``."""
    cards = data.get('cards') if isinstance(data, Mapping) else None
    raw = cards.get('request_outcome') if isinstance(cards, dict) else None
    if not isinstance(raw, dict) or not all(name in raw and check(raw[name]) for name, check in _CHECKS):
        return None
    if (raw['result'] == 'applied') != (raw['code'] == 'applied'):
        return None
    outcome = {name: raw[name] for name in FIELDS}
    outcome['reason'] = _sanitize_reason(raw['reason'])
    outcome['finished_at'] = _finished_at(raw['finished_at'])
    return outcome


def outcome_effect(row, outcome):
    """``(clear_pending, new card_choice_outcome or None)``, or ``None`` when the id was not issued.

    ``row`` maps ``card_choice_request``, ``card_choice_issued`` and
    ``card_choice_outcome`` to their stored values.
    """
    history = row['card_choice_issued']
    issued = [entry for entry in (history if isinstance(history, list) else []) if isinstance(entry, dict)]
    ids = [entry.get('id') for entry in issued]
    if outcome['id'] not in ids:
        return None
    pending, stored = row['card_choice_request'], row['card_choice_outcome']
    clear = isinstance(pending, dict) and pending.get('id') == outcome['id']
    stored_id = stored.get('id') if isinstance(stored, dict) else None
    newer = stored_id not in ids or ids.index(outcome['id']) > ids.index(stored_id)
    if not newer:
        return clear, None
    entry = issued[ids.index(outcome['id'])]
    return clear, {**outcome, 'requested_by': entry.get('requested_by'), 'requested_at': entry.get('requested_at')}


def warn_unknown(worker, outcome_id):
    """One WARNING naming the worker for an outcome id not issued to it; DEBUG while it repeats."""
    with _warned_lock:
        seen = _warned_unknown.setdefault(worker.pk, deque(maxlen=WARNED_IDS_PER_WORKER))
        first = outcome_id not in seen
        if first:
            seen.append(outcome_id)
    (logger.warning if first else logger.debug)(
        "Ignoring the outcome of graphics card change %s from worker %s: not issued to it.",
        outcome_id, worker.hostname,
    )


def log_outcome(hostname, record):
    """FR-CM-13's INFO line for a newly stored outcome."""
    logger.info("Worker %s %s graphics card change %s (%s).",
                hostname, record['result'], record['id'], record['code'])
