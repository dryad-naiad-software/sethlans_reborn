# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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
from workers.image_utils import generate_thumbnail, THUMBNAIL_WIDTH


@pytest.mark.parametrize("source_size, expected_thumb_size", [
    ((1920, 1080), (256, 144)),  # 16:9 aspect ratio
    ((640, 480), (256, 192)),   # 4:3 aspect ratio
    ((1024, 1024), (256, 256)), # 1:1 aspect ratio
])
def test_generate_thumbnail_with_fixed_width(source_size, expected_thumb_size):
    """
    Tests that a valid image is correctly resized to a fixed width
    while maintaining its aspect ratio.
    """
    # Arrange: Create a dummy in-memory image file larger than the thumbnail size
    source_image = Image.new('RGB', source_size, color='red')
    buffer = io.BytesIO()
    source_image.save(buffer, format='PNG')
    buffer.seek(0)

    # Mock a Django FileField
    mock_field_file = MagicMock()
    mock_field_file.name = "source_image.png"
    mock_field_file.open.return_value.__enter__.return_value = buffer
    mock_field_file.open.return_value.__exit__.return_value = None

    # Act
    thumbnail_content_file = generate_thumbnail(mock_field_file)

    # Assert
    assert thumbnail_content_file is not None
    assert thumbnail_content_file.name.startswith("thumb_source_image")
    assert thumbnail_content_file.name.endswith(".png")

    # Verify the image content and new dimensions
    thumb_buffer = io.BytesIO(thumbnail_content_file.read())
    thumb_image = Image.open(thumb_buffer)
    assert thumb_image.format == 'PNG'
    assert thumb_image.size == expected_thumb_size
    assert thumb_image.width == THUMBNAIL_WIDTH


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