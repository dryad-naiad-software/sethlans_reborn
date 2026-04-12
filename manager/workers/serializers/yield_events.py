# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Serializer for the WorkerYieldEvent model.
"""

from rest_framework import serializers

from ..models import WorkerYieldEvent


class WorkerYieldEventSerializer(serializers.ModelSerializer):
    """Serializer for yield events reported by worker agents."""

    class Meta:
        model = WorkerYieldEvent
        fields = [
            'id', 'worker', 'timestamp', 'reason',
            'grace_outcome', 'progress_at_yield', 'job', 'job_name',
        ]
        read_only_fields = ['id', 'worker', 'timestamp']

    def validate_progress_at_yield(self, value):
        if not (0.0 <= value <= 1.0):
            raise serializers.ValidationError(
                "progress_at_yield must be between 0.0 and 1.0."
            )
        return value
