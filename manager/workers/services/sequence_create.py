# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Turn a plan into rows (Unreal sequence rendering FR-SP-8, FR-SR-1, D8,
FR-SW-2).

:func:`plan_submission` runs in the caller's transaction, under
``lock_project`` and (for a waiting submission) the discovery lock.  It
locks the submission and moves it from DISCOVERING by a compare-and-set:
a submission that is no longer DISCOVERING (canceled) is skipped and
nothing is created.  Then, all or nothing: the shot rules that need the
discovery (a failure sets the submission ERROR), the plan (FR-SP), the
``SequenceShot`` rows, the piece jobs (``bulk_create`` with the fields
``Job.save`` would derive, ``unreal_target_sha256`` included), the pilot
of the key, and one manifest update after commit.

Every piece carries in its own row what it renders (FR-SW-2): the lead-in
(``unreal_lead_in_frames``) and the manager-only ``render_settings`` keys
``unreal.piece_shots`` (``[index, start, end]``, end exclusive, clipped to
the piece), ``unreal.output_size``, ``unreal.shot_set_sha256``,
``unreal.resolution``, ``unreal.output_format`` and ``unreal.draft``.  The
key fields are therefore read by every later ``save()`` and by
``Target.of`` (D8).

With sound, one ``audio`` job per run (Q7, FR-SA-1, FR-SP-5) is created
first, so the poll order gives it out early: the run as its frames, the
audio lead-in (``run start - r0``) as ``unreal_lead_in_frames``, and only
the map, sequence, preset and the run's shot parts
(``shared.unreal.sound_result.RUN_SHOTS_KEY``, the piece key's shape) as
settings; resolution and draft never apply to it.
"""

import logging

from django.db import transaction
from django.utils import timezone

from shared.unreal.sound_result import RUN_SHOTS_KEY
from shared.unreal.validation import shot_set_sha256

from ..constants import RenderDevice, Renderer, UnrealKind
from ..constants_sequence import (
    DRAFT_KEY, FRAME_EXTENSIONS, MAX_SUBMISSION_WARNINGS, OUTPUT_SIZE_KEY, PIECE_SHOTS_KEY, RESOLUTION_KEY,
    SHOT_SET_KEY, SequenceStatus, ShotStatus, audio_job_name, piece_job_name,
)
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..renderers.unreal_settings import MAP, OUTPUT_FORMAT, PRESET, SEQUENCE
from ..serializers.sequence_validation import effective_size, facts_errors, facts_warnings, submission_values
from .row_locks import lock_own_rows
from .sequence_pilot import measured_ranges, pilot_frames
from .sequence_plan import PlanError, Selection, plan, shots_from
from .unreal_pilot import ensure_pilot
from .unreal_target import Target

logger = logging.getLogger(__name__)

SAMPLED_PILOT = (
    "The memory test measured {kept} of {total} shots; a heavier unmeasured shot can still fail on a smaller card."
)


def _selection(submission):
    names = submission.selected_shots
    return Selection(
        shots=frozenset(names) if names is not None else None,
        frame_start=submission.frame_start, frame_end=submission.frame_end,
    )


def _fail(submission, message):
    SequenceSubmission.objects.filter(pk=submission.pk).update(
        status=SequenceStatus.ERROR, error_message=message, completed_at=timezone.now(),
    )
    logger.info("Sequence submission %s cannot be planned: %s", submission.pk, message)


def _base_settings(submission, discovery, shot_set):
    settings = {
        MAP: submission.map, SEQUENCE: submission.sequence, PRESET: submission.preset,
        OUTPUT_FORMAT: submission.frame_format, SHOT_SET_KEY: shot_set,
    }
    if submission.resolution_x is not None:
        settings[RESOLUTION_KEY] = [submission.resolution_x, submission.resolution_y]
    size = effective_size(submission_values(submission), discovery.preset_facts or {})
    if size is not None:
        settings[OUTPUT_SIZE_KEY] = size
    if submission.draft:
        settings[DRAFT_KEY] = dict(submission.draft)
    return settings


def _piece_jobs(submission, result, settings, now):
    extension = FRAME_EXTENSIONS[submission.frame_format]
    jobs = []
    for number, piece in enumerate(result.pieces, start=1):
        name = piece_job_name(submission.pk, number)
        job = Job(
            name=name, asset_id=submission.asset_id, output_file_pattern=f'//sethlans_seq/{name}.{extension}',
            renderer=Renderer.UNREAL, engine_build=submission.engine_build, unreal_kind=UnrealKind.RENDER,
            render_device=RenderDevice.GPU, start_frame=piece.start, end_frame=piece.end - 1,
            unreal_lead_in_frames=piece.lead, sequence_submission_id=submission.pk, status=JobStatus.QUEUED,
            submitted_at=now, render_settings=dict(settings, **{PIECE_SHOTS_KEY: piece.piece_shots()}),
        )
        job.unreal_target_sha256 = job.compute_target_sha256()
        jobs.append(job)
    Job.objects.bulk_create(jobs, batch_size=500)
    return jobs


def _audio_jobs(submission, result, now):
    """FR-SA-1: one audio job per run, rendered from the run's ``r0``."""
    jobs = []
    for number, (run, audio) in enumerate(zip(result.runs, result.audio), start=1):
        name = audio_job_name(submission.pk, number)
        settings = {
            MAP: submission.map, SEQUENCE: submission.sequence, PRESET: submission.preset,
            RUN_SHOTS_KEY: [[part.index, part.start, part.end] for part in run],
        }
        job = Job(
            name=name, asset_id=submission.asset_id, output_file_pattern=f'//sethlans_seq/{name}.wav',
            renderer=Renderer.UNREAL, engine_build=submission.engine_build, unreal_kind=UnrealKind.AUDIO,
            render_device=RenderDevice.GPU, start_frame=audio.start, end_frame=audio.end - 1,
            unreal_lead_in_frames=audio.start - audio.r0, sequence_submission_id=submission.pk,
            status=JobStatus.QUEUED, submitted_at=now, render_settings=settings,
        )
        job.unreal_target_sha256 = job.compute_target_sha256()
        jobs.append(job)
    Job.objects.bulk_create(jobs, batch_size=500)
    return jobs


def _shot_rows(submission, result):
    SequenceShot.objects.bulk_create([
        SequenceShot(submission_id=submission.pk, index=part.index, name=part.name,
                     start_frame=part.start, end_frame=part.end, status=ShotStatus.PENDING)
        for part in result.parts
    ], batch_size=500)


def _pilot(submission, frames, shot_set):
    resolution = None
    if submission.resolution_x is not None:
        resolution = (submission.resolution_x, submission.resolution_y)
    target = Target(submission.engine_build, submission.map, submission.sequence, submission.preset,
                    resolution, shot_set, tuple(frames))
    return ensure_pilot(submission.project, submission.asset, target, reason='sequence')


def _sound_offset(submission, discovery):
    key = f"{submission.preset}@{discovery.frame_rate_num}/{discovery.frame_rate_den}"
    value = (submission.sound_offsets or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _planned(submission, discovery):
    """The plan, or ``None`` after setting the submission ERROR."""
    errors = facts_errors(submission_values(submission), discovery)
    if errors:
        _fail(submission, next(iter(errors.values())))
        return None
    try:
        return plan(shots_from(discovery.shots), _selection(submission),
                    (discovery.frame_rate_num, discovery.frame_rate_den), submission.piece_seconds,
                    submission.lead_in_ms, discovery.playback_start or 0)
    except PlanError as exc:
        _fail(submission, str(exc))
        return None


def plan_submission(submission, discovery):
    """FR-SP-8 for one submission on the DONE ``discovery``; True when it was planned.

    Runs under the caller's ``lock_project`` (``ensure_pilot`` checks it).
    """
    locked = lock_own_rows(SequenceSubmission.objects).filter(
        pk=submission.pk, status=SequenceStatus.DISCOVERING,
    ).first()
    if locked is None:
        return False
    result = _planned(locked, discovery)
    if result is None:
        return False
    shots = shots_from(discovery.shots)
    # D8: the key's shot set is what the pilot measures (chosen parts and reached shots).
    shot_set = shot_set_sha256(measured_ranges(result, shots, locked.sound))
    frames, total = pilot_frames(result, shots, locked.sound)
    warnings = list(facts_warnings(discovery))
    if total > len(frames):
        warnings.append(SAMPLED_PILOT.format(kept=len(frames), total=total))
    _shot_rows(locked, result)
    now = timezone.now()
    if locked.sound:
        _audio_jobs(locked, result, now)
    _piece_jobs(locked, result, _base_settings(locked, discovery, shot_set), now)
    SequenceSubmission.objects.filter(pk=locked.pk, status=SequenceStatus.DISCOVERING).update(
        status=SequenceStatus.QUEUED, shot_set_sha256=shot_set, pilot_frames=frames,
        sound_offset_ms=_sound_offset(locked, discovery), warnings=warnings[:MAX_SUBMISSION_WARNINGS],
    )
    locked.pilot_frames = frames
    _pilot(locked, frames, shot_set)
    project_id = locked.project_id
    transaction.on_commit(lambda pid=project_id: _update_manifest(pid), robust=True)
    logger.info("Planned sequence submission %s: %d shots, %d pieces.", locked.pk, len(result.parts),
                len(result.pieces))
    return True


def _update_manifest(project_id):
    from ..manifest_generator import update_project_manifest
    update_project_manifest(project_id)
