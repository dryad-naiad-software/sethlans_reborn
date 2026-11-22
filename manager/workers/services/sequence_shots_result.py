# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A ``shots`` job's result (Unreal sequence rendering FR-SD-8, D22).

One transaction: ``lock_project``, the job lock and its checks, the
discovery lock, the result stored, discovery and job DONE, then every
submission still DISCOVERING on the discovery is locked and planned
(``sequence_discovery.plan_waiting_submissions``).  A failure anywhere
rolls all of it back.  Lock order: Project -> Job -> SequenceDiscovery ->
SequenceSubmission.
"""

import logging

from django.db import transaction
from django.utils import timezone

from ..constants import UnrealKind
from ..constants_sequence import OPEN_DISCOVERY_STATUSES, DiscoveryStatus
from ..models import Job, JobStatus, Project, SequenceDiscovery
from . import sequence_discovery
from .row_locks import lock_own_rows
from .unreal_pilot import lock_project
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

NOT_RENDERING_HERE = "Job is not rendering on this worker."
NOT_A_SHOTS_JOB = "Job is not a shots job."
NOT_WAITING = "This job's shot discovery is no longer waiting for a result."


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
        lock_workers()  # D15: Workers before the discovery; planning creates pilots
        discovery = lock_own_rows(SequenceDiscovery.objects).filter(job_id=job.pk).first()
        if discovery is None or discovery.status not in OPEN_DISCOVERY_STATUSES:
            return 409, {'error': NOT_WAITING}
        _store(discovery, job, result, timezone.now())
        sequence_discovery.plan_waiting_submissions(discovery)
    logger.info("Shot discovery %s DONE: %d shots from %s.", discovery.pk, len(result['shots']), worker.hostname)
    return 200, {'status': JobStatus.DONE, 'discovery': discovery.pk, 'shots': len(result['shots'])}
