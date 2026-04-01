# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the atomic job claiming endpoint (POST /api/jobs/{id}/claim/).

Covers: successful claim, double-claim (409), missing worker_id (400),
invalid worker_id (400), non-existent job (404), claiming non-QUEUED job (409).
"""

import pytest
from unittest.mock import MagicMock
from rest_framework import status
from rest_framework.test import APIRequestFactory

from workers.models import Job, JobStatus, Worker
from workers.views.jobs import JobViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture(autouse=True)
def _bypass_permissions(mocker):
    """
    These tests verify claim logic, not auth. Bypass permission
    checks so tests remain focused on the claim state machine.
    """
    mocker.patch.object(
        JobViewSet, 'check_permissions', return_value=None,
    )


@pytest.fixture
def mock_worker(mocker):
    """Create a mock Worker object with pk=1."""
    worker = MagicMock(spec=Worker)
    worker.pk = 1
    worker.hostname = "test-worker"
    return worker


@pytest.fixture
def mock_queued_job(mocker):
    """Create a mock Job in QUEUED status with no assigned worker."""
    job = MagicMock(spec=Job)
    job.pk = 42
    job.id = 42
    job.name = "Test Job"
    job.status = JobStatus.QUEUED
    job.assigned_worker = None
    return job


class TestJobClaimAction:
    """Tests for the JobViewSet.claim action."""

    def test_successful_claim(self, factory, mocker, mock_worker, mock_queued_job):
        """
        POST /api/jobs/42/claim/ with valid worker_id should return 200,
        set status to RENDERING, assign the worker, and set started_at.
        """
        mocker.patch.object(
            Worker.objects, 'get', return_value=mock_worker
        )
        mock_select_for_update = MagicMock()
        mock_select_for_update.get.return_value = mock_queued_job
        mocker.patch.object(
            Job.objects, 'select_for_update', return_value=mock_select_for_update
        )
        mock_serializer_data = {"id": 42, "status": "RENDERING"}
        mocker.patch.object(
            JobViewSet, 'get_serializer',
            return_value=MagicMock(data=mock_serializer_data)
        )

        request = factory.post('/api/jobs/42/claim/', {'worker_id': 1}, format='json')
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_200_OK
        assert mock_queued_job.status == JobStatus.RENDERING
        assert mock_queued_job.assigned_worker == mock_worker
        assert mock_queued_job.started_at is not None
        mock_queued_job.save.assert_called_once()

    def test_double_claim_returns_409(self, factory, mocker, mock_worker):
        """
        Attempting to claim a job already in RENDERING status should return 409.
        """
        rendering_job = MagicMock(spec=Job)
        rendering_job.pk = 42
        rendering_job.status = JobStatus.RENDERING
        rendering_job.assigned_worker = mock_worker

        mocker.patch.object(Worker.objects, 'get', return_value=mock_worker)
        mock_sfu = MagicMock()
        mock_sfu.get.return_value = rendering_job
        mocker.patch.object(Job.objects, 'select_for_update', return_value=mock_sfu)

        request = factory.post('/api/jobs/42/claim/', {'worker_id': 1}, format='json')
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "not available" in response.data["error"]

    def test_missing_worker_id_returns_400(self, factory, mocker):
        """
        POST /api/jobs/42/claim/ with no worker_id should return 400.
        """
        request = factory.post('/api/jobs/42/claim/', {}, format='json')
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "worker_id" in response.data["error"]

    def test_invalid_worker_id_returns_400(self, factory, mocker):
        """
        POST /api/jobs/42/claim/ with a worker_id that doesn't exist returns 400.
        """
        mocker.patch.object(
            Worker.objects, 'get', side_effect=Worker.DoesNotExist
        )

        request = factory.post(
            '/api/jobs/42/claim/', {'worker_id': 9999}, format='json'
        )
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not found" in response.data["error"]

    def test_nonexistent_job_returns_404(self, factory, mocker, mock_worker):
        """
        POST /api/jobs/999/claim/ when the job PK does not exist returns 404.
        """
        mocker.patch.object(Worker.objects, 'get', return_value=mock_worker)
        mock_sfu = MagicMock()
        mock_sfu.get.side_effect = Job.DoesNotExist
        mocker.patch.object(Job.objects, 'select_for_update', return_value=mock_sfu)

        request = factory.post(
            '/api/jobs/999/claim/', {'worker_id': 1}, format='json'
        )
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=999)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.data["error"].lower()

    def test_claiming_non_queued_job_returns_409(self, factory, mocker, mock_worker):
        """
        Trying to claim a DONE job should return 409 Conflict.
        """
        done_job = MagicMock(spec=Job)
        done_job.pk = 42
        done_job.status = JobStatus.DONE
        done_job.assigned_worker = None

        mocker.patch.object(Worker.objects, 'get', return_value=mock_worker)
        mock_sfu = MagicMock()
        mock_sfu.get.return_value = done_job
        mocker.patch.object(Job.objects, 'select_for_update', return_value=mock_sfu)

        request = factory.post(
            '/api/jobs/42/claim/', {'worker_id': 1}, format='json'
        )
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "not available" in response.data["error"]

    def test_claiming_queued_but_already_assigned_returns_409(
        self, factory, mocker, mock_worker
    ):
        """
        A job in QUEUED status but with an assigned_worker should return 409.
        This catches the edge case where the status check passes but the
        worker assignment check catches the conflict.
        """
        assigned_job = MagicMock(spec=Job)
        assigned_job.pk = 42
        assigned_job.status = JobStatus.QUEUED
        assigned_job.assigned_worker = mock_worker  # Already assigned

        mocker.patch.object(Worker.objects, 'get', return_value=mock_worker)
        mock_sfu = MagicMock()
        mock_sfu.get.return_value = assigned_job
        mocker.patch.object(Job.objects, 'select_for_update', return_value=mock_sfu)

        request = factory.post(
            '/api/jobs/42/claim/', {'worker_id': 1}, format='json'
        )
        view = JobViewSet.as_view({'post': 'claim'})
        response = view(request, pk=42)

        assert response.status_code == status.HTTP_409_CONFLICT
