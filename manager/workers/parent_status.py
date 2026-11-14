# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Recompute TiledJob / Animation / AnimationFrame status from children.

Issue #219.  The decision rules live in ``parent_status_rules.py``; this
module applies them to the database.  Callers go through
``parent_dispatch.py``, which also runs whatever a ``True`` return asks
for (assembly, video assembly).

Locking (``lock_parent``): each refresh runs in ``transaction.atomic`` and
its FIRST statement is a write, a zero-row ``UPDATE`` of the parent row.
On SQLite that makes the deferred transaction take the write lock before
it reads anything, so a concurrent writer makes it wait on
``busy_timeout``; reading first and writing later fails at once with
SQLITE_BUSY_SNAPSHOT ("database is locked") when another connection
commits in between.  It then takes ``SELECT ... FOR UPDATE`` on the parent
row only (no ``select_related`` join) for PostgreSQL/MySQL.  Two children
finishing together therefore serialise on the parent row, and the second
refresh always counts the first one's write.

Lock order: Job (child) -> AnimationFrame -> Animation, and tile Job ->
TiledJob, the same order the job-mutation views use.

Writes are ``QuerySet.update()`` (no ``post_save`` recursion), skipped
when nothing changed, and the ASSEMBLING claim is a compare-and-set on
the status that was read.
"""

import logging

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    Animation, AnimationFrame, AnimationFrameStatus,
    JobStatus, TiledJob, TiledJobStatus,
)
from .parent_status_rules import (
    COMPLETE, FAILED, ChildSummary, failure_reason, is_assembly_failure,
    next_parent_state,
)

logger = logging.getLogger(__name__)

# A status value no row ever has; see ``take_write_lock``.
_LOCK_SENTINEL = '__write_lock__'


def take_write_lock(queryset):
    """Run a zero-row UPDATE so SQLite takes its write lock now.

    Matches no row on any backend (``status`` never equals the sentinel),
    so it changes nothing; it only makes the current transaction a write
    transaction from its first statement.
    """
    queryset.filter(status=_LOCK_SENTINEL).update(status=_LOCK_SENTINEL)


def lock_parent(model, pk):
    """Write-lock and return the parent row (``None`` if it is gone)."""
    take_write_lock(model.objects.filter(pk=pk))
    return model.objects.select_for_update().filter(pk=pk).first()


def summarize_jobs(queryset):
    """Return a ``ChildSummary`` for a ``Job`` queryset (one query)."""
    agg = queryset.aggregate(
        total=Count('id'),
        queued=Count('id', filter=Q(status=JobStatus.QUEUED)),
        done=Count('id', filter=Q(status=JobStatus.DONE)),
        error=Count('id', filter=Q(status=JobStatus.ERROR)),
        canceled=Count('id', filter=Q(status=JobStatus.CANCELED)),
        render_time=Sum(
            'render_time_seconds', filter=Q(status=JobStatus.DONE),
        ),
    )
    agg['render_time'] = agg['render_time'] or 0
    return ChildSummary(**agg)


def _first_failed_job(queryset):
    """Return ``(name, error_message)`` of one failed job, or None."""
    return queryset.filter(
        status=JobStatus.ERROR,
    ).exclude(error_message='').order_by('name').values_list(
        'name', 'error_message',
    ).first()


def _write(parent, fields):
    """Update only the fields whose value differs from *parent*'s."""
    changed = {
        name: value for name, value in fields.items()
        if getattr(parent, name) != value
    }
    if changed:
        type(parent).objects.filter(pk=parent.pk).update(**changed)
    return changed


def _claim_assembly(parent, assembling_status, **extra):
    """CAS the parent into ASSEMBLING; True only if this call won.

    An ERROR parent whose reason is an assembly failure is never claimed
    again (every child is DONE, so a refresh would loop into assembly).
    """
    if parent.status == JobStatus.ERROR and is_assembly_failure(parent.error_message):
        return False
    claimed = type(parent).objects.filter(
        pk=parent.pk, status=parent.status,
    ).update(status=assembling_status, error_message='', **extra)
    return claimed == 1


def _settle(parent, state, summary, noun, first_failure, **fields):
    """Write the FAILED / active outcome for a TiledJob or Animation."""
    if state == FAILED:
        fields.update(
            status=JobStatus.ERROR,
            error_message=failure_reason(summary, noun, first_failure()),
            completed_at=parent.completed_at or timezone.now(),
        )
    else:
        fields.update(status=state, error_message='', completed_at=None)
    if _write(parent, fields).get('status') == JobStatus.ERROR:
        logger.warning(
            "%s %s set to ERROR: %s", type(parent).__name__, parent.pk,
            fields['error_message'],
        )


def _complete_animation(parent, render_time):
    _write(parent, {
        'status': JobStatus.DONE,
        'error_message': '',
        'total_render_time_seconds': render_time,
        'completed_at': parent.completed_at or timezone.now(),
    })


def refresh_tiled_job(tiled_job_id):
    """Recompute a TiledJob's status from its tile jobs.

    Returns:
        True when every tile is DONE and this call claimed assembly (the
        parent was moved to ASSEMBLING); the caller must then schedule
        ``assemble_tiled_job_image``.  False otherwise.
    """
    with transaction.atomic():
        parent = lock_parent(TiledJob, tiled_job_id)
        if parent is None or parent.status in (
            TiledJobStatus.ASSEMBLING, TiledJobStatus.DONE,
        ):
            return False
        tiles = parent.jobs.all()
        summary = summarize_jobs(tiles)
        expected = parent.tile_count_x * parent.tile_count_y
        state = next_parent_state(summary, expected, parent.status)
        if state == COMPLETE:
            return _claim_assembly(
                parent, TiledJobStatus.ASSEMBLING,
                total_render_time_seconds=summary.render_time,
            )
        _settle(
            parent, state, summary, 'tiles', lambda: _first_failed_job(tiles),
            total_render_time_seconds=summary.render_time,
        )
    return False


def refresh_untiled_animation(animation_id):
    """Recompute a non-tiled Animation's status from its frame jobs.

    Returns:
        True when every frame job is DONE (animation is DONE); the caller
        then triggers video assembly (an idempotent CAS).
    """
    with transaction.atomic():
        parent = lock_parent(Animation, animation_id)
        if parent is None:
            return False
        jobs = parent.jobs.all()
        summary = summarize_jobs(jobs)
        if summary.total == 0:
            return False
        state = next_parent_state(summary, summary.total, parent.status)
        if state == COMPLETE:
            _complete_animation(parent, summary.render_time)
            return True
        _settle(
            parent, state, summary, 'frames', lambda: _first_failed_job(jobs),
            total_render_time_seconds=summary.render_time,
        )
    return False


def refresh_animation_frame(frame_id):
    """Recompute a tiled-animation frame's status from its tile jobs.

    Returns:
        True when every tile is DONE and this call claimed assembly (the
        frame was moved to ASSEMBLING); the caller must then schedule
        ``assemble_animation_frame_image``.  False otherwise.
    """
    with transaction.atomic():
        frame = lock_parent(AnimationFrame, frame_id)
        if frame is None or frame.status in (
            AnimationFrameStatus.ASSEMBLING, AnimationFrameStatus.DONE,
        ):
            return False
        tiles = frame.tile_jobs.all()
        summary = summarize_jobs(tiles)
        tile_x, tile_y = frame.animation.get_tile_counts()  # lazy, unlocked
        state = next_parent_state(summary, tile_x * tile_y, frame.status)
        if state == COMPLETE:
            return _claim_assembly(frame, AnimationFrameStatus.ASSEMBLING)
        if state == FAILED:
            _write(frame, {
                'status': AnimationFrameStatus.ERROR,
                'error_message': failure_reason(
                    summary, 'tiles', _first_failed_job(tiles),
                ),
            })
        elif frame.status == AnimationFrameStatus.ERROR:
            _write(frame, {'status': AnimationFrameStatus.PENDING, 'error_message': ''})
    return False


def _summarize_frames(animation):
    """Return a frame-level ``ChildSummary`` for a tiled animation.

    ``queued`` is either 0 (some tile job has started or a frame is DONE)
    or ``total``; only ``started > 0`` matters to the rules.
    """
    agg = animation.frames.aggregate(
        total=Count('id'),
        done=Count('id', filter=Q(status=AnimationFrameStatus.DONE)),
        error=Count('id', filter=Q(status=AnimationFrameStatus.ERROR)),
        render_time=Sum(
            'render_time_seconds', filter=Q(status=AnimationFrameStatus.DONE),
        ),
    )
    started = agg['done'] > 0 or animation.jobs.exclude(
        status=JobStatus.QUEUED,
    ).exists()
    agg['queued'] = 0 if started else agg['total']
    agg['render_time'] = agg['render_time'] or 0
    return ChildSummary(**agg)


def _first_failed_frame(animation):
    row = animation.frames.filter(
        status=AnimationFrameStatus.ERROR,
    ).exclude(error_message='').order_by('frame_number').values_list(
        'frame_number', 'error_message',
    ).first()
    return (f"Frame {row[0]}", row[1]) if row else None


def refresh_tiled_animation(animation_id):
    """Recompute a tiled Animation's status from its AnimationFrames.

    Returns:
        True when every expected frame is DONE (animation is DONE); the
        caller then triggers video assembly (an idempotent CAS).
    """
    with transaction.atomic():
        parent = lock_parent(Animation, animation_id)
        if parent is None:
            return False
        expected = len(range(
            parent.start_frame, parent.end_frame + 1, parent.frame_step,
        ))
        summary = _summarize_frames(parent)
        state = next_parent_state(summary, expected, parent.status)
        if state == COMPLETE:
            _complete_animation(parent, summary.render_time)
            return True
        _settle(
            parent, state, summary, 'frames',
            lambda: _first_failed_frame(parent),
            total_render_time_seconds=summary.render_time,
        )
    return False
