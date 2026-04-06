# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import os
import tempfile
import zipfile

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils.text import slugify
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..constants import RenderEngine, RenderSettings, TilingConfiguration
from ..models import Animation, AnimationFrame, Job, JobStatus
from ..permissions import IsAdmin
from ..serializers import AnimationSerializer

logger = logging.getLogger(__name__)

MAX_DOWNLOADABLE_FRAMES = 1000


@extend_schema_view(
    list=extend_schema(tags=['Management UI']),
    retrieve=extend_schema(tags=['Management UI']),
    create=extend_schema(tags=['Management UI']),
    update=extend_schema(tags=['Management UI']),
    partial_update=extend_schema(tags=['Management UI']),
    destroy=extend_schema(tags=['Management UI']),
    pause=extend_schema(tags=['Management UI']),
    unpause=extend_schema(tags=['Management UI']),
)
class AnimationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing multi-frame animation jobs.

    A POST request to this endpoint will create a parent `Animation` object
    and automatically spawn a child `Job` for each frame in the sequence,
    or a grid of `Job`s for tiled animations.
    """
    permission_classes = [IsAdmin]
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
            'project', 'asset', 'asset__project',
            'blender_version', 'asset__project__blender_version',
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
                        output_pattern = os.path.join(tile_output_dir, f"tile_{y}_{x}_####.png")

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

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """
        Download a zip archive of all completed frame output files for this animation.
        """
        animation = self.get_object()
        media_root = os.path.realpath(settings.MEDIA_ROOT)

        # Determine frame source based on tiling configuration.
        if animation.tiling_config == TilingConfiguration.NONE:
            source_items = Job.objects.filter(
                animation=animation, status='DONE'
            ).order_by('start_frame')
            frame_entries = [
                (job.start_frame, job.output_file) for job in source_items
            ]
        else:
            source_items = AnimationFrame.objects.filter(
                animation=animation, status='DONE'
            ).order_by('frame_number')
            frame_entries = [
                (frame.frame_number, frame.output_file) for frame in source_items
            ]

        # Enforce hard frame limit.
        if len(frame_entries) > MAX_DOWNLOADABLE_FRAMES:
            return Response(
                {"error": "Animation exceeds maximum downloadable frame count (1000)."},
                status=413,
            )

        if not frame_entries:
            return Response(
                {"error": "No completed frames available for download."},
                status=404,
            )

        # Collect valid files, skipping missing ones.
        valid_files = []
        for frame_number, file_field in frame_entries:
            if not file_field:
                logger.warning(
                    "Animation %s frame %d: output_file field is empty, skipping.",
                    animation.id, frame_number,
                )
                continue
            abs_path = os.path.realpath(file_field.path)
            if not abs_path.startswith(media_root):
                logger.warning(
                    "Animation %s frame %d: path %s is outside MEDIA_ROOT, skipping.",
                    animation.id, frame_number, abs_path,
                )
                continue
            if not os.path.isfile(abs_path):
                logger.warning(
                    "Animation %s frame %d: file %s does not exist on disk, skipping.",
                    animation.id, frame_number, abs_path,
                )
                continue
            valid_files.append((frame_number, abs_path))

        if not valid_files:
            return Response(
                {"error": "No completed frames available for download."},
                status=404,
            )

        # Build zip archive.
        tmp = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for frame_number, file_path in valid_files:
                ext = os.path.splitext(file_path)[1] or '.png'
                zf.write(file_path, arcname=f"frame_{frame_number:04d}{ext}")
        tmp.seek(0)

        safe_name = slugify(animation.name) or 'animation'
        response = FileResponse(tmp, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{safe_name}.zip"'
        return response

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """Cascade pause: pause all QUEUED child jobs."""
        animation = self.get_object()
        with transaction.atomic():
            count = Job.objects.filter(
                animation=animation,
                status=JobStatus.QUEUED,
                is_paused=False,
            ).update(is_paused=True)
        logger.info(
            f"Cascade paused {count} child jobs for "
            f"Animation '{animation.name}' (ID: {animation.id})."
        )
        return Response({"paused": count})

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """Cascade unpause: unpause all paused QUEUED child jobs."""
        animation = self.get_object()
        with transaction.atomic():
            count = Job.objects.filter(
                animation=animation,
                status=JobStatus.QUEUED,
                is_paused=True,
            ).update(is_paused=False)
        logger.info(
            f"Cascade unpaused {count} child jobs for "
            f"Animation '{animation.name}' (ID: {animation.id})."
        )
        return Response({"unpaused": count})
