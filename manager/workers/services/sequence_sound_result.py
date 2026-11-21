# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An audio job's result (Unreal sequence rendering FR-SA-6, FR-SA-11, D15,
D22, D24; the ``job_output_upload`` pattern).

Before anything is stored: the job is an audio job RENDERING on the
calling worker (409), the file is at most 4 GiB (413), its header and
length pass FR-SA-5 again on the temporary file (400) and the ``offset``
object passes the shared strict check (400, ``serializers/sequence_sound``).
The ``.wav`` is then written under a name the manager makes
(``runs/<job id>.wav``, never the worker's) outside any transaction, and
recorded in one transaction: the job lock with ownership checked again,
then the submission lock (Job -> SequenceSubmission -> SequenceShot, D22).
There the run joins ``sound_runs`` with its correction (D24), the
submission's warnings are updated (the worker's text cleaned of control
characters and NUL): the run's offset warnings, then the audio job's own
summary warnings (``sound_runs.summary_warnings``, #284), read from the
same request's ``summary`` field and not from ``last_output``, which this
record writes without line breaks.  The job becomes DONE with the file as its
``output_file``, and ``refresh_submission`` settles the sound items.  A
rejected record removes the new file; a replaced run file is removed
after commit.  Cutting the shots' sound is a work-queue task (FR-SV-7).
"""

import contextlib
import logging
import threading
import wave

from django.db import transaction
from django.utils import timezone

from sethlans_manager.body_limits import SOUND_FILE_CAP
from shared.unreal.shots_result import clean_text
from shared.unreal.sound_result import SoundResultError, check_wav_params, expected_samples

from ..constants import UnrealKind
from ..constants_sequence import MAX_SUBMISSION_WARNINGS
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..models.upload_paths_sequence import checked_path, run_sound_name
from ..parent_status import lock_parent
from .row_locks import lock_own_rows
from .sequence_frames import FrameRefused
from .sequence_status import refresh_submission
from .sound_runs import SILENT, merged_runs, merged_warnings, run_entry, run_warnings, with_summary_warnings
from .worker_job_writes import run_with_lock_retry

logger = logging.getLogger(__name__)

NOT_AN_AUDIO_JOB = "Job is not a sequence audio job."
NOT_RENDERING_HERE = "Job is not rendering on this worker."
READ_CHUNK = 1 << 20
UPLOAD_IN_PROGRESS = "A sound upload for this job is already in progress."
_IN_FLIGHT = set()
_IN_FLIGHT_LOCK = threading.Lock()
SUMMARY_KEEP = 32 * 1024


def owned_audio(job, worker):
    """Raise :class:`FrameRefused` (409) unless ``job`` is an audio job RENDERING on ``worker``."""
    if job.unreal_kind != UnrealKind.AUDIO or job.sequence_submission_id is None:
        raise FrameRefused(409, NOT_AN_AUDIO_JOB)
    if job.status != JobStatus.RENDERING or job.assigned_worker_id != worker.pk:
        raise FrameRefused(409, NOT_RENDERING_HERE)


@contextlib.contextmanager
def upload_slot(job_pk):
    """One ``sound-result/`` upload per job at a time in this process; a second gets 409 at once.

    Taken before the body is parsed, so a worker's retry after a client
    timeout is refused without a second temporary copy or stored file (the
    manager runs one serving process, D16; across processes the job lock
    still refuses the later record).
    """
    with _IN_FLIGHT_LOCK:
        if job_pk in _IN_FLIGHT:
            raise FrameRefused(409, UPLOAD_IN_PROGRESS)
        _IN_FLIGHT.add(job_pk)
    try:
        yield
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(job_pk)


def _is_silent(reader):
    reader.rewind()
    while True:
        data = reader.readframes(READ_CHUNK)
        if not data:
            return True
        if data.strip(b'\x00'):
            return False


def check_wav(file_obj, job, rate_fraction, latency_skipped=False):
    """FR-SA-5 on the uploaded file; returns ``(rate, channels, samples, silent)``.

    ``latency_skipped`` (the form's flag): MRG drained 7 more frames at the
    tail, so the expected length is 7 frames longer (``expected_samples``).

    Raises:
        FrameRefused: 413 above 4 GiB, 400 for a file that is not the run's ``.wav``.
    """
    if file_obj.size > SOUND_FILE_CAP:
        raise FrameRefused(413, f"A sound file may be at most {SOUND_FILE_CAP} bytes.")
    frames = job.end_frame - job.start_frame + 1 + job.unreal_lead_in_frames
    file_obj.seek(0)
    try:
        with wave.open(file_obj, 'rb') as reader:
            channels, width, rate, samples = reader.getparams()[:4]
            if samples * channels * width > file_obj.size:
                raise SoundResultError('file', "The sound file is shorter than its header says.")
            expected = expected_samples(frames, rate, *rate_fraction, latency_skipped=latency_skipped)
            check_wav_params(channels, width, rate, samples, expected)
            silent = _is_silent(reader)
    except SoundResultError as exc:
        raise FrameRefused(400, str(exc)) from None
    except (wave.Error, EOFError, ValueError, OSError):
        raise FrameRefused(400, "The file is not a PCM .wav file.") from None
    finally:
        file_obj.seek(0)
    return rate, channels, samples, silent


def rate_fraction_of(job):
    """The sequence's ``(num, den)`` of an audio job's submission, or ``None``."""
    row = SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
        'discovery__frame_rate_num', 'discovery__frame_rate_den').first()
    if row is None or not all(row):
        return None
    return row


def _run_names(submission_id, job):
    """``{index: name}`` of the run's shots in edit order (runs are disjoint, so each shot is in one)."""
    return dict(SequenceShot.objects.filter(
        submission_id=submission_id, start_frame__gte=job.start_frame, end_frame__lte=job.end_frame + 1,
    ).order_by('start_frame').values_list('index', 'name'))


def _summary(body, rate, channels, samples):
    text = clean_text(body.get('summary') or '')[:SUMMARY_KEEP]
    return f"audio_samples={samples} audio_rate={rate} audio_channels={channels}" + (f" {text}" if text else '')


def _run_entry(job, submission, body, wav):
    rate, channels, samples, silent = wav
    offset = dict(body['offset'], reason=clean_text(body['offset']['reason']))
    extra = {'channels': channels, 'samples': samples, 'silent': silent,
             'latency_compensation_skipped': body['latency_compensation_skipped']}
    entry = run_entry(job, offset, rate, submission.sound_offset_ms, extra)
    entry['warnings'] = run_warnings(offset, rate, entry['applied_source'], entry['applied_samples'],
                                     _run_names(submission.pk, job), submission.preset)
    if silent:
        entry['warnings'].append(SILENT)
    entry['warnings'] = with_summary_warnings(entry['warnings'], body.get('summary'), MAX_SUBMISSION_WARNINGS)
    return entry


def _record(job_pk, worker, stored, wav, body):
    """Under the job, then the submission lock: the run, the warnings, the job DONE; returns the entry."""
    with transaction.atomic():
        job = lock_own_rows(Job.objects).filter(pk=job_pk).first()
        if job is None:
            raise Job.DoesNotExist
        owned_audio(job, worker)
        submission = lock_parent(SequenceSubmission, job.sequence_submission_id)
        if submission is None:
            raise FrameRefused(409, NOT_AN_AUDIO_JOB)
        entry = _run_entry(job, submission, body, wav)
        runs = submission.sound_runs or []
        old = next((run for run in runs if isinstance(run, dict) and run.get('job') == job.pk), None)
        SequenceSubmission.objects.filter(pk=submission.pk).update(
            sound_runs=merged_runs(runs, entry),
            warnings=merged_warnings(submission.warnings, old, entry['warnings'], MAX_SUBMISSION_WARNINGS),
        )
        previous = job.output_file.name if job.output_file else ''
        Job.objects.filter(pk=job.pk).update(
            status=JobStatus.DONE, completed_at=timezone.now(), output_file=stored,
            last_output=_summary(body, *wav[:3]),
            render_time_seconds=None if body.get('render_s') is None else round(body['render_s']),
        )
        refresh_submission(submission.pk, [], audio_jobs=[job.pk])
        if previous and previous != stored:
            transaction.on_commit(lambda: _discard(previous), robust=True)
    return entry


def _storage():
    return Job._meta.get_field('output_file').storage


def _discard(name):
    try:
        if not Job.objects.filter(output_file=name).exists():
            _storage().delete(name)
    except Exception:
        logger.exception("Could not remove sound file %s.", name)


def store_sound(job_pk, worker, file_obj, body, wav):
    """FR-SA-6: store the checked ``.wav`` and record the run; returns the response body.

    Raises:
        Job.DoesNotExist: no such job (404).
        FrameRefused: the job no longer accepts the result (409).
    """
    job = Job.objects.filter(pk=job_pk).first()
    if job is None:
        raise Job.DoesNotExist
    owned_audio(job, worker)
    project_id = SequenceSubmission.objects.filter(pk=job.sequence_submission_id).values_list(
        'project_id', flat=True).first()
    storage = _storage()
    name = run_sound_name(project_id, job.sequence_submission_id, job.pk)
    checked_path(storage, name)
    file_obj.seek(0)
    stored = storage.save(name, file_obj)
    try:
        checked_path(storage, stored)
        entry = run_with_lock_retry(lambda: _record(job_pk, worker, stored, wav, body), job_pk)
    except BaseException:
        _discard(stored)
        raise
    logger.info("Audio job %s DONE: offset %s (%s).", job_pk, entry['applied_samples'], entry['applied_source'])
    return {'status': JobStatus.DONE, 'applied_samples': entry['applied_samples'],
            'applied_source': entry['applied_source']}
