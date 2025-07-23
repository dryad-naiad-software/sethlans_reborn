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
#

from rest_framework.response import Response
from rest_framework import status
from .models import Worker, Job, JobStatus
from .serializers import WorkerSerializer, JobSerializer
from django.utils import timezone

from rest_framework import viewsets
# from rest_framework.permissions import IsAuthenticatedOrReadOnly

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters  # <-- ADDED THIS IMPORT!

import logging

logger = logging.getLogger(__name__)


class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register themselves.
    Handles both registration (creation) and listing of workers (for debugging/admin).
    GET /api/heartbeat/ : List all registered workers.
    POST /api/heartbeat/ : Receive heartbeat/registration from a worker.
    """

    def list(self, request):
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        logger.debug("Listing all registered workers.")
        return Response(serializer.data)

    def create(self, request):
        hostname = request.data.get('hostname')
        ip_address = request.data.get('ip_address')
        os_info = request.data.get('os')
        available_tools = request.data.get('available_tools', {})

        if not hostname:
            logger.error("Heartbeat: Hostname is required for worker registration.")
            return Response({"detail": "Hostname is required."}, status=status.HTTP_400_BAD_REQUEST)

        worker, created = Worker.objects.get_or_create(hostname=hostname)

        if ip_address:
            worker.ip_address = ip_address
        if os_info:
            worker.os = os_info

        if available_tools:
            worker.available_tools = available_tools

        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.save()

        logger.info(
            f"Worker heartbeat/registration successful. Hostname: {worker.hostname}, ID: {worker.id}, Created: {created}, Tools: {available_tools}")

        serializer = WorkerSerializer(worker)
        return Response(serializer.data, status=status.HTTP_200_OK)


class JobViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows render jobs to be viewed or created.
    GET /api/jobs/ : List all jobs.
    POST /api/jobs/ : Create a new job.
    GET /api/jobs/{id}/ : Retrieve a specific job.
    PUT /api/jobs/{id}/ : Update a specific job.
    PATCH /api/jobs/{id}/ : Partially update a specific job.
    DELETE /api/jobs/{id}/ : Delete a specific job.
    """
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    # permission_classes = [IsAuthenticatedOrReadOnly]

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'assigned_worker']
    search_fields = ['name', 'blend_file_path']
    ordering_fields = ['submitted_at', 'status', 'name']

    def perform_create(self, serializer):
        job = serializer.save(status=JobStatus.QUEUED, submitted_at=timezone.now())
        logger.info(f"New job '{job.name}' (ID: {job.id}) created with status {job.status}.")

    def list(self, request, *args, **kwargs):
        logger.debug("API: Listing jobs.")
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        logger.debug(f"API: Retrieving job {kwargs.get('pk')}.")
        return super().retrieve(request, *args, **kwargs)