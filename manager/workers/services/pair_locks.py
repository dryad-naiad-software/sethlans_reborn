# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The record locks and the driver re-test of one (worker, card kind) pair,
shared by ``pilot-result/`` and ``memory-report/`` (Unreal per-machine
memory test D3, D15).

Both callers hold the Job row, then the caller's own Worker row, before
:func:`lock_pair` locks records (the invariant of ``worker_locks``: every
writer of a record's ``current``, ``verdict`` or ``stale_reason`` holds
that record's Worker row first).
"""

import logging

from django.db.models import Q

from ..models import UnrealMeasurement
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

DRIVER_CHANGED = 'driver_changed'


def lock_pair(record, worker, driver, extra=()):
    """D15: lock the record, the pks in ``extra`` and the pair's current records (any key) of another
    driver, in one ``ORDER BY pk`` query.

    With ``driver`` unknown ('') only the record and ``extra`` are locked (D3 stales nothing).
    ``memory-report/`` passes a derived record's ``covered_by`` record in ``extra``.
    """
    wanted = Q(pk=record.pk) | Q(pk__in=list(extra))
    if driver:
        wanted |= Q(worker_id=worker.pk, card_kind=record.card_kind, current=True) & ~Q(driver_version=driver)
    return list(lock_own_rows(UnrealMeasurement.objects.filter(wanted)).order_by('pk'))


def stale_older_drivers(record, rows, driver, include_record=False):
    """D3: when another current record has a non-null driver other than ``driver``, stale every
    current record of the pair whose driver is not ``driver`` (unknown '' included); returns their pks.

    ``rows`` are the pair's locked rows.  ``record`` itself is a candidate only with
    ``include_record`` (``memory-report/``: the result the job ran on; ``pilot-result/`` writes
    the reported driver into its own record instead).
    """
    if not driver:
        return []
    others = [row for row in rows if (include_record or row.pk != record.pk)
              and row.current and row.driver_version != driver]
    if not any(row.driver_version for row in others):
        return []
    stale = [row.pk for row in others]
    UnrealMeasurement.objects.filter(pk__in=stale).update(stale_reason=DRIVER_CHANGED)
    logger.info("Worker %s, card kind %s: driver %s replaces an earlier driver; results %s are re-tested.",
                record.worker_hostname, record.card_kind, driver, stale)
    return stale
