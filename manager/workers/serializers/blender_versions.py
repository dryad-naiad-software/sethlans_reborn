# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializers for the SupportedBlenderVersion model.
"""

import re

from rest_framework import serializers
from ..models import SupportedBlenderVersion
from ..utils.blender_release_parser import resolve_latest_patch

SERIES_REGEX = re.compile(r'^\d+\.\d+$')


class SupportedBlenderVersionSerializer(serializers.ModelSerializer):
    """
    Serializer for ``SupportedBlenderVersion`` CRUD operations.

    On create, ``series`` is validated as ``major.minor`` format and
    resolved to the latest patch version via the Blender release page.
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
        resolved = resolve_latest_patch(series, timeout=5)
        if not resolved:
            raise serializers.ValidationError({
                'series': (
                    f"No patch versions found for series '{series}'. "
                    "Verify the series exists on the Blender download site."
                ),
            })
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
