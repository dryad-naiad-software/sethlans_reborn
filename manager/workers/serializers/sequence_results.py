# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Strict worker result bodies of Unreal sequence rendering (FR-SD-8).

The fields describe the body for the OpenAPI schema; validation is the
shared, strict check of ``shared.unreal.shots_result`` (the same bounds the
worker applies, FR-SD-7), so DRF never coerces a string into a number.
Unknown keys are refused by the view before this runs.
"""

from rest_framework import serializers

from shared.unreal.render_context import GAME_OVERRIDES_VALUES, TEXTURE_STREAMING_VALUES
from shared.unreal.shots_result import (
    MAX_SHOTS, MAX_WARNINGS, PRESET_KINDS, SHOT_SOURCES, WARNING_MAX_CHARS, ShotsResultError,
    check_shots_result,
)


def _pair(help_text, allow_null=False):
    return serializers.ListField(
        child=serializers.IntegerField(), min_length=2, max_length=2, allow_null=allow_null, help_text=help_text,
    )


class ShotsPresetFactsSerializer(serializers.Serializer):
    """FR-SD-5 ``preset_facts`` (schema only)."""

    resolution = _pair("[width, height] of the preset's output, or null when it could not be read.", allow_null=True)
    temporal_samples = serializers.IntegerField(min_value=1)
    spatial_samples = serializers.IntegerField(min_value=1)
    layers_ok = serializers.BooleanField()
    can_png = serializers.BooleanField()
    can_exr = serializers.BooleanField()
    can_sound = serializers.BooleanField()
    game_overrides = serializers.ChoiceField(choices=GAME_OVERRIDES_VALUES)
    texture_streaming = serializers.ChoiceField(choices=TEXTURE_STREAMING_VALUES)
    custom_frame_rate = _pair("[num, den], or null when the preset sets none.", allow_null=True)
    frame_step = serializers.IntegerField(allow_null=True)
    handle_frames = serializers.IntegerField(allow_null=True)
    frame_number_offset = serializers.IntegerField(allow_null=True)
    zero_pad = serializers.IntegerField(allow_null=True)


class ShotsResultRequestSerializer(serializers.Serializer):
    """``POST /api/jobs/{id}/shots-result/`` body; see ``shared.unreal.shots_result``."""

    preset_kind = serializers.ChoiceField(choices=PRESET_KINDS)
    playback_range = _pair("[start, end] edit frames, end exclusive, start >= 0.")
    frame_rate = _pair("The sequence's display rate as [num, den].")
    shot_source = serializers.ChoiceField(choices=SHOT_SOURCES)
    shots = serializers.ListField(
        child=serializers.ListField(child=serializers.JSONField(), min_length=3, max_length=3),
        min_length=1, max_length=MAX_SHOTS,
        help_text="[[name, start, end], ...] in edit order, end exclusive, non-overlapping.",
    )
    preset_facts = ShotsPresetFactsSerializer()
    warnings = serializers.ListField(
        child=serializers.CharField(max_length=WARNING_MAX_CHARS, allow_blank=True), max_length=MAX_WARNINGS,
    )

    def to_internal_value(self, data):
        """The shared strict check; a failure is a 400 keyed by the bad field."""
        try:
            return check_shots_result(data)
        except ShotsResultError as exc:
            raise serializers.ValidationError({exc.field: [str(exc)]}) from exc


class ShotsResultResponseSerializer(serializers.Serializer):
    """``shots-result/`` success body."""

    status = serializers.CharField(help_text="Always DONE.")
    discovery = serializers.IntegerField(help_text="The discovery record now DONE.")
    shots = serializers.IntegerField(help_text="How many shots were stored.")
