# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Asset model, handling file uploads.
"""

from rest_framework import serializers
from ..models import Asset, Project
from .projects import ProjectSerializer

# 500 MB — asset-specific limit (separate from Django's global setting)
MAX_BLEND_FILE_SIZE = 500 * 1024 * 1024

# .blend files start with these 7 bytes
BLEND_MAGIC_BYTES = b'BLENDER'


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Asset` model, handling file uploads.
    """
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Asset
        fields = ['id', 'name', 'blend_file', 'created_at', 'project', 'project_details']
        read_only_fields = ['created_at', 'project_details']
        extra_kwargs = {
            'project': {'write_only': True}
        }

    def validate_blend_file(self, value):
        """
        Validate the uploaded .blend file:
        1. File extension must be .blend
        2. File size must not exceed 500 MB
        3. File must start with the BLENDER magic bytes
        """
        # Extension check
        if not value.name.lower().endswith('.blend'):
            raise serializers.ValidationError(
                "Only .blend files are accepted."
            )

        # Size check
        if value.size > MAX_BLEND_FILE_SIZE:
            raise serializers.ValidationError(
                f"File exceeds "
                f"{MAX_BLEND_FILE_SIZE // (1024 * 1024)}MB limit."
            )

        # Magic bytes check
        header = value.read(7)
        value.seek(0)
        if header != BLEND_MAGIC_BYTES:
            raise serializers.ValidationError(
                "File does not appear to be a valid .blend file."
            )

        return value
