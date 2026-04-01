# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.response import Response

from ..models import Worker
from ..serializers import WorkerSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Worker Agent']),
    create=extend_schema(tags=['Worker Agent']),
)
class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register with the manager.

    A POST request with full system information will either register a new worker
    or update an existing one. Subsequent POSTs with just the hostname will
    simply update the 'last_seen' timestamp.
    """

    @staticmethod
    def _validate_ui_url(raw_url):
        """Validate and return a ui_url, or None if invalid."""
        if not raw_url:
            return None
        try:
            URLValidator(schemes=['http', 'https'])(raw_url)
            return raw_url
        except DjangoValidationError:
            return None

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
            ui_url = self._validate_ui_url(request.data.get('ui_url'))
            worker, created = Worker.objects.update_or_create(
                hostname=hostname,
                defaults={
                    'ip_address': request.data.get('ip_address'),
                    'os': request.data.get('os'),
                    'available_tools': request.data.get('available_tools', {}),
                    'last_seen': timezone.now(),
                    'is_active': True,
                    'ui_url': ui_url,
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
                worker.ui_url = self._validate_ui_url(
                    request.data.get('ui_url')
                )
                worker.save(update_fields=['last_seen', 'is_active', 'ui_url'])
                logger.debug(f"Worker periodic heartbeat. Hostname: {worker.hostname}")
            except Worker.DoesNotExist:
                return Response(
                    {"detail": "Worker not found. Please re-register with full system info."},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = WorkerSerializer(worker)
        return Response(serializer.data, status=status.HTTP_200_OK)
