# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Sound items from the audio jobs (Unreal sequence rendering FR-SA-8,
FR-SV-8, FR-SS-3, D22).

:func:`settle_sound` runs inside ``refresh_submission``, under the
submission lock (the caller already holds any Job lock: Job ->
SequenceSubmission -> SequenceShot).  For each audio job it is given:

* ERROR or CANCELED -- every sound item of the shots in its run (and the
  merged item when ``merge_sound``) becomes ERROR with "The sound render
  failed: <reason>." (or "... was canceled."), so a submission whose sound
  failed still reaches a final state;
* QUEUED, RENDERING or DONE (requeued, or finished) -- items that only a
  failed sound render set ERROR are cleared again (null: "not built yet").

The whole-submission sound items follow the same rule over every audio job
of the submission.  Writes are ``QuerySet.update()`` limited to rows that
change.  Building the cuts (setting items PENDING) belongs to the work
queue (FR-SV-7).

:func:`retry_sound` is ``POST .../retry-sound/``: one transaction, the
failed audio jobs locked first (ascending id), then the submission.  Like
re-rendering a failed shot, it reopens a finished version (PARTLY_DONE or
DONE) only for a sound item a failed sound render left ERROR: a canceled
version, or one with no such item, gets 409.  It requeues the jobs with the
existing requeue rules and refreshes, which clears their items.
"""

from django.db import transaction
from django.db.models import Q

from shared.unreal.shots_result import clean_text

from ..constants import UnrealKind
from ..constants_sequence import SOUND_RENDER_CANCELED, SOUND_RENDER_FAILED, SOUND_RENDER_PREFIX, SequenceStatus
from ..constants_video import VideoStatus
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..parent_status import lock_parent
from .row_locks import lock_own_rows

FAILED_JOB_STATUSES = (JobStatus.ERROR, JobStatus.CANCELED)
REASON_MAX = 300
NOTHING_TO_RETRY = "No sound render of this version failed."
CANCELED_VERSION = "This version was canceled."


class RetryRefused(Exception):
    """``retry-sound/`` is refused with HTTP ``status`` and ``message``."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def failure_text(status, error):
    """FR-SA-8: the item error of a failed or canceled audio job."""
    if status == JobStatus.CANCELED:
        return SOUND_RENDER_CANCELED
    reason = clean_text(error or '').strip().rstrip('.')[:REASON_MAX] or 'no reason given'
    return SOUND_RENDER_FAILED.format(reason=reason)


def _fail(queryset, message, merge):
    fields = {'sound_status': VideoStatus.ERROR, 'sound_error': message}
    settled = Q(sound_status=VideoStatus.ERROR, sound_error=message)
    if merge:
        fields['sound_video_status'] = VideoStatus.ERROR
        settled &= Q(sound_video_status=VideoStatus.ERROR)
    queryset.exclude(settled).update(**fields)


def _clear(queryset):
    queryset.filter(sound_status=VideoStatus.ERROR, sound_error__startswith=SOUND_RENDER_PREFIX).update(
        sound_status=None, sound_video_status=None, sound_error='',
    )


def _run_shots(submission, start, end):
    """The shots of the run ``start..end`` (inclusive): runs are disjoint, so each shot is in one."""
    return SequenceShot.objects.filter(submission=submission, start_frame__gte=start, end_frame__lte=end + 1)


def settle_sound(submission, job_ids=None):
    """FR-SA-8 for the audio jobs ``job_ids`` (all when ``None``), then the whole items."""
    jobs = Job.objects.filter(sequence_submission=submission, unreal_kind=UnrealKind.AUDIO)
    chosen = jobs if job_ids is None else jobs.filter(pk__in=list(job_ids))
    for start, end, status, error in chosen.values_list('start_frame', 'end_frame', 'status', 'error_message'):
        shots = _run_shots(submission, start, end)
        if status in FAILED_JOB_STATUSES:
            _fail(shots, failure_text(status, error), submission.merge_sound)
        else:
            _clear(shots)
    first = jobs.filter(status__in=FAILED_JOB_STATUSES).order_by('start_frame').values_list(
        'status', 'error_message').first()
    whole = SequenceSubmission.objects.filter(pk=submission.pk)
    if first is not None:
        _fail(whole, failure_text(*first), submission.merge_sound)
    elif submission.sound_status == VideoStatus.ERROR and submission.sound_error.startswith(SOUND_RENDER_PREFIX):
        _clear(whole)


def _has_failed_item(submission):
    """Whether a failed sound render left a sound item of the version ERROR (FR-SA-8)."""
    failed = Q(sound_status=VideoStatus.ERROR, sound_error__startswith=SOUND_RENDER_PREFIX)
    if SequenceSubmission.objects.filter(failed, pk=submission.pk).exists():
        return True
    return SequenceShot.objects.filter(failed, submission=submission).exists()


def retry_sound(submission_id):
    """FR-SV-8 ``retry-sound/``: requeue the failed audio jobs; returns how many.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission (404).
        RetryRefused: a canceled version, or no failed audio job (409).
    """
    from .sequence_status import refresh_submission
    with transaction.atomic():
        ids = list(lock_own_rows(Job.objects).filter(
            sequence_submission_id=submission_id, unreal_kind=UnrealKind.AUDIO, status__in=FAILED_JOB_STATUSES,
        ).order_by('pk').values_list('pk', flat=True))
        submission = lock_parent(SequenceSubmission, submission_id)
        if submission is None:
            raise SequenceSubmission.DoesNotExist
        if submission.cancel_requested or submission.status == SequenceStatus.CANCELED:
            raise RetryRefused(409, CANCELED_VERSION)
        if not ids or not _has_failed_item(submission):
            raise RetryRefused(409, NOTHING_TO_RETRY)
        Job.objects.filter(pk__in=ids).update(
            status=JobStatus.QUEUED, assigned_worker=None, started_at=None, completed_at=None, error_message='',
            last_output='', auto_requeue_count=0, is_paused=False, unreal_memory_report=None,
        )
        refresh_submission(submission_id, [], audio_jobs=ids)
    return len(ids)
