# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The stuck-job reaper survives a database error on one job (issue #225).

``requeue_stuck_jobs`` runs one ``atomic()`` per stuck job at the end of
every heartbeat.  A lock error on one job used to abort the rest of the
loop and turn the (already saved) heartbeat into a 500.  It now logs a
warning for that job, leaves it RENDERING for the next pass and goes on
with the others.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.utils import timezone

import workers.views.stuck_jobs as stuck_jobs
from workers.models import Job, JobStatus, Worker


@pytest.fixture(autouse=True)
def _reset_debounce():
    stuck_jobs._last_stuck_check = 0.0


@pytest.fixture
def stale_worker(db):
    user = get_user_model().objects.create_user(username='reaper_stale_user')
    worker = Worker.objects.create(hostname='reaper-stale', user=user, is_active=True)
    Worker.objects.filter(pk=worker.pk).update(
        last_seen=timezone.now() - timedelta(seconds=300),
    )
    return worker


def _stuck_job(asset, worker, name):
    return Job.objects.create(
        name=name, asset=asset, output_file_pattern='//render/#.png',
        status=JobStatus.RENDERING, assigned_worker=worker,
        started_at=timezone.now(),
    )


@pytest.mark.django_db
def test_reaper_skips_a_failing_job_and_processes_the_rest(mocker, asset, stale_worker):
    failing = _stuck_job(asset, stale_worker, 'ReapFail01')
    others = [_stuck_job(asset, stale_worker, f'ReapOk0{n}') for n in (1, 2)]
    real_requeue_or_fail = stuck_jobs.requeue_or_fail

    def flaky(job, message):
        if job.pk == failing.pk:
            raise OperationalError('database is locked')
        return real_requeue_or_fail(job, message)

    mocker.patch.object(stuck_jobs, 'requeue_or_fail', side_effect=flaky)
    warning = mocker.patch.object(stuck_jobs.logger, 'warning')

    assert stuck_jobs.requeue_stuck_jobs() == 2

    failing.refresh_from_db()
    assert failing.status == JobStatus.RENDERING
    assert failing.assigned_worker == stale_worker
    assert failing.auto_requeue_count == 0
    for job in others:
        job.refresh_from_db()
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
    warning.assert_called_once()
    assert warning.call_args.args[1] == failing.pk
    assert warning.call_args.kwargs.get('exc_info') is True


@pytest.mark.django_db
def test_failed_job_is_reaped_on_the_next_pass(mocker, asset, stale_worker):
    job = _stuck_job(asset, stale_worker, 'ReapRetry1')
    real_requeue_or_fail = stuck_jobs.requeue_or_fail
    flaky = mocker.patch.object(
        stuck_jobs, 'requeue_or_fail',
        side_effect=OperationalError('database is locked'),
    )
    mocker.patch.object(stuck_jobs.logger, 'warning')

    assert stuck_jobs.requeue_stuck_jobs() == 0
    stuck_jobs._last_stuck_check = 0.0
    flaky.side_effect = real_requeue_or_fail
    assert stuck_jobs.requeue_stuck_jobs() == 1

    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED
    assert job.auto_requeue_count == 1
