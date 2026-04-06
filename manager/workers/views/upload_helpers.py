# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Upload validation and filename sanitization helpers for job file uploads.
"""

import logging
import os
import re

from django.conf import settings as django_settings
from PIL import Image

from ..constants import FORMAT_EXTENSIONS

logger = logging.getLogger(__name__)

# 100 MB default, overridable via settings
MAX_UPLOAD_SIZE = getattr(
    django_settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 104857600
)

# Magic bytes for non-Pillow-readable formats
_EXR_MAGIC = b'\x76\x2f\x31\x01'
_HDR_MAGIC_RADIANCE = b'#?RADIANCE'
_HDR_MAGIC_RGBE = b'#?RGBE'

# Extensions that require magic-bytes validation instead of Pillow
_MAGIC_BYTE_EXTENSIONS = {'.exr', '.hdr'}

# All valid output file extensions
_VALID_EXTENSIONS = set(FORMAT_EXTENSIONS.values())


def _check_exr_hdr_magic(file_obj):
    """
    Check whether a file's magic bytes match EXR or HDR format.

    Reads the first 16 bytes and checks for known magic byte patterns.
    Resets the file position to the beginning after reading.

    Args:
        file_obj: A file-like object supporting read() and seek().

    Returns:
        str or None: ``'exr'`` if EXR magic matches, ``'hdr'`` if HDR
        magic matches, or ``None`` if neither matches.
    """
    file_obj.seek(0)
    header = file_obj.read(16)
    file_obj.seek(0)

    if header[:4] == _EXR_MAGIC:
        return 'exr'
    if header[:10] == _HDR_MAGIC_RADIANCE or header[:6] == _HDR_MAGIC_RGBE:
        return 'hdr'
    return None


def validate_upload(file_obj):
    """
    Validate that the uploaded file is within size limits and is a
    genuine image file.

    For Pillow-readable formats, validates via ``Image.open().verify()``.
    For EXR and HDR files, validates via magic bytes instead (Pillow
    cannot read these formats).

    Returns an error message string, or ``None`` if valid.
    """
    if file_obj.size > MAX_UPLOAD_SIZE:
        limit_mb = MAX_UPLOAD_SIZE / (1024 * 1024)
        size_mb = file_obj.size / (1024 * 1024)
        return (
            f"File size {size_mb:.1f}MB exceeds the "
            f"{limit_mb:.0f}MB limit."
        )

    # Determine the file extension
    _, ext = os.path.splitext(file_obj.name or '')
    ext = ext.lower()

    # Check if this is an EXR/HDR file requiring magic-bytes validation
    if ext in _MAGIC_BYTE_EXTENSIONS:
        file_obj.seek(0)
        detected = _check_exr_hdr_magic(file_obj)
        if ext == '.exr' and detected != 'exr':
            return (
                "File extension is .exr but file contents "
                "do not match EXR format."
            )
        if ext == '.hdr' and detected != 'hdr':
            return (
                "File extension is .hdr but file contents "
                "do not match HDR format."
            )
        logger.debug(
            "Validated %s file via magic bytes (not Pillow-readable).",
            ext,
        )
        return None

    # Verify the file is a valid image using Pillow
    try:
        file_obj.seek(0)
        with Image.open(file_obj) as img:
            img.verify()
        file_obj.seek(0)
    except Exception:
        return "Uploaded file is not a valid image."

    return None


def sanitize_filename(filename):
    """
    Strip path components and replace unsafe characters so the
    filename is safe for storage.
    """
    name = os.path.basename(filename)
    name = re.sub(r'[^\w.\-]', '_', name)
    return name or 'output.png'
