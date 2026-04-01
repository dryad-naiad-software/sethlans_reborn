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

from ..constants import RenderEngine, RenderSettings, TilingConfiguration
from ..models import Animation, AnimationFrame, Job, JobStatus
from ..serializers import AnimationSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
)
class AnimationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing multi-frame animation jobs.

    A POST request to this endpoint will create a parent `Animation` object
    and automatically spawn a child `Job` for each frame in the sequence,
    or a grid of `Job`s for tiled animations.
    """
    serializer_class = AnimationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'project']

    def get_queryset(self):
        return Animation.objects.annotate(
            annotated_completed_jobs=Count(
                'jobs', filter=Q(jobs__status=JobStatus.DONE)
            ),
            annotated_completed_frames=Count(
                'frames', filter=Q(frames__status='DONE')
            ),
        ).select_related(
            'project', 'asset', 'asset__project'
        ).prefetch_related(
            'frames'
        ).order_by('-submitted_at')

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Spawns individual `Job` objects for each frame of the animation after the parent
        `Animation` object is created. Handles both standard and tiled animations.
        """
        animation = serializer.save()
        logger.info(f"Created new animation '{animation.name}' (ID: {animation.id}). Spawning jobs...")

        # Prepare the base render settings that will be injected into child jobs.
        base_render_settings = animation.render_settings.copy()
        base_render_settings[RenderSettings.RENDER_ENGINE] = animation.render_engine
        if animation.render_engine == RenderEngine.CYCLES:
            base_render_settings[RenderSettings.CYCLES_FEATURE_SET] = animation.cycles_feature_set

        jobs_to_create = []

        if animation.tiling_config == TilingConfiguration.NONE:
            # --- Standard Animation Job Spawning ---
            logger.info(f"Spawning standard frame jobs for animation '{animation.name}'.")
            for frame_num in range(animation.start_frame, animation.end_frame + 1, animation.frame_step):
                job = Job(
                    animation=animation,
                    name=f"{animation.name}_Frame_{frame_num:04d}",
                    asset=animation.asset,
                    output_file_pattern=animation.output_file_pattern,
                    start_frame=frame_num,
                    end_frame=frame_num,
                    blender_version=animation.blender_version,
                    render_engine=animation.render_engine,
                    render_device=animation.render_device,
                    cycles_feature_set=animation.cycles_feature_set,
                    render_settings=base_render_settings,
                )
                jobs_to_create.append(job)
        else:
            # --- Tiled Animation Job Spawning ---
            logger.info(f"Spawning tiled jobs for animation '{animation.name}' with config {animation.tiling_config}")
            tile_count_x, tile_count_y = animation.get_tile_counts()
            tile_width = 1.0 / tile_count_x
            tile_height = 1.0 / tile_count_y

            for frame_num in range(animation.start_frame, animation.end_frame + 1, animation.frame_step):
                # Create the parent frame object to group the tiles
                anim_frame = AnimationFrame.objects.create(animation=animation, frame_number=frame_num)

                for y in range(tile_count_y):
                    for x in range(tile_count_x):
                        border_min_x = x * tile_width
                        border_max_x = (x + 1) * tile_width
                        border_min_y = y * tile_height
                        border_max_y = (y + 1) * tile_height

                        tile_render_settings = base_render_settings.copy()
                        tile_render_settings.update({
                            RenderSettings.RESOLUTION_X: animation.render_settings.get(RenderSettings.RESOLUTION_X),
                            RenderSettings.RESOLUTION_Y: animation.render_settings.get(RenderSettings.RESOLUTION_Y),
                            RenderSettings.RESOLUTION_PERCENTAGE: 100,
                            RenderSettings.USE_BORDER: True,
                            RenderSettings.CROP_TO_BORDER: True,
                            RenderSettings.BORDER_MIN_X: round(border_min_x, 6),
                            RenderSettings.BORDER_MAX_X: round(border_max_x, 6),
                            RenderSettings.BORDER_MIN_Y: round(border_min_y, 6),
                            RenderSettings.BORDER_MAX_Y: round(border_max_y, 6),
                        })

                        tile_output_dir = os.path.join("tiled_anim_frames", str(anim_frame.id))
                        output_pattern = os.path.join(tile_output_dir, f"tile_{y}_{x}_####")

                        job = Job(
                            animation=animation,
                            animation_frame=anim_frame,
                            name=f"{animation.name}_Frame_{frame_num:04d}_Tile_{y}_{x}",
                            asset=animation.asset,
                            output_file_pattern=output_pattern,
                            start_frame=frame_num,
                            end_frame=frame_num,
                            blender_version=animation.blender_version,
                            render_engine=animation.render_engine,
                            render_device=animation.render_device,
                            cycles_feature_set=animation.cycles_feature_set,
                            render_settings=tile_render_settings,
                        )
                        jobs_to_create.append(job)

        Job.objects.bulk_create(jobs_to_create)
        logger.info(f"Successfully spawned {len(jobs_to_create)} jobs for animation ID {animation.id}.")
