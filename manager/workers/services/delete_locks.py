# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The locks a project or asset delete takes before Django's delete collector
runs (Unreal per-machine memory test D15; issues #287, #288).

The collector writes the measurement records first (the ``SET_NULL`` of
``pilot_job`` and ``covered_by``, in index order, then their delete), then
deletes the Job rows, the sequence shots before their submission, the
assets and, for a project delete, the Project row last.
``pilot-result/`` and ``memory-report/`` lock Job -> Worker -> records (by
pk), the project's write paths lock Project -> Job rows, and the sequence
writers (``video_sequence``, ``sequence_assembly``, ``sequence_control``,
``refresh_submission``) lock a submission alone, then update its shots,
so the collector alone could deadlock with any of them on
PostgreSQL/MySQL.

:func:`lock_for_delete` takes D15's order instead, for the delete's scope
(the asset or the project): the Project row (``lock_for_write``), the Job
rows by pk, the Worker rows of the scope's records by pk (a record lock
needs its Worker row first; an ascending subset of
``worker_locks.lock_workers``' order, and none when the scope has no
record with a worker), the records by pk, then the scope's
SequenceDiscovery rows and its SequenceSubmission rows, each by pk.  After
it the collector writes only rows it already holds, rows below a locked
submission (shots, frames) and rows that only reference the jobs.

The records' workers are read after the Job rows are locked: with the
Project row and every Job of the scope held, no record of the scope can be
created (pilots and derived records need ``lock_project``) or have its
worker changed (``pilot-result/`` holds the pilot's Job row first).
``covered_by`` always points to a record of the same asset
(``machine_coverage.derive``), so the scope's records cover every row the
collector's ``SET_NULL`` reaches.
"""

from ..models import Project, SequenceDiscovery, SequenceSubmission, UnrealMeasurement, Worker
from .row_locks import lock_own_rows


def _lock_by_pk(queryset):
    """Lock ``queryset``'s own rows ascending by pk; return their pks."""
    return list(lock_own_rows(queryset.order_by('pk')).values_list('pk', flat=True))


def lock_for_delete(strategy, project_pk, jobs, **scope):
    """Lock the scope in D15 order, each step by pk; False if the project is gone.

    Order: the Project row, ``jobs``, the Worker rows of the scope's records,
    the records, the SequenceDiscovery rows, the SequenceSubmission rows.

    Args:
        strategy: The renderer strategy of the project (its ``lock_for_write`` takes the Project lock).
        project_pk: The pk of the project being deleted, or of the asset's project.  Only the
            pk is passed, so a project deleted after the view fetched its row is found here,
            under the lock, and not by a lazy relation read before it.
        jobs: Queryset of the Job rows the delete removes.
        **scope: The filter that selects the delete's records, discoveries and submissions
            (``project=...`` or ``asset=...``; all three models carry both).

    Runs inside the caller's ``transaction.atomic()``; the caller deletes
    nothing when this returns False.
    """
    try:
        strategy.lock_for_write(Project(pk=project_pk))
    except Project.DoesNotExist:
        return False
    _lock_by_pk(jobs)
    records = UnrealMeasurement.objects.filter(**scope)
    workers = sorted(set(records.exclude(worker_id=None).values_list('worker_id', flat=True)))
    if workers:
        _lock_by_pk(Worker.objects.filter(pk__in=workers))
    _lock_by_pk(records)
    _lock_by_pk(SequenceDiscovery.objects.filter(**scope))
    _lock_by_pk(SequenceSubmission.objects.filter(**scope))
    return True
