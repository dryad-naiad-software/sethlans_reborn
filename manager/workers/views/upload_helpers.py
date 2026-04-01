# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Upload validation and filename sanitization helpers for job file uploads.
"""

import os
import re

from django.conf import settings as django_settings
from PIL import Image

# 100 MB default, overridable via settings
MAX_UPLOAD_SIZE = getattr(
    django_settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 104857600
)


def validate_upload(file_obj):
    """
    Validate that the uploaded file is within size limits and is a
    genuine image that Pillow can open.

    Returns an error message string, or None if valid.
    """
    if file_obj.size > MAX_UPLOAD_SIZE:
        limit_mb = MAX_UPLOAD_SIZE / (1024 * 1024)
        size_mb = file_obj.size / (1024 * 1024)
        return (
            f"File size {size_mb:.1f}MB exceeds the "
            f"{limit_mb:.0f}MB limit."
        )

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
