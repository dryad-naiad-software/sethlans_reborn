# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The shot discovery API (Unreal sequence rendering FR-SD-1, FR-SD-2).

``SequenceDiscoveryCreateSerializer`` checks the request body only: an
asset of an Unreal project and three Unreal object paths.  The record
itself is found or created by ``services/sequence_discovery.py`` and is
read-only through the API.
"""

from rest_framework import serializers

from shared.unreal.validation import OBJECT_PATH_MAX, validate_object_path

from ..constants import Renderer
from ..models import Asset, SequenceDiscovery

CREATE_KEYS = frozenset({'asset', 'map', 'sequence', 'preset'})
NOT_UNREAL_MESSAGE = "Shot discovery needs an asset of an Unreal project."


def _object_path(value):
    try:
        return validate_object_path(value)
    except ValueError as exc:
        raise serializers.ValidationError(str(exc)) from exc


class SequenceDiscoveryCreateSerializer(serializers.Serializer):
    """``POST /api/sequence-discoveries/`` body (FR-SD-2)."""

    asset = serializers.PrimaryKeyRelatedField(queryset=Asset.objects.select_related('project'))
    map = serializers.CharField(max_length=OBJECT_PATH_MAX, trim_whitespace=False, help_text="Unreal map object path.")
    sequence = serializers.CharField(
        max_length=OBJECT_PATH_MAX, trim_whitespace=False, help_text="Level Sequence object path.",
    )
    preset = serializers.CharField(
        max_length=OBJECT_PATH_MAX, trim_whitespace=False, help_text="Movie Render Queue preset object path.",
    )

    def validate_asset(self, value):
        if value.project.renderer != Renderer.UNREAL:
            raise serializers.ValidationError(NOT_UNREAL_MESSAGE)
        return value

    def validate_map(self, value):
        return _object_path(value)

    def validate_sequence(self, value):
        return _object_path(value)

    def validate_preset(self, value):
        return _object_path(value)


class SequenceDiscoverySerializer(serializers.ModelSerializer):
    """A discovery record as the API shows it (read-only)."""

    shots = serializers.JSONField(
        read_only=True, help_text='[{"index", "name", "start", "end"}]: 1-based index, end exclusive.',
    )
    preset_facts = serializers.JSONField(
        read_only=True,
        help_text=(
            "The preset's facts (FR-SD-5): preset_kind, resolution, temporal_samples, spatial_samples, "
            "layers_ok, can_png, can_exr, can_sound, game_overrides, texture_streaming, "
            "custom_frame_rate, frame_step, handle_frames, frame_number_offset, zero_pad."
        ),
    )
    warnings = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = SequenceDiscovery
        fields = [
            'id', 'project', 'asset', 'engine_build', 'map', 'sequence', 'preset', 'status', 'job',
            'created_at', 'playback_start', 'playback_end', 'frame_rate_num', 'frame_rate_den',
            'shot_source', 'shots', 'preset_facts', 'warnings', 'error_message',
        ]
        read_only_fields = fields


SUMMARY_FIELDS = [f for f in SequenceDiscoverySerializer.Meta.fields if f not in ('shots', 'warnings')]


class SequenceDiscoverySummarySerializer(serializers.ModelSerializer):
    """A list row: the record without its shots and warnings (the detail view has them)."""

    preset_facts = serializers.JSONField(read_only=True, help_text="The preset's facts (FR-SD-5), as in the detail view.")

    class Meta:
        model = SequenceDiscovery
        fields = SUMMARY_FIELDS
        read_only_fields = fields
