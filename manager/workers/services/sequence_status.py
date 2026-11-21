# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Refresh a sequence submission from its jobs (Unreal sequence rendering
FR-SS-3, FR-SS-4, D22).

:func:`refresh_submission` runs on every save of a piece or audio job
(``parent_dispatch.dispatch_for_job``), after ``piece-result/`` and after
the actions.  Locking as ``parent_status.lock_parent``: a zero-row UPDATE
of the submission first (SQLite takes its write lock), then ``SELECT ...
FOR UPDATE`` of that row only.  The caller already holds any Job lock, so
the order is Job -> SequenceSubmission -> SequenceShot (``update()``).

It recomputes **only the shots the change covers** (``shot_indexes``;
all when ``None``) from one query of the overlapping pieces, appends the
R7 timings of a finished piece under the submission lock, then derives
the submission from aggregate counts (``sequence_status_rules``).  Writes
are ``QuerySet.update()``, skipped when nothing changed; no ``save()``,
so no signal recursion.  For an audio job's change (and for a full
refresh) the sound items follow the audio jobs (``sequence_sound``,
FR-SA-8), so a failed sound render leaves them final.  Result items are
set PENDING and queued by the work queue (FR-SV-7); this refresh only
reads them.
"""

from bisect import bisect_right

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..constants import UnrealKind
from ..constants_sequence import (
    FINAL_SEQUENCE_STATUSES, MAX_TIMINGS, PIECE_SHOTS_KEY, SequenceStatus, ShotStatus,
)
from ..models import Job, JobStatus, SequenceShot, SequenceSubmission
from ..parent_status import lock_parent
from .job_kind_rules import is_piece
from .sequence_sound import settle_sound
from .sequence_status_rules import (
    ACTIVE_JOB_STATUSES, OPEN_ITEM_STATUSES, STARTED_JOB_STATUSES, SubmissionCounts, failure_message,
    shot_state, submission_state,
)

MAX_SHOT_ERROR = 500
_ITEM_FIELDS = ('video_status', 'sound_status', 'sound_video_status')


def _covering(pieces, starts, row):
    """Statuses (and the first error) of the disjoint, sorted ``pieces`` that overlap ``row``."""
    statuses, error = [], ''
    i = bisect_right(starts, row['end_frame'] - 1) - 1
    while i >= 0 and pieces[i][1] >= row['start_frame']:
        statuses.append(pieces[i][2])
        if pieces[i][2] == JobStatus.ERROR and pieces[i][3]:
            error = pieces[i][3]
        i -= 1
    return statuses, error


def _refresh_shots(submission, shot_indexes):
    shots = SequenceShot.objects.filter(submission=submission)
    if shot_indexes is not None:
        if not shot_indexes:
            return
        shots = shots.filter(index__in=list(shot_indexes))
    rows = list(shots.values('pk', 'start_frame', 'end_frame', 'status', 'error_message'))
    if not rows:
        return
    low, high = min(r['start_frame'] for r in rows), max(r['end_frame'] for r in rows)
    pieces = sorted(Job.objects.filter(
        sequence_submission=submission, unreal_kind=UnrealKind.RENDER, start_frame__lt=high, end_frame__gte=low,
    ).values_list('start_frame', 'end_frame', 'status', 'error_message'))
    starts = [piece[0] for piece in pieces]
    changes = {}
    for row in rows:
        statuses, error = _covering(pieces, starts, row)
        state = shot_state(statuses)
        message = error[:MAX_SHOT_ERROR] if state == ShotStatus.ERROR else ''
        if (state, message) != (row['status'], row['error_message']):
            changes.setdefault((state, message), []).append(row['pk'])
    for (state, message), pks in changes.items():
        SequenceShot.objects.filter(pk__in=pks).update(status=state, error_message=message)


def _render_time(timings):
    """Seconds of the latest timing of each job (a re-render replaces its earlier timing)."""
    latest = {}
    for entry in timings:
        latest[entry.get('job')] = entry.get('seconds') or 0
    return round(sum(latest.values()))


def _append_timings(submission, timings):
    """FR-SS-4: one R7 timing per covered shot (``{index: entry}``), at most 50 per shot."""
    rows = SequenceShot.objects.filter(submission=submission, index__in=list(timings)).values('pk', 'index', 'timings')
    for row in rows:
        kept = (list(row['timings'] or []) + [timings[row['index']]])[-MAX_TIMINGS:]
        SequenceShot.objects.filter(pk=row['pk']).update(timings=kept, render_time_seconds=_render_time(kept))


def _counts(submission):
    open_item = Q(video_status__in=OPEN_ITEM_STATUSES) | Q(sound_status__in=OPEN_ITEM_STATUSES) | Q(
        sound_video_status__in=OPEN_ITEM_STATUSES)
    shots = SequenceShot.objects.filter(submission=submission).aggregate(
        total=Count('pk'), done=Count('pk', filter=Q(status=ShotStatus.DONE)),
        error=Count('pk', filter=Q(status=ShotStatus.ERROR)), canceled=Count('pk', filter=Q(status=ShotStatus.CANCELED)),
        open_items=Count('pk', filter=Q(status=ShotStatus.DONE) & open_item),
    )
    jobs = Job.objects.filter(sequence_submission=submission).aggregate(
        active=Count('pk', filter=Q(status__in=ACTIVE_JOB_STATUSES)),
        started=Count('pk', filter=Q(status__in=STARTED_JOB_STATUSES)),
        render_time=Sum('render_time_seconds', filter=Q(unreal_kind=UnrealKind.RENDER, status=JobStatus.DONE)),
    )
    whole_open = any(getattr(submission, field) in OPEN_ITEM_STATUSES for field in _ITEM_FIELDS)
    counts = SubmissionCounts(
        shots=shots['total'], done=shots['done'], error=shots['error'], canceled=shots['canceled'],
        active_jobs=jobs['active'], started_jobs=jobs['started'],
        open_items=bool(shots['open_items']) or whole_open, cancel_requested=submission.cancel_requested,
    )
    return counts, jobs['render_time'] or 0


def _first_failure(submission):
    return SequenceShot.objects.filter(
        submission=submission, status__in=(ShotStatus.ERROR, ShotStatus.CANCELED),
    ).order_by('index').values_list('name', 'error_message').first()


def _refresh_whole(submission):
    counts, render_time = _counts(submission)
    if not counts.shots:
        return submission.status
    state = submission_state(counts)
    fields = {'status': state, 'total_render_time_seconds': render_time, 'completed_at': None, 'error_message': ''}
    if state in FINAL_SEQUENCE_STATUSES:
        fields['completed_at'] = submission.completed_at or timezone.now()
        if state in (SequenceStatus.ERROR, SequenceStatus.PARTLY_DONE):
            fields['error_message'] = failure_message(counts, _first_failure(submission))
    changed = {name: value for name, value in fields.items() if getattr(submission, name) != value}
    if changed:
        SequenceSubmission.objects.filter(pk=submission.pk).update(**changed)
    return state


def refresh_submission(submission_id, shot_indexes=None, timings=None, audio_jobs=None):
    """FR-SS-3: recompute the changed shots, then the submission; returns its status (or ``None``).

    Args:
        shot_indexes: the shot indexes the change covers; ``None`` for all (every audio job too),
            empty for none.
        timings: ``{shot index: R7 entry}`` to append under the submission lock (FR-SS-4).
        audio_jobs: ids of audio jobs whose change the sound items follow (FR-SA-8).
    """
    with transaction.atomic():
        submission = lock_parent(SequenceSubmission, submission_id)
        if submission is None or submission.status == SequenceStatus.DISCOVERING:
            return None
        _refresh_shots(submission, shot_indexes)
        if timings:
            _append_timings(submission, timings)
        if shot_indexes is None or audio_jobs:
            settle_sound(submission, None if shot_indexes is None else audio_jobs)
        return _refresh_whole(submission)


def covered_indexes(job):
    """The shot indexes a piece covers (from its row); an audio job covers none here."""
    if not is_piece(job):
        return []
    settings = job.render_settings if isinstance(job.render_settings, dict) else {}
    return [row[0] for row in settings.get(PIECE_SHOTS_KEY) or [] if isinstance(row, (list, tuple)) and row]


def refresh_for_job(job):
    """``post_save`` of a piece or audio job: refresh its submission for the shots it covers."""
    audio = [job.pk] if job.unreal_kind == UnrealKind.AUDIO else None
    return refresh_submission(job.sequence_submission_id, covered_indexes(job), audio_jobs=audio)
