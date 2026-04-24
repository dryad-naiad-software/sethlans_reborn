# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Deferred thumbnail generation helpers.

Issue #118: thumbnail generation is scheduled via ``transaction.on_commit``
so it runs *after* the outer ``output_file.save`` transaction commits and
releases the ``workers_job`` write lock.  Doing the Pillow resize + inner
``thumbnail.save`` inside that lock serialises concurrent uploads and can
trigger ``database table is locked`` on SQLite even with a 30 s
``busy_timeout``.

``transaction.on_commit`` runs its callback immediately when no
transaction is active (Django semantics), so callers outside the upload
path — e.g. tile assembly triggered from worker threads — are
unaffected.
"""

import logging

from django.db import transaction

from .signal_helpers import _save_thumbnails_for_instances

logger = logging.getLogger(__name__)


def schedule_job_thumbnail(instance, generate_thumbnail, handler):
    """Schedule a server-side thumbnail generation for a standard Job.

    Parameters
    ----------
    instance : Job
        The job whose output file should be thumbnailed.  Must not be a
        tile job or animation frame and must have ``output_file`` set.
    generate_thumbnail : callable
        The ``image_utils.generate_thumbnail`` function (injected so
        tests can stub it out without monkey-patching).
    handler : callable
        The ``post_save`` handler, passed through to
        ``_save_thumbnails_for_instances`` for backward compatibility.
    """
    if (
        instance.animation_frame
        or instance.tiled_job
        or not instance.output_file
    ):
        return
    if instance.thumbnail:
        logger.debug(
            "Job %s already has a worker-provided thumbnail. "
            "Skipping server-side generation.",
            instance.id,
        )
        return

    # Snapshot identity *before* the callback — the ``instance`` reference
    # stays alive via the closure, but we re-fetch inside the callback to
    # avoid acting on stale field state.
    job_pk = instance.pk

    def _run_after_commit():
        from .models import Job
        try:
            fresh = Job.objects.get(pk=job_pk)
        except Job.DoesNotExist:
            return
        if not fresh.output_file or fresh.thumbnail:
            return
        logger.debug(
            "Job %s post-commit: generating server-side thumbnail.",
            job_pk,
        )
        thumb_content = generate_thumbnail(fresh.output_file)
        if not thumb_content:
            return
        targets = [fresh]
        # If this job belongs to an (untiled) animation, also update the
        # animation thumbnail once.
        if fresh.animation:
            targets.append(fresh.animation)
        _save_thumbnails_for_instances(
            targets,
            sender=Job,
            handler=handler,
            thumb_content=thumb_content,
        )

    transaction.on_commit(_run_after_commit)


def schedule_animation_frame_thumbnail(
    instance, generate_thumbnail, handler,
):
    """Schedule a post-commit refresh of the parent animation thumbnail.

    The image_assembler creates the ``AnimationFrame``'s own thumbnail;
    this helper only updates the *parent animation* thumbnail so the UI
    shows render progress.
    """
    if not instance.output_file:
        return

    frame_pk = instance.pk
    animation_pk = instance.animation.pk

    def _run_after_commit():
        from .models import AnimationFrame, Animation
        try:
            fresh_frame = AnimationFrame.objects.get(pk=frame_pk)
            fresh_anim = Animation.objects.get(pk=animation_pk)
        except (AnimationFrame.DoesNotExist, Animation.DoesNotExist):
            return
        if not fresh_frame.output_file:
            return
        thumb_content = generate_thumbnail(fresh_frame.output_file)
        if not thumb_content:
            return
        _save_thumbnails_for_instances(
            [fresh_anim],
            sender=AnimationFrame,
            handler=handler,
            thumb_content=thumb_content,
        )

    transaction.on_commit(_run_after_commit)
