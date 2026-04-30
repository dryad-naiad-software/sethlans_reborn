# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import threading

from django.db import transaction
from django.utils import timezone
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    Job,
    AnimationFrame,
    Animation,
    TiledJob,
    JobStatus,
    AnimationFrameStatus,
    Project,
    Asset,  # Import Asset
)
from .constants import TilingConfiguration
from .manifest_generator import update_project_manifest
from .signal_helpers import _skip_thumbnails

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


@receiver(post_save, sender=Project)
@receiver(post_save, sender=Asset)  # FIX: Added Asset as a sender
@receiver(post_save, sender=Job)
@receiver(post_save, sender=Animation)
@receiver(post_save, sender=TiledJob)
def handle_manifest_update(sender, instance, created, **kwargs):
    """
    Signal handler to create or update the project manifest file.

    This function is triggered whenever a Project is created or a new Job of any
    type (Job, Animation, TiledJob) is added to a project. It calls a utility
    to regenerate the `manifest.txt` file, ensuring it stays up-to-date.

    Args:
        sender (Model): The model class that sent the signal.
        instance (Model): The actual instance being saved.
        created (bool): True if a new record was created.
        **kwargs: Additional keyword arguments.
    """
    if not created:
        return

    project_id = None
    if sender is Project:
        project_id = instance.id
    elif hasattr(instance, 'project') and instance.project:
        project_id = instance.project.id
    elif hasattr(instance, 'asset') and instance.asset and instance.asset.project:
        project_id = instance.asset.project.id

    if project_id:
        logger.debug(f"Signal received from {sender.__name__} for project {project_id}. Updating manifest.")
        transaction.on_commit(lambda pid=project_id: update_project_manifest(pid))
    else:
        logger.warning(f"Could not determine project ID from saved {sender.__name__} instance. Manifest not updated.")


def _handle_untiled_animation_progress(animation):
    """Update status and trigger video assembly for non-tiled animations."""
    all_jobs = animation.jobs.all()
    total_jobs_count = all_jobs.count()
    if total_jobs_count == 0:
        return

    time_aggregate = all_jobs.filter(status=JobStatus.DONE).aggregate(
        total=Sum("render_time_seconds")
    )
    total_time = time_aggregate["total"] or 0
    finished_jobs_count = all_jobs.filter(
        status__in=[JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED]
    ).count()
    animation_completed = total_jobs_count == finished_jobs_count

    current_status = animation.status
    new_status = current_status
    if current_status == JobStatus.QUEUED and finished_jobs_count > 0:
        new_status = JobStatus.RENDERING
    if animation_completed:
        new_status = JobStatus.DONE

    update_fields = {
        "total_render_time_seconds": total_time,
        "status": new_status,
    }
    if animation_completed and not animation.completed_at:
        update_fields["completed_at"] = timezone.now()

    Animation.objects.filter(pk=animation.pk).update(**update_fields)

    if animation_completed:
        animation.refresh_from_db()
        _trigger_video_assembly(animation)


@receiver(post_save, sender=Job)
def handle_job_completion(sender, instance, **kwargs):
    """
    - Update parent Animation/TiledJob status on job completion.
    - Trigger thumbnail generation for standard jobs' output files.
    """
    # Phase 4: early-return when the current thread is inside a
    # ``_skip_thumbnail_signals()`` context.  This replaces the pre-Phase-4
    # ``post_save.disconnect(handler, sender=Job)`` dance that silently
    # suppressed signals across ALL threads.  The guard keeps the inner
    # thumbnail-write saves from re-entering this handler without
    # affecting concurrent Job saves on other threads.
    if _skip_thumbnails():
        return

    from .image_assembler import assemble_tiled_job_image, assemble_animation_frame_image
    from .image_utils import generate_thumbnail

    # --- Parent Status Update Logic ---
    if instance.animation_frame and instance.status == JobStatus.DONE:
        frame = instance.animation_frame
        animation = frame.animation
        tile_count_x, tile_count_y = animation.get_tile_counts()
        total_tiles_for_frame = tile_count_x * tile_count_y
        completed_tiles = frame.tile_jobs.filter(status=JobStatus.DONE).count()
        if completed_tiles >= total_tiles_for_frame:
            logger.info(
                f"All {total_tiles_for_frame} tiles for {frame} are complete. Triggering assembly."
            )
            assemble_animation_frame_image(frame.id)

    elif instance.animation and instance.animation.tiling_config == TilingConfiguration.NONE:
        _handle_untiled_animation_progress(instance.animation)

    elif instance.tiled_job:
        tiled_job = instance.tiled_job
        all_tile_jobs = tiled_job.jobs.all()
        total_tiles = tiled_job.tile_count_x * tiled_job.tile_count_y
        time_aggregate = all_tile_jobs.filter(status=JobStatus.DONE).aggregate(
            total=Sum("render_time_seconds")
        )
        total_time = time_aggregate["total"] or 0
        completed_tiles = all_tile_jobs.filter(status=JobStatus.DONE).count()

        # set to RENDERING while tiles are progressing
        TiledJob.objects.filter(pk=tiled_job.pk).update(
            total_render_time_seconds=total_time, status=JobStatus.RENDERING
        )

        if completed_tiles == total_tiles:
            logger.info(
                f"All {total_tiles} tiles for TiledJob {tiled_job.id} are complete. Triggering assembly."
            )
            assemble_tiled_job_image(tiled_job.id)

    # --- Thumbnail Generation for Standard (non-frame, non-tiled) Jobs ---
    _maybe_generate_job_thumbnail(instance, generate_thumbnail)


def _maybe_generate_job_thumbnail(instance, generate_thumbnail):
    """Schedule a server-side thumbnail generation for a standard Job.

    Issue #118: the actual generate + save pair runs in a
    ``transaction.on_commit`` callback so the outer ``output_file.save``
    write lock on ``workers_job`` is released before Pillow resize + inner
    ``thumbnail.save`` run.  Implementation lives in
    ``thumbnail_signals.schedule_job_thumbnail``.
    """
    from .thumbnail_signals import schedule_job_thumbnail
    schedule_job_thumbnail(
        instance, generate_thumbnail, handle_job_completion,
    )


@receiver(post_save, sender=AnimationFrame)
def handle_animation_frame_completion(sender, instance, **kwargs):
    """
    - Update parent Animation status when frames finish (QUEUED -> RENDERING).
    - When all frames are DONE, mark Animation DONE, set total time and completed_at.
    - Generate thumbnails for frame and ALWAYS refresh the parent animation thumbnail
      to provide a visual progress indicator during rendering.
    """
    # Phase 4: per-thread skip guard (see ``handle_job_completion``).
    if _skip_thumbnails():
        return

    from .image_utils import generate_thumbnail
    from .thumbnail_signals import schedule_animation_frame_thumbnail

    animation = instance.animation

    # --- Thumbnail Generation (progress thumbnails) ---
    # The image_assembler is responsible for creating the AnimationFrame's
    # own thumbnail.  This signal's job is to always update the PARENT
    # animation's thumbnail to show the latest progress.  Issue #118:
    # generation is deferred via ``transaction.on_commit``.
    schedule_animation_frame_thumbnail(
        instance, generate_thumbnail, handle_animation_frame_completion,
    )

    # --- Parent Animation Status Update ---
    if instance.status == AnimationFrameStatus.DONE and animation.status == JobStatus.QUEUED:
        Animation.objects.filter(pk=animation.pk).update(status=JobStatus.RENDERING)

    expected_frames_count = len(
        range(animation.start_frame, animation.end_frame + 1, animation.frame_step)
    )
    completed_frames = animation.frames.filter(status=AnimationFrameStatus.DONE)
    completed_frames_count = completed_frames.count()

    if expected_frames_count > 0 and completed_frames_count == expected_frames_count:
        logger.info(
            f"All {expected_frames_count} frames for Animation '{animation.name}' are complete."
        )
        time_aggregate = completed_frames.aggregate(total=Sum("render_time_seconds"))
        total_time = time_aggregate["total"] or 0
        Animation.objects.filter(pk=animation.pk).update(
            status=JobStatus.DONE,
            completed_at=timezone.now(),
            total_render_time_seconds=total_time,
        )

        animation.refresh_from_db()
        _trigger_video_assembly(animation)
