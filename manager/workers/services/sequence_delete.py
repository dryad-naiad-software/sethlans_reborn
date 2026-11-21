# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Delete a version with its files (Unreal sequence rendering FR-SS-7, D22).

One transaction: the version's Job rows are locked (``SELECT ... FOR
UPDATE``, ascending id), then the submission (``lock_parent``).  A job
still RENDERING, or a video or sound file still being built (an item
ASSEMBLING on the submission or one of its shots), refuses the delete with
409; otherwise the submission row is deleted, and its jobs, shots and
frames cascade.  The shared ``shots`` job and pilot are not the version's
and stay.  After commit the storage folder
``sequences/<project>/<submission>/`` is removed (its path checked under
``MEDIA_ROOT``) and the project manifest is refreshed, even when the
folder cannot be removed yet; such a folder is removed at the next server
start (:func:`remove_orphan_folders`, from the D16 start hook).  A work-queue task
for a deleted version finds no row and does nothing.
"""

import logging
import os
import shutil

from django.db import transaction
from django.db.models import Q

from ..constants_video import VideoStatus
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..models.upload_paths_sequence import checked_path, submission_dir
from ..parent_status import lock_parent, take_write_lock
from .row_locks import lock_own_rows
from .sequence_sound import RetryRefused

logger = logging.getLogger(__name__)

STILL_RENDERING = "A render of this version is still running."
STILL_BUILDING = "A video or sound file of this version is still being built."
_ITEMS = ('video_status', 'sound_status', 'sound_video_status')


def _assembling():
    query = Q()
    for field in _ITEMS:
        query |= Q(**{field: VideoStatus.ASSEMBLING})
    return query


def _storage():
    return SequenceSubmission._meta.get_field('video_file').storage


def _remove_folder(project_id, submission_id):
    """Remove the version's storage folder; returns whether it is gone.

    A path outside ``MEDIA_ROOT`` is refused and logged.  A folder that
    cannot be removed now (on Windows a file a zip download still has open)
    is logged and left for :func:`remove_orphan_folders` at the next start.
    """
    try:
        path = checked_path(_storage(), submission_dir(project_id, submission_id))
    except ValueError:
        logger.error("Not removing the files of sequence submission %s: path outside MEDIA_ROOT.", submission_id)
        return False
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
    except OSError as exc:
        logger.warning("Could not remove the files of sequence submission %s yet (%s); "
                       "they are removed at the next server start.", submission_id, exc.__class__.__name__)
        return False
    logger.info("Removed the files of sequence submission %s.", submission_id)
    return True


def _after_delete(project_id, submission_id):
    try:
        _remove_folder(project_id, submission_id)
    finally:
        from .sequence_create import _update_manifest
        _update_manifest(project_id)


def _orphans(root):
    """``(project folder name, submission id)`` of version folders under ``root`` with no submission row."""
    found = []
    for project in sorted(os.listdir(root)):
        project_dir = os.path.join(root, project)
        if not os.path.isdir(project_dir):
            continue
        # ASCII digits only: ``str.isdigit`` also accepts characters such as '²' that ``int`` refuses.
        ids = [int(name) for name in os.listdir(project_dir)
               if name.isascii() and name.isdigit() and os.path.isdir(os.path.join(project_dir, name))]
        alive = set(SequenceSubmission.objects.filter(pk__in=ids).values_list('pk', flat=True))
        found += [(project, pk) for pk in sorted(set(ids) - alive)]
    return found


def remove_orphan_folders():
    """Server start (D16 hook): remove version folders whose submission was deleted; returns how many.

    Skipped when the database has no submission at all: a swapped or empty
    database must never wipe every version folder.  Each removal is logged
    with the folder's ids.
    """
    try:
        root = checked_path(_storage(), 'sequences')
    except ValueError:
        return 0
    if not os.path.isdir(root):
        return 0
    if not SequenceSubmission.objects.exists():
        logger.info("Not sweeping sequence folders: the database has no sequence submission.")
        return 0
    removed = 0
    for project, pk in _orphans(root):
        if _remove_folder(project, pk):
            removed += 1
            logger.info("Start sweep removed the folder of deleted sequence submission %s (project %s).", pk, project)
    return removed


def delete_submission(submission_id):
    """FR-SS-7 ``DELETE``: remove the version, its rows and (after commit) its files.

    Raises:
        SequenceSubmission.DoesNotExist: no such submission (404).
        RetryRefused: a job is RENDERING or an item is ASSEMBLING (409).
    """
    with transaction.atomic():
        take_write_lock(Job.objects.filter(sequence_submission_id=submission_id))
        states = list(lock_own_rows(Job.objects).filter(
            sequence_submission_id=submission_id).order_by('pk').values_list('status', flat=True))
        submission = lock_parent(SequenceSubmission, submission_id)
        if submission is None:
            raise SequenceSubmission.DoesNotExist
        if JobStatus.RENDERING in states:
            raise RetryRefused(409, STILL_RENDERING)
        building = _assembling()
        if (SequenceSubmission.objects.filter(building, pk=submission.pk).exists()
                or SequenceShot.objects.filter(building, submission=submission).exists()):
            raise RetryRefused(409, STILL_BUILDING)
        project_id = submission.project_id
        SequenceSubmission.objects.filter(pk=submission.pk).delete()
        transaction.on_commit(lambda: _after_delete(project_id, submission_id), robust=True)
    logger.info("Deleted sequence submission %s (%d jobs).", submission_id, len(states))
