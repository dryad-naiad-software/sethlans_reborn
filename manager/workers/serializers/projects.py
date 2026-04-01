# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Project model.
"""

from rest_framework import serializers
from ..models import Project, SupportedBlenderVersion
from .blender_versions import SupportedBlenderVersionSerializer


class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Project` model.

    ``blender_version`` accepts a SupportedBlenderVersion PK on write
    and returns a nested object on read. When omitted on create, the
    default version is used.
    """
    blender_version = serializers.PrimaryKeyRelatedField(
        queryset=SupportedBlenderVersion.objects.all(),
        required=False,
        help_text="PK of a SupportedBlenderVersion. Defaults to the default version.",
    )
    blender_version_details = SupportedBlenderVersionSerializer(
        source='blender_version', read_only=True,
    )

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'blender_version', 'blender_version_details',
            'created_at', 'is_paused',
        ]
        read_only_fields = ['id', 'created_at', 'is_paused']

    def create(self, validated_data):
        if 'blender_version' not in validated_data:
            default = SupportedBlenderVersion.objects.filter(
                is_default=True,
            ).first()
            if not default:
                raise serializers.ValidationError({
                    'blender_version': (
                        "No default Blender version is configured. "
                        "Please create a supported version first."
                    ),
                })
            validated_data['blender_version'] = default
        return super().create(validated_data)
