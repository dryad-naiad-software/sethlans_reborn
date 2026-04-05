# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import hmac
import logging
import os
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..constants import WorkerStatus
from ..models import SupportedBlenderVersion, Worker
from ..permissions import IsAdmin
from ..rate_limiter import InMemoryRateLimiter
from ..serializers import WorkerSerializer
from .stuck_jobs import requeue_stuck_jobs
from .token_actions import WorkerTokenActionsMixin

logger = logging.getLogger(__name__)

WORKER_ACCEPTED_STATUSES = {WorkerStatus.IDLE, WorkerStatus.RENDERING}

# Rate limiter for enrollment: 5 attempts per IP per 5 minutes
_enrollment_rate_limiter = InMemoryRateLimiter(
    max_attempts=5, window_seconds=300,
)


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
class WorkerHeartbeatViewSet(WorkerTokenActionsMixin, viewsets.ViewSet):
    """Worker enrollment, heartbeats, and admin token management."""

    def get_permissions(self):
        if self.action == 'create':
            return [AllowAny()]
        return [IsAdmin()]

    def get_authenticators(self):
        """Use TokenAuthentication for create to skip CSRF."""
        cur_action = getattr(self, 'action', None)
        if cur_action is None:
            action_map = getattr(self, 'action_map', {})
            method = ''
            if hasattr(self, 'request') and hasattr(self.request, 'method'):
                method = self.request.method.lower()
            cur_action = action_map.get(method)
        if cur_action == 'create':
            return [TokenAuthentication()]
        return super().get_authenticators()

    @staticmethod
    def _get_enrollment_key():
        """Read enrollment key: env var > manager.ini > empty string."""
        key = os.environ.get('SETHLANS_SECURITY_ENROLLMENT_KEY', '')
        if not key:
            config = configparser.ConfigParser()
            config_path = settings.BASE_DIR / 'manager.ini'
            if config_path.exists():
                config.read(config_path)
            key = config.get('security', 'enrollment_key', fallback='')
        return key

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
    def _get_client_ip(request):
        """Extract client IP from the request."""
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

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
        """Worker enrollment (key-based) or authenticated heartbeat."""
        # Token-authenticated workers skip enrollment key check
        if isinstance(request.auth, Token):
            return self._process_heartbeat(request)

        # Enrollment path: rate limit + key validation
        client_ip = self._get_client_ip(request)
        if _enrollment_rate_limiter.is_rate_limited(client_ip):
            return Response(
                {"detail": "Too many enrollment attempts. Try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        provided_key = request.META.get('HTTP_X_ENROLLMENT_KEY', '')
        expected_key = self._get_enrollment_key()
        if (
            not expected_key
            or not provided_key
            or not hmac.compare_digest(provided_key, expected_key)
        ):
            _enrollment_rate_limiter.record_attempt(client_ip)
            logger.warning(
                "Enrollment rejected: invalid key from %s", client_ip,
            )
            return Response(
                {"detail": "Invalid or missing enrollment key."},
                status=status.HTTP_403_FORBIDDEN,
            )

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
        return Response(result, status=status.HTTP_200_OK)

    def _handle_full_registration(self, request, hostname):
        """Create or update Worker, User, and Token atomically."""
        User = get_user_model()
        ui_url = self._validate_ui_url(request.data.get('ui_url'))

        with transaction.atomic():
            username = f'worker_{hostname}'
            user = self._get_or_create_worker_user(
                User, username, hostname,
            )
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
        update_fields = [
            'last_seen', 'is_active', 'ui_url',
            'cpu_name', 'status',
        ]
        if 'available_tools' in request.data:
            worker.available_tools = request.data['available_tools']
            worker.gpu_name = self._extract_gpu_name(
                request.data['available_tools'],
            )
            update_fields.extend(['available_tools', 'gpu_name'])
        worker.save(update_fields=update_fields)
        logger.debug("Worker heartbeat. Hostname: %s", hostname)
        requeue_stuck_jobs()
        return WorkerSerializer(worker).data

    @staticmethod
    def _get_or_create_worker_user(User, username, hostname):
        """Create a worker User, retrying with numeric suffix (10x)."""
        for attempt in range(10):
            candidate = (
                username if attempt == 0
                else f'{username}_{attempt}'
            )
            try:
                user, created = User.objects.get_or_create(
                    username=candidate,
                    defaults={'is_staff': False, 'is_active': True},
                )
                if created:
                    user.set_unusable_password()
                    user.save()
                return user
            except IntegrityError:
                continue
        logger.error("Failed to create user for worker '%s' after 10 retries.", hostname)
        return None
