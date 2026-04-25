# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker heartbeat endpoint.

Token-authenticated only.  All enrollment-key handling has been removed
from this file — new workers must hit ``POST /api/enroll/`` first to
obtain a token, then call this endpoint on subsequent heartbeats.

See ``enroll.py`` for the enrollment flow and
``development/specs/worker-enrollment.md`` for the full rationale.
"""

import logging
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import models, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authentication import (
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from sethlans_manager.middleware.setup_gate import _get_data_dir

from ..constants import WorkerStatus
from ..models import SupportedBlenderVersion, Worker
from ..serializers import WorkerSerializer
from ..services.sentinel import is_setup_complete
from ._helpers import get_or_create_worker_user
from .stuck_jobs import requeue_stuck_jobs
from .token_actions import WorkerTokenActionsMixin
from .yield_actions import WorkerYieldActionsMixin

logger = logging.getLogger(__name__)

WORKER_ACCEPTED_STATUSES = {WorkerStatus.IDLE, WorkerStatus.RENDERING}


def _validate_worker_status(raw_status):
    """Validate status from worker payload. Returns a valid status string."""
    if raw_status in WORKER_ACCEPTED_STATUSES:
        return raw_status
    return WorkerStatus.IDLE


def _sanitize_cpu_name(cpu_name):
    """Sanitize cpu_name input, rejecting strings with HTML/script chars."""
    if not isinstance(cpu_name, str):
        return ''
    if not re.match(r'^[\w\s\-().@,/#+]*$', cpu_name, re.ASCII):
        return ''
    return cpu_name


@extend_schema_view(
    list=extend_schema(tags=['Worker Agent']),
    create=extend_schema(tags=['Worker Agent']),
)
class WorkerHeartbeatViewSet(
    WorkerYieldActionsMixin,
    WorkerTokenActionsMixin,
    viewsets.ViewSet,
):
    """Worker heartbeats and admin token management.

    Token authentication is required for every action — the enrollment
    key path has moved to ``POST /api/enroll/``.  See spec FR-19.
    """

    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

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

    @staticmethod
    def _validate_ui_cert_fingerprint(raw_value):
        """Validate and return a ui_cert_fingerprint, or '' if invalid.

        Accepts a 64-character lowercase hex string (SHA-256).
        Logs a warning and returns '' for non-conforming values.
        """
        if not raw_value:
            return ''
        if not isinstance(raw_value, str):
            logger.warning(
                "Rejecting non-string ui_cert_fingerprint: %r",
                type(raw_value).__name__,
            )
            return ''
        if not re.fullmatch(r'[0-9a-f]{64}', raw_value):
            logger.warning(
                "Rejecting invalid ui_cert_fingerprint: %r",
                raw_value[:80],
            )
            return ''
        return raw_value

    @staticmethod
    def _extract_gpu_name(available_tools):
        """Extract GPU name(s) from available_tools JSON."""
        if not isinstance(available_tools, dict):
            return ''
        details = available_tools.get('gpu_devices_details', [])
        if not isinstance(details, list):
            return ''
        names = [
            d.get('name', '') for d in details
            if isinstance(d, dict) and d.get('name')
        ]
        result = ', '.join(names)
        return result[:255]

    def list(self, request):
        """Lists all registered workers with has_token status."""
        token_exists = Token.objects.filter(
            user_id=OuterRef('user_id'),
        )
        workers = Worker.objects.annotate(
            _has_token=Exists(token_exists),
        )
        serializer = WorkerSerializer(
            workers, many=True,
            context={'now': timezone.now()},
        )
        return Response(serializer.data)

    def create(self, request):
        """Authenticated heartbeat — update or register an existing worker."""
        return self._process_heartbeat(request)

    def _process_heartbeat(self, request):
        """Validate hostname and dispatch to registration or heartbeat."""
        hostname = request.data.get('hostname')
        if not hostname:
            return Response(
                {"detail": "Hostname is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        handler = (self._handle_full_registration if 'os' in request.data
                   else self._handle_heartbeat)
        result = handler(request, hostname)
        if isinstance(result, Response):
            return result
        # Append required Blender versions to every heartbeat response
        result['required_blender_versions'] = list(
            SupportedBlenderVersion.objects.values(
                'series', version=models.F('resolved_version'),
            )
        )
        # Surface setup-complete state so workers can self-gate on
        # download/job-claim while the manager wizard is in progress
        # (issue #126).  Read each heartbeat — sentinel writes are
        # infrequent and atomic.
        result['manager_setup_complete'] = is_setup_complete(
            _get_data_dir(),
        )
        return Response(result, status=status.HTTP_200_OK)

    def _handle_full_registration(self, request, hostname):
        """Create or update Worker, User, and Token atomically."""
        User = get_user_model()
        ui_url = self._validate_ui_url(request.data.get('ui_url'))

        with transaction.atomic():
            username = f'worker_{hostname}'
            user = get_or_create_worker_user(User, username, hostname)
            if user is None:
                return Response(
                    {"detail": "Could not create worker user after retries."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            token, _ = Token.objects.get_or_create(user=user)
            raw_status = request.data.get('status', 'IDLE')
            available_tools = request.data.get('available_tools', {})
            worker, created = Worker.objects.update_or_create(
                hostname=hostname,
                defaults={
                    'ip_address': request.data.get('ip_address'),
                    'os': request.data.get('os'),
                    'available_tools': available_tools,
                    'last_seen': timezone.now(),
                    'is_active': True,
                    'ui_url': ui_url,
                    'user': user,
                    'cpu_name': _sanitize_cpu_name(
                        request.data.get('cpu_name', ''),
                    ),
                    'gpu_name': self._extract_gpu_name(
                        available_tools,
                    ),
                    'status': _validate_worker_status(raw_status),
                    'ui_cert_fingerprint': self._validate_ui_cert_fingerprint(
                        request.data.get('ui_cert_fingerprint', ''),
                    ),
                },
            )

        if created:
            logger.info("Worker registration. Hostname: %s", hostname)
        else:
            logger.debug("Worker re-registration. Hostname: %s", hostname)
        data = WorkerSerializer(worker).data
        data['token'] = token.key
        return data

    def _handle_heartbeat(self, request, hostname):
        """Update last_seen for an existing worker."""
        try:
            worker = Worker.objects.get(hostname=hostname)
        except Worker.DoesNotExist:
            return Response(
                {"detail": "Worker not found. Re-register with full system info."},
                status=status.HTTP_404_NOT_FOUND,
            )
        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.ui_url = self._validate_ui_url(
            request.data.get('ui_url'),
        )
        raw_status = request.data.get('status', worker.status)
        worker.cpu_name = _sanitize_cpu_name(
            request.data.get('cpu_name', worker.cpu_name),
        )
        worker.status = _validate_worker_status(raw_status)
        worker.ui_cert_fingerprint = self._validate_ui_cert_fingerprint(
            request.data.get('ui_cert_fingerprint', ''),
        )
        update_fields = [
            'last_seen', 'is_active', 'ui_url',
            'cpu_name', 'status', 'ui_cert_fingerprint',
        ]
        if 'available_tools' in request.data:
            worker.available_tools = request.data['available_tools']
            worker.gpu_name = self._extract_gpu_name(
                request.data['available_tools'],
            )
            update_fields.extend(['available_tools', 'gpu_name'])

        schedule_data = request.data.get('schedule')
        if schedule_data is not None:
            if isinstance(schedule_data, dict):
                recognized_keys = {
                    'enabled', 'days', 'start', 'end',
                    'timezone', 'overrides_idle_detection',
                }
                cleaned = {
                    k: v for k, v in schedule_data.items()
                    if k in recognized_keys
                }
                worker.schedule_config = cleaned
                update_fields.append('schedule_config')
            else:
                logger.warning(
                    "Ignoring non-dict schedule payload from "
                    "worker %s",
                    hostname,
                )

        worker.save(update_fields=update_fields)
        logger.debug("Worker heartbeat. Hostname: %s", hostname)
        requeue_stuck_jobs()
        return WorkerSerializer(worker).data
