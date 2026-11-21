# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Store and attach a piece's frame (Unreal sequence rendering FR-SS-2,
FR-SS-2b, D12, D22; the ``job_output_upload`` pattern).

Before anything is stored: the job is a piece RENDERING on the calling
worker (409), the frame lies in the piece's kept range and in one of the
shots of its ``unreal.piece_shots`` (400; lead-in frames are never
accepted), and the file passes the checks of the security notes (size
413; extension, PNG verify and size, EXR magic 400).  The file is then
written under a manager-made name outside any transaction, and attached
under the job lock with the ownership checked again (Job ->
SequenceFrame).  A repeat upload of a frame replaces the row's file; the
old file is removed after commit.  A rejected attach removes the new file
unless a row references it.

The frame clash (chunk 7): once a shot is rendered again beside a piece
still running, the same frame can arrive from two jobs, and two inserts
can race on the unique (shot, frame) key.  The losing insert re-reads the
winner's row; **the frame of the job that started later is kept**.  An
upload from the older job is dropped (its file removed after commit) and
answered 200 with ``kept_newer`` true, so the worker goes on with its
other frames; ``piece-result/`` still finds the frame stored.
"""

import logging
import os

from django.db import IntegrityError, transaction
from PIL import Image

from sethlans_manager.body_limits import FRAME_FILE_CAP

from ..constants_sequence import FRAME_EXTENSIONS, OUTPUT_SIZE_KEY, PIECE_SHOTS_KEY, FrameFormat
from ..models import Job, JobStatus, SequenceFrame, SequenceShot
from ..models.upload_paths_sequence import checked_path, frame_name
from ..renderers.unreal_settings import OUTPUT_FORMAT
from .job_kind_rules import is_piece
from .row_locks import lock_own_rows
from .worker_job_writes import run_with_lock_retry

logger = logging.getLogger(__name__)

EXR_MAGIC = b'\x76\x2f\x31\x01'
NOT_A_PIECE = "Job is not a sequence piece."
NOT_RENDERING_HERE = "Job is not rendering on this worker."
SHOT_GONE = "The frame's shot no longer exists."


class FrameRefused(Exception):
    """The frame is refused with HTTP ``status`` and ``message``."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def owned_piece(job, worker):
    """Raise :class:`FrameRefused` (409) unless ``job`` is a piece RENDERING on ``worker``."""
    if not is_piece(job):
        raise FrameRefused(409, NOT_A_PIECE)
    if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
        raise FrameRefused(409, NOT_RENDERING_HERE)


def piece_shots(job):
    settings = job.render_settings if isinstance(job.render_settings, dict) else {}
    return [tuple(row) for row in settings.get(PIECE_SHOTS_KEY) or []]


def shot_index_for(job, frame):
    """FR-SS-2b: the shot index of a kept ``frame`` of the piece, else :class:`FrameRefused` (400)."""
    if not job.start_frame <= frame <= job.end_frame:
        raise FrameRefused(400, f"Frame {frame} is outside this piece's kept frames "
                                f"({job.start_frame}-{job.end_frame}).")
    for index, start, end in piece_shots(job):
        if start <= frame < end:
            return index
    raise FrameRefused(400, f"Frame {frame} is in no shot of this piece "
                            f"({job.start_frame}-{job.end_frame}).")


def _check_png(file_obj, output_size):
    try:
        file_obj.seek(0)
        with Image.open(file_obj) as image:
            if image.format != 'PNG':
                raise ValueError(image.format)
            size = list(image.size)
            image.verify()
    except Exception:
        raise FrameRefused(400, "The file is not a valid PNG image.") from None
    finally:
        file_obj.seek(0)
    if output_size and size != list(output_size):
        raise FrameRefused(400, f"The frame is {size[0]}x{size[1]}; this piece renders "
                                f"{output_size[0]}x{output_size[1]}.")


def check_frame_file(file_obj, frame_format, output_size):
    """The security notes' checks on an uploaded frame; :class:`FrameRefused` on failure."""
    if file_obj.size > FRAME_FILE_CAP:
        raise FrameRefused(413, f"A frame may be at most {FRAME_FILE_CAP} bytes.")
    extension = FRAME_EXTENSIONS.get(frame_format, 'png')
    if os.path.splitext(file_obj.name or '')[1].lower() != f'.{extension}':
        raise FrameRefused(400, f"This piece renders {frame_format}: the file must end with .{extension}.")
    if frame_format == FrameFormat.EXR:
        file_obj.seek(0)
        magic = file_obj.read(4)
        file_obj.seek(0)
        if magic != EXR_MAGIC:
            raise FrameRefused(400, "The file is not an EXR image.")
        return
    _check_png(file_obj, output_size)


def _discard(storage, name):
    """Remove a stored file that no frame row references."""
    try:
        if not SequenceFrame.objects.filter(file=name).exists():
            storage.delete(name)
    except Exception:
        logger.exception("Could not remove rejected frame file %s.", name)


def _order(started_at, pk):
    """Sort key of a render: its claim time, then its id (an unstarted job sorts first)."""
    return (started_at is not None, started_at or 0, pk)


def holder_is_newer(holder_id, job):
    """Whether the frame's current job ``holder_id`` started after ``job`` (FR-SS-2, the frame clash).

    A deleted holder (``None``) or ``job`` itself is never newer.  The holder's
    row is read without a lock: D22 locks Job rows only in ascending id, and
    a holder requeued meanwhile has no claim time, so it reads as older.
    """
    if holder_id is None or holder_id == job.pk:
        return False
    started = Job.objects.filter(pk=holder_id).values_list('started_at', flat=True).first()
    if started is None:
        return False
    return _order(started, holder_id) > _order(job.started_at, job.pk)


def _insert(shot_pk, job_pk, frame, stored):
    """Insert the row; ``None`` when another job inserted the same frame first (the unique clash)."""
    try:
        with transaction.atomic():
            SequenceFrame.objects.create(shot_id=shot_pk, job_id=job_pk, frame_number=frame, file=stored)
        return 'stored'
    except IntegrityError:
        return None


def _attach(job_pk, worker, shot_pk, frame, stored, storage):
    """Under the job lock: re-check ownership, then insert or replace the frame row.

    Returns ``'stored'``, ``'replaced'`` or ``'kept_newer'`` (the frame from a
    job that started later is kept and this upload is dropped).
    """
    with transaction.atomic():
        locked = lock_own_rows(Job.objects).filter(pk=job_pk).first()
        if locked is None:
            raise Job.DoesNotExist
        owned_piece(locked, worker)
        rows = lock_own_rows(SequenceFrame.objects).filter(shot_id=shot_pk, frame_number=frame)
        row = rows.first()
        if row is None:
            outcome = _insert(shot_pk, job_pk, frame, stored)
            if outcome:
                return outcome
            row = rows.first()  # the other job's row, committed meanwhile
            if row is None:
                raise IntegrityError(f"Frame {frame} of shot {shot_pk} could not be stored.")
        if holder_is_newer(row.job_id, locked):
            transaction.on_commit(lambda: _discard(storage, stored), robust=True)
            return 'kept_newer'
        old = row.file.name
        SequenceFrame.objects.filter(pk=row.pk).update(file=stored, job_id=job_pk)
        if old and old != stored:
            transaction.on_commit(lambda: _discard(storage, old), robust=True)
        return 'replaced'


def store_frame(job_pk, worker, frame, file_obj):
    """FR-SS-2b: check, store and attach one frame; returns the response body.

    Raises:
        Job.DoesNotExist: no such job (404).
        FrameRefused: a check failed (its status).
    """
    job = Job.objects.filter(pk=job_pk).first()
    if job is None:
        raise Job.DoesNotExist
    owned_piece(job, worker)
    index = shot_index_for(job, frame)
    settings = job.render_settings
    frame_format = settings.get(OUTPUT_FORMAT, FrameFormat.PNG)
    check_frame_file(file_obj, frame_format, settings.get(OUTPUT_SIZE_KEY))
    shot = SequenceShot.objects.filter(submission_id=job.sequence_submission_id, index=index).values(
        'pk', 'submission__project_id').first()
    if shot is None:
        raise FrameRefused(409, SHOT_GONE)
    storage = SequenceFrame._meta.get_field('file').storage
    name = frame_name(shot['submission__project_id'], job.sequence_submission_id, index, frame,
                      FRAME_EXTENSIONS[frame_format])
    checked_path(storage, name)
    file_obj.seek(0)
    stored = storage.save(name, file_obj)
    try:
        checked_path(storage, stored)
        outcome = run_with_lock_retry(lambda: _attach(job_pk, worker, shot['pk'], frame, stored, storage), job_pk)
    except BaseException:
        _discard(storage, stored)
        raise
    # A dropped upload still answers 200: the worker treats 409 as "not my job" and stops (FR-SW-10).
    return {'frame': frame, 'shot': index, 'replaced': outcome == 'replaced', 'kept_newer': outcome == 'kept_newer'}
