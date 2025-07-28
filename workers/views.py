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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views.py

from rest_framework.response import Response
from rest_framework import status
from .models import Worker, Job, JobStatus, Animation, Asset
from .serializers import WorkerSerializer, JobSerializer, AnimationSerializer, AssetSerializer
from django.utils import timezone

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FileUploadParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

import logging

logger = logging.getLogger(__name__)


class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register themselves.
    """

    def list(self, request):
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        return Response(serializer.data)

    def create(self, request):
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
    API endpoint for creating and viewing Animation jobs.
    """
    queryset = Animation.objects.all().order_by('-submitted_at')
    serializer_class = AnimationSerializer

    def perform_create(self, serializer):
        animation = serializer.save(status='QUEUED')
        logger.info(f"Created new animation '{animation.name}' (ID: {animation.id}). Spawning frame jobs...")

        jobs_to_create = []
        for frame_num in range(animation.start_frame, animation.end_frame + 1):
            job = Job(
                animation=animation,
                name=f"{animation.name}_Frame_{frame_num:04d}",
                asset=animation.asset, # <-- UPDATED
                output_file_pattern=animation.output_file_pattern,
                start_frame=frame_num,
                end_frame=frame_num,
                blender_version=animation.blender_version,
                render_engine=animation.render_engine,
                render_device=animation.render_device,
                render_settings=animation.render_settings,
            )
            jobs_to_create.append(job)

        Job.objects.bulk_create(jobs_to_create)
        logger.info(f"Successfully spawned {len(jobs_to_create)} frame jobs for animation ID {animation.id}.")


class AssetViewSet(viewsets.ModelViewSet):
    """
    API endpoint for uploading and managing .blend file assets.
    """
    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    parser_classes = (MultiPartParser, FileUploadParser)


class JobViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows render jobs to be viewed or created.
    """
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'assigned_worker', 'animation']
    search_fields = ['name', 'asset__name'] # <-- UPDATED
    ordering_fields = ['submitted_at', 'status', 'name']

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        job = self.get_object()
        old_status = job.status
        job.status = JobStatus.CANCELED
        if not job.completed_at:
            job.completed_at = timezone.now()
        job.save()
        logger.info(f"Job '{job.name}' (ID: {job.id}) CANCELED. Status: {old_status} -> {job.status}.")
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)