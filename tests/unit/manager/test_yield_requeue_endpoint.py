# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``POST /api/jobs/{pk}/yield-requeue/``
(``manager/workers/views/job_yield_actions.py``).

Core endpoint tests: status checks, ownership verification,
409 responses, and success path (status/field mutations, response codes).

Count-related tests live in ``test_yield_requeue_counts.py``.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

from workers.models.jobs import JobStatus
from workers.serializers.jobs import VALID_STATUS_TRANSITIONS
from workers.views.job_yield_actions import (
    JobYieldActionsMixin,
    _get_worker_for_request,
)


# ---------------------------------------------------------------------------
# Autouse fixture: patch transaction.atomic so no DB connection is needed
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_db_transaction():
    """Replace transaction.atomic() with a no-op context manager."""
    with patch(
        "workers.views.job_yield_actions.transaction.atomic",
        side_effect=lambda: nullcontext(),
    ):
        yield

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_worker():
    """A mock Worker representing the requesting worker."""
    worker = MagicMock()
    worker.pk = 3
    worker.hostname = "render-box-03"
    return worker


@pytest.fixture
def mock_job(mock_worker):
    """A mock Job in RENDERING state assigned to mock_worker."""
    job = MagicMock()
    job.pk = 42
    job.name = "Tile-001"
    job.status = JobStatus.RENDERING
    job.assigned_worker = mock_worker
    job.yield_requeue_count = 0
    job.auto_requeue_count = 0
    job.started_at = "2025-01-01T00:00:00Z"
    job.error_message = ""
    job.save = MagicMock()
    return job


@pytest.fixture
def mock_request(mock_worker):
    """A mock DRF request with a worker_profile."""
    request = MagicMock()
    request.user = MagicMock()
    request.user.worker_profile = mock_worker
    request.data = {"reason": "artist_return_gpu_contention"}
    return request


@pytest.fixture
def mixin():
    """Instantiate the mixin directly for unit testing."""
    return JobYieldActionsMixin()

# ---------------------------------------------------------------------------
# _get_worker_for_request helper
# ---------------------------------------------------------------------------


class TestGetWorkerForRequest:
    """The helper extracts the worker_profile from the request user."""

    def test_returns_worker_when_present(self, mock_request, mock_worker):
        result = _get_worker_for_request(mock_request)
        assert result is mock_worker

    def test_returns_none_when_no_worker_profile(self):
        request = MagicMock()
        request.user = MagicMock(spec=[])  # no worker_profile attr
        assert _get_worker_for_request(request) is None

# ---------------------------------------------------------------------------
# RENDERING -> QUEUED NOT in VALID_STATUS_TRANSITIONS (FR-7a)
# ---------------------------------------------------------------------------


class TestTransitionNotInMap:
    """RENDERING -> QUEUED must NOT be in VALID_STATUS_TRANSITIONS."""

    def test_rendering_to_queued_not_allowed(self):
        allowed = VALID_STATUS_TRANSITIONS.get(JobStatus.RENDERING, [])
        assert JobStatus.QUEUED not in allowed, (
            "RENDERING -> QUEUED must only be allowed via the "
            "dedicated yield-requeue endpoint, not the transition map."
        )

# ---------------------------------------------------------------------------
# Happy path: successful yield-requeue (FR-7b)
# ---------------------------------------------------------------------------


class TestYieldRequeueSuccess:
    """POST /api/jobs/{pk}/yield-requeue/ returns 200 on success."""

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_returns_200(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        response = mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert response.status_code == 200

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_response_body_contains_requeued_status(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        response = mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert response.data == {"status": "requeued"}

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_job_status_set_to_queued(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.status == JobStatus.QUEUED

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_assigned_worker_cleared(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.assigned_worker is None

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_started_at_cleared(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.started_at is None

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_error_message_set_with_reason(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.error_message == (
            "Interrupted: artist_return_gpu_contention"
        )

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_error_message_uses_unknown_when_no_reason(
        self, mock_qs, mixin, mock_job, mock_worker,
    ):
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        request = MagicMock()
        request.user = MagicMock()
        request.user.worker_profile = mock_worker
        request.data = {}  # no reason key
        mixin.yield_requeue(request, pk=mock_job.pk)
        assert mock_job.error_message == "Interrupted: unknown"

# ---------------------------------------------------------------------------
# 409: Job not in RENDERING status (FR-7b)
# ---------------------------------------------------------------------------


class TestYieldRequeueWrongStatus:
    """Return 409 when the job is not in RENDERING status."""

    @pytest.mark.parametrize("bad_status", [
        JobStatus.QUEUED,
        JobStatus.DONE,
        JobStatus.ERROR,
        JobStatus.CANCELED,
    ])
    @patch("workers.views.job_yield_actions.Job.objects")
    def test_non_rendering_status_returns_409(
        self, mock_qs, mixin, mock_request, mock_job, bad_status,
    ):
        mock_job.status = bad_status
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        response = mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert response.status_code == 409
        assert "Cannot yield-requeue" in response.data["error"]

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_409_body_includes_current_status(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        mock_job.status = JobStatus.DONE
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        response = mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert "DONE" in response.data["error"]

# ---------------------------------------------------------------------------
# 409: Requesting worker is not the assigned_worker (FR-7b)
# ---------------------------------------------------------------------------


class TestYieldRequeueWrongWorker:
    """Return 409 when the requesting worker is not the assigned worker."""

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_different_worker_returns_409(
        self, mock_qs, mixin, mock_job,
    ):
        other_worker = MagicMock()
        other_worker.pk = 99
        other_worker.hostname = "impostor-box"
        request = MagicMock()
        request.user = MagicMock()
        request.user.worker_profile = other_worker
        request.data = {"reason": "manual_override"}

        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        response = mixin.yield_requeue(request, pk=mock_job.pk)
        assert response.status_code == 409
        assert "assigned worker" in response.data["error"]

# ---------------------------------------------------------------------------
# 403: No worker_profile on request user
# ---------------------------------------------------------------------------


class TestYieldRequeueNoWorkerProfile:
    """Return 403 when the request user has no worker_profile."""

    def test_no_worker_profile_returns_403(self, mixin):
        request = MagicMock()
        request.user = MagicMock(spec=[])  # no worker_profile
        request.data = {}
        response = mixin.yield_requeue(request, pk=42)
        assert response.status_code == 403
        assert "No worker profile" in response.data["error"]

# ---------------------------------------------------------------------------
# 404: Job not found
# ---------------------------------------------------------------------------


class TestYieldRequeueJobNotFound:
    """Return 404 when the job pk does not exist."""

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_nonexistent_job_returns_404(
        self, mock_qs, mixin, mock_request,
    ):
        from workers.models import Job
        mock_qs.select_for_update.return_value.get.side_effect = (
            Job.DoesNotExist
        )
        response = mixin.yield_requeue(mock_request, pk=9999)
        assert response.status_code == 404
        assert response.data["error"] == "Job not found."
