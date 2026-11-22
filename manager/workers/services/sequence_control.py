# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cancel a version and render one shot again (Unreal sequence rendering
FR-SS-5, FR-SS-6, Q9, D22).

Both run in one transaction and keep D22's order: Job rows first, then the
submission (``lock_parent``), then its shots by ``update()``.

* :func:`cancel_submission` (``cancel/``): every piece and audio job of
  the version is locked in ascending id (as ``rerender/`` and ``DELETE``
  lock), then one ``UPDATE`` cancels the QUEUED and RENDERING ones; a
  piece requeued by a ``rerender/`` that committed first is therefore
  canceled too, and the claim refuses a piece of a version with
  ``cancel_requested`` (``unreal_sequence.version_canceled``); then the submission lock,
  ``cancel_requested`` and a refresh.  The shared ``shots`` job and the
  shared pilot carry no ``sequence_submission`` and are never touched, so
  other versions waiting on the same discovery are not affected.  A
  DISCOVERING version becomes CANCELED at once and is skipped when its
  discovery finishes (FR-SP-8).  A finished version is refused (409): a
  cancel must not turn DONE into CANCELED.  Files already made stay.
* :func:`rerender_shot` (``shots/<index>/rerender/``): the shot's covering
  pieces are locked (ascending id), then the submission; under that lock
  the version must not be canceled and the shot must be ERROR or CANCELED
  (else 409, rolled back).  Every ERROR or CANCELED covering piece is
  requeued with the existing requeue rules, the shot becomes PENDING and
  the shots those pieces cover are refreshed.  Frames of other shots in a
  shared piece are uploaded again and replace the old ones
  (``sequence_frames``).
"""

from django.db import transaction
from django.utils import timezone

from ..constants import UnrealKind
from ..constants_sequence import FINAL_SEQUENCE_STATUSES, SequenceStatus, ShotStatus
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..parent_status import lock_parent, take_write_lock
from .row_locks import lock_own_rows
from .sequence_sound import CANCELED_VERSION, RetryRefused
from .sequence_status import covered_indexes, refresh_submission

ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RENDERING)
FAILED_JOB_STATUSES = (JobStatus.ERROR, JobStatus.CANCELED)
FINISHED_VERSION = "This version has finished; there is nothing to cancel."
SHOT_NOT_FAILED = "Only a failed or canceled shot can be rendered again."
NO_SUCH_SHOT = "No such shot in this version."
# The existing requeue rules (``retry-sound/``, the job requeue action).
REQUEUED = {
    'status': JobStatus.QUEUED, 'assigned_worker': None, 'started_at': None, 'completed_at': None,
    'error_message': '', 'last_output': '', 'auto_requeue_count': 0, 'is_paused': False,
    'unreal_memory_report': None,
}


def _locked(submission_id):
    submission = lock_parent(SequenceSubmission, submission_id)
    if submission is None:
        raise SequenceSubmission.DoesNotExist
    return submission


def cancel_submission(submission_id):
    """FR-SS-6 ``cancel/``: returns ``(status, canceled job count)``.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission (404).
        RetryRefused: the version has already finished (409).
    """
    with transaction.atomic():
        # D22: lock every job of the version in ascending id first (as rerender and delete do), so a
        # piece a concurrent rerender requeues is seen after its commit and no lock order can deadlock.
        mine = Job.objects.filter(sequence_submission_id=submission_id,
                                  unreal_kind__in=(UnrealKind.RENDER, UnrealKind.AUDIO))
        take_write_lock(mine)  # SQLite: a write transaction from the first statement
        ids = list(lock_own_rows(mine).order_by('pk').values_list('pk', flat=True))
        count = Job.objects.filter(pk__in=ids, status__in=ACTIVE_JOB_STATUSES).update(
            status=JobStatus.CANCELED, completed_at=timezone.now(), assigned_worker=None, is_paused=False,
            unreal_memory_report=None)
        submission = _locked(submission_id)
        if submission.status in FINAL_SEQUENCE_STATUSES and not count:
            raise RetryRefused(409, FINISHED_VERSION)
        if submission.status == SequenceStatus.DISCOVERING:
            SequenceSubmission.objects.filter(pk=submission.pk, status=SequenceStatus.DISCOVERING).update(
                status=SequenceStatus.CANCELED, cancel_requested=True, completed_at=timezone.now())
            return SequenceStatus.CANCELED, count
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        return refresh_submission(submission_id), count


def _covering_pieces(submission_id, shot):
    """The pieces whose frames overlap ``shot`` (``end`` exclusive), locked in ascending id."""
    return list(lock_own_rows(Job.objects).filter(
        sequence_submission_id=submission_id, unreal_kind=UnrealKind.RENDER,
        start_frame__lt=shot['end_frame'], end_frame__gte=shot['start_frame'],
    ).order_by('pk'))


def rerender_shot(submission_id, index):
    """FR-SS-5 ``rerender/``: requeue the shot's failed pieces; returns ``(status, requeued count)``.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission or shot (404).
        RetryRefused: a canceled version, or a shot that is not ERROR or CANCELED (409).
    """
    with transaction.atomic():
        shot = SequenceShot.objects.filter(submission_id=submission_id, index=index).values(
            'pk', 'start_frame', 'end_frame').first()
        if shot is None:
            raise SequenceSubmission.DoesNotExist
        take_write_lock(Job.objects.filter(sequence_submission_id=submission_id))
        pieces = _covering_pieces(submission_id, shot)
        submission = _locked(submission_id)
        if submission.cancel_requested or submission.status == SequenceStatus.CANCELED:
            raise RetryRefused(409, CANCELED_VERSION)
        # Shot rows are written under the submission lock, so this read is current.
        state = SequenceShot.objects.filter(pk=shot['pk']).values_list('status', flat=True).first()
        if state not in (ShotStatus.ERROR, ShotStatus.CANCELED):
            raise RetryRefused(409, SHOT_NOT_FAILED)
        failed = [piece for piece in pieces if piece.status in FAILED_JOB_STATUSES]
        Job.objects.filter(pk__in=[piece.pk for piece in failed]).update(**REQUEUED)
        SequenceShot.objects.filter(pk=shot['pk']).update(status=ShotStatus.PENDING, error_message='')
        indexes = {index}
        for piece in failed:
            indexes.update(covered_indexes(piece))
        return refresh_submission(submission_id, sorted(indexes)), len(failed)
