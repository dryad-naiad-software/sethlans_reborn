# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Asset model, handling file uploads.
"""

from rest_framework import serializers
from ..models import Asset, Project
from .projects import ProjectSerializer


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
