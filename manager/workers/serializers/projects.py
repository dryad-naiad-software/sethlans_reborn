# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Project model.
"""

from rest_framework import serializers
from ..models import Project


class ProjectSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Project` model.
    """
    class Meta:
        model = Project
        fields = ['id', 'name', 'created_at', 'is_paused']
        read_only_fields = ['id', 'created_at', 'is_paused']
