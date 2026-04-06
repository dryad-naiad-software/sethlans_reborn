# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for upload_helpers.py.

Tests EXR/HDR magic bytes validation, file size limits, and Pillow
verification bypass for non-Pillow-readable formats.
"""

import io

from PIL import Image

from workers.views.upload_helpers import (
    validate_upload,
    sanitize_filename,
    MAX_UPLOAD_SIZE,
)


class TestValidateUploadExrHdr:
    """Validate EXR and HDR files via magic bytes."""

    def _make_file(self, name, data, size=None):
        """Create a file-like object with the given data and name."""
        buf = io.BytesIO(data)
        buf.name = name
        buf.size = size or len(data)
        return buf

    def test_valid_exr_accepted(self):
        """EXR with matching magic bytes passes validation."""
        data = b'\x76\x2f\x31\x01' + b'\x00' * 100
        f = self._make_file('render.exr', data)
        result = validate_upload(f)
        assert result is None

    def test_valid_hdr_radiance_accepted(self):
        """HDR with RADIANCE magic bytes passes validation."""
        data = b'#?RADIANCE' + b'\n' * 100
        f = self._make_file('render.hdr', data)
        result = validate_upload(f)
        assert result is None

    def test_valid_hdr_rgbe_accepted(self):
        """HDR with RGBE magic bytes passes validation."""
        data = b'#?RGBE' + b'\n' * 100
        f = self._make_file('render.hdr', data)
        result = validate_upload(f)
        assert result is None

    def test_exr_wrong_magic_rejected(self):
        """EXR extension with non-EXR magic bytes is rejected."""
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        f = self._make_file('render.exr', data)
        result = validate_upload(f)
        assert result is not None
        assert 'do not match EXR format' in result

    def test_hdr_wrong_magic_rejected(self):
        """HDR extension with non-HDR magic bytes is rejected."""
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        f = self._make_file('render.hdr', data)
        result = validate_upload(f)
        assert result is not None
        assert 'do not match HDR format' in result

    def test_exr_too_large_rejected(self):
        """EXR exceeding size limit is rejected before magic check."""
        data = b'\x76\x2f\x31\x01' + b'\x00' * 100
        f = self._make_file('render.exr', data, size=MAX_UPLOAD_SIZE + 1)
        result = validate_upload(f)
        assert result is not None
        assert 'exceeds' in result

    def test_png_still_uses_pillow(self):
        """PNG files use standard Pillow verification, not magic bytes."""
        # Create a real PNG
        img = Image.new('RGB', (10, 10))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        data = buf.getvalue()

        f = self._make_file('render.png', data)
        result = validate_upload(f)
        assert result is None

    def test_corrupt_png_rejected(self):
        """Corrupt PNG files are rejected by Pillow verification."""
        f = self._make_file('render.png', b'not an image')
        result = validate_upload(f)
        assert result is not None
        assert 'not a valid image' in result


class TestSanitizeFilename:

    def test_strips_path(self):
        assert sanitize_filename('/tmp/evil/../../render.png') == 'render.png'

    def test_replaces_unsafe_chars(self):
        result = sanitize_filename('hello world!@#.png')
        assert ' ' not in result
        assert '!' not in result
        assert result.endswith('.png')

    def test_empty_fallback(self):
        assert sanitize_filename('') == 'output.png'

    def test_normal_filename(self):
        assert sanitize_filename('my_render_001.exr') == 'my_render_001.exr'
