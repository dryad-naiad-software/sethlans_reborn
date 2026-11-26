# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers for the SupportedBlenderVersion model.
"""

import re

from rest_framework import serializers

from shared.blender_sources import (
    BlenderSourcesConfigError, BlenderSourcesError, latest_patch,
)

from ..models import SupportedBlenderVersion

SERIES_REGEX = re.compile(r'^\d+\.\d+$')


class SupportedBlenderVersionSerializer(serializers.ModelSerializer):
    """
    Serializer for ``SupportedBlenderVersion`` CRUD operations.

    On create, ``series`` is validated as ``major.minor`` format and
    resolved to the latest patch version via the Blender release page of
    the first source that lists one (``shared.blender_sources``).
    """

    class Meta:
        model = SupportedBlenderVersion
        fields = [
            'id', 'major', 'minor', 'series', 'resolved_version',
            'is_default', 'added_at', 'last_patch_check',
        ]
        read_only_fields = [
            'id', 'major', 'minor', 'resolved_version',
            'added_at', 'last_patch_check',
        ]

    def validate_series(self, value):
        if not SERIES_REGEX.match(value):
            raise serializers.ValidationError(
                "Series must be in 'major.minor' format (e.g., '4.2')."
            )
        return value

    def create(self, validated_data):
        series = validated_data['series']
        try:
            # 5 s per request; with several sources the whole lookup can
            # take that many times longer.
            resolved = latest_patch(series, timeout=5)
        except BlenderSourcesConfigError as exc:
            # A subclass of BlenderSourcesError, so it must come first: the
            # series is fine, the manager's Blender source setting is not.
            raise serializers.ValidationError({
                'series': (
                    f"Cannot look up series '{series}': the Blender source "
                    f"setting is invalid: {exc}."
                ),
            }) from None
        except (BlenderSourcesError, ValueError) as exc:
            # A source failure is the caller's 400, not a 500 (BS-REL-9).
            raise serializers.ValidationError({
                'series': (
                    f"No patch versions found for series '{series}': {exc}. "
                    "Verify the series exists on the Blender download site."
                ),
            }) from None
        validated_data['resolved_version'] = resolved
        # Populate major/minor (also done in save(), but needed for create)
        parts = series.split('.')
        validated_data['major'] = int(parts[0])
        validated_data['minor'] = int(parts[1])
        return super().create(validated_data)


class EffectiveBlenderVersionSerializer(serializers.Serializer):
    """
    Read-only nested representation of a resolved Blender version.

    Used on Job, TiledJob, and Animation serializers to expose the
    ``effective_blender_version`` property.
    """
    series = serializers.CharField(read_only=True)
    resolved_version = serializers.CharField(read_only=True)
