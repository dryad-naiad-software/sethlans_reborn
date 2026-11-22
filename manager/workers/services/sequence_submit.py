# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Creating sequence submissions (Unreal sequence rendering FR-SQ-2, FR-SQ-4,
FR-SQ-8, D18, D22) and the hooks a finished discovery calls.

``create_submission`` runs one transaction: ``lock_project`` first, the
discovery found or created (FR-SD-2), the rules that need the shots when
the discovery is DONE (400 otherwise nothing is written), the version (one
more than the highest of the project and sequence), the row, the project's
``unreal_sound`` set to the submission's, then the planning when the
shots are known.  Lock order: Project -> SequenceDiscovery ->
SequenceSubmission (the new row) -> its shots and jobs (inserted).

``plan_waiting`` / ``fail_waiting`` run under the project and discovery
locks of a ``shots-result/`` or a settle, and lock each DISCOVERING
submission in pk order.
"""

import logging

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import serializers

from ..constants_sequence import SUBMISSION_NAME_MAX, DiscoveryStatus, SequenceStatus
from ..models import Project, SequenceSubmission
from ..serializers.sequence_validation import READ_SHOTS_FIRST, facts_errors
from .row_locks import lock_own_rows
from .sequence_create import plan_submission
from .sequence_discovery_create import find_or_create_discovery
from .unreal_pilot import lock_project
from .worker_locks import lock_workers

logger = logging.getLogger(__name__)

DISCOVERY_FAILED = "Reading the shots failed: {reason}"
PLANNING_FAILED = "Planning this version failed on the manager; submit it again."


def submission_name(sequence, version):
    """D20: ``<sequence asset name> v<version>``, at most 80 characters."""
    suffix = f" v{version}"
    asset_name = sequence.rsplit('/', 1)[-1].split('.', 1)[0] or 'Sequence'
    return asset_name[:SUBMISSION_NAME_MAX - len(suffix)] + suffix


def preset_offsets(project, preset):
    """D18: the project's manual sound offsets of ``preset`` (``{"<preset>@<num>/<den>": ms}``)."""
    offsets = project.unreal_sound_offsets if isinstance(project.unreal_sound_offsets, dict) else {}
    return {key: ms for key, ms in offsets.items() if str(key).rpartition('@')[0] == preset}


def _next_version(project, sequence):
    highest = SequenceSubmission.objects.filter(project=project, sequence=sequence).aggregate(v=Max('version'))['v']
    return (highest or 0) + 1


def _refuse_on_facts(data, discovery):
    if discovery.status != DiscoveryStatus.DONE:
        if data.get('shots') is not None:
            raise serializers.ValidationError({'shots': [READ_SHOTS_FIRST]})
        return
    errors = facts_errors(data, discovery)
    if errors:
        raise serializers.ValidationError({field: [message] for field, message in errors.items()})


def _row(project, discovery, data, version):
    return SequenceSubmission.objects.create(
        project=project, asset=data['asset'], discovery=discovery, engine_build=project.engine_build,
        map=data['map'], sequence=data['sequence'], preset=data['preset'], version=version,
        name=submission_name(data['sequence'], version), selected_shots=data.get('shots'),
        frame_start=data.get('frame_start'), frame_end=data.get('frame_end'),
        resolution_x=data.get('resolution_x'), resolution_y=data.get('resolution_y'),
        frame_format=data['frame_format'], video_settings=data.get('video_settings'), sound=data['sound'],
        merge_sound=data['merge_sound'], draft=data.get('draft'), piece_seconds=project.unreal_piece_seconds,
        lead_in_ms=project.unreal_lead_in_ms, sound_offsets=preset_offsets(project, data['preset']),
        status=SequenceStatus.DISCOVERING, submitted_at=timezone.now(),
    )


def create_submission(data):
    """FR-SQ-4: create a version from a checked request body; plan it when the shots are known.

    Raises:
        rest_framework.exceptions.ValidationError: a rule that needs the shots failed (nothing written).
    """
    asset = data['asset']
    with transaction.atomic():
        project = lock_project(asset.project)
        lock_workers()  # D15: Workers before the discovery and the submission; planning creates pilots
        discovery, _created = find_or_create_discovery(asset, data['map'], data['sequence'], data['preset'])
        _refuse_on_facts(data, discovery)
        submission = _row(project, discovery, data, _next_version(project, data['sequence']))
        Project.objects.filter(pk=project.pk).update(unreal_sound=data['sound'])
        if discovery.status == DiscoveryStatus.DONE:
            plan_submission(submission, discovery)
    logger.info("Created sequence submission %s (%s) on discovery %s.", submission.pk, submission.name, discovery.pk)
    submission.refresh_from_db()
    return submission


def _waiting(discovery):
    return lock_own_rows(SequenceSubmission.objects).filter(
        discovery=discovery, status=SequenceStatus.DISCOVERING,
    ).order_by('pk')


def plan_waiting(discovery):
    """FR-SD-8: plan every submission still DISCOVERING on ``discovery`` (now DONE).

    Each one in its own savepoint: an unexpected error while planning one
    rolls back only that one, which becomes ERROR; the discovery and the
    others are kept.
    """
    for submission in list(_waiting(discovery)):
        try:
            with transaction.atomic():
                plan_submission(submission, discovery)
        except Exception:  # noqa: BLE001 -- one bad submission must not undo the discovery
            logger.exception("Planning sequence submission %s failed.", submission.pk)
            SequenceSubmission.objects.filter(pk=submission.pk, status=SequenceStatus.DISCOVERING).update(
                status=SequenceStatus.ERROR, error_message=PLANNING_FAILED, completed_at=timezone.now(),
            )


def fail_waiting(discovery, reason):
    """FR-SD-9: every submission still DISCOVERING on ``discovery`` becomes ERROR."""
    ids = [submission.pk for submission in _waiting(discovery)]
    if ids:
        SequenceSubmission.objects.filter(pk__in=ids, status=SequenceStatus.DISCOVERING).update(
            status=SequenceStatus.ERROR, error_message=DISCOVERY_FAILED.format(reason=reason),
            completed_at=timezone.now(),
        )
        logger.info("Submissions %s failed: shot discovery %s failed.", ids, discovery.pk)
