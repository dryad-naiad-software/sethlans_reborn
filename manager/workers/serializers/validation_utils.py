# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared render_settings validation logic used by multiple serializers.
"""

import os

from rest_framework import serializers

from ..constants import (
    DEFAULT_OUTPUT_FORMAT,
    FORMAT_COLOR_DEPTHS,
    FORMAT_EXTENSIONS,
    OutputFormat,
    RenderSettings,
)

# Every color depth some format accepts: '8', '16', '32'.
COLOR_DEPTH_VALUES = tuple(sorted(
    {depth for depths in FORMAT_COLOR_DEPTHS.values() for depth in depths}, key=int,
))

# Image-settings keys that may be left out but never sent as null (#243).
NON_NULL_IMAGE_KEYS = (
    RenderSettings.IMAGE_FILE_FORMAT,
    RenderSettings.IMAGE_QUALITY,
    RenderSettings.IMAGE_COLOR_DEPTH,
)


def _quoted(values):
    """``('8', '16')`` -> ``"'8', '16'"`` for error messages."""
    return ', '.join(f"'{value}'" for value in values)


def validate_render_settings(render_settings):
    """
    Field-level validator for the ``render_settings`` JSON dict.

    Rules:
    - ``render.image_settings.file_format``, ``.quality`` and ``.color_depth``
      must not be present with a null value (#243): the worker would assign
      ``None`` and Blender rejects it. Leaving a key out uses the default.
    - ``render.image_settings.file_format`` must be a valid OutputFormat value.
    - ``render.image_settings.quality`` must be an int between 1 and 100.
    - ``render.image_settings.color_depth`` must be a string the job's file
      format accepts (``FORMAT_COLOR_DEPTHS``, #243); see
      ``_validate_color_depth``.

    Every serializer that accepts ``render_settings`` (jobs, tiled jobs,
    animations; create and update) runs this as its field validator. An
    update replaces the whole dict, so the dict checked here is the one
    that will be stored.

    Returns the validated render_settings dict, or raises ValidationError.
    """
    if not render_settings or not isinstance(render_settings, dict):
        return render_settings

    _reject_null_image_keys(render_settings)

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

    _validate_color_depth(render_settings)
    return render_settings


def _reject_null_image_keys(render_settings):
    """Reject an image-settings key that is present with a null value."""
    for key in NON_NULL_IMAGE_KEYS:
        if key in render_settings and render_settings[key] is None:
            raise serializers.ValidationError(
                f"'{key}' must not be null; leave the key out to use the default."
            )


def _validate_color_depth(render_settings):
    """
    Reject a ``color_depth`` that Blender does not accept for the job's format.

    The depth must be one of the strings in ``COLOR_DEPTH_VALUES`` (the form
    Blender's enum takes; integers are refused). Without a file format the
    job renders in ``DEFAULT_OUTPUT_FORMAT``, so the depth is checked against
    that. A format with no entry in ``FORMAT_COLOR_DEPTHS`` gets no
    format-specific depth rule.

    Private: it assumes ``validate_render_settings`` has already rejected
    null keys and an unknown or non-string file format.
    """
    color_depth = render_settings.get(RenderSettings.IMAGE_COLOR_DEPTH)
    if color_depth is None:
        return
    if color_depth not in COLOR_DEPTH_VALUES:
        raise serializers.ValidationError(
            f"Color depth must be one of {_quoted(COLOR_DEPTH_VALUES)} (a string)."
        )

    file_format = render_settings.get(RenderSettings.IMAGE_FILE_FORMAT)
    note = ""
    if file_format is None:
        file_format = DEFAULT_OUTPUT_FORMAT
        note = " (the default when no file format is set)"
    allowed = FORMAT_COLOR_DEPTHS.get(file_format)
    if allowed is None or color_depth in allowed:
        return
    raise serializers.ValidationError(
        f"Color depth '{color_depth}' is not supported for output format "
        f"'{file_format}'{note}. Allowed color depths for '{file_format}': "
        f"{_quoted(allowed)}."
    )


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
        RenderSettings.IMAGE_FILE_FORMAT, DEFAULT_OUTPUT_FORMAT
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
