# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager-owned card fields and their part of the heartbeat answer
(worker card selection D24, FR-CR-4).

``sync`` runs once per heartbeat or registration, after the handler's own
save (which never lists the manager-owned fields).  It reads the row
without a lock first and takes the row lock only when it has something to
write: dismissal ids that no longer name an alert in the stored
``card_status`` (the worker applied the dismissal and stopped reporting
the alert).  Under the lock it re-reads and prunes again, so a dismissal
stored by the dismiss action at any moment before the lock is kept; the
same re-read decides the outcome's effect, so a request stored by the
card-choice action before the lock is never cleared by an older outcome.

The card change from the manager (FR-CM-12, FR-CM-13) is the other
reason to take the lock: a reported outcome that clears the pending
request or replaces the stored outcome (``heartbeat_card_outcome``).  A
repeated outcome, which the worker sends in every heartbeat, writes
nothing and takes no lock.  The answer carries the pending request
(``card_choice_request``, ``null`` when none) after the outcome was
processed, and the stored outcome.

The worker accepts dismissal ids only from this authenticated answer.
Alert ids are visible on the worker's anonymous card page, so knowing an
id is never permission: only the admin action adds a dismissal.
"""

import logging

from django.db import DatabaseError, transaction

from ..models import Worker
from ..services.row_locks import lock_own_rows
from .heartbeat_card_outcome import log_outcome, outcome_effect, reported_outcome, warn_unknown

logger = logging.getLogger(__name__)

# The manager-owned card fields and the report they depend on.
FIELDS = (
    'card_status', 'card_alert_dismissals',
    'card_choice_request', 'card_choice_issued', 'card_choice_outcome',
)
_EMPTY = {'card_status': {}, 'card_alert_dismissals': [], 'card_choice_request': {},
          'card_choice_issued': [], 'card_choice_outcome': {}}


def alert_ids(card_status):
    """The ids of the alerts in a stored ``card_status``, in order."""
    alerts = card_status.get('alerts') if isinstance(card_status, dict) else None
    if not isinstance(alerts, list):
        return []
    return [
        alert['id'] for alert in alerts
        if isinstance(alert, dict) and isinstance(alert.get('id'), str)
    ]


def pruned_dismissals(dismissals, card_status):
    """The dismissal ids that still name a stored alert, in order, once each."""
    open_ids = set(alert_ids(card_status))
    kept = []
    for alert_id in dismissals if isinstance(dismissals, list) else []:
        if isinstance(alert_id, str) and alert_id in open_ids and alert_id not in kept:
            kept.append(alert_id)
    return kept


def _answer(row):
    dismissals = row['card_alert_dismissals']
    return {
        'dismissed_card_alerts': list(dismissals),
        'card_alert_dismissals': list(dismissals),
        'card_choice_request': row['card_choice_request'] or None,
        'card_choice_outcome': row['card_choice_outcome'] or None,
    }


def _prune_locked(pk, outcome=None):
    """Re-read the row under its lock, prune its dismissals, apply ``outcome``; save what changed.

    Returns the row's values (``FIELDS``) as written, or ``None`` when the
    row is gone.  A newly stored outcome is logged after the commit.
    """
    record = None
    with transaction.atomic():
        locked = lock_own_rows(Worker.objects).only('pk', 'hostname', *FIELDS).filter(pk=pk).first()
        if locked is None:
            return None
        changed = []
        kept = pruned_dismissals(locked.card_alert_dismissals, locked.card_status)
        if kept != locked.card_alert_dismissals:
            locked.card_alert_dismissals = kept
            changed.append('card_alert_dismissals')
        values = {name: getattr(locked, name) for name in FIELDS}
        clear, record = (outcome_effect(values, outcome) if outcome is not None else None) or (False, None)
        if clear:
            locked.card_choice_request = {}
            changed.append('card_choice_request')
        if record is not None:
            locked.card_choice_outcome = record
            changed.append('card_choice_outcome')
        if changed:
            locked.save(update_fields=changed)
    if record is not None:
        log_outcome(locked.hostname, record)
    return {name: getattr(locked, name) for name in FIELDS}


def _checked_outcome(worker, row, data):
    """The reported outcome when it would change ``row``, else ``None`` (an unknown id is warned about)."""
    outcome = reported_outcome(data)
    if outcome is None:
        return None
    effect = outcome_effect(row, outcome)
    if effect is None:
        warn_unknown(worker, outcome['id'])
        return None
    return outcome if any(effect) else None


def sync(worker, data):
    """Write the manager-owned card fields if needed; return their answer keys.

    Returns ``{"dismissed_card_alerts", "card_alert_dismissals",
    "card_choice_request", "card_choice_outcome"}``, read from the row
    used, to be merged into the answer over the serializer's values.
    ``data`` is the request body (its ``cards.request_outcome``).  On
    ``DatabaseError`` one WARNING is logged and only
    ``dismissed_card_alerts`` is returned, from the row as it was loaded
    for this request; the next heartbeat retries.  The answer then carries
    the serializer's ``card_choice_request`` and ``card_choice_outcome``
    from the start of the request, so a request withdrawn meanwhile can be
    delivered for one more beat (FR-CM-11 allows that: its outcome is
    still shown).
    """
    try:
        row = Worker.objects.filter(pk=worker.pk).values(*FIELDS).first() or _EMPTY
        outcome = _checked_outcome(worker, row, data)
        kept = pruned_dismissals(row['card_alert_dismissals'], row['card_status'])
        if outcome is not None:
            row = _prune_locked(worker.pk, outcome) or _EMPTY
        elif kept != row['card_alert_dismissals']:
            row = _prune_locked(worker.pk) or _EMPTY
    except DatabaseError:
        logger.warning(
            "Card alert sync for %s failed; retried next heartbeat.",
            worker.hostname, exc_info=True,
        )
        loaded = worker.card_alert_dismissals
        return {'dismissed_card_alerts': [
            alert_id for alert_id in (loaded if isinstance(loaded, list) else [])
            if isinstance(alert_id, str)
        ]}
    worker.card_alert_dismissals = row['card_alert_dismissals']
    worker.card_choice_request = row['card_choice_request']
    worker.card_choice_outcome = row['card_choice_outcome']
    return _answer(row)
