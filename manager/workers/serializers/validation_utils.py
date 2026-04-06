# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared render_settings validation logic used by multiple serializers.
"""

import os

from rest_framework import serializers

from ..constants import (
    FORMAT_EXTENSIONS,
    OutputFormat,
    RenderSettings,
)


def validate_render_settings(render_settings):
    """
    Field-level validator for the ``render_settings`` JSON dict.

    Rules:
    - ``render.image_settings.file_format`` must be a valid OutputFormat value.
    - ``render.image_settings.quality`` must be an int between 1 and 100.
    - ``render.image_settings.color_depth`` must be ``'16'`` or ``'32'``.

    Returns the validated render_settings dict, or raises ValidationError.
    """
    if not render_settings or not isinstance(render_settings, dict):
        return render_settings

    file_format = render_settings.get(RenderSettings.IMAGE_FILE_FORMAT)
    if file_format is not None:
        if file_format not in OutputFormat.values:
            raise serializers.ValidationError(
                f"Invalid output format '{file_format}'. "
                f"Supported formats: {list(OutputFormat.values)}"
            )

    quality = render_settings.get(RenderSettings.IMAGE_QUALITY)
    if quality is not None:
        if not isinstance(quality, int) or not (1 <= quality <= 100):
            raise serializers.ValidationError(
                "JPEG quality must be an integer between 1 and 100."
            )

    color_depth = render_settings.get(RenderSettings.IMAGE_COLOR_DEPTH)
    if color_depth is not None:
        if color_depth not in ('16', '32'):
            raise serializers.ValidationError(
                "Color depth must be '16' (Half Float) or '32' (Full Float)."
            )

    return render_settings


def validate_output_pattern_extension(data):
    """
    Validate that ``output_file_pattern`` extension matches the format
    specified in ``render_settings``.

    Only applies when both ``output_file_pattern`` and a file format are
    present in the data.
    """
    render_settings = data.get('render_settings') or {}
    output_pattern = data.get('output_file_pattern')

    if not output_pattern:
        return

    file_format = render_settings.get(
        RenderSettings.IMAGE_FILE_FORMAT, 'PNG'
    )
    expected_ext = FORMAT_EXTENSIONS.get(file_format)
    if not expected_ext:
        return

    _, actual_ext = os.path.splitext(output_pattern)
    if actual_ext.lower() != expected_ext.lower():
        raise serializers.ValidationError(
            f"Output file pattern extension '{actual_ext}' does not match "
            f"the selected format '{file_format}' "
            f"(expected '{expected_ext}')."
        )
