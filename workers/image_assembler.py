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

import logging
import re
import io
from django.utils import timezone
from django.core.files.base import ContentFile
from PIL import Image
from .models import TiledJob, TiledJobStatus, JobStatus

logger = logging.getLogger(__name__)
TILE_COORD_REGEX = re.compile(r"_Tile_(\d+)_(\d+)$")


def assemble_tiled_job_image(tiled_job_id):
    """
    Finds all completed tile jobs for a TiledJob, stitches them
    together into a final image, and saves it.
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

            # ** THIS IS THE FIX **
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