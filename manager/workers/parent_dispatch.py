# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Refresh a parent's status and run what the refresh asks for (issue #219).

The ``refresh_*`` functions in ``parent_status.py`` return True when the
caller must act: schedule tile assembly (TiledJob / AnimationFrame) or
trigger video assembly (Animation).  Every caller (the ``post_save``
handlers, cascade requeue, project cancel-all) goes through these
``dispatch_*`` functions so no True return is dropped.

Image assembly is deferred with ``transaction.on_commit``: it deletes the
tile files, so it must only run once the ASSEMBLING claim and the child
writes that led to it are committed.  Under autocommit (no outer
transaction) the callback runs immediately.  The callbacks are
``robust``: an assembly error is logged by Django instead of escaping
into a caller whose write has already committed.
"""

import logging
import threading

from django.db import transaction

from .constants import TilingConfiguration
from .models import Animation, AnimationFrame, AnimationFrameStatus
from .parent_status import (
    refresh_animation_frame,
    refresh_tiled_animation,
    refresh_tiled_job,
    refresh_untiled_animation,
)

logger = logging.getLogger(__name__)


def _trigger_video_assembly(animation):
    """
    Atomic CAS trigger for video assembly after animation completion.

    Uses filter(pk=..., video_status='PENDING').update() to ensure
    exactly one concurrent handler wins the race to spawn the assembly
    thread.

    Short-circuits when ``animation.video_settings is None`` (per spec
    FR §141, wizard-ffmpeg-rewrite).  Race-impossibility argument:
    animations created during the FFmpeg loading window have
    ``video_settings = None`` (the admin UI grey-out and the create
    serializer's ``validate_video_settings`` guard block that path),
    AND ``video_settings`` is immutable after creation (the update
    serializer rejects any change with
    ``code="video_settings_immutable"``).  An animation can therefore
    never enter the assembly path with FFmpeg not-yet-ready.

    NOTE: this handler MUST NOT call ``instance.save()`` — issue #10
    in development/code_review_findings.md is the recurring footgun.
    All state writes use ``Animation.objects.filter(pk=...).update(...)``.
    """
    from .video_assembler import assemble_animation_video

    if animation.video_settings is None:
        return

    with transaction.atomic():
        updated = Animation.objects.filter(
            pk=animation.pk,
            video_status='PENDING',
        ).update(video_status='ASSEMBLING')
        if updated == 1:
            transaction.on_commit(
                lambda aid=animation.pk: threading.Thread(
                    target=assemble_animation_video,
                    args=(aid,),
                    daemon=True,
                ).start()
            )


def _trigger_video_for(animation_id):
    """Fetch the animation and run the video-assembly CAS trigger."""
    animation = Animation.objects.filter(pk=animation_id).first()
    if animation is not None:
        _trigger_video_assembly(animation)


def dispatch_tiled_job(tiled_job_id):
    """Refresh a TiledJob; schedule assembly when it was claimed."""
    from .image_assembler import assemble_tiled_job_image

    if refresh_tiled_job(tiled_job_id):
        logger.info(f"All tiles for TiledJob {tiled_job_id} are complete. Scheduling assembly.")
        transaction.on_commit(
            lambda pk=tiled_job_id: assemble_tiled_job_image(pk), robust=True,
        )


def dispatch_untiled_animation(animation_id):
    """Refresh a non-tiled Animation; trigger video when it is DONE."""
    if refresh_untiled_animation(animation_id):
        _trigger_video_for(animation_id)


def dispatch_tiled_animation(animation_id):
    """Refresh a tiled Animation from its frames; video when DONE."""
    if refresh_tiled_animation(animation_id):
        logger.info(f"All frames for Animation {animation_id} are complete.")
        _trigger_video_for(animation_id)


def dispatch_frame_only(frame_id):
    """Refresh one frame; schedule its assembly when claimed.

    Returns True when assembly was scheduled (the assembler's
    ``frame.save()`` calls then update the animation).
    """
    from .image_assembler import assemble_animation_frame_image

    if not refresh_animation_frame(frame_id):
        return False
    logger.info(f"All tiles for AnimationFrame {frame_id} are complete. Scheduling assembly.")
    transaction.on_commit(
        lambda pk=frame_id: assemble_animation_frame_image(pk), robust=True,
    )
    return True


def dispatch_animation_frame(frame_id):
    """Refresh a frame, then its animation unless assembly was scheduled."""
    if dispatch_frame_only(frame_id):
        return
    animation_id = AnimationFrame.objects.filter(
        pk=frame_id,
    ).values_list('animation_id', flat=True).first()
    if animation_id is not None:
        dispatch_tiled_animation(animation_id)


def dispatch_for_job(job):
    """Recompute the parent of a saved child ``Job``."""
    if job.animation_frame_id:
        dispatch_animation_frame(job.animation_frame_id)
    elif job.animation_id:
        if job.animation.tiling_config == TilingConfiguration.NONE:
            dispatch_untiled_animation(job.animation_id)
    elif job.tiled_job_id:
        dispatch_tiled_job(job.tiled_job_id)


def dispatch_animation(animation_id):
    """Refresh a whole animation, every unfinished frame included.

    Used after cascade requeue, whose ``QuerySet.update()`` bypasses
    ``post_save``.  Frames are refreshed in pk order (stable lock order),
    then the animation once.
    """
    animation = Animation.objects.filter(pk=animation_id).first()
    if animation is None:
        return
    if animation.tiling_config == TilingConfiguration.NONE:
        dispatch_untiled_animation(animation_id)
        return
    frame_ids = animation.frames.exclude(
        status__in=[AnimationFrameStatus.DONE, AnimationFrameStatus.ASSEMBLING],
    ).order_by('pk').values_list('pk', flat=True)
    for frame_id in list(frame_ids):
        dispatch_frame_only(frame_id)
    dispatch_tiled_animation(animation_id)


def dispatch_parents(rows):
    """Refresh the parents of a batch of child jobs.

    Args:
        rows: iterable of ``(tiled_job_id, animation_id, animation_frame_id)``
            for the child jobs that changed (e.g. a bulk cancel).

    Each parent is refreshed once, in sorted order: tiled jobs, then
    frames, then animations (frame before animation, as everywhere).
    """
    rows = list(rows)
    tiled_ids = sorted({t for t, _a, _f in rows if t})
    frame_ids = sorted({f for _t, _a, f in rows if f})
    animation_ids = sorted({a for _t, a, _f in rows if a})
    for pk in tiled_ids:
        dispatch_tiled_job(pk)
    for pk in frame_ids:
        dispatch_frame_only(pk)
    for pk in animation_ids:
        dispatch_animation_status(pk)


def dispatch_animation_status(animation_id):
    """Refresh an animation's own status (frames are not refreshed)."""
    tiling = Animation.objects.filter(pk=animation_id).values_list(
        'tiling_config', flat=True,
    ).first()
    if tiling == TilingConfiguration.NONE:
        dispatch_untiled_animation(animation_id)
    elif tiling is not None:
        dispatch_tiled_animation(animation_id)
