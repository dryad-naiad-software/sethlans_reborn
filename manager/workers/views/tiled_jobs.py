# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import os

from django.db import transaction
from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import FORMAT_EXTENSIONS, RenderEngine, RenderSettings
from ..models import Job, JobStatus, TiledJob
from ..permissions import IsAdmin
from ..serializers import TiledJobSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    pause=extend_schema(tags=['Management UI']),
    unpause=extend_schema(tags=['Management UI']),
    requeue=extend_schema(tags=['Management UI']),
)
class TiledJobViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing Tiled Render jobs.

    A POST request to this endpoint will create a parent `TiledJob` object and
    automatically spawn a child `Job` for each tile in the specified grid.
    These tile jobs contain the necessary render border overrides.
    """
    permission_classes = [IsAdmin]
    serializer_class = TiledJobSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'project']

    def get_queryset(self):
        return TiledJob.objects.annotate(
            annotated_completed_tiles=Count(
                'jobs', filter=Q(jobs__status=JobStatus.DONE)
            ),
        ).select_related(
            'project', 'asset', 'asset__project',
            'blender_version', 'asset__project__blender_version',
        ).order_by('-submitted_at')

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Spawns individual `Job` objects for each tile of the render after the parent
        `TiledJob` object is created.
        """
        tiled_job = serializer.save()
        logger.info(f"Created new TiledJob '{tiled_job.name}' (ID: {tiled_job.id}). Spawning tile jobs...")

        # Prepare the base render settings that will be injected into child jobs.
        base_render_settings = tiled_job.render_settings.copy()
        base_render_settings[RenderSettings.RENDER_ENGINE] = tiled_job.render_engine
        if tiled_job.render_engine == RenderEngine.CYCLES:
            base_render_settings[RenderSettings.CYCLES_FEATURE_SET] = tiled_job.cycles_feature_set

        jobs_to_create = []
        tile_count_x = tiled_job.tile_count_x
        tile_count_y = tiled_job.tile_count_y
        tile_width = 1.0 / tile_count_x
        tile_height = 1.0 / tile_count_y

        tile_output_dir = os.path.join("tiled_jobs", str(tiled_job.id))
        output_format = tiled_job.render_settings.get(
            RenderSettings.IMAGE_FILE_FORMAT, 'PNG'
        )
        tile_ext = FORMAT_EXTENSIONS.get(output_format, '.png')

        for y in range(tile_count_y):
            for x in range(tile_count_x):
                border_min_x = x * tile_width
                border_max_x = (x + 1) * tile_width
                border_min_y = y * tile_height
                border_max_y = (y + 1) * tile_height

                tile_render_settings = base_render_settings.copy()
                tile_render_settings.update({
                    RenderSettings.RESOLUTION_X: tiled_job.final_resolution_x,
                    RenderSettings.RESOLUTION_Y: tiled_job.final_resolution_y,
                    RenderSettings.RESOLUTION_PERCENTAGE: 100,
                    RenderSettings.USE_BORDER: True,
                    RenderSettings.CROP_TO_BORDER: True,
                    RenderSettings.BORDER_MIN_X: round(border_min_x, 6),
                    RenderSettings.BORDER_MAX_X: round(border_max_x, 6),
                    RenderSettings.BORDER_MIN_Y: round(border_min_y, 6),
                    RenderSettings.BORDER_MAX_Y: round(border_max_y, 6),
                })

                output_pattern = os.path.join(
                    tile_output_dir, f"tile_{y}_{x}_####{tile_ext}"
                )

                job = Job(
                    tiled_job=tiled_job,
                    name=f"{tiled_job.name}_Tile_{y}_{x}",
                    asset=tiled_job.asset,
                    output_file_pattern=output_pattern,
                    start_frame=1,
                    end_frame=1,
                    blender_version=tiled_job.blender_version,
                    render_engine=tiled_job.render_engine,
                    render_device=tiled_job.render_device,
                    cycles_feature_set=tiled_job.cycles_feature_set,
                    render_settings=tile_render_settings,
                )
                jobs_to_create.append(job)

        Job.objects.bulk_create(jobs_to_create)
        logger.info(f"Successfully spawned {len(jobs_to_create)} tile jobs for TiledJob ID {tiled_job.id}.")

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Cascade pause: pause all QUEUED child jobs."""
        tiled_job = self.get_object()
        with transaction.atomic():
            count = Job.objects.filter(
                tiled_job=tiled_job,
                status=JobStatus.QUEUED,
                is_paused=False,
            ).update(is_paused=True)
        logger.info(
            f"Cascade paused {count} child jobs for "
            f"TiledJob '{tiled_job.name}' (ID: {tiled_job.id})."
        )
        return Response({"paused": count})

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """Cascade unpause: unpause all paused QUEUED child jobs."""
        tiled_job = self.get_object()
        with transaction.atomic():
            count = Job.objects.filter(
                tiled_job=tiled_job,
                status=JobStatus.QUEUED,
                is_paused=True,
            ).update(is_paused=False)
        logger.info(
            f"Cascade unpaused {count} child jobs for "
            f"TiledJob '{tiled_job.name}' (ID: {tiled_job.id})."
        )
        return Response({"unpaused": count})

    @action(detail=True, methods=['post'])
    def requeue(self, request, pk=None):
        """Cascade requeue: requeue all ERROR/CANCELED child jobs."""
        tiled_job = self.get_object()
        with transaction.atomic():
            count = Job.objects.filter(
                tiled_job=tiled_job,
                status__in=[JobStatus.ERROR, JobStatus.CANCELED],
            ).update(
                status=JobStatus.QUEUED,
                assigned_worker=None,
                started_at=None,
                completed_at=None,
                error_message='',
                last_output='',
                auto_requeue_count=0,
                is_paused=False,
            )
        logger.info(
            f"Cascade requeued {count} child jobs for "
            f"TiledJob '{tiled_job.name}' (ID: {tiled_job.id})."
        )
        return Response({"requeued": count})
