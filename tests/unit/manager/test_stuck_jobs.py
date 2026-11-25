# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for stuck job detection in workers/views/stuck_jobs.py.

Covers AC-6, AC-6a, AC-6b, AC-6c, AC-7: auto-requeue of RENDERING
jobs with stale workers, max requeue limit, re-check after lock,
debounce, and last_output preservation.
"""

import time
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from tests.unit.manager._stuck_jobs_helpers import (  # noqa: F401 - the fixtures are autouse
    LOCK_CALLS, _make_mock_job, _patch_job_and_transaction, _tx_patch, fixed_row_lock, reset_debounce,
)
from workers.models.jobs import JobStatus
from workers.views import stuck_jobs as stuck_jobs_module
from workers.views.stuck_jobs import (
    _MAX_AUTO_REQUEUE,
    _STUCK_CHECK_INTERVAL,
    requeue_stuck_jobs,
)


class TestRequeueStuckJobsDebounce:
    """AC-6c: Debounce prevents rapid repeated checks."""

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_first_call_runs(self, mock_job_cls, _tx):
        """First call after reset runs the check."""
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []
        result = requeue_stuck_jobs()
        assert result == 0
        mock_job_cls.objects.filter.assert_called_once()

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_second_call_within_interval_skipped(self, mock_job_cls, _tx):
        """Second call within _STUCK_CHECK_INTERVAL is skipped."""
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []
        requeue_stuck_jobs()
        mock_job_cls.objects.filter.reset_mock()

        result = requeue_stuck_jobs()
        assert result == 0
        mock_job_cls.objects.filter.assert_not_called()

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_call_after_interval_runs(self, mock_job_cls, _tx):
        """Call after interval elapses runs the check again."""
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = []
        requeue_stuck_jobs()

        stuck_jobs_module._last_stuck_check = (
            time.monotonic() - _STUCK_CHECK_INTERVAL - 1
        )
        mock_job_cls.objects.filter.reset_mock()

        result = requeue_stuck_jobs()
        assert result == 0
        mock_job_cls.objects.filter.assert_called_once()


class TestRequeueStuckJobsRequeue:
    """AC-6: Stuck jobs with stale workers are requeued."""

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_requeues_stuck_job(self, mock_job_cls, _tx):
        """A RENDERING job with a stale worker is requeued to QUEUED."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(pk=10, worker_last_seen=stale_time)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        locked = _make_mock_job(pk=10, worker_last_seen=stale_time)
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        count = requeue_stuck_jobs()

        assert count == 1
        assert locked.status == JobStatus.QUEUED
        assert locked.assigned_worker is None
        assert locked.started_at is None
        assert locked.completed_at is None
        assert locked.auto_requeue_count == 1
        assert "Auto-requeued" in locked.error_message
        locked.save.assert_called_once()

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_preserves_last_output(self, mock_job_cls, _tx):
        """AC-7: Auto-requeue does NOT clear last_output."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(pk=20, worker_last_seen=stale_time)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        locked = _make_mock_job(
            pk=20, worker_last_seen=stale_time,
            last_output="Render progress: 85%",
        )
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        requeue_stuck_jobs()

        assert locked.last_output == "Render progress: 85%"
        save_kwargs = locked.save.call_args.kwargs
        assert 'last_output' not in save_kwargs.get('update_fields', [])

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_increments_auto_requeue_count(self, mock_job_cls, _tx):
        """auto_requeue_count is incremented by 1 on each requeue."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(
            pk=30, auto_requeue_count=2, worker_last_seen=stale_time,
        )
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        locked = _make_mock_job(
            pk=30, auto_requeue_count=2, worker_last_seen=stale_time,
        )
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        requeue_stuck_jobs()
        assert locked.auto_requeue_count == 3
        assert locked.status == JobStatus.QUEUED

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_locks_only_the_job_row_through_lock_own_rows(self, mock_job_cls, _tx):
        """The job row is locked by the backend-aware helper, with its worker loaded."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [_make_mock_job(pk=40, worker_last_seen=stale_time)]
        locked = _make_mock_job(pk=40, worker_last_seen=stale_time)
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        requeue_stuck_jobs()

        assert LOCK_CALLS == [(mock_job_cls.objects, ('assigned_worker',), {})]
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.assert_called_once_with(pk=40)


class TestRequeueStuckJobsMaxExceeded:
    """AC-6a: Jobs exceeding max auto-requeue are set to ERROR."""

    @_tx_patch
    @patch.object(stuck_jobs_module, 'Job')
    def test_exceeds_max_sets_error(self, mock_job_cls, _tx):
        """Job with auto_requeue_count=3 gets set to ERROR on next."""
        _patch_job_and_transaction(mock_job_cls)
        stale_time = timezone.now() - timedelta(seconds=200)
        snapshot = _make_mock_job(
            pk=40, auto_requeue_count=3, worker_last_seen=stale_time,
        )
        mock_job_cls.objects.filter.return_value \
            .select_related.return_value = [snapshot]

        locked = _make_mock_job(
            pk=40, auto_requeue_count=3, worker_last_seen=stale_time,
        )
        mock_job_cls.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = locked

        count = requeue_stuck_jobs()

        assert count == 1
        assert locked.status == JobStatus.ERROR
        assert locked.auto_requeue_count == 4
        assert "Max auto-requeue attempts exceeded" in locked.error_message
        assert f"({_MAX_AUTO_REQUEUE})" in locked.error_message
        assert locked.completed_at is not None
        locked.save.assert_called_once()
