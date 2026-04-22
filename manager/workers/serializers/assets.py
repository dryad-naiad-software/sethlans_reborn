# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Asset model, handling file uploads.
"""

from rest_framework import serializers
from ..models import Asset, Project
from .projects import ProjectSerializer

# 10 GB — asset-specific limit (separate from Django's global setting)
MAX_BLEND_FILE_SIZE = 10 * 1024 * 1024 * 1024

# .blend files start with one of these magic byte sequences:
# - Uncompressed: b'BLENDER' (7 bytes)
# - Gzip-compressed: b'\x1f\x8b' (2 bytes)
# - Zstd-compressed (Blender 3.0+): b'\x28\xb5\x2f\xfd' (4 bytes)
BLEND_MAGIC_BYTES = b'BLENDER'
GZIP_MAGIC_BYTES = b'\x1f\x8b'
ZSTD_MAGIC_BYTES = b'\x28\xb5\x2f\xfd'


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
        2. File size must not exceed 10 GB
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

        # Magic bytes check — Blender files may be uncompressed,
        # gzip-compressed, or zstd-compressed.
        header = value.read(7)
        value.seek(0)
        is_valid = (
            header[:7] == BLEND_MAGIC_BYTES
            or header[:2] == GZIP_MAGIC_BYTES
            or header[:4] == ZSTD_MAGIC_BYTES
        )
        if not is_valid:
            raise serializers.ValidationError(
                "File does not appear to be a valid .blend file."
            )

        return value
