#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views.py

from rest_framework.response import Response
from rest_framework import status
from .models import Worker, Job, JobStatus, Animation, Asset, Project, TiledJob, AnimationFrame
from .serializers import WorkerSerializer, JobSerializer, AnimationSerializer, AssetSerializer, ProjectSerializer, \
    TiledJobSerializer
from .constants import RenderSettings, TilingConfiguration, RenderEngine, CyclesFeatureSet, RenderDevice
from django.utils import timezone
from .image_utils import generate_thumbnail

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FileUploadParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class ProjectViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating, retrieving, and managing rendering projects.

    Projects serve as the top-level organizational entity for assets and jobs.
    They can be paused to temporarily stop workers from processing their jobs.
    """
    queryset = Project.objects.all().order_by('-created_at')
    serializer_class = ProjectSerializer

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        """
        Pauses a project, preventing workers from picking up any of its jobs.

        Args:
            request: The request object.
            pk: The primary key of the project to pause.

        Returns:
            A Response containing the updated project data.
        """
        project = self.get_object()
        project.is_paused = True
        project.save(update_fields=['is_paused'])
        logger.info(f"Project '{project.name}' (ID: {project.id}) has been paused.")
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=['post'])
    def unpause(self, request, pk=None):
        """
        Unpauses a project, allowing workers to resume processing its jobs.

        Args:
            request: The request object.
            pk: The primary key of the project to unpause.

        Returns:
            A Response containing the updated project data.
        """
        project = self.get_object()
        project.is_paused = False
        project.save(update_fields=['is_paused'])
        logger.info(f"Project '{project.name}' (ID: {project.id}) has been unpaused.")
        return Response(self.get_serializer(project).data)


class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register with the manager.

    A POST request with full system information will either register a new worker
    or update an existing one. Subsequent POSTs with just the hostname will
    simply update the 'last_seen' timestamp.
    """

    def list(self, request):
        """Lists all registered workers."""
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        return Response(serializer.data)

    def create(self, request):
        """
        Handles worker registration and periodic heartbeats.

        Args:
            request: The request object containing worker data.

        Returns:
            A Response containing the worker's data.
        """
        hostname = request.data.get('hostname')
        if not hostname:
            return Response({"detail": "Hostname is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Differentiate between a full registration and a simple heartbeat
        is_full_registration = 'os' in request.data or 'available_tools' in request.data

        if is_full_registration:
            # Handle initial registration or a full update of worker info
            worker, created = Worker.objects.update_or_create(
                hostname=hostname,
                defaults={
                    'ip_address': request.data.get('ip_address'),
                    'os': request.data.get('os'),
                    'available_tools': request.data.get('available_tools', {}),
                    'last_seen': timezone.now(),
                    'is_active': True
                }
            )
            log_msg = "registration/full update" if not created else "registration"
            logger.info(f"Worker {log_msg}. Hostname: {worker.hostname}")
        else:
            # Handle a simple, periodic heartbeat to keep the worker alive
            try:
                worker = Worker.objects.get(hostname=hostname)
                worker.last_seen = timezone.now()
                worker.is_active = True
                worker.save(update_fields=['last_seen', 'is_active'])
                logger.debug(f"Worker periodic heartbeat. Hostname: {worker.hostname}")
            except Worker.DoesNotExist:
                return Response(
                    {"detail": "Worker not found. Please re-register with full system info."},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = WorkerSerializer(worker)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AnimationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing multi-frame animation jobs.

    A POST request to this endpoint will create a parent `Animation` object
    and automatically spawn a child `Job` for each frame in the sequence,
    or a grid of `Job`s for tiled animations.
    """
    queryset = Animation.objects.all().order_by('-submitted_at')
    serializer_class = AnimationSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'project']

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
            tile_counts = [int(i) for i in animation.tiling_config.split('x')]
            tile_count_x, tile_count_y = tile_counts[0], tile_counts[1]
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


class TiledJobViewSet(viewsets.ModelViewSet):
    """
    API endpoint for creating and managing Tiled Render jobs.

    A POST request to this endpoint will create a parent `TiledJob` object and
    automatically spawn a child `Job` for each tile in the specified grid.
    These tile jobs contain the necessary render border overrides.
    """
    queryset = TiledJob.objects.all().order_by('-submitted_at')
    serializer_class = TiledJobSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['status', 'project']

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


class AssetViewSet(viewsets.ModelViewSet):
    """
    API endpoint for uploading and managing .blend file assets.
    Assets are uploaded as multipart/form-data.
    """
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    parser_classes = (MultiPartParser, FileUploadParser)
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project']


class JobViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows render jobs to be viewed or created.

    Workers use this endpoint to poll for new jobs, claim them for rendering,
    and update their status and final output.
    """
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'assigned_worker', 'animation', 'asset__project', 'tiled_job']
    search_fields = ['name', 'asset__name', 'asset__project__name']
    ordering_fields = ['submitted_at', 'status', 'name']

    def get_queryset(self):
        """
        Overrides the default queryset to allow filtering based on worker GPU capability
        and to exclude jobs from paused projects when workers poll for jobs.

        If a worker polls with `gpu_available=true`, only jobs that can use a GPU are returned.
        If a worker polls with `gpu_available=false`, only jobs that can use a CPU are returned.
        If a worker is not polling (i.e., this is a regular API request), all jobs are returned.
        """
        queryset = super().get_queryset()
        gpu_available_param = self.request.query_params.get('gpu_available')

        # A worker poll is identified by the presence of these specific query parameters.
        is_worker_poll = 'status' in self.request.query_params and 'assigned_worker__isnull' in self.request.query_params

        # Filter out jobs from paused projects ONLY when a worker is polling for available work.
        # This allows direct access to a job's details via its ID even if paused.
        if is_worker_poll:
            queryset = queryset.filter(asset__project__is_paused=False)

        if gpu_available_param == 'true':
            logger.debug("Filtering jobs for a GPU-capable worker. Including GPU and ANY jobs.")
            return queryset.filter(render_device__in=[RenderDevice.GPU, RenderDevice.ANY])
        elif gpu_available_param == 'false':
            logger.debug("Filtering jobs for a CPU-only worker. Including CPU and ANY jobs.")
            return queryset.filter(render_device__in=[RenderDevice.CPU, RenderDevice.ANY])

        return queryset

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancels a render job, setting its status to 'CANCELED'.

        Args:
            request: The request object.
            pk: The primary key of the job to cancel.

        Returns:
            A Response containing the updated job data.
        """
        job = self.get_object()
        old_status = job.status
        job.status = JobStatus.CANCELED
        if not job.completed_at:
            job.completed_at = timezone.now()
        job.save()
        logger.info(f"Job '{job.name}' (ID: {job.id}) CANCELED. Status: {old_status} -> {job.status}.")
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser])
    def upload_output(self, request, pk=None):
        """
        Action for a worker to upload the final rendered output file for a job.

        Expects a multipart/form-data request with a file field named `output_file`.
        Saving the file will trigger a signal to generate the thumbnail.

        Args:
            request: The request object containing the uploaded file.
            pk: The primary key of the job to attach the file to.

        Returns:
            A Response containing the updated job data.
        """
        job = self.get_object()
        file_obj = request.data.get('output_file')

        if not file_obj:
            return Response(
                {"error": "Missing 'output_file' in request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        job.output_file.save(file_obj.name, file_obj, save=True)

        logger.info(f"Received output file for job ID {job.id}. Saved to {job.output_file.name}")
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)