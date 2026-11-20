# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Find or create a shot discovery (Unreal sequence rendering FR-SD-2,
FR-SD-3, D22).

The latest record of a key (asset, engine build, map, sequence, preset) is
reused while it is QUEUED, RUNNING or DONE; otherwise a new record and its
``shots`` job are inserted.  Runs under the caller's ``lock_project``
(Project -> SequenceDiscovery; new rows are not locks).
"""

import logging

from django.db import transaction
from django.utils import timezone

from shared.unreal.validation import target_sha256

from ..constants import RenderDevice, UnrealKind
from ..constants_sequence import OPEN_DISCOVERY_STATUSES, REUSABLE_DISCOVERY_STATUSES, DiscoveryStatus, shots_job_name
from ..models import Job, JobStatus, SequenceDiscovery
from ..renderers.unreal_settings import MAP, PRESET, SEQUENCE
from . import sequence_discovery
from .unreal_pilot import lock_project, locked_project

logger = logging.getLogger(__name__)


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
    if (latest is not None and latest.status in OPEN_DISCOVERY_STATUSES
            and sequence_discovery._settle_locked(latest.pk)):
        latest.status = DiscoveryStatus.ERROR
    if latest is not None and latest.status in REUSABLE_DISCOVERY_STATUSES:
        return latest, False
    return _create(project, asset, build, map_path, sequence, preset), True


def request_discovery(asset, map_path, sequence, preset):
    """``POST /api/sequence-discoveries/``: :func:`find_or_create_discovery` in its own transaction."""
    with transaction.atomic():
        lock_project(asset.project)
        return find_or_create_discovery(asset, map_path, sequence, preset)
