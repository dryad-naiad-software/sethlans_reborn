# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Dashboard statistics endpoint for the management UI.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes as perm_classes
from rest_framework.response import Response

from ..permissions import IsAdmin

from ..models import Job, Project, Worker


@extend_schema(
    tags=['Management UI'],
    responses=inline_serializer(
        name='DashboardStats',
        fields={
            'workers': serializers.DictField(),
            'jobs': serializers.DictField(),
            'projects': serializers.DictField(),
            'recent_completions': serializers.ListField(),
        }
    )
)
@api_view(['GET'])
@perm_classes([IsAdmin])
def dashboard_stats(request):
    """
    Returns aggregate statistics for the manager dashboard.

    All counts use database-level aggregation to avoid N+1 queries.
    """
    now = timezone.now()
    stale_threshold = now - timedelta(minutes=5)

    worker_stats = Worker.objects.aggregate(
        total=Count('id'),
        active=Count(
            'id',
            filter=Q(last_seen__gte=stale_threshold, is_active=True),
        ),
    )

    job_stats = Job.objects.aggregate(
        queued=Count('id', filter=Q(status='QUEUED')),
        rendering=Count('id', filter=Q(status='RENDERING')),
        done=Count('id', filter=Q(status='DONE')),
        error=Count('id', filter=Q(status='ERROR')),
    )

    project_count = Project.objects.count()

    recent_completions = list(
        Job.objects.filter(status='DONE')
        .exclude(completed_at__isnull=True)
        .order_by('-completed_at')[:10]
        .values('id', 'name', 'completed_at', 'asset__project__name')
    )

    return Response({
        'workers': worker_stats,
        'jobs': job_stats,
        'projects': {'total': project_count},
        'recent_completions': recent_completions,
    })
