# FILENAME: workers/image_utils.py
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
# Created by Mario Estrella on 08/01/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# workers/image_utils.py
"""
A collection of utility functions for image processing tasks.

This module provides helpers for generating thumbnails from source images
using the Pillow library. It is used by the manager to create previews
for the UI.
"""
import logging
import io
from pathlib import Path

from PIL import Image
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

THUMBNAIL_WIDTH = 256


def generate_thumbnail(source_file_field):
    """
    Generates a thumbnail with a fixed width, preserving the aspect ratio.

    This function opens an image, resizes it to a fixed width defined by
    THUMBNAIL_WIDTH, and calculates the height proportionally. It returns
    the result as a Django ContentFile, ready to be saved to an ImageField.

    Args:
        source_file_field (django.db.models.fields.files.FieldFile): The source
            image file field from a Django model instance (e.g., job.output_file).

    Returns:
        ContentFile or None: A new ContentFile containing the PNG thumbnail data,
                             or None if the source image cannot be processed.
    """
    if not source_file_field:
        return None

    try:
        # Open the source image using a context manager
        with source_file_field.open(mode='rb') as f:
            img = Image.open(f)

            # Calculate the new height to maintain aspect ratio
            width, height = img.size
            if width == 0:
                return None  # Avoid division by zero

            aspect_ratio = height / width
            new_height = int(THUMBNAIL_WIDTH * aspect_ratio)

            # Resize using a high-quality downsampling filter
            img = img.resize((THUMBNAIL_WIDTH, new_height), Image.Resampling.LANCZOS)

            # Save the thumbnail to an in-memory buffer
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)

            # Create a Django-compatible file object
            file_name = f"thumb_{Path(source_file_field.name).stem}.png"
            return ContentFile(buffer.getvalue(), name=file_name)

    except Exception as e:
        logger.error(f"Failed to generate thumbnail for {source_file_field.name}: {e}", exc_info=True)
        return None