# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A piece's result (Unreal sequence rendering FR-SS-4, D12, R7, D22).

One transaction: the job lock and ownership (409), the body's shots
against the piece's ``unreal.piece_shots`` (400), every kept frame stored
(409 naming the first missing one), then the job DONE by ``update()`` and
:func:`sequence_status.refresh_submission`, which appends one R7 timing
per covered shot after taking the submission lock.  Lock order: Job ->
SequenceSubmission -> SequenceShot.  A piece becomes DONE only here.
"""

import logging

from django.db import transaction
from django.utils import timezone

from shared.unreal.shots_result import clean_text

from ..models import Job, JobStatus, SequenceFrame, SequenceShot
from .row_locks import lock_own_rows
from .sequence_frames import FrameRefused, owned_piece, piece_shots
from .sequence_status import refresh_submission

logger = logging.getLogger(__name__)


def _shot_errors(job, shots):
    """400 text when the body's shots do not match the piece's, else ``None``."""
    expected = piece_shots(job)
    if [shot['index'] for shot in shots] != [row[0] for row in expected]:
        return f"shots must list the piece's shots in order: {[row[0] for row in expected]}."
    for shot, (index, start, end) in zip(shots, expected):
        if shot['frames'] != end - start:
            return f"Shot {index} has {end - start} kept frames in this piece, not {shot['frames']}."
    return None


def first_missing_frame(job):
    """The first kept frame of the piece with no stored file, else ``None``."""
    indexes = [row[0] for row in piece_shots(job)]
    shot_ids = SequenceShot.objects.filter(
        submission_id=job.sequence_submission_id, index__in=indexes,
    ).values_list('pk', flat=True)
    present = set(SequenceFrame.objects.filter(
        shot_id__in=list(shot_ids), frame_number__gte=job.start_frame, frame_number__lte=job.end_frame,
    ).values_list('frame_number', flat=True))
    return next((frame for frame in range(job.start_frame, job.end_frame + 1) if frame not in present), None)


def _timings(job, worker, body, now):
    gpu = body.get('gpu') or {}
    common = {
        'job': job.pk, 'startup_s': body.get('startup_s'), 'lead_in_s': body.get('lead_in_s'),
        'worker': worker.hostname, 'gpu_name': gpu.get('name', ''), 'card_key': gpu.get('card_key', ''),
        'vram_peak_mb': body.get('vram_peak_mb'), 'at': now.isoformat(),
    }
    return {
        shot['index']: dict(common, frames=shot['frames'], seconds=shot['seconds'], s_per_frame=shot['s_per_frame'])
        for shot in body['shots']
    }


def _last_output(body):
    lines = [body.get('summary') or '']
    lines += [f"WARNING: {clean_text(text)}" for text in body.get('warnings') or []]
    return '\n'.join(line for line in lines if line)


def record_piece_result(job_pk, worker, body):
    """Apply a checked ``piece-result/`` body; returns ``(status, body)``.

    Raises:
        Job.DoesNotExist: no such job (404).
    """
    with transaction.atomic():
        job = lock_own_rows(Job.objects).filter(pk=job_pk).first()
        if job is None:
            raise Job.DoesNotExist
        try:
            owned_piece(job, worker)
        except FrameRefused as exc:
            return exc.status, {'error': exc.message}
        problem = _shot_errors(job, body['shots'])
        if problem:
            return 400, {'shots': [problem]}
        missing = first_missing_frame(job)
        if missing is not None:
            return 409, {'error': f"Frame {missing} of this piece was not uploaded."}
        now = timezone.now()
        Job.objects.filter(pk=job.pk).update(
            status=JobStatus.DONE, completed_at=now, render_time_seconds=round(body['render_s']),
            last_output=_last_output(body),
        )
        indexes = [row[0] for row in piece_shots(job)]
        refresh_submission(job.sequence_submission_id, indexes, timings=_timings(job, worker, body, now))
    logger.info("Piece %s DONE: frames %s-%s from %s.", job.pk, job.start_frame, job.end_frame, worker.hostname)
    return 200, {'status': JobStatus.DONE, 'frames': job.end_frame - job.start_frame + 1}
