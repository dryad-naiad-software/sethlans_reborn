# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shot discovery records (Unreal sequence rendering FR-SD-2, FR-SD-8,
FR-SD-9, D22).

* :func:`find_or_create_discovery` -- the latest record of a key is reused
  while it is QUEUED, RUNNING or DONE; otherwise a new record and its
  ``shots`` job are inserted.  Runs under the caller's ``lock_project``.
* :func:`record_shots_result` -- the worker's result, in one transaction:
  Project -> Job -> SequenceDiscovery, then the waiting submissions.
* :func:`settle_discovery` -- a ``shots`` job that ended ERROR, CANCELED,
  DONE without a result, or was deleted, fails its discovery.  It runs
  after commit under ``lock_project`` and is idempotent; reads call it for
  open records whose job is final, and the server start hook (D16) calls
  :func:`settle_open_discoveries`.

Lock order (D22): Project -> Job -> SequenceDiscovery -> SequenceSubmission.
The status mirror (:func:`on_shots_job_saved`) runs after the caller's Job
lock and writes the discovery by ``update()``, never ``save()``.
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from shared.unreal.shots_result import clean_text
from shared.unreal.validation import target_sha256

from ..constants import RenderDevice, UnrealKind
from ..constants_sequence import (
    OPEN_DISCOVERY_STATUSES, REUSABLE_DISCOVERY_STATUSES, SHOTS_JOB_CANCELED, SHOTS_JOB_DELETED,
    SHOTS_JOB_DONE_WITHOUT_RESULT, SHOTS_JOB_FAILED, DiscoveryStatus, shots_job_name,
)
from ..models import Job, JobStatus, Project, SequenceDiscovery
from ..renderers.unreal_settings import MAP, PRESET, SEQUENCE
from .row_locks import lock_own_rows
from .unreal_pilot import lock_project, locked_project

logger = logging.getLogger(__name__)

NOT_RENDERING_HERE = "Job is not rendering on this worker."
NOT_A_SHOTS_JOB = "Job is not a shots job."
NOT_WAITING = "This job's shot discovery is no longer waiting for a result."
MAX_REASON = 500
_ACTIVE = (JobStatus.QUEUED, JobStatus.RENDERING)


def plan_waiting_submissions(discovery):
    """FR-SD-8 hook: plan every submission still DISCOVERING on ``discovery``.

    Runs in :func:`record_shots_result`'s transaction, after the discovery
    is DONE.  Submissions arrive with chunk 3 (FR-SP-8); none exist yet.
    """


def fail_waiting_submissions(discovery, reason):
    """FR-SD-9 hook: set every submission still DISCOVERING on ``discovery`` ERROR.

    Runs under the discovery lock.  Submissions arrive with chunk 3.
    """


def _key(asset, build, map_path, sequence, preset):
    return SequenceDiscovery.objects.filter(
        asset=asset, target_sha256=target_sha256(build, map_path, sequence, preset),
        engine_build=build, map=map_path, sequence=sequence, preset=preset,
    )


def _create(project, asset, build, map_path, sequence, preset):
    """Insert the record, then its ``shots`` job (FR-SD-3), then link them."""
    now = timezone.now()
    discovery = SequenceDiscovery.objects.create(
        project=project, asset=asset, engine_build=build, map=map_path, sequence=sequence, preset=preset,
        target_sha256=target_sha256(build, map_path, sequence, preset), created_at=now,
    )
    name = shots_job_name(discovery.pk)
    job = Job.objects.create(
        name=name, asset=asset, output_file_pattern=f"//sethlans_shots/{name}.png",
        engine_build=build, unreal_kind=UnrealKind.SHOTS, render_device=RenderDevice.GPU,
        start_frame=0, end_frame=0, render_settings={MAP: map_path, SEQUENCE: sequence, PRESET: preset},
        status=JobStatus.QUEUED, submitted_at=now,
    )
    SequenceDiscovery.objects.filter(pk=discovery.pk).update(job=job)
    discovery.job = job
    logger.info("Created shot discovery %s (shots job %s) for asset %s, sequence %s.",
                discovery.pk, job.pk, asset.pk, sequence)
    return discovery


def find_or_create_discovery(asset, map_path, sequence, preset):
    """FR-SD-2 inside the caller's transaction; returns ``(discovery, created)``.

    Raises:
        ProjectLockMissing: the caller did not take ``lock_project`` first.
    """
    project = locked_project(asset.project)
    build = project.engine_build
    latest = _key(asset, build, map_path, sequence, preset).order_by('-created_at', '-id').first()
    if latest is not None and latest.status in OPEN_DISCOVERY_STATUSES and _settle_locked(latest.pk):
        latest.status = DiscoveryStatus.ERROR
    if latest is not None and latest.status in REUSABLE_DISCOVERY_STATUSES:
        return latest, False
    return _create(project, asset, build, map_path, sequence, preset), True


def request_discovery(asset, map_path, sequence, preset):
    """``POST /api/sequence-discoveries/``: :func:`find_or_create_discovery` in its own transaction."""
    with transaction.atomic():
        lock_project(asset.project)
        return find_or_create_discovery(asset, map_path, sequence, preset)


def failure_reason(job_row):
    """Why a discovery fails for its job (``{'status', 'error_message'}`` or ``None``), else ``None``."""
    if job_row is None:
        return SHOTS_JOB_DELETED
    status = job_row['status']
    if status == JobStatus.ERROR:
        return clean_text(job_row['error_message'] or '').strip()[:MAX_REASON] or SHOTS_JOB_FAILED
    if status == JobStatus.CANCELED:
        return SHOTS_JOB_CANCELED
    if status == JobStatus.DONE:
        return SHOTS_JOB_DONE_WITHOUT_RESULT
    return None


def _settle_locked(discovery_id):
    """Under the project lock: lock the open discovery and fail it if its job ended."""
    discovery = lock_own_rows(SequenceDiscovery.objects).filter(
        pk=discovery_id, status__in=OPEN_DISCOVERY_STATUSES,
    ).first()
    if discovery is None:
        return False
    job_row = None
    if discovery.job_id is not None:
        job_row = Job.objects.filter(pk=discovery.job_id).values('status', 'error_message').first()
    reason = failure_reason(job_row)
    if reason is None:
        return False
    SequenceDiscovery.objects.filter(pk=discovery.pk).update(status=DiscoveryStatus.ERROR, error_message=reason)
    fail_waiting_submissions(discovery, reason)
    logger.info("Shot discovery %s failed: %s", discovery.pk, reason)
    return True


def settle_discovery(discovery_id):
    """FR-SD-9 in a new transaction under ``lock_project``; True when it failed the record."""
    project_id = SequenceDiscovery.objects.filter(pk=discovery_id).values_list('project_id', flat=True).first()
    if project_id is None:
        return False
    with transaction.atomic():
        try:
            lock_project(Project(pk=project_id))
        except Project.DoesNotExist:
            return False
        return _settle_locked(discovery_id)


def settle_for_job(job_pk):
    """Settle the discovery of a ``shots`` job, if it has one."""
    discovery_id = SequenceDiscovery.objects.filter(job_id=job_pk).values_list('pk', flat=True).first()
    return discovery_id is not None and settle_discovery(discovery_id)


def stale_discoveries(queryset=None):
    """Open discoveries whose job is final or gone (FR-SD-9 recovery)."""
    queryset = SequenceDiscovery.objects.all() if queryset is None else queryset
    return queryset.filter(status__in=OPEN_DISCOVERY_STATUSES).filter(
        Q(job__isnull=True) | ~Q(job__status__in=_ACTIVE),
    )


def settle_open_discoveries(queryset=None):
    """Settle every stale discovery of ``queryset`` (the start hook); returns how many failed."""
    settled = 0
    for discovery_id in list(stale_discoveries(queryset).values_list('pk', flat=True)):
        try:
            settled += bool(settle_discovery(discovery_id))
        except Exception:  # noqa: BLE001 -- one bad row must not stop the others
            logger.exception("Settling shot discovery %s failed.", discovery_id)
    return settled


def on_shots_job_saved(job):
    """``post_save`` of a ``shots`` job: mirror QUEUED/RUNNING; settle ERROR or CANCELED after commit.

    A DONE save queues nothing: ``shots-result/`` stored the result in the
    same transaction, and a DONE without a result is settled by a read.
    """
    if job.status == JobStatus.RENDERING:
        SequenceDiscovery.objects.filter(job_id=job.pk, status=DiscoveryStatus.QUEUED).update(
            status=DiscoveryStatus.RUNNING)
    elif job.status == JobStatus.QUEUED:
        SequenceDiscovery.objects.filter(job_id=job.pk, status=DiscoveryStatus.RUNNING).update(
            status=DiscoveryStatus.QUEUED)
    elif job.status in (JobStatus.ERROR, JobStatus.CANCELED):
        transaction.on_commit(lambda pk=job.pk: settle_for_job(pk), robust=True)


def settle_canceled_shots_jobs(project, canceled_at):
    """Project cancel-all (FR-SS-8): settle the discoveries of the shots jobs it canceled.

    Runs in the cancel's transaction after its job UPDATE, under the
    project lock (D22: Project -> Job rows -> SequenceDiscovery), in pk order.
    """
    locked_project(project)
    ids = SequenceDiscovery.objects.filter(
        job__asset__project=project, job__status=JobStatus.CANCELED, job__completed_at=canceled_at,
        status__in=OPEN_DISCOVERY_STATUSES,
    ).order_by('pk').values_list('pk', flat=True)
    return sum(_settle_locked(pk) for pk in list(ids))


def before_shots_job_delete(job):
    """Call before deleting a locked ``shots`` job: settle its discovery after commit."""
    discovery_id = SequenceDiscovery.objects.filter(job_id=job.pk).values_list('pk', flat=True).first()
    if discovery_id is not None:
        transaction.on_commit(lambda pk=discovery_id: settle_discovery(pk), robust=True)


def _store(discovery, job, result, now):
    """Write the result by ``update()`` and onto ``discovery`` too, so the planning hook reads it."""
    fields = {
        'status': DiscoveryStatus.DONE, 'playback_start': result['playback_start'],
        'playback_end': result['playback_end'], 'frame_rate_num': result['frame_rate_num'],
        'frame_rate_den': result['frame_rate_den'], 'shot_source': result['shot_source'],
        'shots': result['shots'], 'preset_facts': dict(result['preset_facts'], preset_kind=result['preset_kind']),
        'warnings': result['warnings'], 'error_message': '',
    }
    SequenceDiscovery.objects.filter(pk=discovery.pk).update(**fields)
    for name, value in fields.items():
        setattr(discovery, name, value)
    job.status = JobStatus.DONE
    job.completed_at = now
    if job.started_at is not None:
        job.render_time_seconds = max(0, round((now - job.started_at).total_seconds()))
    job.save(update_fields=['status', 'completed_at', 'render_time_seconds'])


def record_shots_result(job_pk, worker, result):
    """Apply a checked ``shots-result/`` body (FR-SD-8); return ``(status, body)``.

    Raises:
        Job.DoesNotExist: no such job (the view answers 404).
    """
    project_id = Job.objects.filter(pk=job_pk).values_list('asset__project_id', flat=True).first()
    if project_id is None:
        raise Job.DoesNotExist
    with transaction.atomic():
        lock_project(Project(pk=project_id))
        job = lock_own_rows(Job.objects).filter(pk=job_pk).first()
        if job is None:
            raise Job.DoesNotExist
        if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
            return 409, {'error': NOT_RENDERING_HERE}
        if job.unreal_kind != UnrealKind.SHOTS:
            return 409, {'error': NOT_A_SHOTS_JOB}
        discovery = lock_own_rows(SequenceDiscovery.objects).filter(job_id=job.pk).first()
        if discovery is None or discovery.status not in OPEN_DISCOVERY_STATUSES:
            return 409, {'error': NOT_WAITING}
        _store(discovery, job, result, timezone.now())
        plan_waiting_submissions(discovery)
    logger.info("Shot discovery %s DONE: %d shots from %s.", discovery.pk, len(result['shots']), worker.hostname)
    return 200, {'status': JobStatus.DONE, 'discovery': discovery.pk, 'shots': len(result['shots'])}
