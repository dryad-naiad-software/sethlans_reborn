# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/serializers/projects.py
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
