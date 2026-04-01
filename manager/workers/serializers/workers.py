# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Worker model.
"""

from rest_framework import serializers
from ..models import Worker


class WorkerSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Worker` model.
    """
    class Meta:
        model = Worker
        fields = ['id', 'hostname', 'ip_address', 'os', 'last_seen', 'is_active', 'available_tools', 'ui_url']
        read_only_fields = ['last_seen']
