# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/serializers/workers.py.

Covers staleness logic (AC-5), last_heartbeat alias (AC-7/14),
and correct timedelta import (AC-14).
"""

import importlib
import inspect
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from workers.constants import WORKER_STALENESS_SECONDS, WorkerStatus
from workers.serializers import WorkerSerializer


@pytest.fixture
def mock_worker():
    """Create a mock Worker with all fields the serializer needs."""
    worker = MagicMock()
    worker.id = 1
    worker.hostname = 'test-worker'
    worker.ip_address = '192.168.1.100'
    worker.os = 'Linux 6.1'
    worker.last_seen = timezone.now()
    worker.is_active = True
    worker.available_tools = {}
    worker.ui_url = None
    worker.user_id = None
    worker.cpu_name = 'Intel Core i7'
    worker.gpu_name = 'NVIDIA RTX 4090'
    worker.status = WorkerStatus.IDLE
    return worker


class TestGetStatus:
    """Tests for WorkerSerializer.get_status() — AC-5."""

    def test_returns_idle_when_heartbeat_is_fresh(self, mock_worker):
        mock_worker.status = WorkerStatus.IDLE
        mock_worker.last_seen = timezone.now()
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        assert serializer.data['status'] == 'IDLE'

    def test_returns_rendering_when_heartbeat_is_fresh(self, mock_worker):
        mock_worker.status = WorkerStatus.RENDERING
        mock_worker.last_seen = timezone.now()
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        assert serializer.data['status'] == 'RENDERING'

    def test_returns_offline_when_heartbeat_is_stale(self, mock_worker):
        """AC-5: OFFLINE override when last_seen is stale (>90 seconds)."""
        mock_worker.status = WorkerStatus.RENDERING
        mock_worker.last_seen = timezone.now() - timedelta(seconds=91)
        serializer = WorkerSerializer(
            mock_worker, context={'now': timezone.now()},
        )
        assert serializer.data['status'] == 'OFFLINE'

    def test_returns_offline_at_exact_boundary(self, mock_worker):
        """Exactly at the threshold: status should NOT be OFFLINE."""
        now = timezone.now()
        mock_worker.status = WorkerStatus.IDLE
        mock_worker.last_seen = now - timedelta(seconds=WORKER_STALENESS_SECONDS)
        serializer = WorkerSerializer(mock_worker, context={'now': now})
        assert serializer.data['status'] == 'IDLE'

    def test_returns_offline_one_second_past_boundary(self, mock_worker):
        """One second past the threshold: should be OFFLINE."""
        now = timezone.now()
        mock_worker.status = WorkerStatus.IDLE
        mock_worker.last_seen = now - timedelta(
            seconds=WORKER_STALENESS_SECONDS + 1,
        )
        serializer = WorkerSerializer(mock_worker, context={'now': now})
        assert serializer.data['status'] == 'OFFLINE'

    def test_returns_offline_when_last_seen_is_none(self, mock_worker):
        """Worker with no last_seen should be OFFLINE."""
        mock_worker.last_seen = None
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        assert serializer.data['status'] == 'OFFLINE'

    def test_uses_context_now_instead_of_calling_timezone_now(
        self, mock_worker, mocker,
    ):
        """Serializer respects 'now' from context, does not call timezone.now()."""
        fixed_now = timezone.now()
        mock_worker.last_seen = fixed_now - timedelta(seconds=10)
        mock_worker.status = WorkerStatus.IDLE
        mock_tz_now = mocker.patch(
            'workers.serializers.workers.timezone.now',
            return_value=fixed_now,
        )
        serializer = WorkerSerializer(mock_worker, context={'now': fixed_now})
        serializer.data  # triggers get_status
        mock_tz_now.assert_not_called()

    def test_falls_back_to_timezone_now_if_no_context(self, mock_worker, mocker):
        """If 'now' is not in context, get_status calls timezone.now()."""
        fixed_now = timezone.now()
        mock_worker.last_seen = fixed_now - timedelta(seconds=10)
        mock_worker.status = WorkerStatus.IDLE
        mocker.patch(
            'workers.serializers.workers.timezone.now',
            return_value=fixed_now,
        )
        serializer = WorkerSerializer(mock_worker, context={})
        assert serializer.data['status'] == 'IDLE'


class TestLastHeartbeatAlias:
    """Tests for last_heartbeat alias field — AC-7, AC-14."""

    def test_last_heartbeat_equals_last_seen(self, mock_worker):
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        data = serializer.data
        assert data['last_heartbeat'] == data['last_seen']

    def test_both_fields_present_in_response(self, mock_worker):
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        data = serializer.data
        assert 'last_seen' in data
        assert 'last_heartbeat' in data


class TestTimedeltaImport:
    """AC-14: Serializer uses datetime.timedelta, not timezone.timedelta."""

    def test_imports_timedelta_from_datetime(self):
        module = importlib.import_module('workers.serializers.workers')
        source = inspect.getsource(module)
        assert 'from datetime import timedelta' in source
        assert 'timezone.timedelta' not in source


class TestSerializerFields:
    """Verify all expected fields are present in serializer output."""

    def test_contains_new_detail_fields(self, mock_worker):
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        data = serializer.data
        assert 'cpu_name' in data
        assert 'gpu_name' in data
        assert 'status' in data

    def test_cpu_name_value(self, mock_worker):
        mock_worker.cpu_name = 'AMD Ryzen 9 5950X'
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        assert serializer.data['cpu_name'] == 'AMD Ryzen 9 5950X'

    def test_gpu_name_value(self, mock_worker):
        mock_worker.gpu_name = 'NVIDIA RTX 4090'
        serializer = WorkerSerializer(mock_worker, context={'now': timezone.now()})
        assert serializer.data['gpu_name'] == 'NVIDIA RTX 4090'
