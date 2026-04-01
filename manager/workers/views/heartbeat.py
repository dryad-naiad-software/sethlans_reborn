# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# Created by Mario Estrella on 07/22/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
# workers/views/heartbeat.py

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..models import Worker
from ..permissions import IsAdmin
from ..serializers import WorkerSerializer

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Worker Agent']),
    create=extend_schema(tags=['Worker Agent']),
)
class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """
    API endpoint for workers to send heartbeats and register with the
    manager. Also provides admin actions for token management.
    """

    def get_permissions(self):
        if self.action == 'create':
            # AllowAny is intentional: unauthenticated workers use this
            # endpoint for enrollment with X-Enrollment-Key header
            return [AllowAny()]
        elif self.action in (
            'revoke_token', 'regenerate_token', 'force_reenroll',
        ):
            return [IsAdmin()]
        else:
            return [IsAdmin()]

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
        """Lists all registered workers with has_token status."""
        workers = Worker.objects.all()
        serializer = WorkerSerializer(workers, many=True)
        data = serializer.data
        for item, worker in zip(data, workers):
            has_token = False
            if worker.user_id:
                has_token = Token.objects.filter(
                    user_id=worker.user_id
                ).exists()
            item['has_token'] = has_token
        return Response(data)

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
            return Response(
                {"detail": "Hostname is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Differentiate between a full registration and a simple heartbeat
        is_full_registration = (
            'os' in request.data or 'available_tools' in request.data
        )

        if is_full_registration:
            ui_url = self._validate_ui_url(request.data.get('ui_url'))
            worker, created = Worker.objects.update_or_create(
                hostname=hostname,
                defaults={
                    'ip_address': request.data.get('ip_address'),
                    'os': request.data.get('os'),
                    'available_tools': request.data.get(
                        'available_tools', {}
                    ),
                    'last_seen': timezone.now(),
                    'is_active': True,
                    'ui_url': ui_url,
                }
            )
            action_str = (
                "registration" if created else "registration/full update"
            )
            logger.info(
                f"Worker {action_str}. Hostname: {worker.hostname}"
            )
        else:
            try:
                worker = Worker.objects.get(hostname=hostname)
                worker.last_seen = timezone.now()
                worker.is_active = True
                worker.ui_url = self._validate_ui_url(
                    request.data.get('ui_url')
                )
                worker.save(
                    update_fields=['last_seen', 'is_active', 'ui_url']
                )
                logger.debug(
                    "Worker periodic heartbeat. "
                    f"Hostname: {worker.hostname}"
                )
            except Worker.DoesNotExist:
                return Response(
                    {"detail": "Worker not found. "
                     "Please re-register with full system info."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        serializer = WorkerSerializer(worker)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='revoke_token')
    def revoke_token(self, request, pk=None):
        """Delete the DRF Token associated with a worker's User."""
        worker = self._get_worker_or_404(pk)
        if not worker:
            return Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not worker.user:
            return Response(
                {"detail": "Worker has no linked user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Token.objects.filter(user=worker.user).delete()
        logger.info(f"Token revoked for worker {worker.hostname}")
        return Response({"detail": "Token revoked."})

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='regenerate_token')
    def regenerate_token(self, request, pk=None):
        """Delete old token and create a new one. Returns new value."""
        worker = self._get_worker_or_404(pk)
        if not worker:
            return Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not worker.user:
            return Response(
                {"detail": "Worker has no linked user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Token.objects.filter(user=worker.user).delete()
        token = Token.objects.create(user=worker.user)
        logger.info(
            f"Token regenerated for worker {worker.hostname}"
        )
        return Response({"token": token.key})

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='force_reenroll')
    def force_reenroll(self, request, pk=None):
        """Revoke token so the worker re-enrolls on next heartbeat."""
        worker = self._get_worker_or_404(pk)
        if not worker:
            return Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not worker.user:
            return Response(
                {"detail": "Worker has no linked user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        Token.objects.filter(user=worker.user).delete()
        logger.info(
            f"Forced re-enrollment for worker {worker.hostname}"
        )
        return Response(
            {"detail": "Token revoked. Worker will re-enroll."}
        )

    @staticmethod
    def _get_worker_or_404(pk):
        """Return Worker by pk or None."""
        try:
            return Worker.objects.select_related('user').get(pk=pk)
        except Worker.DoesNotExist:
            return None
