# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The admin measurement retry (Unreal foundation FR-PM-6).

``POST /api/projects/{id}/unreal-measurement/retry/`` creates a new
measurement record and pilot for one key of the project: the key of the
job named by ``job_id``, or of the project's oldest held QUEUED normal
job whose key is failed or none.  A measured key is measured again only
with ``remeasure``; its old peak stays in force until the new pilot
succeeds (a key uses its latest **successful** record).

The transaction locks the project first (FR-PM-10).  Refusals raise
:class:`RetryRefused`, which the view maps to its status and body.
"""

import logging

from django.db import transaction

from ..constants import UnrealKind
from ..models import Asset, Job, JobStatus
from ..renderers import get_strategy
from ..renderers.unreal_measured import (
    FAILED, JOB_KEY_REFS, MEASURED, NONE, PENDING, key_aliases, key_state_case,
)
from .unreal_pilot import Target, create_pilot, key_state_of

logger = logging.getLogger(__name__)

NOT_UNREAL = "Not an Unreal project."
NO_ASSET = "This project has no Unreal asset yet."
NOT_A_JOB = "Not an Unreal job of this project."
NO_HELD_JOB = "No held Unreal job needs a measurement; pass job_id."
ALREADY_PENDING = "A pilot render is already queued or running."
ALREADY_MEASURED = 'This target is already measured; send "remeasure": true to measure it again.'


class RetryRefused(Exception):
    """The retry is refused with HTTP ``status`` and response ``body``."""

    def __init__(self, status, body):
        super().__init__(body)
        self.status = status
        self.body = body


def _normal_jobs(project):
    return Job.objects.filter(
        asset__project=project, unreal_kind=UnrealKind.RENDER,
    ).exclude(unreal_target_sha256='').select_related('asset')


def _chosen_job(project, job_id):
    if job_id is not None:
        job = _normal_jobs(project).filter(pk=job_id).first()
        if job is None:
            raise RetryRefused(400, {'job_id': [NOT_A_JOB]})
        return job
    held = _normal_jobs(project).filter(status=JobStatus.QUEUED).alias(
        **key_aliases(JOB_KEY_REFS, '_rk_'),
    ).alias(_rk_state=key_state_case('_rk_')).filter(_rk_state__in=(FAILED, NONE))
    job = held.order_by('submitted_at', 'id').first()
    if job is None:
        raise RetryRefused(409, {'error': NO_HELD_JOB})
    return job


def retry_measurement(project, *, job_id=None, remeasure=False, username=''):
    """Create a new record and pilot for one key of ``project`` (FR-PM-6).

    Returns:
        The new ``UnrealMeasurement`` (its ``pilot_job`` set).

    Raises:
        RetryRefused: 400 for a Blender project or a bad ``job_id``; 409
            with no Unreal asset, no held job, a pending key, or a measured
            key without ``remeasure``.
    """
    strategy = get_strategy(project.renderer)
    with transaction.atomic():
        locked = strategy.lock_for_write(project)
        if not strategy.supports('pilot_measurement'):
            raise RetryRefused(400, {'detail': NOT_UNREAL})
        if not Asset.objects.filter(project=locked).exists():
            raise RetryRefused(409, {'error': NO_ASSET})
        job = _chosen_job(locked, job_id)
        target = Target.of(job)
        state = key_state_of(locked, job.asset_id, locked.unreal_overrides_sha256, target.sha256()).state
        if state == PENDING:
            raise RetryRefused(409, {'error': ALREADY_PENDING})
        if state == MEASURED and not remeasure:
            raise RetryRefused(409, {'error': ALREADY_MEASURED})
        record = create_pilot(
            locked, job.asset, target, reason='retry', frame=job.start_frame,
            remeasure=state == MEASURED,
        )
    logger.warning(
        "Unreal measurement retry by %s for project %s, asset %s, target %s %s %s %s "
        "(remeasure=%s).", username, project.pk, job.asset_id, target.map, target.sequence,
        target.preset, target.engine_build, record.remeasure,
    )
    return record
