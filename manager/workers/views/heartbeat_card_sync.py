# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Manager-owned card fields and their part of the heartbeat answer
(worker card selection D24, FR-CR-4).

``sync`` runs once per heartbeat or registration, after the handler's own
save (which never lists the manager-owned fields).  It reads the row
without a lock first and takes the row lock only when it has something to
write: today, dismissal ids that no longer name an alert in the stored
``card_status`` (the worker applied the dismissal and stopped reporting
the alert).  Under the lock it re-reads and prunes again, so a dismissal
stored by the dismiss action at any moment before the lock is kept.

Chunk 11 adds the pending card-choice request and the outcome processing
(FR-CM-12, FR-CM-13): the outcome is one more reason to take the lock,
and the answer gains ``card_choice_request``.

The worker accepts dismissal ids only from this authenticated answer.
Alert ids are visible on the worker's anonymous card page, so knowing an
id is never permission: only the admin action adds a dismissal.
"""

import logging

from django.db import DatabaseError, transaction

from ..models import Worker
from ..services.row_locks import lock_own_rows

logger = logging.getLogger(__name__)


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


def _answer(dismissals):
    return {
        'dismissed_card_alerts': list(dismissals),
        'card_alert_dismissals': list(dismissals),
    }


def _prune_locked(pk):
    """Re-read the row under its lock, prune its dismissals and save them."""
    with transaction.atomic():
        locked = (
            lock_own_rows(Worker.objects)
            .only('pk', 'card_status', 'card_alert_dismissals')
            .filter(pk=pk).first()
        )
        if locked is None:
            return []
        kept = pruned_dismissals(locked.card_alert_dismissals, locked.card_status)
        if kept != locked.card_alert_dismissals:
            locked.card_alert_dismissals = kept
            locked.save(update_fields=['card_alert_dismissals'])
        return kept


def sync(worker, data):
    """Write the manager-owned card fields if needed; return their answer keys.

    Returns ``{"dismissed_card_alerts": [...], "card_alert_dismissals":
    [...]}``, read from the row used, to be merged into the answer over
    the serializer's values.  ``data`` is the request body (read by
    chunk 11's outcome processing).  On ``DatabaseError`` one WARNING is
    logged and only ``dismissed_card_alerts`` is returned, from the row
    as it was loaded for this request; the next heartbeat retries.
    """
    try:
        row = (
            Worker.objects.filter(pk=worker.pk)
            .values('card_status', 'card_alert_dismissals').first()
        )
        if row is None:
            return _answer([])
        dismissals = row['card_alert_dismissals']
        kept = pruned_dismissals(dismissals, row['card_status'])
        if kept != dismissals:
            kept = _prune_locked(worker.pk)
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
    worker.card_alert_dismissals = kept
    return _answer(kept)
