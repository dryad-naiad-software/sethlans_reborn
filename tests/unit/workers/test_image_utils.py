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
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-137, USA.
#
#
# Created by Mario Estrella on 08/01/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#
# tests/unit/workers/test_image_utils.py
"""
Unit tests for the image processing utilities.
"""
import io
import pytest
from PIL import Image
from unittest.mock import MagicMock

# Module to be tested
from workers.image_utils import generate_thumbnail, THUMBNAIL_SIZE


def test_generate_thumbnail_success():
    """
    Tests that a valid image file is correctly resized into a thumbnail.
    """
    # Arrange: Create a dummy in-memory image file larger than the thumbnail size
    source_image = Image.new('RGB', (512, 512), color='red')
    buffer = io.BytesIO()
    source_image.save(buffer, format='PNG')
    buffer.seek(0)

    # Mock a Django FileField
    mock_field_file = MagicMock()
    mock_field_file.name = "source_image.png"
    # The __enter__/__exit__ is to support the 'with' statement in the function
    mock_field_file.open.return_value.__enter__.return_value = buffer
    mock_field_file.open.return_value.__exit__.return_value = None

    # Act
    thumbnail_content_file = generate_thumbnail(mock_field_file)

    # Assert
    assert thumbnail_content_file is not None
    assert thumbnail_content_file.name.startswith("thumb_source_image")
    assert thumbnail_content_file.name.endswith(".png")

    # Verify the image content
    thumb_buffer = io.BytesIO(thumbnail_content_file.read())
    thumb_image = Image.open(thumb_buffer)
    assert thumb_image.format == 'PNG'
    assert thumb_image.width <= THUMBNAIL_SIZE[0]
    assert thumb_image.height <= THUMBNAIL_SIZE[1]


def test_generate_thumbnail_with_invalid_image_data():
    """
    Tests that the function returns None if the source file is not a valid image.
    """
    # Arrange: Create a buffer with non-image data
    buffer = io.BytesIO(b"this is not an image")

    # Mock a Django FileField
    mock_field_file = MagicMock()
    mock_field_file.name = "invalid_file.txt"
    mock_field_file.open.return_value.__enter__.return_value = buffer
    mock_field_file.open.return_value.__exit__.return_value = None

    # Act
    result = generate_thumbnail(mock_field_file)

    # Assert
    assert result is None


def test_generate_thumbnail_with_none_input():
    """
    Tests that the function returns None if the input field is None.
    """
    # Act
    result = generate_thumbnail(None)

    # Assert
    assert result is None