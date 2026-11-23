# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One build task of the work queue (Unreal sequence rendering D16, FR-SV-7,
D22): claim, build, record, advance.

1. **Claim**, under the submission lock: a compare-and-set of the item from
   PENDING to ASSEMBLING that writes the serving process's boot id to the
   row's ``assembly_boot``.  Nothing is claimed for a canceled version (its
   PENDING item goes back to null), when the boot id is empty (no serving
   process) or when the item's inputs are not DONE yet.  While this
   process's parts check still runs (FFmpeg's status unknown, issue #294),
   nothing is claimed either: the item stays PENDING and is queued again
   when the check ends (``parts_check_startup``).
2. **Build** outside any transaction (``video_sequence_build``: each ffmpeg
   run holds the assembly semaphore shared with the animation assembler),
   into a fresh temporary folder that is always removed.
3. **Record**, under the submission lock: ASSEMBLING under this boot id ->
   DONE with the stored file.  When the row is gone or no longer ours, the
   new file is removed; a replaced file is removed after commit.
4. ``finally``: an item still ASSEMBLING under this boot id becomes ERROR
   (the :class:`BuildError` text, else the exception's class name; the
   details are logged), then ``sequence_assembly.advance`` marks and queues
   what became possible and refreshes the submission, so ASSEMBLING always
   ends in a final state.  These writes and the result write are retried on
   SQLite lock errors (``run_with_lock_retry``), so a busy database does not
   leave the item ASSEMBLING until the next start.

No Job row is ever locked here.  Writes are ``QuerySet.update()``.
"""

import logging
import shutil
import tempfile

from django.db import transaction

from .constants_video import VideoStatus
from .models import SequenceShot, SequenceSubmission
from .parent_status import lock_parent
from .video_sequence_build import BuildError, build, storage

logger = logging.getLogger(__name__)

INTERRUPTED = "The build stopped before it finished."
ERROR_MAX = 1000


def _rows(submission_id, index):
    if index is None:
        return SequenceSubmission.objects.filter(pk=submission_id)
    return SequenceShot.objects.filter(submission_id=submission_id, index=index)


def claim(key, boot):
    """Step 1; returns whether this task now owns the item."""
    from .services.sequence_items import item_ready
    submission_id, index, item = key
    status = f'{item}_status'
    with transaction.atomic():
        submission = lock_parent(SequenceSubmission, submission_id)
        if submission is None or not boot:
            return False
        pending = _rows(submission_id, index).filter(**{status: VideoStatus.PENDING})
        if submission.cancel_requested:
            pending.update(**{status: None})
            return False
        if not item_ready(submission, index, item):
            return False
        return bool(pending.update(**{status: VideoStatus.ASSEMBLING, 'assembly_boot': boot}))


def _discard(name):
    try:
        storage().delete(name)
    except Exception:  # noqa: BLE001 -- a leftover file must not fail the task
        logger.exception("Could not remove built file %s.", name)


def record(key, boot, stored):
    """Step 3; returns whether the item became DONE."""
    submission_id, index, item = key
    with transaction.atomic():
        if lock_parent(SequenceSubmission, submission_id) is None:
            transaction.on_commit(lambda: _discard(stored), robust=True)
            return False
        ours = _rows(submission_id, index).filter(**{f'{item}_status': VideoStatus.ASSEMBLING, 'assembly_boot': boot})
        old = ours.values_list(f'{item}_file', flat=True).first()
        done = ours.update(**{f'{item}_status': VideoStatus.DONE, f'{item}_file': stored, f'{item}_error': ''})
        if not done:
            transaction.on_commit(lambda: _discard(stored), robust=True)
        elif old and old != stored:
            transaction.on_commit(lambda: _discard(old), robust=True)
    return bool(done)


def fail_if_ours(key, boot, message):
    """Step 4: ASSEMBLING under ``boot`` -> ERROR with ``message``; returns whether it changed."""
    submission_id, index, item = key
    with transaction.atomic():
        if lock_parent(SequenceSubmission, submission_id) is None:
            return False
        return bool(_rows(submission_id, index).filter(
            **{f'{item}_status': VideoStatus.ASSEMBLING, 'assembly_boot': boot},
        ).update(**{f'{item}_status': VideoStatus.ERROR, f'{item}_error': message[:ERROR_MAX]}))


def _build(key):
    temp = tempfile.mkdtemp(prefix='sethlans_seq_')
    try:
        return build(key, temp)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_task(key, boot):
    """Claim, build and record the item ``key``; the queue's thread calls this."""
    from .services import parts_check
    from .services.sequence_assembly import advance
    from .services.worker_job_writes import run_with_lock_retry
    if parts_check.checks_in_progress():
        # Issue #294: FFmpeg's status is not known yet.  Leave the item
        # PENDING; parts_check_startup queues it again when the check ends.
        logger.info("Build %s waits for the parts check (FFmpeg).", key)
        return False
    if not claim(key, boot):
        return False
    message = INTERRUPTED
    try:
        stored = _build(key)
        run_with_lock_retry(lambda: record(key, boot, stored), key)
        message = ''
    except BuildError as exc:
        message = str(exc) or INTERRUPTED
        logger.warning("Build %s failed: %s", key, message)
    except Exception as exc:
        message = f"The build failed ({type(exc).__name__})."
        logger.exception("Build %s failed.", key)
    finally:
        if message:
            run_with_lock_retry(lambda: fail_if_ours(key, boot, message), key)
        run_with_lock_retry(lambda: advance(key[0]), key)
    return not message
