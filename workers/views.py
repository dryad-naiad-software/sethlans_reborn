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
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .models import Worker
from .serializers import WorkerSerializer
from django.utils import timezone


class WorkerHeartbeatAPIView(APIView):
    """
    API endpoint for workers to send heartbeats and register themselves.
    Handles both registration (creation) and updates (heartbeat).
    """
    def post(self, request, *args, **kwargs):
        hostname = request.data.get('hostname')
        ip_address = request.data.get('ip_address')
        os_info = request.data.get('os')

        if not hostname:
            return Response({"detail": "Hostname is required."}, status=status.HTTP_400_BAD_REQUEST)

        worker, created = Worker.objects.get_or_create(hostname=hostname)

        if ip_address:
            worker.ip_address = ip_address
        if os_info:
            worker.os = os_info

        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.save()

        serializer = WorkerSerializer(worker)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def get(self, request, *args, **kwargs):
        """
        Optional: For debugging/admin purposes, list all registered workers.
        """
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        return Response(serializer.data)