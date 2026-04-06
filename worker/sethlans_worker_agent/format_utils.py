# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Format utilities for mapping Blender output format strings to file extensions.

This module provides the worker-side FORMAT_EXTENSIONS mapping and a helper
function to extract the format and extension from a job's render_settings.
The mapping is duplicated from the manager's constants.py because the worker
is a standalone deployable that cannot import from the Django manager.
"""

FORMAT_EXTENSIONS = {
    'PNG': '.png',
    'JPEG': '.jpg',
    'OPEN_EXR': '.exr',
    'OPEN_EXR_MULTILAYER': '.exr',
    'TIFF': '.tif',
    'BMP': '.bmp',
    'HDR': '.hdr',
    'TARGA': '.tga',
}

# Formats that require a worker-side thumbnail render pass because
# Pillow cannot read them on the manager.
EXR_HDR_FORMATS = frozenset({'OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'HDR'})


def get_format_and_extension(render_settings):
    """
    Extract the Blender format string and file extension from render_settings.

    Reads 'render.image_settings.file_format' from the dict, defaulting to
    'PNG' if the key is missing or the value is not a recognised format.

    Args:
        render_settings: dict of render settings from the job data.

    Returns:
        A tuple of (blender_format_string, extension_string).
        Example: ('JPEG', '.jpg')
    """
    fmt = render_settings.get('render.image_settings.file_format', 'PNG')
    if fmt not in FORMAT_EXTENSIONS:
        fmt = 'PNG'
    ext = FORMAT_EXTENSIONS[fmt]
    return fmt, ext
