# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
# workers/views/heartbeat.py

import configparser
import hmac
import logging
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..models import Worker
from ..permissions import IsAdmin
from ..rate_limiter import InMemoryRateLimiter
from ..serializers import WorkerSerializer

logger = logging.getLogger(__name__)

# Rate limiter for enrollment: 5 attempts per IP per 5 minutes
_enrollment_rate_limiter = InMemoryRateLimiter(
    max_attempts=5, window_seconds=300,
)


@extend_schema_view(
    list=extend_schema(tags=['Worker Agent']),
    create=extend_schema(tags=['Worker Agent']),
)
class WorkerHeartbeatViewSet(viewsets.ViewSet):
    """Worker enrollment, heartbeats, and admin token management."""

    def get_permissions(self):
        if self.action == 'create':
            return [AllowAny()]
        return [IsAdmin()]

    def get_authenticators(self):
        """
        Use TokenAuthentication only for ``create`` to skip CSRF.
        Derive the action from ``action_map`` since ``self.action``
        is not yet set when this runs during ``initialize_request``.
        """
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

    def list(self, request):
        """Lists all registered workers with has_token status."""
        token_exists = Token.objects.filter(
            user_id=OuterRef('user_id'),
        )
        workers = Worker.objects.annotate(
            _has_token=Exists(token_exists),
        )
        serializer = WorkerSerializer(workers, many=True)
        data = serializer.data
        for item, worker in zip(data, workers):
            item['has_token'] = worker._has_token
        return Response(data)

    def create(self, request):
        """Worker registration / heartbeat with enrollment key auth."""
        client_ip = self._get_client_ip(request)
        if _enrollment_rate_limiter.is_rate_limited(client_ip):
            return Response(
                {"detail": "Too many enrollment attempts. "
                 "Try again later."},
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

        hostname = request.data.get('hostname')
        if not hostname:
            return Response(
                {"detail": "Hostname is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_full = 'os' in request.data or 'available_tools' in request.data
        result = (
            self._handle_full_registration(request, hostname)
            if is_full
            else self._handle_heartbeat(request, hostname)
        )
        if isinstance(result, Response):
            return result
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
                    {"detail": "Could not create worker user "
                     "after retries."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            token, _ = Token.objects.get_or_create(user=user)
            worker, created = Worker.objects.update_or_create(
                hostname=hostname,
                defaults={
                    'ip_address': request.data.get('ip_address'),
                    'os': request.data.get('os'),
                    'available_tools': request.data.get(
                        'available_tools', {},
                    ),
                    'last_seen': timezone.now(),
                    'is_active': True,
                    'ui_url': ui_url,
                    'user': user,
                },
            )

        action_str = "registration" if created else "re-registration"
        logger.info("Worker %s. Hostname: %s", action_str, hostname)
        data = WorkerSerializer(worker).data
        data['token'] = token.key
        return data

    def _handle_heartbeat(self, request, hostname):
        """Update last_seen for an existing worker."""
        try:
            worker = Worker.objects.get(hostname=hostname)
        except Worker.DoesNotExist:
            return Response(
                {"detail": "Worker not found. "
                 "Please re-register with full system info."},
                status=status.HTTP_404_NOT_FOUND,
            )
        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.ui_url = self._validate_ui_url(
            request.data.get('ui_url'),
        )
        worker.save(
            update_fields=['last_seen', 'is_active', 'ui_url'],
        )
        logger.debug("Worker heartbeat. Hostname: %s", hostname)
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
        logger.error(
            "Failed to create user for worker '%s' after 10 retries.",
            hostname,
        )
        return None

    @staticmethod
    def _get_worker_or_404(pk):
        """Return Worker by pk or None."""
        try:
            return Worker.objects.select_related('user').get(pk=pk)
        except Worker.DoesNotExist:
            return None

    @staticmethod
    def _require_worker_with_user(pk):
        """Return (worker, None) or (None, error Response)."""
        try:
            worker = Worker.objects.select_related('user').get(pk=pk)
        except Worker.DoesNotExist:
            return None, Response(
                {"detail": "Worker not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not worker.user:
            return None, Response(
                {"detail": "Worker has no linked user."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return worker, None

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='revoke_token')
    def revoke_token(self, request, pk=None):
        """Delete the DRF Token associated with a worker's User."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        logger.info("Token revoked for worker %s", worker.hostname)
        return Response({"detail": "Token revoked."})

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='regenerate_token')
    def regenerate_token(self, request, pk=None):
        """Delete old token and create a new one. Returns new value."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        token = Token.objects.create(user=worker.user)
        logger.info("Token regenerated for worker %s", worker.hostname)
        return Response({"token": token.key})

    @extend_schema(tags=['Management UI'])
    @action(detail=True, methods=['post'], url_path='force_reenroll')
    def force_reenroll(self, request, pk=None):
        """Revoke token so the worker re-enrolls on next heartbeat."""
        worker, err = self._require_worker_with_user(pk)
        if err:
            return err
        Token.objects.filter(user=worker.user).delete()
        logger.info("Forced re-enrollment for worker %s", worker.hostname)
        return Response(
            {"detail": "Token revoked. Worker will re-enroll."},
        )
