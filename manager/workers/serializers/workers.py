# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Worker model.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from ..constants import WORKER_STALENESS_SECONDS
from ..models import Worker


class WorkerSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Worker` model.
    """
    has_token = serializers.SerializerMethodField()
    last_heartbeat = serializers.DateTimeField(
        source='last_seen', read_only=True,
    )
    status = serializers.SerializerMethodField()

    class Meta:
        model = Worker
        fields = [
            'id', 'hostname', 'ip_address', 'os', 'last_seen',
            'last_heartbeat', 'is_active', 'available_tools', 'ui_url',
            'has_token', 'cpu_name', 'gpu_name', 'status',
        ]
        read_only_fields = ['last_seen', 'last_heartbeat', 'has_token']

    def get_status(self, obj):
        """Return OFFLINE if heartbeat is stale, otherwise the stored status."""
        now = self.context.get('now') or timezone.now()
        staleness_threshold = timedelta(seconds=WORKER_STALENESS_SECONDS)
        if not obj.last_seen:
            return 'OFFLINE'
        if (now - obj.last_seen) > staleness_threshold:
            return 'OFFLINE'
        return obj.status

    def get_has_token(self, obj):
        """Return whether the worker has an active auth token."""
        if hasattr(obj, '_has_token'):
            return obj._has_token
        if not obj.user_id:
            return False
        from rest_framework.authtoken.models import Token
        return Token.objects.filter(user_id=obj.user_id).exists()
