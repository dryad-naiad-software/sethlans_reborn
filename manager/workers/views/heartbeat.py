# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Worker heartbeat endpoint.

Heartbeats need a worker token and are bound to the caller's own row;
listing workers and the token actions are admin-only
(``IsAdminNotWorker``, issue #220).  All enrollment-key handling has been
removed from this file — new workers must hit ``POST /api/enroll/``
first to obtain a token (enrollment creates the ``User``, ``Token`` and
``Worker`` row), then call this endpoint on subsequent heartbeats.  A
heartbeat only ever acts on the caller's own ``Worker`` row and never
creates a worker, user or token (issue #215, see ``heartbeat_auth``).

See ``enroll.py`` for the enrollment flow and
``development/specs/worker-enrollment.md`` for the full rationale.
"""

import logging

from django.db import DatabaseError, models, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authentication import (
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.authtoken.models import Token
from rest_framework.response import Response

from ..models import SupportedBlenderVersion, Worker
from ..permissions import HeartbeatViewSetPermission
from ..serializers import WorkerSerializer
from ..services.machine_tests import ensure_for_worker
# Module import, attributes read at call time: ``worker_reconcile`` imports
# ``views.stuck_jobs``, so a ``from`` import here would be an import cycle.
from ..services import worker_reconcile
from ..services.blender_cache import runner as blender_cache_runner
from . import heartbeat_card_sync
from .card_actions import WorkerCardActionsMixin
from .heartbeat_auth import bind_heartbeat_caller
from .heartbeat_cards import apply_card_report
from .heartbeat_schema import WorkerHeartbeatResponseSerializer
from .heartbeat_unreal import (
    _validate_active_job_ids, _validate_boot_id, apply_unreal_heartbeat,
    apply_unreal_registration,
)
# Validators are re-exported below so tests that import them from this
# module (``from workers.views.heartbeat import _sanitize_cpu_name``)
# keep working after the split.
from .heartbeat_validators import (  # noqa: F401
    WORKER_ACCEPTED_STATUSES,
    _extract_gpu_name,
    _sanitize_cpu_name,
    _sanitize_os,
    _validate_available_tools,
    _validate_blender_platform,
    _validate_ip_address,
    _validate_ui_cert_fingerprint,
    _validate_ui_url,
    _validate_worker_status,
)
from .stuck_jobs import requeue_stuck_jobs
from .token_actions import WorkerTokenActionsMixin
from .yield_actions import WorkerYieldActionsMixin

logger = logging.getLogger(__name__)


@extend_schema_view(
    list=extend_schema(tags=['Worker Agent']),
    create=extend_schema(
        tags=['Worker Agent'],
        responses=WorkerHeartbeatResponseSerializer,
    ),
)
class WorkerHeartbeatViewSet(
    WorkerYieldActionsMixin,
    WorkerTokenActionsMixin,
    WorkerCardActionsMixin,
    viewsets.ViewSet,
):
    """Worker heartbeats and admin token management.

    The enrollment key path has moved to ``POST /api/enroll/`` (spec
    FR-19).  Access is decided per action.  ``create`` requires a
    worker token and binds to the caller's own worker (issue #215, see
    ``heartbeat_auth``); ``yield-event`` checks worker ownership in its
    mixin; ``list`` and the admin token actions (revoke/regenerate/
    force re-enroll) require ``IsAdminNotWorker`` (issue #220).
    """

    authentication_classes = [TokenAuthentication, SessionAuthentication]
    # ``list`` admin-only, other actions IsAuthenticated (issue #220).
    permission_classes = [HeartbeatViewSetPermission]

    # Back-compat shim: existing unit tests
    # (``tests/unit/manager/test_heartbeat_helpers.py``) call
    # ``WorkerHeartbeatViewSet._extract_gpu_name(...)`` directly.  The
    # implementation now lives in ``heartbeat_validators`` — this
    # staticmethod just delegates so the test surface stays stable.
    _extract_gpu_name = staticmethod(_extract_gpu_name)

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
        """Worker heartbeat or full re-registration of the caller's own row.

        Only token-authenticated worker users may call this; the
        target ``Worker`` is the caller's own (issue #215).  See
        ``heartbeat_auth.bind_heartbeat_caller``.
        """
        return self._process_heartbeat(request)

    def _process_heartbeat(self, request):
        """Bind the caller's worker and dispatch to registration or heartbeat."""
        worker, error = bind_heartbeat_caller(request, self)
        if error is not None:
            return error

        handler = (self._handle_full_registration if 'os' in request.data
                   else self._handle_heartbeat)
        try:
            result = handler(request, worker)
        except worker_reconcile.ReconcileError:
            # The previous boot_id is restored: the worker's registration
            # retry repeats the reconciliation (FR-RT-12).
            return Response(
                {"error": "Worker restart reconciliation failed; retry registration."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        result.update(heartbeat_card_sync.sync(worker, request.data))  # after the handler's save (D24)
        transaction.on_commit(lambda: ensure_for_worker(worker.pk, worker=worker), robust=True)  # memory tests D16
        # Append required Blender versions to every heartbeat response
        result['required_blender_versions'] = list(
            SupportedBlenderVersion.objects.values(
                'series', version=models.F('resolved_version'),
            )
        )
        return Response(result, status=status.HTTP_200_OK)

    def _handle_full_registration(self, request, worker):
        """Refresh the caller's own Worker row with full system info.

        The row, its ``User`` and its ``Token`` were created by
        enrollment; this branch never creates or re-links any of them
        and never returns a token (the caller already holds its own).
        """
        first_registration = not worker.os
        available_tools = _validate_available_tools(
            request.data.get('available_tools', {}),
        )
        worker.ip_address = _validate_ip_address(
            request.data.get('ip_address'),
        )
        worker.os = _sanitize_os(request.data.get('os'))
        worker.available_tools = available_tools
        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.ui_url = _validate_ui_url(request.data.get('ui_url'))
        worker.cpu_name = _sanitize_cpu_name(request.data.get('cpu_name', ''))
        worker.gpu_name = _extract_gpu_name(available_tools)
        worker.status = _validate_worker_status(
            request.data.get('status', 'IDLE'),
        )
        worker.ui_cert_fingerprint = _validate_ui_cert_fingerprint(
            request.data.get('ui_cert_fingerprint', ''),
        )
        old_platform = worker.blender_platform
        worker.blender_platform = _validate_blender_platform(request.data.get('blender_platform'))
        worker.save(update_fields=[
            'ip_address', 'os', 'available_tools', 'last_seen',
            'is_active', 'ui_url', 'cpu_name', 'gpu_name', 'status',
            'ui_cert_fingerprint', 'blender_platform',
        ] + apply_unreal_registration(worker, request.data) + apply_card_report(worker, request.data))
        if worker.blender_platform != old_platform:  # the wanted builds may change (BS-MGR-5, D7)
            transaction.on_commit(blender_cache_runner.reconcile_and_wake, robust=True)
        boot_id = _validate_boot_id(request.data.get('worker_boot_id'))
        if boot_id is not None:
            worker_reconcile.reconcile_worker_restart(worker, boot_id)

        if first_registration:
            logger.info("Worker registration. Hostname: %s", worker.hostname)
        else:
            logger.debug("Worker re-registration. Hostname: %s", worker.hostname)
        return WorkerSerializer(worker).data

    def _handle_heartbeat(self, request, worker):
        """Update last_seen and reported state for the caller's worker."""
        hostname = worker.hostname
        worker.last_seen = timezone.now()
        worker.is_active = True
        worker.ui_url = _validate_ui_url(
            request.data.get('ui_url'),
        )
        raw_status = request.data.get('status', worker.status)
        worker.cpu_name = _sanitize_cpu_name(
            request.data.get('cpu_name', worker.cpu_name),
        )
        worker.status = _validate_worker_status(raw_status)
        worker.ui_cert_fingerprint = _validate_ui_cert_fingerprint(
            request.data.get('ui_cert_fingerprint', ''),
        )
        update_fields = [
            'last_seen', 'is_active', 'ui_url',
            'cpu_name', 'status', 'ui_cert_fingerprint',
        ]
        if 'available_tools' in request.data:
            worker.available_tools = _validate_available_tools(
                request.data['available_tools'],
            )
            worker.gpu_name = _extract_gpu_name(worker.available_tools)
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

        update_fields.extend(apply_unreal_heartbeat(worker, request.data))
        update_fields.extend(apply_card_report(worker, request.data))
        worker.save(update_fields=update_fields)
        logger.debug("Worker heartbeat. Hostname: %s", hostname)
        requeue_stuck_jobs()
        try:  # only for the process that registered last (#247)
            worker_reconcile.reconcile_heartbeat(
                worker, _validate_active_job_ids(request.data.get('active_job_ids')),
                _validate_boot_id(request.data.get('worker_boot_id')), now=worker.last_seen)
        except DatabaseError:
            logger.warning("Live reconciliation for %s failed; retried next heartbeat.",
                           hostname, exc_info=True)
        return WorkerSerializer(worker).data
