# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/serializers/workers.py
"""
Serializer for the Worker model.
"""

from rest_framework import serializers
from ..models import Worker


class WorkerSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Worker` model.
    """
    has_token = serializers.SerializerMethodField()

    class Meta:
        model = Worker
        fields = [
            'id', 'hostname', 'ip_address', 'os', 'last_seen',
            'is_active', 'available_tools', 'ui_url', 'has_token',
        ]
        read_only_fields = ['last_seen', 'has_token']

    def get_has_token(self, obj):
        """Return whether the worker has an active auth token."""
        if not obj.user_id:
            return False
        from rest_framework.authtoken.models import Token
        return Token.objects.filter(user_id=obj.user_id).exists()
