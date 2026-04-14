# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for .blend file upload validation on AssetSerializer.

Covers extension checking, file size limits, and magic-byte verification
added to fix GitHub issue #34.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.exceptions import ValidationError

from workers.serializers.assets import AssetSerializer

# Real .blend files start with "BLENDER" (7 bytes) followed by pointer
# size and endianness info.  We only need the 7-byte prefix for tests.
BLEND_MAGIC = b"BLENDER"

# Maximum allowed upload size: 500 MB.
MAX_SIZE = 500 * 1024 * 1024


def _blend_file(name="scene.blend", size=1024, magic=BLEND_MAGIC):
    """Build a SimpleUploadedFile that mimics a .blend upload.

    The content starts with *magic* and is zero-padded to *size* bytes.
    If *size* is smaller than len(magic), the content is truncated.
    """
    content = (magic + b"\x00" * max(0, size - len(magic)))[:size]
    return SimpleUploadedFile(name=name, content=content)


def _validate(uploaded_file):
    """Call the field-level validator under test."""
    serializer = AssetSerializer()
    return serializer.validate_blend_file(uploaded_file)


# ---- Extension validation ------------------------------------------------

class TestExtensionValidation:
    """File extension must be '.blend' (case-insensitive)."""

    def test_txt_extension_rejected(self):
        f = _blend_file(name="scene.txt")
        with pytest.raises(ValidationError, match="Only .blend files"):
            _validate(f)

    def test_exe_extension_rejected(self):
        f = _blend_file(name="scene.exe")
        with pytest.raises(ValidationError, match="Only .blend files"):
            _validate(f)

    def test_blend1_extension_rejected(self):
        """'.blend1' is a Blender backup, not a valid upload."""
        f = _blend_file(name="scene.blend1")
        with pytest.raises(ValidationError, match="Only .blend files"):
            _validate(f)

    def test_no_extension_rejected(self):
        f = _blend_file(name="scene")
        with pytest.raises(ValidationError, match="Only .blend files"):
            _validate(f)

    def test_dot_blend_dot_zip_rejected(self):
        f = _blend_file(name="scene.blend.zip")
        with pytest.raises(ValidationError, match="Only .blend files"):
            _validate(f)


class TestMixedCaseExtension:
    """Mixed-case '.blend' variants must be accepted."""

    def test_uppercase_blend(self):
        f = _blend_file(name="scene.BLEND")
        result = _validate(f)
        assert result is not None

    def test_title_case_blend(self):
        f = _blend_file(name="scene.Blend")
        result = _validate(f)
        assert result is not None

    def test_random_case_blend(self):
        f = _blend_file(name="scene.bLeNd")
        result = _validate(f)
        assert result is not None


# ---- Size validation -----------------------------------------------------

class TestSizeValidation:
    """Upload must not exceed 500 MB."""

    def test_file_exceeding_max_size(self):
        """One byte over the limit must be rejected."""
        f = SimpleUploadedFile(
            name="huge.blend",
            content=BLEND_MAGIC,
        )
        # Fake the size attribute instead of allocating 500 MB+.
        f.size = MAX_SIZE + 1
        with pytest.raises(ValidationError, match="500"):
            _validate(f)

    def test_file_at_exact_max_size(self):
        """Exactly 500 MB must be accepted (boundary)."""
        f = SimpleUploadedFile(
            name="exact.blend",
            content=BLEND_MAGIC + b"\x00" * 100,
        )
        f.size = MAX_SIZE
        result = _validate(f)
        assert result is not None

    def test_small_file_accepted(self):
        f = _blend_file(name="small.blend", size=256)
        result = _validate(f)
        assert result is not None


# ---- Magic-byte validation -----------------------------------------------

class TestCompressedBlendFiles:
    """Blender files may be gzip or zstd compressed."""

    def test_gzip_compressed_blend_accepted(self):
        gzip_magic = b"\x1f\x8b\x08\x00\x00\x00\x00"
        f = _blend_file(name="scene.blend", magic=gzip_magic)
        result = _validate(f)
        assert result is f

    def test_zstd_compressed_blend_accepted(self):
        zstd_magic = b"\x28\xb5\x2f\xfd\x00\x00\x00"
        f = _blend_file(name="scene.blend", magic=zstd_magic)
        result = _validate(f)
        assert result is f


class TestMagicByteValidation:
    """First 7 bytes must match a known .blend format."""

    def test_png_magic_rejected(self):
        png_magic = b"\x89PNG\r\n\x1a\n"
        f = _blend_file(name="fake.blend", magic=png_magic)
        with pytest.raises(ValidationError):
            _validate(f)

    def test_jpeg_magic_rejected(self):
        jpeg_magic = b"\xff\xd8\xff\xe0"
        f = _blend_file(name="fake.blend", magic=jpeg_magic)
        with pytest.raises(ValidationError):
            _validate(f)

    def test_random_bytes_rejected(self):
        f = _blend_file(name="random.blend", magic=b"\x00\x01\x02\x03\x04\x05\x06")
        with pytest.raises(ValidationError):
            _validate(f)

    def test_partial_magic_rejected(self):
        """'BLEND' (5 bytes) is not enough -- must be full 'BLENDER'."""
        f = _blend_file(name="partial.blend", magic=b"BLEND\x00\x00")
        with pytest.raises(ValidationError):
            _validate(f)

    def test_empty_file_rejected(self):
        """0-byte file cannot contain magic bytes."""
        f = SimpleUploadedFile(name="empty.blend", content=b"")
        with pytest.raises(ValidationError):
            _validate(f)

    def test_file_shorter_than_magic_rejected(self):
        """File with fewer than 7 bytes cannot match 'BLENDER'."""
        f = SimpleUploadedFile(name="tiny.blend", content=b"BLE")
        with pytest.raises(ValidationError):
            _validate(f)

    def test_seek_position_after_validation(self):
        """File position must be reset after reading magic bytes."""
        f = _blend_file(name="scene.blend", size=512)
        _validate(f)
        assert f.tell() == 0


# ---- Full happy path -----------------------------------------------------

class TestHappyPath:
    """Valid .blend file with correct extension, size, and magic bytes."""

    def test_valid_blend_file_passes(self):
        f = _blend_file(name="project.blend", size=2048)
        result = _validate(f)
        assert result is not None

    def test_returns_the_uploaded_file(self):
        """Validator must return the file object for DRF to continue."""
        f = _blend_file(name="scene.blend", size=1024)
        result = _validate(f)
        assert result is f

    def test_blend_with_realistic_header(self):
        """Realistic header: 'BLENDER' + pointer-size + endianness."""
        realistic_magic = b"BLENDER-v293"
        f = _blend_file(name="real.blend", size=4096, magic=realistic_magic)
        result = _validate(f)
        assert result is f
