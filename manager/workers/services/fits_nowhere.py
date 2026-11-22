# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The fits-nowhere stop of one measurement key (Unreal per-machine memory
test Q9, D12, D15, D16, FR-MT-N-1, FR-MT-N-2).

**Rule (D12).**  Key K = (asset, the project's current CVar overrides,
target).  Its QUEUED render jobs and the QUEUED audio jobs whose
submission's piece key is K become ERROR when **all** hold:

* at least one eligible online pair (``machine_tests.eligible_pairs`` for
  the key's build) exists;
* every eligible online pair's current record of K is ``does_not_fit``
  and not stale (a pair not tried, testing, failed or ``retest_due``
  blocks the stop);
* no current record of K with verdict ``fits`` exists on any worker,
  online or not, stale or not (a machine that fit and is off, or is due a
  re-test, may still take the render);
* no job of K is RENDERING: no render job, no audio job of the piece key,
  no pilot of the key (by its record).

Only records of the project's **current** overrides count; a check
triggered for an earlier CVar set stops nothing (the current key has its
own triggers).  The message (FR-MT-N-2) names the largest card tried
among the eligible online pairs' records (largest ``gpu_total_mb``, ties
by the larger lower bound), on each job's ``error_message`` and on each
affected submission's ``error_message`` when its refresh leaves it ERROR
or PARTLY_DONE.  An audio job's size text is its pieces' (a piece of the
key), since the figure is the pieces'.

**Locks (D15), in its own transaction:** :func:`check` refuses to run in
a transaction that already locked the Worker rows (``RuntimeError``),
then runs ``lock_project`` first, the key's QUEUED jobs in ascending id,
every Worker row (``worker_locks.lock_workers``; the D3 staling and the
``memory-report/`` verdict changes write records under the Worker row
without ``lock_project``, so the pair states are read only after it),
then reads the records and the RENDERING jobs (no record lock: nothing
here writes a record).  "Nothing RENDERING" holds until the commit
because every transition to RENDERING sets ``assigned_worker`` from NULL:
that write's foreign-key check (and a claim of one of the locked QUEUED
jobs, its Job row) waits on the locks held here.

**Writes.**  The only ``post_save`` receivers that matter for a QUEUED
render or audio job turned ERROR are the submission refresh
(``parent_dispatch.dispatch_for_job``); the manifest receiver acts on
creation only and the thumbnail one needs an ``output_file`` and is of no
use for a stopped job.  So the jobs are written with one queryset
``update()`` per distinct message (``status``, ``error_message``,
``completed_at``; a ``save()`` per job would refresh the submission once
per job under the global Worker locks) and the refresh hook is then run
explicitly: ``sequence_status.refresh_submission`` once per affected
submission, in ascending pk order, which locks each submission row last.

**Triggers.**  :func:`check_on_commit` from ``pilot-result/`` after every
stored verdict and from ``memory-report/`` after a verdict change; the
heartbeat sweep (``machine_sweep``, D16 (b)) reads stop candidates in its
lock-free query (:func:`candidate_q`), confirms each with the lock-free
:func:`stop_due` and only then runs :func:`check`.  That covers a key
whose last RENDERING job ended, a machine that went off, and "Render
again" (re-queued jobs of the same key are stopped by the next sweep
unless an untried machine came online or a result became ``retest_due``).
"""

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from ..constants import WORKER_STALENESS_SECONDS, UnrealKind
from ..constants_sequence import SequenceStatus
from ..models import Job, JobStatus, Project, SequenceSubmission, UnrealMeasurement
from ..renderers.unreal_machine import DOES_NOT_FIT, FITS, current_records, pair_state_case
from ..renderers.unreal_sequence import output_size_text
from .machine_tests import eligible_pairs
from .row_locks import lock_own_rows
from .sequence_status import refresh_submission
from .unreal_pilot import lock_project
from .worker_locks import lock_workers, workers_locked

logger = logging.getLogger(__name__)

MESSAGE = (
    "This render does not fit on any graphics card that is on. On the largest card tried, "
    "{card} ({total} MB) on {host}, it used at least {lower} MB {size} without fitting. "
    "Options: render at a lower resolution, use draft settings, or add a machine with a larger card "
    "and render again."
)
_KEYED = (UnrealKind.RENDER, UnrealKind.AUDIO)
_FAILED_VERSION = (SequenceStatus.ERROR, SequenceStatus.PARTLY_DONE)
_MAX_SUBMISSION_ERROR = 1000


def _submissions(asset_id, target_hash):
    """The submissions whose piece key target is ``target_hash`` (their audio jobs belong to the key)."""
    return list(SequenceSubmission.objects.filter(
        asset_id=asset_id, piece_target_sha256=target_hash,
    ).order_by('pk').values_list('pk', flat=True))


def _key_jobs(asset_id, target_hash, submissions, status):
    """The key's render jobs and its audio jobs (by submission id: no join, so a lock stays on Job rows)."""
    wanted = Q(unreal_kind=UnrealKind.RENDER, unreal_target_sha256=target_hash)
    if submissions:
        wanted |= Q(unreal_kind=UnrealKind.AUDIO, sequence_submission_id__in=submissions)
    return Job.objects.filter(wanted, asset_id=asset_id, status=status)


def _rendering(asset_id, target_hash, submissions):
    """Whether a render, audio or pilot job of the key is RENDERING (one read)."""
    wanted = Q(unreal_kind=UnrealKind.RENDER, unreal_target_sha256=target_hash) | Q(
        unreal_kind=UnrealKind.PILOT, unreal_measurement__target_sha256=target_hash)
    if submissions:
        wanted |= Q(unreal_kind=UnrealKind.AUDIO, sequence_submission_id__in=submissions)
    return Job.objects.filter(wanted, asset_id=asset_id, status=JobStatus.RENDERING).exists()


def _lower_bound(record):
    """The record's lower bound; without one, its result peak (D2)."""
    if record.lower_bound_mb is not None:
        return record.lower_bound_mb
    return max(record.peak_mb or 0, record.job_peak_max_mb or 0)


def largest_tried(asset_id, overrides, target_hash, build, within=None):
    """D12 on the rows as they are now: the largest card tried when K fits nowhere, else ``None``.

    Returns ``(record, hostname)`` of the eligible online pairs' ``does_not_fit``
    records with the largest ``gpu_total_mb`` (ties: larger lower bound).
    The RENDERING rule is the caller's.  ``within`` limits the workers to
    those ``lock_workers`` locked.
    """
    pairs = eligible_pairs(build, within=within)
    if not pairs or current_records(asset_id, overrides, target_hash).filter(verdict=FITS).exists():
        return None
    hosts = {worker.pk: worker.hostname for worker, _kind in pairs}
    records = current_records(asset_id, overrides, target_hash).filter(
        worker__in=sorted(hosts)).annotate(_pair_state=pair_state_case()).order_by('pk')
    by_pair = {(record.worker_id, record.card_kind): record for record in records}
    tried = [by_pair.get((worker.pk, kind)) for worker, kind in pairs]
    if any(record is None or record._pair_state != DOES_NOT_FIT for record in tried):
        return None
    largest = max(tried, key=lambda record: (record.gpu_total_mb or 0, _lower_bound(record)))
    return largest, hosts[largest.worker_id]


def _message(largest, size):
    record, host = largest
    return MESSAGE.format(card=record.gpu_name or record.card_kind, total=record.gpu_total_mb,
                          host=host, lower=_lower_bound(record), size=size)


def _piece_size(jobs, asset_id, target_hash):
    """The size text of the key's pieces (FR-SR-4): a render job of the key, else ``None``."""
    piece = next((job for job in jobs if job.unreal_kind == UnrealKind.RENDER), None)
    if piece is None:
        piece = Job.objects.filter(asset_id=asset_id, unreal_kind=UnrealKind.RENDER,
                                   unreal_target_sha256=target_hash).order_by('pk').first()
    return None if piece is None else output_size_text(piece)


def _stop(jobs, largest, asset_id, target_hash):
    """FR-MT-N-1/2: the locked jobs to ERROR (one ``update()`` per message), then one refresh per submission."""
    now = timezone.now()
    piece_size = _piece_size(jobs, asset_id, target_hash)
    by_message, messages = defaultdict(list), {}
    for job in jobs:
        size = output_size_text(job) if job.unreal_kind == UnrealKind.RENDER else piece_size or output_size_text(job)
        text = _message(largest, size)
        by_message[text].append(job.pk)
        if job.sequence_submission_id is not None:
            messages.setdefault(job.sequence_submission_id, text)
    for text, pks in by_message.items():
        Job.objects.filter(pk__in=pks, status=JobStatus.QUEUED).update(
            status=JobStatus.ERROR, error_message=text, completed_at=now)
    for submission_id in sorted(messages):
        # The refresh hook the jobs' post_save would have run (FR-MT-N-1), once per submission.
        if refresh_submission(submission_id) in _FAILED_VERSION:
            SequenceSubmission.objects.filter(pk=submission_id).update(
                error_message=messages[submission_id][:_MAX_SUBMISSION_ERROR])
    record, host = largest
    logger.warning("Unreal render fits nowhere: stopped %d queued job(s) of asset %s, target %s; largest card "
                   "tried %s (%s MB) on %s.", len(jobs), asset_id, target_hash, record.gpu_name or record.card_kind,
                   record.gpu_total_mb, host)


def _build(jobs):
    """The key's engine build: a render job's (the key holds it), else an audio job's."""
    keyed = sorted(jobs, key=lambda job: (job.unreal_kind != UnrealKind.RENDER, job.pk))
    return keyed[0].engine_build


def check(project_id, asset_id, overrides, target_hash):
    """Stop the key's QUEUED jobs when no online machine fits it (D12); return the number stopped.

    Its own transaction; see the module docstring for the rule and the locks.

    Raises:
        RuntimeError: called in a transaction that already locked the Worker rows (D15: the
            Project and Job locks come first, so the check must start its own transaction).
    """
    if workers_locked():
        raise RuntimeError("fits_nowhere.check must run in its own transaction, not after lock_workers")
    if not target_hash:
        return 0
    with transaction.atomic():
        try:
            project = lock_project(Project(pk=project_id))
        except Project.DoesNotExist:
            return 0
        if project.unreal_overrides_sha256 != overrides:
            return 0
        submissions = _submissions(asset_id, target_hash)
        jobs = list(lock_own_rows(_key_jobs(asset_id, target_hash, submissions, JobStatus.QUEUED)).order_by('pk'))
        if not jobs:
            return 0
        within = lock_workers()  # D15: after the Job rows; the pair states are read under it
        if _rendering(asset_id, target_hash, submissions):
            return 0
        largest = largest_tried(asset_id, overrides, target_hash, _build(jobs), within=within)
        if largest is None:
            return 0
        _stop(jobs, largest, asset_id, target_hash)
        return len(jobs)


def check_on_commit(project_id, asset_id, overrides, target_hash):
    """Run :func:`check` for the key after the current transaction commits."""
    transaction.on_commit(lambda: check(project_id, asset_id, overrides, target_hash), robust=True)


def stop_due(asset_id, overrides, target_hash):
    """D16 (b): whether :func:`check` would stop the key now, read without any lock."""
    submissions = _submissions(asset_id, target_hash)
    jobs = list(_key_jobs(asset_id, target_hash, submissions, JobStatus.QUEUED).only(
        'pk', 'unreal_kind', 'engine_build')[:1])
    if not jobs or _rendering(asset_id, target_hash, submissions):
        return False
    return largest_tried(asset_id, overrides, target_hash, jobs[0].engine_build) is not None


def candidate_q(target_ref, overrides_ref):
    """D16 (b): the sweep's stop-candidate condition on a Job query, by its key's references.

    True when no current record of the key fits on any worker, no job of
    the key is RENDERING, an online worker holds a current, not stale
    ``does_not_fit`` record of it, and no online worker's current record is
    testing with a QUEUED pilot (a RENDERING pilot is in the RENDERING
    term).  "Online" is active and seen within ``WORKER_STALENESS_SECONDS``.
    Failed records, eligibility (build, share, ticked kinds), pairs not
    tried and ``retest_due`` are left to :func:`stop_due`, which applies
    D12 exactly: a failed record of a worker no longer eligible must not
    hide the key.
    """
    cutoff = timezone.now() - timedelta(seconds=WORKER_STALENESS_SECONDS)
    online = Q(worker__is_active=True, worker__last_seen__gte=cutoff)
    records = UnrealMeasurement.objects.filter(
        asset=OuterRef('asset_id'), overrides_sha256=overrides_ref, target_sha256=target_ref, current=True)
    rendering = Job.objects.filter(asset=OuterRef('asset_id'), status=JobStatus.RENDERING).filter(
        Q(unreal_kind=UnrealKind.RENDER, unreal_target_sha256=target_ref)
        | Q(unreal_kind=UnrealKind.AUDIO, sequence_submission__piece_target_sha256=target_ref)
        | Q(unreal_kind=UnrealKind.PILOT, unreal_measurement__target_sha256=target_ref))
    return (Q(unreal_kind__in=_KEYED)
            & ~Exists(records.filter(verdict=FITS))
            & ~Exists(rendering)
            & Exists(records.filter(online, verdict=DOES_NOT_FIT, stale_reason=''))
            & ~Exists(records.filter(online, verdict='', pilot_job__status=JobStatus.QUEUED)))
