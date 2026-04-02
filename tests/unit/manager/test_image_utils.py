# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/image_utils.py.

PIL Image operations are mocked to avoid real image I/O.
"""

import io
from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile

from workers.image_utils import generate_thumbnail, THUMBNAIL_WIDTH


class TestGenerateThumbnail:

    def _make_source_field(self, width, height, name="render.png"):
        """
        Create a mock source_file_field whose open() yields an
        image with the given dimensions.
        """
        field = MagicMock()
        field.name = name

        # Build a minimal real PNG in memory via PIL
        from PIL import Image as RealImage
        img = RealImage.new("RGBA", (width, height), "red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        raw_bytes = buf.getvalue()

        # Make field.open() return a context manager yielding bytes
        file_mock = io.BytesIO(raw_bytes)
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=file_mock)
        ctx.__exit__ = MagicMock(return_value=False)
        field.open = MagicMock(return_value=ctx)

        return field

    def test_returns_content_file(self):
        field = self._make_source_field(1920, 1080)
        result = generate_thumbnail(field)
        assert isinstance(result, ContentFile)

    def test_thumbnail_filename_includes_stem(self):
        field = self._make_source_field(800, 600, "my_render.png")
        result = generate_thumbnail(field)
        assert result.name == "thumb_my_render.png"

    def test_thumbnail_preserves_aspect_ratio(self):
        """For 1920x1080, aspect = 1080/1920 = 0.5625."""
        field = self._make_source_field(1920, 1080)
        result = generate_thumbnail(field)
        assert result is not None
        # Verify the generated image dimensions
        from PIL import Image as RealImage
        img = RealImage.open(io.BytesIO(result.read()))
        assert img.size[0] == THUMBNAIL_WIDTH  # 256
        expected_h = int(THUMBNAIL_WIDTH * (1080 / 1920))
        assert img.size[1] == expected_h

    def test_square_image(self):
        field = self._make_source_field(500, 500)
        result = generate_thumbnail(field)
        from PIL import Image as RealImage
        img = RealImage.open(io.BytesIO(result.read()))
        assert img.size == (THUMBNAIL_WIDTH, THUMBNAIL_WIDTH)

    def test_tall_image(self):
        field = self._make_source_field(100, 400)
        result = generate_thumbnail(field)
        from PIL import Image as RealImage
        img = RealImage.open(io.BytesIO(result.read()))
        assert img.size[0] == THUMBNAIL_WIDTH
        expected_h = int(THUMBNAIL_WIDTH * (400 / 100))
        assert img.size[1] == expected_h

    def test_returns_none_for_falsy_field(self):
        assert generate_thumbnail(None) is None
        assert generate_thumbnail("") is None
        assert generate_thumbnail(0) is None

    def test_returns_none_for_zero_width_image(self):
        """A zero-width image triggers division-by-zero guard."""
        field = MagicMock()
        field.name = "zero_width.png"
        field.__bool__ = MagicMock(return_value=True)

        # Create a mock Image with size (0, 100)
        mock_img = MagicMock()
        mock_img.size = (0, 100)

        file_bytes = io.BytesIO(b"fake")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=file_bytes)
        ctx.__exit__ = MagicMock(return_value=False)
        field.open = MagicMock(return_value=ctx)

        with patch("workers.image_utils.Image") as MockImage:
            MockImage.open.return_value = mock_img
            result = generate_thumbnail(field)
        assert result is None

    def test_returns_none_on_corrupt_file(self):
        field = MagicMock()
        field.name = "corrupt.png"
        bad_bytes = io.BytesIO(b"not an image")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=bad_bytes)
        ctx.__exit__ = MagicMock(return_value=False)
        field.open = MagicMock(return_value=ctx)

        result = generate_thumbnail(field)
        assert result is None

    def test_thumbnail_width_constant(self):
        assert THUMBNAIL_WIDTH == 256
