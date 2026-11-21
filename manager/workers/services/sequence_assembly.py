# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Advance a sequence submission's builds (Unreal sequence rendering FR-SV-6,
FR-SV-7, FR-SV-8, D16, D22).

:func:`advance` runs after every work-queue task and at server start: it
refreshes the submission (``refresh_submission`` with no changed shot),
which marks the items that became possible, queues the PENDING items whose
inputs are DONE after commit, and derives the submission's state, so
ASSEMBLING ends DONE, PARTLY_DONE or ERROR once nothing is left to build.

The two actions take the submission lock only (no Job row is touched):

* :func:`retry_video` (``retry-video/``): every built item a build left
  ERROR -- videos, sound cuts, merged copies, per shot and whole -- goes
  back to PENDING; sound items a failed sound render set ERROR are left to
  ``retry-sound/``.  A PENDING item whose inputs failed again becomes ERROR
  again in the same refresh; when nothing is left to build the action is
  refused (409) and rolled back.
* :func:`merge_sound` (``merge-sound/``): turns ``merge_sound`` on for a
  version with a video and sound, then advances; asking again is harmless.

Both refuse a canceled version (409); ``merge-sound/`` also refuses a
version still reading its shots (409).
"""

from django.db import transaction
from django.db.models import Q

from ..constants_sequence import SOUND_RENDER_PREFIX, SequenceStatus
from ..constants_video import VideoStatus
from ..models import SequenceShot, SequenceSubmission
from ..parent_status import lock_parent
from .sequence_items import ITEMS, wants
from .sequence_sound import CANCELED_VERSION, RetryRefused
from .sequence_status import refresh_submission

NOTHING_TO_RETRY = "No video or sound file of this version failed to build."
NO_MERGE = "Merging needs a video and sound; this version has no video or no sound."
STILL_DISCOVERING = "The shots of this version are still being read."


def advance(submission_id):
    """FR-SV-7: mark and queue the possible items, then refresh; returns the submission's status."""
    return refresh_submission(submission_id, [])


def _locked(submission_id):
    submission = lock_parent(SequenceSubmission, submission_id)
    if submission is None:
        raise SequenceSubmission.DoesNotExist
    if submission.cancel_requested or submission.status == SequenceStatus.CANCELED:
        raise RetryRefused(409, CANCELED_VERSION)
    return submission


def _retry(queryset):
    """Reset the failed builds of ``queryset``; returns how many rows changed."""
    changed = 0
    built_sound = ~Q(sound_error__startswith=SOUND_RENDER_PREFIX)
    for item, extra in (('video', Q()), ('sound', built_sound), ('sound_video', Q())):
        changed += queryset.filter(extra, **{f'{item}_status': VideoStatus.ERROR}).update(
            **{f'{item}_status': VideoStatus.PENDING, f'{item}_error': ''})
    return changed


def _pending_count(submission_id):
    """How many items of the submission and its shots are PENDING."""
    count = 0
    for item in ITEMS:
        status = {f'{item}_status': VideoStatus.PENDING}
        count += SequenceSubmission.objects.filter(pk=submission_id, **status).count()
        count += SequenceShot.objects.filter(submission_id=submission_id, **status).count()
    return count


def retry_video(submission_id):
    """FR-SV-8 ``retry-video/``: reset the failed builds and advance; returns how many items will be built.

    An item reset only to fail again at once (its input is still ERROR, for
    example a merged copy whose sound render failed) does not count; when
    nothing can be rebuilt, the whole action is rolled back with 409.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission (404).
        RetryRefused: a canceled version, or nothing to rebuild (409).
    """
    with transaction.atomic():
        submission = _locked(submission_id)
        changed = _retry(SequenceSubmission.objects.filter(pk=submission.pk))
        changed += _retry(SequenceShot.objects.filter(submission=submission))
        if changed:
            refresh_submission(submission_id, [])
        count = _pending_count(submission_id) if changed else 0
        if not count:
            raise RetryRefused(409, NOTHING_TO_RETRY)
    return count


def merge_sound(submission_id):
    """FR-SV-6 ``merge-sound/``: merge the sound into the videos; returns the submission's status.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission (404).
        RetryRefused: a canceled version or one still reading its shots (409), or one without video or
            sound (400).
    """
    with transaction.atomic():
        submission = _locked(submission_id)
        if submission.status == SequenceStatus.DISCOVERING:
            raise RetryRefused(409, STILL_DISCOVERING)
        submission.merge_sound = True
        if not wants(submission)['sound_video']:
            raise RetryRefused(400, NO_MERGE)
        SequenceSubmission.objects.filter(pk=submission.pk, merge_sound=False).update(merge_sound=True)
        return refresh_submission(submission_id, [])
