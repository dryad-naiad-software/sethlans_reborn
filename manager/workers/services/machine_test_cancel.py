# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Canceling QUEUED memory tests that can no longer run (Unreal per-machine
memory test D5, FR-MT-P-1).

:func:`cancel_ineligible_pilots` cancels a project's QUEUED targeted
pilots when:

* their pair is no longer eligible while the worker is online (seen
  within ``WORKER_STALENESS_SECONDS``): the card kind is un-ticked or gone,
  the worker stopped reporting the build, the share or a live card
  reading, or it was deactivated ("This machine no longer uses this card
  kind.").  A pilot of an offline worker waits;
* their record is no longer current (a later record of the pair replaced
  it).  Such a pilot is never offered or claimed; canceling it is cleanup.

A pilot created under other CVar overrides than the project's current
ones is kept: it is stale only while the overrides differ, and it is
offered again when they change back.

The canceled pilot's record stops being current, so the pair reads "not
tried" again (not "failed", which would wait for the admin retry): when
the kind is ticked again, the next trigger or heartbeat tests it.

**Locks (D15), in two steps:**

1. :func:`cancel_ineligible_pilots`, under the caller's ``lock_project``
   and before ``lock_workers()`` (a claim holds its Job row and waits on
   the Worker row; it raises when the Worker rows are already locked):
   the candidate Job rows ascending by pk with ``SKIP LOCKED``, re-checked
   QUEUED, then canceled.  It never waits on a Job row: the caller may
   already hold another Job row (the admin job update), and asset
   deletion or a Worker deletion lock Job rows without the project lock,
   so waiting could close a cycle.  A pilot skipped because another
   transaction held it is canceled by the next trigger or sweep.
2. :func:`release_records`, after ``lock_workers()``: the canceled
   pilots' current records by pk, which stop being current.  Records are
   therefore never locked before the Worker rows in a transaction that
   then locks the Worker rows (a ``memory-report/`` holds its Worker row
   and then locks the pair's records).

Queryset ``update()`` only (pilots have no submission; no signal
recursion).
"""

import logging
from datetime import timedelta

from django.utils import timezone

from ..constants import WORKER_STALENESS_SECONDS, UnrealKind
from ..models import Job, JobStatus, UnrealMeasurement
from ..renderers.unreal_machine import eligible_kinds, serves_build
from ..renderers.unreal_measured import SUPERSEDED_MESSAGE
from .row_locks import lock_own_rows
from .sethlans_share import configured_share
from .worker_locks import workers_locked

logger = logging.getLogger(__name__)

NO_LONGER_ELIGIBLE = "This machine no longer uses this card kind."


class _Share:
    """The manager's share, read once and only when a decision needs it."""

    def __init__(self):
        self._value = None

    def get(self):
        if self._value is None:
            self._value = configured_share()
        return self._value


def _record_of(job):
    try:
        return job.unreal_measurement
    except UnrealMeasurement.DoesNotExist:
        return None


def _reason(job, share, cutoff):
    """Why ``job`` (a QUEUED targeted pilot) is canceled, or ``None`` to keep it."""
    record = _record_of(job)
    if record is None or not record.current:
        return SUPERSEDED_MESSAGE
    worker = job.target_worker
    if worker is None or worker.last_seen is None or worker.last_seen < cutoff:
        return None  # no target (never offered) or offline: it waits (D5)
    eligible = (worker.is_active and record.card_kind in eligible_kinds(worker)
                and serves_build(worker, job.engine_build, share.get()))
    return None if eligible else NO_LONGER_ELIGIBLE


def _apply(project, reasons, now):
    """Lock the candidates' Job rows without waiting, cancel those still QUEUED; their pks."""
    pks = list(lock_own_rows(Job.objects.filter(
        pk__in=sorted(reasons), status=JobStatus.QUEUED,
    ), skip_locked=True).order_by('pk').values_list('pk', flat=True))
    for message in sorted({reasons[pk] for pk in pks}):
        chosen = [pk for pk in pks if reasons[pk] == message]
        Job.objects.filter(pk__in=chosen).update(status=JobStatus.CANCELED, completed_at=now, error_message=message)
        logger.info("Unreal project %s: canceled memory test job(s) %s: %s", project.pk, chosen, message)
    return pks


def release_records(pilot_pks):
    """Step 2: the canceled pilots' current records stop being current (after ``lock_workers()``).

    Raises:
        RuntimeError: the Worker rows are not locked yet in this transaction (D15).
    """
    if not pilot_pks:
        return
    if not workers_locked():
        raise RuntimeError("release_records must run after lock_workers()")
    records = UnrealMeasurement.objects.filter(pilot_job__in=sorted(pilot_pks), current=True)
    current = list(lock_own_rows(records).order_by('pk').values_list('pk', flat=True))
    if current:
        UnrealMeasurement.objects.filter(pk__in=current).update(current=False)


def cancel_ineligible_pilots(project):
    """Step 1: cancel the locked ``project``'s QUEUED pilots that can no longer run; their pks.

    The caller then runs ``lock_workers()`` and :func:`release_records`
    with the returned pks in the same transaction.

    Raises:
        RuntimeError: the Worker rows are already locked in this transaction (D15).
    """
    if workers_locked():
        raise RuntimeError("cancel_ineligible_pilots must lock its Job rows before lock_workers()")
    pilots = Job.objects.filter(
        asset__project=project, unreal_kind=UnrealKind.PILOT, status=JobStatus.QUEUED,
    ).select_related('target_worker', 'unreal_measurement').order_by('pk')
    share = _Share()
    cutoff = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
    reasons = {}
    for job in pilots:
        reason = _reason(job, share, cutoff)
        if reason is not None:
            reasons[job.pk] = reason
    if not reasons:
        return []
    return _apply(project, reasons, timezone.now())
