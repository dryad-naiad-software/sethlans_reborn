# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the Worker model.
"""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from ..constants import WORKER_STALENESS_SECONDS
from ..models import Worker

# OpenAPI shapes of the self-reported JSON fields (spec FR-RT-7, FR-RT-16).
# Every key is optional: a worker that never reported stores {}, and
# older workers omit share/share_reachable.
_UNREAL_CAPABILITIES_SCHEMA = inline_serializer('WorkerUnrealCapabilities', {
    'schema': serializers.IntegerField(required=False),
    'enabled': serializers.BooleanField(required=False),
    'disabled_reason': serializers.CharField(required=False),
    'installs': serializers.ListField(required=False, child=inline_serializer('WorkerUnrealInstall', {
        'build': serializers.CharField(required=False),
        'supported': serializers.BooleanField(required=False),
        'source': serializers.ChoiceField(choices=['launcher', 'config'], required=False),
    })),
    'share': serializers.CharField(required=False),
    'share_reachable': serializers.BooleanField(required=False),
}, required=False)
_GPU_MEMORY_SCHEMA = inline_serializer('WorkerGpuMemory', {
    'schema': serializers.IntegerField(required=False),
    'source': serializers.ChoiceField(choices=['nvidia-smi', 'override'], required=False),
    'age_s': serializers.IntegerField(required=False),
    'gpus': serializers.ListField(required=False, child=inline_serializer('WorkerGpuMemoryEntry', {
        'index': serializers.IntegerField(required=False),
        'name': serializers.CharField(required=False),
        'total_mb': serializers.IntegerField(required=False),
        'used_mb': serializers.IntegerField(required=False),
        'sethlans_used_mb': serializers.IntegerField(required=False, allow_null=True),
        'other_used_mb': serializers.IntegerField(required=False, allow_null=True),
    })),
}, required=False)


@extend_schema_field(_UNREAL_CAPABILITIES_SCHEMA)
class UnrealCapabilitiesField(serializers.JSONField):
    """Self-reported Unreal capabilities (``{}`` when never reported)."""


@extend_schema_field(_GPU_MEMORY_SCHEMA)
class GpuMemoryField(serializers.JSONField):
    """Self-reported per-GPU memory reading (``{}`` when never reported)."""


class WorkerSerializer(serializers.ModelSerializer):
    """
    Serializer for the `Worker` model.

    ``boot_id`` is deliberately not exposed (spec FR-RT-8, NF-8).
    """
    has_token = serializers.SerializerMethodField()
    last_heartbeat = serializers.DateTimeField(
        source='last_seen', read_only=True,
    )
    status = serializers.SerializerMethodField()
    unreal_capabilities = UnrealCapabilitiesField(read_only=True)
    gpu_memory = GpuMemoryField(read_only=True)

    class Meta:
        model = Worker
        fields = [
            'id', 'hostname', 'ip_address', 'os', 'last_seen',
            'last_heartbeat', 'is_active', 'available_tools', 'ui_url',
            'has_token', 'cpu_name', 'gpu_name', 'status',
            'schedule_config', 'ui_cert_fingerprint',
            'unreal_capabilities', 'gpu_memory',
        ]
        read_only_fields = [
            'last_seen', 'last_heartbeat', 'has_token',
            'schedule_config', 'ui_cert_fingerprint',
            'unreal_capabilities', 'gpu_memory',
        ]

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
