# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The server start hook of the build queue (Unreal sequence rendering D16,
FR-SD-9).

:func:`on_server_start` is called once by the serving process
(``sethlans_manager.waitress_launcher.launch``, just before the listeners
start; ``--dev`` included), never from ``apps.ready()``, which also runs
for management commands, tests and the reloader's parent.  It:

1. makes the process's boot id (a UUID);
2. resets every item ASSEMBLING under another boot id to PENDING (a build
   that a stopped process left half done; a build claimed under this boot
   id is never touched);
3. settles shot discoveries whose ``shots`` job ended while no callback ran
   (``sequence_discovery.settle_open_discoveries``);
4. starts the queue's thread;
5. queues every PENDING item, through ``sequence_assembly.advance`` of each
   submission that has one;
6. removes the storage folders of deleted versions that a delete could not
   remove at the time (``sequence_delete.remove_orphan_folders``).

Each step logs and swallows its own failure, so the server still starts.
The manager runs one serving process; several would need a lease.
"""

import logging
import uuid

from django.db.models import Q

from . import video_queue
from .constants_video import VideoStatus

logger = logging.getLogger(__name__)


def reset_stale(boot):
    """Step 2: items ASSEMBLING under another boot id -> PENDING; returns how many."""
    from .models import SequenceShot, SequenceSubmission
    reset = 0
    for model in (SequenceSubmission, SequenceShot):  # D22: submission rows before shot rows
        for item in video_queue.ITEMS:
            status = f'{item}_status'
            reset += model.objects.filter(**{status: VideoStatus.ASSEMBLING}).exclude(assembly_boot=boot).update(
                **{status: VideoStatus.PENDING})
    return reset


def pending_submissions():
    """Ids of the submissions (not canceled) with a PENDING item, or ASSEMBLING with every item final, in id order.

    The second kind is a version whose last build finished while its
    refresh did not (a crash between the two); advancing it settles it.
    """
    from .constants_sequence import SequenceStatus
    from .models import SequenceSubmission
    whole, shot = Q(status=SequenceStatus.ASSEMBLING), Q()
    for item in video_queue.ITEMS:
        whole |= Q(**{f'{item}_status': VideoStatus.PENDING})
        shot |= Q(**{f'shots__{item}_status': VideoStatus.PENDING})
    return list(SequenceSubmission.objects.filter(cancel_requested=False).filter(whole | shot).order_by(
        'pk').values_list('pk', flat=True).distinct())


def queue_pending():
    """Step 5: advance every submission with a PENDING item; returns how many were advanced."""
    from .services.sequence_assembly import advance
    advanced = 0
    for submission_id in pending_submissions():
        try:
            advance(submission_id)
            advanced += 1
        except Exception:  # noqa: BLE001 -- one bad row must not stop the others
            logger.exception("Queuing the builds of sequence submission %s failed.", submission_id)
    return advanced


def _step(name, function, *args):
    try:
        return function(*args)
    except Exception:  # noqa: BLE001 -- the server must start
        logger.exception("Server start: %s failed.", name)
        return None


def on_server_start():
    """D16 start hook of the serving process; returns the boot id (the running one when called again)."""
    if video_queue.is_accepting():
        return video_queue.boot_id()
    from .services.sequence_delete import remove_orphan_folders
    from .services.sequence_discovery import settle_open_discoveries
    boot = str(uuid.uuid4())
    reset = _step("resetting interrupted builds", reset_stale, boot)
    settled = _step("settling shot discoveries", settle_open_discoveries)
    video_queue.start(boot)
    queued = _step("queuing pending builds", queue_pending)
    removed = _step("removing the files of deleted versions", remove_orphan_folders)
    logger.info("Build queue ready: %s builds reset, %s discoveries settled, %s versions queued, "
                "%s deleted versions' folders removed.", reset, settled, queued, removed)
    return boot
