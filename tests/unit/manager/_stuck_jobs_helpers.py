# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared mocks and fixtures of the ``workers/views/stuck_jobs.py`` unit tests."""
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from workers.models.jobs import Job as RealJob, JobStatus
from workers.views import stuck_jobs as stuck_jobs_module


def _make_mock_job(
    pk=1,
    status=JobStatus.RENDERING,
    auto_requeue_count=0,
    worker_hostname="worker-01",
    worker_last_seen=None,
    last_output="",
):
    """Build a mock Job with an assigned_worker."""
    job = MagicMock()
    job.pk = pk
    job.name = f"Job-{pk}"
    job.status = status
    job.auto_requeue_count = auto_requeue_count
    job.last_output = last_output
    worker = MagicMock()
    worker.hostname = worker_hostname
    worker.last_seen = worker_last_seen or (
        timezone.now() - timedelta(seconds=200)
    )
    job.assigned_worker = worker
    job.save = MagicMock()
    return job


def _patch_job_and_transaction(mock_job_cls):
    """Configure mock_job_cls.DoesNotExist to be a real exception."""
    mock_job_cls.DoesNotExist = RealJob.DoesNotExist


@pytest.fixture(autouse=True)
def reset_debounce():
    """Reset the module-level debounce timestamp before each test."""
    stuck_jobs_module._last_stuck_check = 0.0
    yield


def _fixed_lock(queryset, *related, skip_locked=False):
    """``lock_own_rows`` on its FOR UPDATE OF path, whatever the test database is.

    On a backend without FOR UPDATE OF (MariaDB) the real helper takes a
    prefetch path, which the mock chains below do not describe; the
    helper's own paths are covered by ``test_row_locks``.
    """
    return queryset.select_for_update().select_related(*related)


# The ``lock_own_rows`` calls of the current test: (queryset, related, kwargs).
LOCK_CALLS = []


@pytest.fixture(autouse=True)
def fixed_row_lock(monkeypatch):
    LOCK_CALLS.clear()

    def lock(queryset, *related, **kwargs):
        LOCK_CALLS.append((queryset, related, kwargs))
        return _fixed_lock(queryset, *related, **kwargs)

    monkeypatch.setattr(stuck_jobs_module, 'lock_own_rows', lock)
    yield
    LOCK_CALLS.clear()


# Patch transaction.atomic for the tests that use it, so the function
# does not try to access the real database.
_tx_patch = patch.object(
    stuck_jobs_module, 'transaction',
    **{'atomic.return_value': nullcontext()},
)
