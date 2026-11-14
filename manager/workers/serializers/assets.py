# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Asset model, handling file uploads.
"""

from rest_framework import serializers

from ..constants import AssetSourceType
from ..models import Asset, Project
from .projects import ProjectSerializer
from .renderer_checks import check_immutable_and_consistent, resolve_source_strategy

# 10 GB — asset-specific limit (separate from Django's global setting)
MAX_BLEND_FILE_SIZE = 10 * 1024 * 1024 * 1024

# .blend files start with one of these magic byte sequences:
# - Uncompressed: b'BLENDER' (7 bytes)
# - Gzip-compressed: b'\x1f\x8b' (2 bytes)
# - Zstd-compressed (Blender 3.0+): b'\x28\xb5\x2f\xfd' (4 bytes)
BLEND_MAGIC_BYTES = b'BLENDER'
GZIP_MAGIC_BYTES = b'\x1f\x8b'
ZSTD_MAGIC_BYTES = b'\x28\xb5\x2f\xfd'

# The path rules check leading/trailing spaces themselves (FR-MU-6/7/15),
# so DRF must hand them the raw value.
_UNTRIMMED = {'trim_whitespace': False}


class AssetSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Asset` model, handling file uploads.
    """
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    project_details = ProjectSerializer(source='project', read_only=True)

    class Meta:
        model = Asset
        fields = [
            'id', 'name', 'source_type', 'blend_file',
            'archive_path', 'uproject_path', 'archive_sha256',
            'overlay_path', 'overlay_sha256',
            'created_at', 'project', 'project_details',
        ]
        read_only_fields = ['created_at', 'project_details']
        extra_kwargs = {
            'project': {'write_only': True},
            'archive_path': _UNTRIMMED,
            'overlay_path': _UNTRIMMED,
            'uproject_path': _UNTRIMMED,
        }

    def _effective_source_type(self):
        """``instance.source_type`` on update, else the request's value."""
        if isinstance(self.instance, Asset):
            return self.instance.source_type
        initial = getattr(self, 'initial_data', None)
        if hasattr(initial, 'get'):
            return initial.get('source_type', AssetSourceType.BLEND_FILE)
        return AssetSourceType.BLEND_FILE

    def get_fields(self):
        """Require ``blend_file`` exactly for ``blend_file`` assets (FR-MR-10).

        ``Asset.blend_file`` is ``blank=True`` (shared-project assets have
        none), which would make DRF build it ``required=False``.  Restoring
        ``required`` here lets DRF's own field code produce today's errors
        byte for byte: a missing key, ``blend_file=''`` and a PUT without a
        file.  Schema generation has no ``initial_data`` and shows it required.
        """
        fields = super().get_fields()
        fields['blend_file'].required = (
            self._effective_source_type() == AssetSourceType.BLEND_FILE
        )
        return fields

    def validate(self, attrs):
        """Generic immutability checks, then the source type's strategy."""
        check_immutable_and_consistent(self.instance, attrs)
        source_type = (
            self.instance.source_type if self.instance is not None
            else attrs.get('source_type', AssetSourceType.BLEND_FILE)
        )
        strategy = resolve_source_strategy(source_type)
        return strategy.validate_asset(attrs, self.instance)

    def create(self, validated_data):
        """Insert the asset with the project locked first, then the hook (FR-PM-10)."""
        source_type = validated_data.get('source_type', AssetSourceType.BLEND_FILE)
        strategy = resolve_source_strategy(source_type)
        # Blender: no transaction, so a .blend upload holds no lock (FR-PM-8).
        with strategy.locked_write(validated_data['project']):
            asset = super().create(validated_data)
            strategy.after_asset_create(asset)
        return asset

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
