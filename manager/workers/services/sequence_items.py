# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The result items of a sequence submission (Unreal sequence rendering
FR-SV-2..7, FR-SS-3, D16): which become PENDING, which fail because an
input failed, and which are ready to build.

An item is ``video``, ``sound`` or ``sound_video`` (the merged copy), per
shot or for the whole submission; its fields are ``<item>_status``,
``<item>_file`` and ``<item>_error``.  Null means "not asked for" or "not
yet"; only the work queue moves PENDING -> ASSEMBLING -> DONE/ERROR.

:func:`open_items` runs inside ``refresh_submission`` under the submission
lock (Job -> SequenceSubmission -> SequenceShot, D22).  It marks, with
``QuerySet.update()`` only:

* a shot's video PENDING when the shot is DONE and video is asked for
  (PNG with ``video_settings``; EXR gives frames only);
* a shot's sound PENDING when the shot is DONE, sound is asked for and the
  audio job of its run is DONE (a failed one set it ERROR, FR-SA-8);
* a shot's merged copy PENDING when ``merge_sound`` is on and its video and
  sound are DONE, ERROR when either failed;
* the whole items once every shot is DONE: video when every shot video is
  DONE, sound when every shot sound is, the merged copy when both whole
  items are; ERROR when an input failed.

It then queues the PENDING items whose inputs are DONE, after commit.  A
canceled submission marks and queues nothing.
"""

from bisect import bisect_right

from django.db.models import Count, Q

from ..constants import UnrealKind
from ..constants_sequence import FrameFormat, ShotStatus
from ..constants_video import VideoStatus
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from .. import video_queue

ITEMS = video_queue.ITEMS
VIDEO_INPUT_FAILED = "A shot video failed; retry the videos."
SOUND_INPUT_FAILED = "The sound of a shot failed."
MERGE_INPUT_FAILED = "The video or the sound to merge failed."
_PENDING, _DONE, _ERROR = VideoStatus.PENDING, VideoStatus.DONE, VideoStatus.ERROR
_UPDATE_CHUNK = 500


def wants(submission):
    """``{item: asked for}`` of a submission (FR-SQ-6, FR-SQ-8, FR-SV-3)."""
    video = submission.video_settings is not None and submission.frame_format == FrameFormat.PNG
    sound = bool(submission.sound)
    return {'video': video, 'sound': sound, 'sound_video': video and sound and bool(submission.merge_sound)}


def _mark_sound(submission, done_shots):
    runs = sorted(Job.objects.filter(
        sequence_submission=submission, unreal_kind=UnrealKind.AUDIO, status=JobStatus.DONE,
    ).values_list('start_frame', 'end_frame'))
    if not runs:
        return
    starts = [run[0] for run in runs]
    ready = []
    for pk, start, end in done_shots.filter(sound_status=None).values_list('pk', 'start_frame', 'end_frame'):
        i = bisect_right(starts, start) - 1
        if i >= 0 and end <= runs[i][1] + 1:
            ready.append(pk)
    for i in range(0, len(ready), _UPDATE_CHUNK):
        SequenceShot.objects.filter(pk__in=ready[i:i + _UPDATE_CHUNK]).update(sound_status=_PENDING)


def _mark_shots(submission, want):
    done = SequenceShot.objects.filter(submission=submission, status=ShotStatus.DONE)
    if want['video']:
        done.filter(video_status=None).update(video_status=_PENDING)
    if want['sound']:
        _mark_sound(submission, done)
    if want['sound_video']:
        done.filter(sound_video_status=None, video_status=_DONE, sound_status=_DONE).update(
            sound_video_status=_PENDING)
        done.filter(Q(sound_video_status=None) | Q(sound_video_status=_PENDING)).filter(
            Q(video_status=_ERROR) | Q(sound_status=_ERROR),
        ).update(sound_video_status=_ERROR, sound_video_error=MERGE_INPUT_FAILED)


def shot_summary(submission):
    """Counts of the submission's shots and their items (one aggregate query)."""
    def count(**kw):
        return Count('pk', filter=Q(**kw))
    return SequenceShot.objects.filter(submission=submission).aggregate(
        total=Count('pk'), done=count(status=ShotStatus.DONE),
        video_done=count(video_status=_DONE), video_error=count(video_status=_ERROR),
        sound_done=count(sound_status=_DONE), sound_error=count(sound_status=_ERROR),
    )


def _whole_next(current, wanted, done, failed, message):
    """``(status, error)`` of a whole item, or ``None`` to leave it."""
    if not wanted or current not in (None, _PENDING):
        return None
    if failed:
        return _ERROR, message
    if done and current is None:
        return _PENDING, ''
    return None


def _mark_whole(submission, want, summary):
    if not summary['total'] or summary['done'] != summary['total']:
        return
    total = summary['total']
    plans = (
        ('video', want['video'], summary['video_done'] == total, summary['video_error'], VIDEO_INPUT_FAILED),
        ('sound', want['sound'], summary['sound_done'] == total, summary['sound_error'], SOUND_INPUT_FAILED),
    )
    fields = {}
    for item, wanted, done, failed, message in plans:
        fields.update(_whole_change(submission, item, _whole_next(
            getattr(submission, f'{item}_status'), wanted, done, failed, message)))
    video, sound = submission.video_status, submission.sound_status
    fields.update(_whole_change(submission, 'sound_video', _whole_next(
        submission.sound_video_status, want['sound_video'], video == _DONE and sound == _DONE,
        _ERROR in (video, sound), MERGE_INPUT_FAILED)))
    if fields:
        SequenceSubmission.objects.filter(pk=submission.pk).update(**fields)


def _whole_change(submission, item, change):
    if change is None:
        return {}
    status, error = change
    setattr(submission, f'{item}_status', status)
    return {f'{item}_status': status, f'{item}_error': error}


def _reload_whole(submission):
    names = [f'{item}_{part}' for item in ITEMS for part in ('status', 'error')]
    values = SequenceSubmission.objects.filter(pk=submission.pk).values(*names).first() or {}
    for name, value in values.items():
        setattr(submission, name, value)


def ready_keys(submission, summary=None):
    """The work-queue keys ``(submission id, shot index or None, item)`` of PENDING items with DONE inputs."""
    summary = summary or shot_summary(submission)
    shots = SequenceShot.objects.filter(submission=submission, status=ShotStatus.DONE)
    keys = [(submission.pk, index, 'video') for index in shots.filter(video_status=_PENDING).values_list(
        'index', flat=True)]
    keys += [(submission.pk, index, 'sound') for index in shots.filter(sound_status=_PENDING).values_list(
        'index', flat=True)]
    keys += [(submission.pk, index, 'sound_video') for index in shots.filter(
        sound_video_status=_PENDING, video_status=_DONE, sound_status=_DONE).values_list('index', flat=True)]
    total = summary['total']
    whole_ready = {
        'video': total and summary['video_done'] == total,
        'sound': total and summary['sound_done'] == total,
        'sound_video': submission.video_status == _DONE and submission.sound_status == _DONE,
    }
    keys += [(submission.pk, None, item) for item in ITEMS
             if getattr(submission, f'{item}_status') == _PENDING and whole_ready[item]]
    return keys


def item_ready(submission, index, item):
    """Whether one PENDING item's inputs are DONE (the work queue checks again at its claim)."""
    if index is not None:
        shot = SequenceShot.objects.filter(submission=submission, index=index).values(
            'status', 'video_status', 'sound_status').first()
        if shot is None or shot['status'] != ShotStatus.DONE:
            return False
        return item != 'sound_video' or (shot['video_status'], shot['sound_status']) == (_DONE, _DONE)
    return any(key == (submission.pk, None, item) for key in ready_keys(submission))


def open_items(submission):
    """FR-SV-7 (a) and (b) under the submission lock: mark items, then queue the ready ones after commit."""
    if submission.cancel_requested:
        return
    want = wants(submission)
    if not any(want.values()):
        return
    _reload_whole(submission)
    _mark_shots(submission, want)
    summary = shot_summary(submission)
    _mark_whole(submission, want, summary)
    video_queue.enqueue_on_commit(ready_keys(submission, summary))
