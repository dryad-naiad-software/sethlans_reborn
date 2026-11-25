# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for stuck job detection in workers/views/stuck_jobs.py: re-checks after the lock,
no stuck jobs, and the constants (the requeue cases are in test_stuck_jobs.py).

Covers AC-6, AC-6a, AC-6b, AC-6c, AC-7: auto-requeue of RENDERING
jobs with stale workers, max requeue limit, re-check after lock,
debounce, and last_output preservation.
"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from tests.unit.manager._stuck_jobs_helpers import (  # noqa: F401 - the fixtures are autouse
    _make_mock_job, _patch_job_and_transaction, _tx_patch, fixed_row_lock, reset_debounce,
)
from workers.models.jobs import Job as RealJob, JobStatus
from workers.views import stuck_jobs as stuck_jobs_module
from workers.views.stuck_jobs import (
    _MAX_AUTO_REQUEUE,
    _STUCK_CHECK_INTERVAL,
    requeue_stuck_jobs,
)


class TestRequeueStuckJobsRecheck:
    """AC-6b: Re-checks after acquiring the row lock."""

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_skips_if_status_changed_after_lock(self, mock_job_cls, _tx):
        """Job completed between filter and lock is NOT requeued."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(pk=50, worker_last_seen=stale_time)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        locked = _make_mock_job(pk=50, worker_last_seen=stale_time)
        locked.status = JobStatus.DONE
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        count = requeue_stuck_jobs()
        assert count == 0
        locked.save.assert_not_called()

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_skips_if_worker_recovered(self, mock_job_cls, _tx):
        """Worker recovered (fresh last_seen) is not requeued."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(pk=60, worker_last_seen=stale_time)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        fresh_time = timezone.now()
        locked = _make_mock_job(pk=60, worker_last_seen=fresh_time)
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        count = requeue_stuck_jobs()
        assert count == 0
        locked.save.assert_not_called()

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_skips_deleted_job(self, mock_job_cls, _tx):
        """Job deleted between filter and lock is silently skipped."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(pk=70, worker_last_seen=stale_time)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value \
            .get.side_effect = RealJob.DoesNotExist

        count = requeue_stuck_jobs()
        assert count == 0


class TestRequeueStuckJobsNoStuckJobs:
    """Edge case: no stuck jobs found."""

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_returns_zero_when_no_stuck_jobs(self, mock_job_cls, _tx):
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []
        assert requeue_stuck_jobs() == 0


class TestRequeueStuckJobsConstants:
    """Verify module constants have expected values."""

    def test_max_auto_requeue(self):
        assert _MAX_AUTO_REQUEUE == 3

    def test_stuck_check_interval(self):
        assert _STUCK_CHECK_INTERVAL == 30
