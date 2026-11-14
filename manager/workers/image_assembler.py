# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Utility functions for assembling tiled render outputs.

This module contains the logic for stitching individual render tiles back together
into a single, high-resolution image using the Pillow library. It is designed
to be called (via ``transaction.on_commit``, see ``parent_dispatch``) once all
child render jobs are DONE.

Only stitching and writing the output file count as assembly: if they raise,
the parent goes to ERROR with a reason (issue #219).  Saving the status, the
``post_save`` parent refresh it fires, the thumbnail and the tile cleanup are
outside that ``try`` so a failure there never turns a finished image into an
assembly ERROR.
"""

import logging
import re
import io
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db import DatabaseError
from django.db.models import Sum
from PIL import Image
from .models import Job, TiledJob, TiledJobStatus, JobStatus, AnimationFrame, AnimationFrameStatus
from .constants import FORMAT_EXTENSIONS, PILLOW_FORMAT_NAMES, RenderSettings
from .image_utils import generate_thumbnail
from .parent_status_rules import assembly_failure_reason

logger = logging.getLogger(__name__)
TILE_COORD_REGEX = re.compile(r"_Tile_(\d+)_(\d+)$")


def _get_format_config(render_settings):
    """
    Extract Pillow format configuration from render_settings.

    Returns a tuple of (pillow_format, file_ext, image_mode, save_kwargs).
    """
    output_format = render_settings.get(
        RenderSettings.IMAGE_FILE_FORMAT, 'PNG'
    )
    pillow_format = PILLOW_FORMAT_NAMES.get(output_format, 'PNG')
    file_ext = FORMAT_EXTENSIONS.get(output_format, '.png')
    image_mode = 'RGB' if output_format in ('JPEG', 'BMP') else 'RGBA'

    save_kwargs = {}
    if output_format == 'JPEG':
        quality = render_settings.get(RenderSettings.IMAGE_QUALITY, 90)
        save_kwargs['quality'] = max(1, min(100, quality))

    return pillow_format, file_ext, image_mode, save_kwargs


def _stitch_tiles(jobs, size, tile_counts, render_settings):
    """Paste each tile job's output into one image.

    Args:
        jobs: DONE tile jobs (names end in ``_Tile_<y>_<x>``).
        size: ``(width, height)`` of the final image.
        tile_counts: ``(tile_count_x, tile_count_y)``.
        render_settings: parent render settings (output format).

    Returns:
        ``(image_bytes, file_ext)``.
    """
    pillow_format, file_ext, image_mode, save_kwargs = _get_format_config(render_settings)
    final_image = Image.new(image_mode, size)
    tile_width = size[0] // tile_counts[0]
    tile_height = size[1] // tile_counts[1]

    for job in jobs:
        match = TILE_COORD_REGEX.search(job.name)
        if not match:
            logger.warning(f"Could not parse tile coordinates from job name '{job.name}'. Skipping.")
            continue
        tile_y, tile_x = map(int, match.groups())
        paste_xy = (tile_x * tile_width, (tile_counts[1] - 1 - tile_y) * tile_height)
        with Image.open(job.output_file.path) as tile_image:
            if image_mode == 'RGB':
                tile_image = tile_image.convert('RGB')
            final_image.paste(tile_image, paste_xy)

    buffer = io.BytesIO()
    final_image.save(buffer, format=pillow_format, **save_kwargs)
    return buffer.getvalue(), file_ext


def _save_status(obj, update_fields=None):
    """Save *obj*; a failing ``post_save`` parent refresh is only logged.

    ``save()`` writes the row first and then runs ``post_save`` (which, for
    an AnimationFrame, refreshes the parent Animation).  If the error came
    from that refresh, the row already holds the new status: log it and
    carry on (the parent catches up on the next child change, or a cascade
    requeue).  If the row itself was not written, re-raise.
    """
    try:
        obj.save(update_fields=update_fields)
    except DatabaseError:
        if not type(obj).objects.filter(pk=obj.pk, status=obj.status).exists():
            raise
        logger.exception(
            "%s saved as %s, but the post_save parent refresh failed.", obj, obj.status,
        )


def _thumbnail_and_cleanup(obj, completed_jobs, label):
    """Generate the thumbnail and delete tile files; failures only logged."""
    try:
        if obj.output_file and not obj.thumbnail:
            thumb_content = generate_thumbnail(obj.output_file)
            if thumb_content:
                obj.thumbnail.save(thumb_content.name, thumb_content, save=False)
                _save_status(obj, ['thumbnail'])
        logger.info(f"Successfully assembled and saved final image and thumbnail for {label}.")

        logger.info(f"Cleaning up {completed_jobs.count()} tile files for {label}.")
        job_ids_to_clear = []
        for job in completed_jobs:
            if job.output_file:
                job_ids_to_clear.append(job.id)
                job.output_file.delete(save=False)
        if job_ids_to_clear:
            Job.objects.filter(id__in=job_ids_to_clear).update(output_file=None)
        logger.info(f"Cleanup complete for {label}.")
    except Exception:
        logger.exception(f"Thumbnail or tile cleanup failed for {label}; status unchanged.")


def _frame_resolution(completed_jobs):
    """Final frame resolution, read from a child tile job's settings."""
    first_job = completed_jobs.first()
    if not first_job or not first_job.render_settings:
        raise ValueError("Cannot determine final resolution; first tile job has no render settings.")
    resolution_x = first_job.render_settings.get(RenderSettings.RESOLUTION_X)
    resolution_y = first_job.render_settings.get(RenderSettings.RESOLUTION_Y)
    if not resolution_x or not resolution_y:
        raise ValueError("Child job render settings are missing resolution data.")
    return resolution_x, resolution_y


def assemble_animation_frame_image(animation_frame_id):
    """
    Assembles completed tiles for a single animation frame and cleans up the tile files.

    This function is triggered when all child tile jobs for an `AnimationFrame`
    are done. It stitches the tiles, saves the final image, generates its
    thumbnail, and then deletes the individual source tile image files.
    """
    try:
        frame = AnimationFrame.objects.select_related('animation').get(id=animation_frame_id)
    except AnimationFrame.DoesNotExist:
        logger.error(f"Cannot assemble image: AnimationFrame with ID {animation_frame_id} not found.")
        return

    logger.info(f"Starting image assembly for {frame}.")
    frame.status = AnimationFrameStatus.ASSEMBLING
    _save_status(frame, ['status'])

    completed_jobs = frame.tile_jobs.filter(status=JobStatus.DONE).order_by('name')
    animation = frame.animation

    try:
        image_bytes, file_ext = _stitch_tiles(
            completed_jobs, _frame_resolution(completed_jobs),
            animation.get_tile_counts(), animation.render_settings,
        )
        file_name = f"anim_{animation.id}_frame_{frame.frame_number:04d}{file_ext}"
        frame.output_file.save(file_name, ContentFile(image_bytes, name=file_name), save=False)
    except Exception as e:
        logger.critical(f"A critical error occurred during image assembly for {frame}: {e}", exc_info=True)
        frame.status = AnimationFrameStatus.ERROR
        frame.error_message = assembly_failure_reason('Frame', e)
        # save() (not update()) so handle_animation_frame_completion runs
        # and the parent Animation can move to ERROR (issue #219).
        _save_status(frame, ['status', 'error_message'])
        return

    frame.status = AnimationFrameStatus.DONE
    frame.error_message = ''
    frame.render_time_seconds = completed_jobs.aggregate(total=Sum('render_time_seconds'))['total'] or 0
    # Explicit fields (#225): *frame* was read before the stitching, so a
    # full-row save would overwrite edits made in the meantime.
    _save_status(frame, ['status', 'error_message', 'render_time_seconds', 'output_file'])
    _thumbnail_and_cleanup(frame, completed_jobs, str(frame))


def assemble_tiled_job_image(tiled_job_id):
    """
    Assembles completed tiles for a TiledJob and cleans up the tile files.

    This function is triggered when all child `Job`s are done. It assembles the
    image, saves it, generates a thumbnail, and then deletes the individual
    source tile image files from storage.
    """
    try:
        tiled_job = TiledJob.objects.get(id=tiled_job_id)
    except TiledJob.DoesNotExist:
        logger.error(f"Cannot assemble image: TiledJob with ID {tiled_job_id} not found.")
        return

    logger.info(f"Starting image assembly for TiledJob '{tiled_job.name}' (ID: {tiled_job.id}).")
    tiled_job.status = TiledJobStatus.ASSEMBLING
    _save_status(tiled_job, ['status'])

    completed_jobs = tiled_job.jobs.filter(status=JobStatus.DONE).order_by('name')

    try:
        image_bytes, file_ext = _stitch_tiles(
            completed_jobs,
            (tiled_job.final_resolution_x, tiled_job.final_resolution_y),
            (tiled_job.tile_count_x, tiled_job.tile_count_y),
            tiled_job.render_settings,
        )
        file_name = f"tiled_job_{str(tiled_job.id)[:8]}_final{file_ext}"
        tiled_job.output_file.save(file_name, ContentFile(image_bytes, name=file_name), save=False)
    except Exception as e:
        logger.critical(f"A critical error occurred during image assembly for TiledJob ID {tiled_job.id}: {e}",
                        exc_info=True)
        tiled_job.status = TiledJobStatus.ERROR
        tiled_job.error_message = assembly_failure_reason('Tile', e)
        tiled_job.completed_at = timezone.now()
        _save_status(tiled_job, ['status', 'error_message', 'completed_at'])
        return

    tiled_job.status = TiledJobStatus.DONE
    tiled_job.error_message = ''
    tiled_job.completed_at = timezone.now()
    # Explicit fields (#225): see assemble_animation_frame_image.
    _save_status(tiled_job, ['status', 'error_message', 'completed_at', 'output_file'])
    _thumbnail_and_cleanup(tiled_job, completed_jobs, f"TiledJob {tiled_job.id}")
