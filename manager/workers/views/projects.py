# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ..models import Asset, Job, JobStatus, Project, SupportedBlenderVersion
from ..parent_dispatch import dispatch_parents
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
        operation in a transaction. After canceling jobs, refreshes the
        parent TiledJob / AnimationFrame / Animation of each canceled job
        with the shared parent-status rules (ERROR once all children are
        terminal and one did not finish).

        Returns:
            {"canceled": <count>} with the number of jobs affected.
        """
        project = self.get_object()

        with transaction.atomic():
            # Bulk cancel all active jobs (the first statement is a write,
            # so SQLite takes its write lock before anything is read).
            canceled_at = timezone.now()
            # ``asset_id__in`` (not ``asset__project``) keeps the status
            # predicate on the UPDATE's own table.  A joined filter compiles
            # to ``WHERE id IN (subquery with the status filter)``, and after
            # a lock wait PostgreSQL rechecks only ``id IN (...)``: a DONE
            # committed meanwhile would be overwritten with CANCELED.
            count = Job.objects.filter(
                asset_id__in=Asset.objects.filter(project=project).values('pk'),
                status__in=[JobStatus.QUEUED, JobStatus.RENDERING],
            ).update(
                status=JobStatus.CANCELED,
                completed_at=canceled_at,
                assigned_worker=None,
                is_paused=False,
            )
            if count > 0:
                # Refresh only the parents of the jobs canceled here, with
                # the shared rules (issue #219).  Child rows are already
                # locked by the UPDATE, so lock order (child -> parent) holds.
                dispatch_parents(Job.objects.filter(
                    asset__project=project,
                    status=JobStatus.CANCELED,
                    completed_at=canceled_at,
                ).values_list(
                    'tiled_job_id', 'animation_id', 'animation_frame_id',
                ).distinct())

        logger.info(
            f"Canceled {count} jobs for project '{project.name}' "
            f"(ID: {project.id})."
        )
        return Response({"canceled": count})
