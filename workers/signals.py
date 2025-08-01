# workers/signals.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn

import logging
from uuid import uuid4

from django.conf import settings
from django.utils import timezone
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile

from .models import (
    Job,
    AnimationFrame,
    Animation,
    TiledJob,
    JobStatus,
    AnimationFrameStatus,
)
from .constants import TilingConfiguration

logger = logging.getLogger(__name__)

# Opt-in toggle to delete old thumbnails before saving new ones
DELETE_OLD_THUMBS = getattr(settings, "WORKERS_DELETE_OLD_THUMBNAILS", False)


# -----------------------------
# Helpers
# -----------------------------
def _filelike_to_bytes(f):
    """
    Safely convert a file-like or raw bytes to bytes for saving into a FileField.
    """
    if f is None:
        return None
    if isinstance(f, (bytes, bytearray)):
        return bytes(f)
    if hasattr(f, "read"):
        pos = None
        try:
            if hasattr(f, "tell"):
                pos = f.tell()
            if hasattr(f, "seek"):
                f.seek(0)
        except Exception:
            pos = None
        data = f.read()
        try:
            if pos is not None and hasattr(f, "seek"):
                f.seek(pos)
        except Exception:
            pass
        return data
    return None


def _delete_existing_filefield(inst, field_name: str):
    """
    If configured, delete the existing file referenced by the FileField
    WITHOUT saving the model (to avoid recursive signals).
    """
    if not DELETE_OLD_THUMBS:
        return
    try:
        field = getattr(inst, field_name, None)
        if not field:
            return
        name = getattr(field, "name", None)
        if not name:
            return
        storage = field.storage
        try:
            if storage.exists(name):
                storage.delete(name)
        except Exception:
            logger.debug(
                "Non-fatal: could not delete old file for %s.%s",
                inst.__class__.__name__,
                field_name,
            )
    except Exception:
        logger.debug(
            "Non-fatal: error while preparing deletion for %s.%s",
            inst.__class__.__name__,
            field_name,
        )


def _deterministic_thumb_name(inst) -> str:
    """
    Compute a stable thumbnail filename for a model instance.
    The model's upload_to controls directory placement.
    """
    pk = getattr(inst, "pk", None)
    if not pk:
        return "temp.png"

    # For Animation specifically, tests expect the basename "{pk}.png"
    if isinstance(inst, Animation):
        return f"{pk}.png"

    # Simple deterministic name for others as well
    return f"{pk}.png"


def _next_animation_thumb_name(animation: Animation, frame_number: int | None) -> str:
    """
    Return a fixed, deterministic filename for Animation thumbnails.

    Always '{pk}.png' (or 'temp.png' if pk isn't available yet). Deletion of the
    previously referenced file (controlled by WORKERS_DELETE_OLD_THUMBNAILS) ensures
    storage won't generate a unique alternative name.
    """
    pk = getattr(animation, "pk", None)
    return f"{pk}.png" if pk is not None else "temp.png"



def _save_thumbnails_for_instances(
    instances, *, sender, handler, thumb_content, field_name="thumbnail", frame_number: int | None = None
):
    """
    Disconnects the sender/handler to prevent recursion, then saves the same
    thumbnail bytes to each instance's <field_name>. Finally reconnects the handler.

    - Uses deterministic filenames (stable path per instance); animations may be versioned.
    - Optionally deletes the existing file first (if WORKERS_DELETE_OLD_THUMBNAILS=True)
    - Relies on model field's upload_to for the directory structure.
    """
    if thumb_content is None:
        return

    data = _filelike_to_bytes(thumb_content)
    if not data:
        return

    post_save.disconnect(handler, sender=sender)
    try:
        for inst in instances:
            # Choose filename
            if isinstance(inst, Animation):
                file_name = _next_animation_thumb_name(inst, frame_number)
            else:
                file_name = _deterministic_thumb_name(inst)

            # Delete old file if configured
            _delete_existing_filefield(inst, field_name)

            # Pass chosen filename so upload_paths can respect it (esp. for Animation)
            getattr(inst, field_name).save(file_name, ContentFile(data), save=True)
    finally:
        post_save.connect(handler, sender=sender)


# -----------------------------
# Signal handlers
# -----------------------------
@receiver(post_save, sender=Job)
def handle_job_completion(sender, instance, **kwargs):
    """
    - Update parent Animation/TiledJob status on job completion.
    - Trigger thumbnail generation for standard jobs' output files.
    """
    from .image_assembler import assemble_tiled_job_image, assemble_animation_frame_image
    from .image_utils import generate_thumbnail

    # --- Parent Status Update Logic ---
    if instance.animation_frame and instance.status == JobStatus.DONE:
        frame = instance.animation_frame
        animation = frame.animation
        tile_counts = [int(i) for i in animation.tiling_config.split("x")]
        total_tiles_for_frame = tile_counts[0] * tile_counts[1]
        completed_tiles = frame.tile_jobs.filter(status=JobStatus.DONE).count()
        if completed_tiles >= total_tiles_for_frame:
            logger.info(
                f"All {total_tiles_for_frame} tiles for {frame} are complete. Triggering assembly."
            )
            assemble_animation_frame_image(frame.id)

    elif instance.animation and instance.animation.tiling_config == TilingConfiguration.NONE:
        animation = instance.animation
        all_jobs = animation.jobs.all()
        total_jobs_count = all_jobs.count()
        if total_jobs_count > 0:
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

            update_fields = {"total_render_time_seconds": total_time, "status": new_status}
            if animation_completed and not animation.completed_at:
                update_fields["completed_at"] = timezone.now()

            Animation.objects.filter(pk=animation.pk).update(**update_fields)

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
    if instance.output_file and not instance.thumbnail:
        if not instance.animation_frame and not instance.tiled_job:
            logger.debug(f"Job {instance.id} has an output file. Generating thumbnail.")
            thumb_content = generate_thumbnail(instance.output_file)
            if thumb_content:
                targets = [instance]
                # If this job belongs to an (untiled) animation, also update the animation thumbnail once.
                if instance.animation:
                    targets.append(instance.animation)
                _save_thumbnails_for_instances(
                    targets,
                    sender=Job,
                    handler=handle_job_completion,
                    thumb_content=thumb_content,
                    # frame_number intentionally omitted here; this is a one-off update
                )


@receiver(post_save, sender=AnimationFrame)
def handle_animation_frame_completion(sender, instance, **kwargs):
    """
    - Update parent Animation status when frames finish (QUEUED -> RENDERING).
    - When all frames are DONE, mark Animation DONE, set total time and completed_at.
    - Generate thumbnails for frame and ALWAYS refresh the parent animation thumbnail
      to provide a visual progress indicator during rendering.
    """
    from .image_utils import generate_thumbnail

    animation = instance.animation

    # --- Thumbnail Generation (progress thumbnails) ---
    if instance.output_file:
        # The image_assembler is responsible for creating the AnimationFrame's
        # own thumbnail. This signal's job is to always update the PARENT
        # animation's thumbnail to show the latest progress.
        thumb_content = generate_thumbnail(instance.output_file)

        if thumb_content:
            _save_thumbnails_for_instances(
                [animation],
                sender=AnimationFrame,
                handler=handle_animation_frame_completion,
                thumb_content=thumb_content,
                frame_number=instance.frame_number,
            )

    # --- Parent Animation Status Update ---
    if instance.status == AnimationFrameStatus.DONE and animation.status == JobStatus.QUEUED:
        animation.status = JobStatus.RENDERING
        animation.save(update_fields=["status"])

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
        animation.status = JobStatus.DONE
        animation.completed_at = timezone.now()
        animation.total_render_time_seconds = total_time
        animation.save(
            update_fields=["status", "completed_at", "total_render_time_seconds"]
        )