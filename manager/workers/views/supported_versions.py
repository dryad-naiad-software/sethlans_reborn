# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
ViewSet for managing supported Blender versions (CRUD + version removal).

Write operations require admin privileges. Read access is available to
both admins and authenticated workers.
"""

import logging

from django.db import models, transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..utils.blender_series_cache import (
    get_available_series, trigger_background_refresh,
)

from ..models import (
    Animation, Job, Project, SupportedBlenderVersion, TiledJob, JobStatus,
)
from ..permissions import IsAdminOrWorkerReadOnly
from ..serializers import SupportedBlenderVersionSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
)
class SupportedBlenderVersionViewSet(viewsets.ModelViewSet):
    """
    CRUD endpoint for supported Blender versions.

    DELETE requires ``?confirm=true`` to execute; without it, returns a
    dry-run preview of affected projects and jobs.
    """
    permission_classes = [IsAdminOrWorkerReadOnly]
    serializer_class = SupportedBlenderVersionSerializer
    queryset = SupportedBlenderVersion.objects.all()

    @extend_schema(tags=['Management UI'])
    @action(detail=False, methods=['get'], url_path='available_series')
    def available_series(self, request):
        """Return Blender series from download.blender.org, minus already-added."""
        trigger_background_refresh()
        data = get_available_series()
        existing = set(
            SupportedBlenderVersion.objects.values_list('series', flat=True)
        )
        filtered = [s for s in data['series'] if s not in existing]
        return Response({
            'series': filtered,
            'cache_ready': data['cache_ready'],
        })

    def destroy(self, request, *args, **kwargs):
        version = self.get_object()
        confirm = request.query_params.get('confirm', '').lower() == 'true'

        # Cannot delete the last version
        if SupportedBlenderVersion.objects.count() <= 1:
            return Response(
                {"error": "Cannot remove the last supported version."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = self._find_migration_target(version)
        affected_projects = Project.objects.filter(
            blender_version=version,
        ).count()
        affected_jobs = self._count_affected_jobs(version)
        warning = self._build_warning(
            affected_projects, version.series, target.series,
        )

        if not confirm:
            return Response({
                "affected_project_count": affected_projects,
                "affected_job_count": affected_jobs,
                "migration_target": {
                    "series": target.series,
                    "resolved_version": target.resolved_version,
                },
                "warning": warning,
            }, status=status.HTTP_200_OK)

        return self._execute_removal(version, target, warning)

    # -- Private helpers --

    @staticmethod
    def _find_migration_target(version):
        """Find the next highest series, or the highest available."""
        higher = SupportedBlenderVersion.objects.filter(
            models.Q(major__gt=version.major)
            | models.Q(major=version.major, minor__gt=version.minor),
        ).exclude(pk=version.pk).order_by('major', 'minor').first()

        if higher:
            return higher

        return SupportedBlenderVersion.objects.exclude(
            pk=version.pk,
        ).order_by('-major', '-minor').first()

    @staticmethod
    def _count_affected_jobs(version):
        """Count jobs with explicit FK to this version (non-RENDERING)."""
        job_count = Job.objects.filter(
            blender_version=version,
        ).exclude(status=JobStatus.RENDERING).count()
        tiled_count = TiledJob.objects.filter(
            blender_version=version,
        ).count()
        anim_count = Animation.objects.filter(
            blender_version=version,
        ).count()
        return job_count + tiled_count + anim_count

    @staticmethod
    def _build_warning(project_count, old_series, new_series):
        return (
            f"{project_count} projects will be migrated from series "
            f"{old_series} to series {new_series}. Render output may "
            f"differ due to engine changes between versions."
        )

    @transaction.atomic
    def _execute_removal(self, version, target, warning):
        """Lock, migrate, null FKs, reassign default, delete."""
        # Lock the version row to prevent concurrent project creation
        locked = SupportedBlenderVersion.objects.select_for_update().get(
            pk=version.pk,
        )

        migrated = Project.objects.filter(
            blender_version=locked,
        ).update(blender_version=target)

        # Null out explicit job FKs (skip RENDERING jobs)
        job_count = Job.objects.filter(
            blender_version=locked,
        ).exclude(status=JobStatus.RENDERING).update(
            blender_version=None,
        )
        tiled_count = TiledJob.objects.filter(
            blender_version=locked,
        ).update(blender_version=None)
        anim_count = Animation.objects.filter(
            blender_version=locked,
        ).update(blender_version=None)

        new_default = None
        if locked.is_default:
            target.is_default = True
            target.save()
            new_default = target.series

        locked.delete()

        total_jobs = job_count + tiled_count + anim_count
        logger.info(
            "Removed Blender version %s. Migrated %d projects and "
            "%d jobs to %s.%s",
            version.series, migrated, total_jobs, target.series,
            f" New default: {new_default}" if new_default else "",
        )

        return Response({
            "migrated_project_count": migrated,
            "affected_job_count": total_jobs,
            "new_default_version": new_default,
            "warning": warning,
        }, status=status.HTTP_200_OK)
