# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ..models import Animation, Job, JobStatus, Project, SupportedBlenderVersion, TiledJob
from ..models.jobs import TiledJobStatus
from ..permissions import IsAdmin
from ..serializers import ProjectSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    cancel_all_jobs=extend_schema(tags=['Management UI']),
)
class ProjectViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating, retrieving, and managing rendering projects.

    Projects serve as the top-level organizational entity for assets and jobs.
    """
    permission_classes = [IsAdmin]
    queryset = Project.objects.select_related('blender_version').order_by('-created_at')
    serializer_class = ProjectSerializer

    def perform_create(self, serializer):
        if SupportedBlenderVersion.objects.count() == 0:
            raise ValidationError(
                "At least one supported Blender version must be "
                "configured before creating projects."
            )
        serializer.save()

    @action(detail=True, methods=['post'], url_path='cancel_all_jobs')
    def cancel_all_jobs(self, request, pk=None):
        """
        Cancel all QUEUED and RENDERING jobs for this project.

        Uses a single bulk update for efficiency and wraps the entire
        operation in a transaction. After canceling jobs, updates parent
        TiledJob and Animation objects whose children are all terminal.

        Returns:
            {"canceled": <count>} with the number of jobs affected.
        """
        project = self.get_object()

        with transaction.atomic():
            # Bulk cancel all active jobs
            count = Job.objects.filter(
                asset__project=project,
                status__in=[JobStatus.QUEUED, JobStatus.RENDERING],
            ).update(
                status=JobStatus.CANCELED,
                completed_at=timezone.now(),
                assigned_worker=None,
                is_paused=False,
            )

            if count > 0:
                # Update parent TiledJobs whose children are all terminal
                terminal = [JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED]
                TiledJob.objects.filter(
                    project=project,
                ).exclude(
                    status__in=[TiledJobStatus.DONE, TiledJobStatus.ERROR],
                ).annotate(
                    non_terminal=Count(
                        'jobs', filter=~Q(jobs__status__in=terminal)
                    ),
                ).filter(
                    non_terminal=0,
                ).update(
                    status=TiledJobStatus.ERROR,
                    completed_at=timezone.now(),
                )

                # Update parent Animations whose children are all terminal
                Animation.objects.filter(
                    project=project,
                ).exclude(
                    status__in=[JobStatus.DONE, JobStatus.ERROR],
                ).annotate(
                    non_terminal=Count(
                        'jobs', filter=~Q(jobs__status__in=terminal)
                    ),
                ).filter(
                    non_terminal=0,
                ).update(
                    status=JobStatus.ERROR,
                    completed_at=timezone.now(),
                )

        logger.info(
            f"Canceled {count} jobs for project '{project.name}' "
            f"(ID: {project.id})."
        )
        return Response({"canceled": count})
