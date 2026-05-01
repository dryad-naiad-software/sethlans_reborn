# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared video_settings validation logic for the Animation serializer.

Split out of ``animations.py`` to keep that file under the 300-line cap.
Public callables are imported by ``animations.AnimationSerializer``:

- ``validate_video_settings_create_guard`` — defensive create-time guard
  that rejects video_settings while FFmpeg is not yet ready
  (spec FR §128-133, ``code="video_assembly_unavailable"``).
- ``expand_and_validate_video_settings`` — preset expansion + format
  compatibility + framerate range checks; mutates and returns the dict.
- ``raise_video_settings_immutable`` — builds the closed-vocab DRF error
  for the post-create immutability rule (spec FR §135-138,
  ``code="video_settings_immutable"`` propagated to the leaf
  ``ErrorDetail``).
"""

from rest_framework import serializers

from ..constants import (
    VIDEO_PRESETS, VIDEO_CODECS, VIDEO_CONTAINERS,
    VIDEO_CODEC_CONTAINER_MAP, VIDEO_COMPATIBLE_FORMATS,
)


def validate_video_settings_create_guard(value):
    """Reject video_settings creates while FFmpeg is not ready.

    Defensive guard for the boot-window race where an admin
    submits an animation with ``video_settings`` before the
    manager's parts-check has finished resolving FFmpeg.  Per
    spec FR §128-133, the rejection is a standard DRF 400 with
    ``code="video_assembly_unavailable"`` (not a custom error
    envelope), and a non-null ``video_settings`` is the only
    case that triggers it.

    Race-window note (LOW concurrency, per spec FR §133):
    there is a microscopic window between this status read and
    the model save where the parts-check could flip
    ``installing -> ready``.  Worst case is a spurious 400
    during the boot-overlap window; the user retries.  Failing
    closed is the safe direction — synchronization is not added.
    """
    if value is not None:
        from ..services import parts_check
        snapshot = parts_check.get_status("ffmpeg")
        if snapshot.status != "ready":
            raise serializers.ValidationError(
                "video_assembly_unavailable",
                code="video_assembly_unavailable",
            )
    return value


def expand_and_validate_video_settings(video_settings, file_format):
    """Validate and expand video_settings, returning the final dict."""
    if not isinstance(video_settings, dict):
        raise serializers.ValidationError(
            {"video_settings": "Must be a JSON object."}
        )

    # Check HDR format restriction
    if file_format not in VIDEO_COMPATIBLE_FORMATS:
        raise serializers.ValidationError(
            {"video_settings": (
                f"Video output is not available for {file_format} format. "
                f"Use PNG, JPEG, TIFF, BMP, or Targa."
            )}
        )

    preset = video_settings.get('preset')
    if preset is None:
        raise serializers.ValidationError(
            {"video_settings": "The 'preset' key is required."}
        )

    if preset != 'custom':
        _expand_preset(video_settings, preset)
    else:
        _validate_custom_settings(video_settings)

    # Validate framerate (required for both modes)
    framerate = video_settings.get('framerate')
    if not isinstance(framerate, int) or not (1 <= framerate <= 120):
        raise serializers.ValidationError(
            {"video_settings": "Framerate must be an integer between 1 and 120."}
        )

    return video_settings


def _expand_preset(video_settings, preset):
    """Look up a preset and merge its values into video_settings."""
    if preset not in VIDEO_PRESETS:
        raise serializers.ValidationError(
            {"video_settings": f"Unknown video preset '{preset}'."}
        )
    preset_config = VIDEO_PRESETS[preset]
    video_settings['container'] = preset_config['container']
    video_settings['codec'] = preset_config['codec']
    video_settings['crf'] = preset_config['crf']


def _validate_custom_settings(video_settings):
    """Validate custom mode container, codec, and crf values."""
    container = video_settings.get('container')
    codec = video_settings.get('codec')
    if container not in VIDEO_CONTAINERS:
        raise serializers.ValidationError(
            {"video_settings": f"Invalid container '{container}'."}
        )
    if codec not in VIDEO_CODECS:
        raise serializers.ValidationError(
            {"video_settings": f"Invalid codec '{codec}'."}
        )
    valid_containers = VIDEO_CODEC_CONTAINER_MAP.get(codec, [])
    if container not in valid_containers:
        raise serializers.ValidationError(
            {"video_settings": (
                f"Codec '{codec}' is not valid for "
                f"container '{container}'."
            )}
        )
    crf = video_settings.get('crf')
    if not isinstance(crf, int) or not (0 <= crf <= 51):
        raise serializers.ValidationError(
            {"video_settings": "CRF must be an integer between 0 and 51."}
        )


def raise_video_settings_immutable():
    """Raise the closed-vocab DRF error for the immutability rule.

    ``serializers.ValidationError(detail=dict, code=...)`` silently drops
    the ``code`` kwarg (DRF wraps the leaves with
    ``ErrorDetail(... code='invalid')``).  Per spec FR §131 the frontend
    matches on the leaf string, but any future API consumer (mobile, CLI,
    integration tests) that introspects
    ``response.data['video_settings'][0].code`` needs the code propagated
    to the leaf — so we construct the ``ErrorDetail`` ourselves with the
    closed-vocab code attached.
    """
    raise serializers.ValidationError({
        "video_settings": [
            serializers.ErrorDetail(
                "video_settings_immutable",
                code="video_settings_immutable",
            ),
        ],
    })
