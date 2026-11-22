# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A render or audio job's memory report (Unreal per-machine memory test
D3, D6, D9, D10, D15, FR-MT-S-3).

Runs after the body passed ``MemoryReportRequestSerializer`` and the job
id parsed.  Under one transaction, checks in order: the job is RENDERING
on the caller (409); it is an Unreal render or audio job (409); a report
already accepted for the job answers an identical body with the stored
result (200, nothing changes) and a different one with 409; ``card_kind``
is the kind of the caller's stored card ``card_key`` (400); the record
``measurement_id`` belongs to the caller, that kind and the job's key
(an audio job: its submission's piece key) (400).  The key is compared
by asset and target: a CVar change during the render leaves the record
the gate used valid for its report.

**Locks (D15):** the Job row (SQLite write lock first), then the
caller's own Worker row (``FOR UPDATE``, one row, no other Worker and no
Job after it), then in ONE ``ORDER BY pk`` ``FOR UPDATE`` query
(``pair_locks.lock_pair``) the target record, its ``covered_by``
record and the pair's current records whose driver differs from the
reported one.  The ``SequenceSubmission`` row for the warning is locked
last (``sequence_create.plan_submission``'s documented inversion relies
on that).  The worker-lock invariant of ``worker_locks`` holds: every
record written here is of the caller's pair and the caller's Worker row
is held.

**Effects, in order** (``memory_report_effects``): (1) the report is
stored on the job (written last in the transaction, together with the
answer it got); (2) while the record is still current, D3 for the pair;
when that stales the record itself (the driver changed) only the
warning (4) follows, no verdict or peak change; (3) D6's job-peak
feedback and D9's ``self`` spill, conditional on the record being current
and ``fits``, and for a derived record also its current ``fits`` covering
record (only one of the caller's pair: a covering record of another
worker or kind is never locked or written, and is logged at WARNING);
(4) for a piece or audio job the version warning, once (the report is
stored once per job); (5) the fits-nowhere check ``on_commit`` for each
key whose verdict (3) changed (anything else is the heartbeat sweep's:
the reporting job is still RENDERING, so D12 could not stop its key
now).  A record no longer current gets only (1) and (4).  The answer is
the pair's D2 state after the effects.
"""

import logging

from django.db import transaction

from ..constants import Renderer, UnrealKind
from ..models import Job, JobStatus, SequenceSubmission, UnrealMeasurement, Worker
from ..parent_status import take_write_lock
from ..renderers.unreal_machine import RETEST_DUE, stored_card_kind
from .fits_nowhere import check_on_commit
from .memory_report_effects import add_warning, feed_back, pair_state
from .pair_locks import lock_pair, stale_older_drivers
from .row_locks import lock_own_rows

logger = logging.getLogger(__name__)

NOT_RENDERING_HERE = "Job is not rendering on this worker."
NOT_A_KEYED_JOB = "Memory reports are only for Unreal render and audio jobs."
ALREADY_REPORTED = "A different memory report was already accepted for this job."
KIND_NOT_THE_CARDS = "Does not match the kind of this worker's card card_key."
NOT_THE_JOBS_RECORD = "Not a memory result of this worker, card kind and job."
_KEYED_KINDS = (UnrealKind.RENDER, UnrealKind.AUDIO)


def _job_refusal(job, worker):
    """The 409 checks on the job, else ``None``."""
    if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
        return 409, {'error': NOT_RENDERING_HERE}
    if job.renderer != Renderer.UNREAL or job.unreal_kind not in _KEYED_KINDS:
        return 409, {'error': NOT_A_KEYED_JOB}
    return None


def _repeat(stored, report):
    """A report already accepted: the stored answer for the same body, else 409."""
    if isinstance(stored, dict) and stored.get('report') == report:
        return 200, {'result': stored.get('result')}
    return 409, {'error': ALREADY_REPORTED}


def _key_target(job):
    """The target hash of the job's key: its own, or (audio) its submission's piece key (D10)."""
    if job.unreal_kind != UnrealKind.AUDIO:
        return job.unreal_target_sha256
    return SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
        'piece_target_sha256', flat=True).first() or ''


def _checked_record(job, worker, data):
    """``(record, errors)``: the 400 checks on the card kind and the record (read, not locked)."""
    errors = {}
    if data['card_kind'] != stored_card_kind(worker, data['card_key']):
        errors['card_kind'] = [KIND_NOT_THE_CARDS]
    record = UnrealMeasurement.objects.filter(pk=data['measurement_id']).first()
    target = _key_target(job)
    if (record is None or record.worker_id != worker.pk or record.card_kind != data['card_kind']
            or record.asset_id != job.asset_id or not target or record.target_sha256 != target):
        errors['measurement_id'] = [NOT_THE_JOBS_RECORD]
    return record, errors


def _same_pair(row, worker, kind):
    return row is not None and row.worker_id == worker.pk and row.card_kind == kind


def _covering_pks(record, worker):
    """The covering record's pk to lock, only when it is of the caller's pair (its Worker row is held)."""
    if record.covered_by_id is None:
        return []
    covering = UnrealMeasurement.objects.filter(pk=record.covered_by_id).only('worker', 'card_kind').first()
    if covering is not None and not _same_pair(covering, worker, record.card_kind):
        logger.warning("Result %s is covered by result %s of another machine or card kind; it is left alone.",
                       record.pk, covering.pk)
        return []
    return [record.covered_by_id]


def _locked_covering(locked, by_pk, worker):
    """The locked covering record of ``locked``, or ``None`` when absent or not of the caller's pair."""
    covering = by_pk.get(locked.covered_by_id) if locked.covered_by_id else None
    if covering is not None and not _same_pair(covering, worker, locked.card_kind):
        logger.warning("Result %s is covered by result %s of another machine or card kind; it is left alone.",
                       locked.pk, covering.pk)
        return None
    return covering


def _apply(job, worker, record, data):
    """Effects (2)-(5) under the record locks; returns the pair's state."""
    driver = data['driver_version'] or ''
    rows = lock_pair(record, worker, driver, extra=_covering_pks(record, worker))
    by_pk = {row.pk: row for row in rows}
    locked = by_pk.get(record.pk)
    if locked is None or not locked.current:  # AC-8: superseded meanwhile, no verdict, peak or driver change
        add_warning(job, worker, data, record)
        return pair_state(record, worker)
    pair = [row for row in rows if _same_pair(row, worker, locked.card_kind)]
    if locked.pk in stale_older_drivers(locked, pair, driver, include_record=True):
        # D3: the driver change wins over any verdict change in the same report; the spill is still told.
        add_warning(job, worker, data, locked)
        return RETEST_DUE
    covering = _locked_covering(locked, by_pk, worker)
    record_changed, covering_changed = feed_back(locked, covering, data)
    add_warning(job, worker, data, locked)
    # (5) Only for a verdict changed here; anything else is the heartbeat sweep's (D16).
    for changed, row in ((record_changed, locked), (covering_changed, covering)):
        if changed:
            check_on_commit(row.project_id, row.asset_id, row.overrides_sha256, row.target_sha256)
    return pair_state(locked, worker)


def record_memory_report(job_pk, worker, data):
    """Apply a validated ``memory-report/`` body; return ``(status, body)``.

    Raises:
        Job.DoesNotExist: no such job (the view answers 404).
    """
    report = dict(data)
    with transaction.atomic():
        # A write first: SQLite takes its write lock now (#225).
        take_write_lock(Job.objects.filter(pk=job_pk))
        job = lock_own_rows(Job.objects).get(pk=job_pk)
        refusal = _job_refusal(job, worker)
        if refusal is not None:
            return refusal
        if job.unreal_memory_report is not None:
            return _repeat(job.unreal_memory_report, report)
        # D15: the caller's own Worker row after the Job row, before any record lock.
        worker = lock_own_rows(Worker.objects).get(pk=worker.pk)
        record, errors = _checked_record(job, worker, data)
        if errors:
            return 400, errors
        result = _apply(job, worker, record, data)
        # (1) The accepted report and its answer, so a retry of the same body gets the same answer.
        Job.objects.filter(pk=job.pk).update(unreal_memory_report={'report': report, 'result': result})
    return 200, {'result': result}
