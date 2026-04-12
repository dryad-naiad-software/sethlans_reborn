# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for yield_requeue_count vs auto_requeue_count separation.

Covers FR-7c from the worker-runtime-behavior spec:
- yield_requeue_count incremented on yield-requeue (NOT auto_requeue_count)
- Count accumulates across multiple yield-requeues
- Job model field configuration and defaults

Core endpoint tests (status checks, ownership, 409/403/404 responses)
live in ``test_yield_requeue_endpoint.py``.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

from workers.models.jobs import JobStatus
from workers.views.job_yield_actions import JobYieldActionsMixin


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
# FR-7c: yield_requeue_count incremented, NOT auto_requeue_count
# ---------------------------------------------------------------------------


class TestYieldRequeueCountIncrement:
    """yield_requeue_count must be incremented on each yield-requeue."""

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_yield_requeue_count_incremented(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """FR-7c: yield_requeue_count incremented, not auto_requeue_count."""
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.yield_requeue_count == 1

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_auto_requeue_count_not_incremented(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """FR-7c: auto_requeue_count must NOT be touched."""
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.auto_requeue_count == 0

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_yield_requeue_count_accumulates(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """FR-7c: count keeps growing across multiple yield-requeues."""
        mock_job.yield_requeue_count = 4
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.yield_requeue_count == 5

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_auto_requeue_untouched_after_multiple_yields(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """auto_requeue_count stays at its initial value even after
        multiple yield-requeue calls."""
        mock_job.auto_requeue_count = 2
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.auto_requeue_count == 2

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_yield_requeue_from_zero(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """First yield-requeue sets count from 0 to 1."""
        mock_job.yield_requeue_count = 0
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        assert mock_job.yield_requeue_count == 1

    @patch("workers.views.job_yield_actions.Job.objects")
    def test_save_called_with_correct_update_fields(
        self, mock_qs, mixin, mock_request, mock_job,
    ):
        """update_fields includes yield_requeue_count but not
        auto_requeue_count."""
        mock_qs.select_for_update.return_value.get.return_value = (
            mock_job
        )
        mixin.yield_requeue(mock_request, pk=mock_job.pk)
        mock_job.save.assert_called_once()
        call_kwargs = mock_job.save.call_args
        update_fields = call_kwargs[1]["update_fields"]
        expected = {
            "status", "assigned_worker", "started_at",
            "yield_requeue_count", "error_message",
        }
        assert set(update_fields) == expected


# ---------------------------------------------------------------------------
# Job model field: yield_requeue_count exists and is separate
# ---------------------------------------------------------------------------


class TestJobYieldRequeueCountField:
    """Verify the yield_requeue_count field on the Job model."""

    def test_field_exists_on_job_model(self):
        from workers.models import Job
        field = Job._meta.get_field("yield_requeue_count")
        assert field is not None

    def test_field_default_is_zero(self):
        from workers.models import Job
        field = Job._meta.get_field("yield_requeue_count")
        assert field.default == 0

    def test_auto_requeue_count_field_exists_separately(self):
        from workers.models import Job
        yield_field = Job._meta.get_field("yield_requeue_count")
        auto_field = Job._meta.get_field("auto_requeue_count")
        assert yield_field is not auto_field

    def test_yield_requeue_count_help_text_mentions_yield(self):
        from workers.models import Job
        field = Job._meta.get_field("yield_requeue_count")
        assert "yield" in field.help_text.lower()
