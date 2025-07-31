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
#
# Created by Mario Estrella on 07/28/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# workers/image_assembler.py
"""
Utility functions for assembling tiled render outputs.

This module contains the logic for stitching individual render tiles back together
into a single, high-resolution image using the Pillow library. It is designed
to be called by Django signals upon completion of all child render jobs.
"""

import logging
import re
import io
from django.utils import timezone
from django.core.files.base import ContentFile
from django.db.models import Sum
from PIL import Image
from .models import TiledJob, TiledJobStatus, JobStatus, AnimationFrame, AnimationFrameStatus
from .constants import RenderSettings

logger = logging.getLogger(__name__)
TILE_COORD_REGEX = re.compile(r"_Tile_(\d+)_(\d+)$")


def assemble_animation_frame_image(animation_frame_id):
    """
    Assembles the completed tiles for a single frame of a tiled animation.

    This function is triggered by a signal when all child tile jobs for a given
    `AnimationFrame` are marked as `DONE`. It fetches the individual tile images,
    stitches them together based on their coordinates, and saves the final
    assembled image to the `AnimationFrame` model's `output_file` field.

    Args:
        animation_frame_id (int): The primary key of the `AnimationFrame` to assemble.
    """
    try:
        frame = AnimationFrame.objects.get(id=animation_frame_id)
    except AnimationFrame.DoesNotExist:
        logger.error(f"Cannot assemble image: AnimationFrame with ID {animation_frame_id} not found.")
        return

    logger.info(f"Starting image assembly for {frame}.")
    frame.status = AnimationFrameStatus.ASSEMBLING
    frame.save(update_fields=['status'])

    completed_jobs = frame.tile_jobs.filter(status=JobStatus.DONE).order_by('name')

    try:
        # Determine final resolution from a child job's settings
        first_job = completed_jobs.first()
        if not first_job or not first_job.render_settings:
            raise ValueError("Cannot determine final resolution; first tile job has no render settings.")

        final_resolution_x = first_job.render_settings.get(RenderSettings.RESOLUTION_X)
        final_resolution_y = first_job.render_settings.get(RenderSettings.RESOLUTION_Y)

        if not final_resolution_x or not final_resolution_y:
            raise ValueError("Child job render settings are missing resolution data.")

        final_image = Image.new('RGBA', (final_resolution_x, final_resolution_y))

        tile_counts = [int(i) for i in frame.animation.tiling_config.split('x')]
        tile_count_x, tile_count_y = tile_counts[0], tile_counts[1]
        tile_pixel_width = final_resolution_x // tile_count_x
        tile_pixel_height = final_resolution_y // tile_count_y

        for job in completed_jobs:
            match = TILE_COORD_REGEX.search(job.name)
            if not match:
                logger.warning(f"Could not parse tile coordinates from job name '{job.name}'. Skipping.")
                continue

            tile_y, tile_x = map(int, match.groups())
            paste_x = tile_x * tile_pixel_width
            paste_y = (tile_count_y - 1 - tile_y) * tile_pixel_height

            with Image.open(job.output_file.path) as tile_image:
                final_image.paste(tile_image, (paste_x, paste_y))

        buffer = io.BytesIO()
        final_image.save(buffer, format='PNG')
        buffer.seek(0)

        file_name = f"anim_{frame.animation.id}_frame_{frame.frame_number:04d}.png"
        content_file = ContentFile(buffer.getvalue(), name=file_name)

        frame.output_file.save(file_name, content_file, save=False)
        frame.status = AnimationFrameStatus.DONE

        # Aggregate render time from child jobs
        time_aggregate = completed_jobs.aggregate(total=Sum('render_time_seconds'))
        frame.render_time_seconds = time_aggregate['total'] or 0

        frame.save()
        logger.info(f"Successfully assembled and saved final image for {frame}.")

    except Exception as e:
        logger.critical(f"A critical error occurred during image assembly for {frame}: {e}", exc_info=True)
        frame.status = AnimationFrameStatus.ERROR
        frame.save(update_fields=['status'])


def assemble_tiled_job_image(tiled_job_id):
    """
    Assembles the completed tiles for a single, high-resolution `TiledJob`.

    This function is triggered by a signal when all child `Job`s for a given
    `TiledJob` are marked as `DONE`. It fetches the individual tile images,
    stitches them together based on their coordinates, and saves the final
    assembled image to the `TiledJob` model's `output_file` field.

    Args:
        tiled_job_id (UUID): The primary key of the `TiledJob` to assemble.
    """
    try:
        tiled_job = TiledJob.objects.get(id=tiled_job_id)
    except TiledJob.DoesNotExist:
        logger.error(f"Cannot assemble image: TiledJob with ID {tiled_job_id} not found.")
        return

    logger.info(f"Starting image assembly for TiledJob '{tiled_job.name}' (ID: {tiled_job.id}).")
    tiled_job.status = TiledJobStatus.ASSEMBLING
    tiled_job.save(update_fields=['status'])

    completed_jobs = tiled_job.jobs.filter(status=JobStatus.DONE).order_by('name')

    try:
        final_image = Image.new('RGBA', (tiled_job.final_resolution_x, tiled_job.final_resolution_y))

        tile_pixel_width = tiled_job.final_resolution_x // tiled_job.tile_count_x
        tile_pixel_height = tiled_job.final_resolution_y // tiled_job.tile_count_y

        for job in completed_jobs:
            match = TILE_COORD_REGEX.search(job.name)
            if not match:
                logger.warning(f"Could not parse tile coordinates from job name '{job.name}'. Skipping.")
                continue

            tile_y, tile_x = map(int, match.groups())

            # Calculate paste coordinates, inverting Y for Pillow's top-left origin.
            paste_x = tile_x * tile_pixel_width
            paste_y = (tiled_job.tile_count_y - 1 - tile_y) * tile_pixel_height

            logger.debug(f"Pasting tile {tile_y}_{tile_x} from job '{job.name}' at position ({paste_x}, {paste_y}).")

            with Image.open(job.output_file.path) as tile_image:
                final_image.paste(tile_image, (paste_x, paste_y))

        # Save the final image to an in-memory buffer
        buffer = io.BytesIO()
        final_image.save(buffer, format='PNG')
        buffer.seek(0)

        # Create a Django ContentFile and save it to the model's FileField
        file_name = f"tiled_job_{tiled_job.id}_final.png"
        content_file = ContentFile(buffer.getvalue(), name=file_name)

        tiled_job.output_file.save(file_name, content_file, save=False)
        tiled_job.status = TiledJobStatus.DONE
        tiled_job.completed_at = timezone.now()
        tiled_job.save()

        logger.info(f"Successfully assembled and saved final image for TiledJob ID {tiled_job.id}.")

    except Exception as e:
        logger.critical(f"A critical error occurred during image assembly for TiledJob ID {tiled_job.id}: {e}",
                        exc_info=True)
        tiled_job.status = TiledJobStatus.ERROR
        tiled_job.save(update_fields=['status'])