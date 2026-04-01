# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/tiled_jobs.py

import logging
import os

from django.db import transaction
from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from ..constants import RenderEngine, RenderSettings
from ..models import Job, JobStatus, TiledJob
from ..serializers import TiledJobSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
)
class TiledJobViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing Tiled Render jobs.

    A POST request to this endpoint will create a parent `TiledJob` object and
    automatically spawn a child `Job` for each tile in the specified grid.
    These tile jobs contain the necessary render border overrides.
    """
    serializer_class = TiledJobSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'project']

    def get_queryset(self):
        return TiledJob.objects.annotate(
            annotated_completed_tiles=Count(
                'jobs', filter=Q(jobs__status=JobStatus.DONE)
            ),
        ).select_related(
            'project', 'asset', 'asset__project'
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

                output_pattern = os.path.join(tile_output_dir, f"tile_{y}_{x}_####")

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
