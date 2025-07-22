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

from rest_framework import viewsets  # Already imported
from rest_framework.permissions import IsAuthenticatedOrReadOnly  # Optional: uncomment if auth is needed


# Changed from APIView to ViewSet. 'list' method handles GET, 'create' handles POST.
class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register themselves.
    Handles both registration (creation) and listing of workers (for debugging/admin).
    GET /api/heartbeat/ : List all registered workers.
    POST /api/heartbeat/ : Receive heartbeat/registration from a worker.
    """

    # This method handles GET requests to /api/heartbeat/
    def list(self, request):
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        return Response(serializer.data)

    # This method handles POST requests to /api/heartbeat/
    def create(self, request):
        hostname = request.data.get('hostname')
        ip_address = request.data.get('ip_address')
        os_info = request.data.get('os')

        if not hostname:
            return Response({"detail": "Hostname is required."}, status=status.HTTP_400_BAD_REQUEST)

        worker, created = Worker.objects.get_or_create(hostname=hostname)

        # Update worker details. Only update if the data is provided in the request.
        if ip_address:
            worker.ip_address = ip_address
        if os_info:
            worker.os = os_info

        # Update last_seen and set active status on every heartbeat
        worker.last_seen = timezone.now()
        worker.is_active = True  # Worker is active if it sends a heartbeat
        worker.save()

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

    # permission_classes = [IsAuthenticatedOrReadOnly] # Uncomment if you enable user authentication

    def perform_create(self, serializer):
        # When a new job is created via POST, set its initial status and submitted_at
        job = serializer.save(status=JobStatus.QUEUED, submitted_at=timezone.now())
        print(f"New job '{job.name}' created with status {job.status}")