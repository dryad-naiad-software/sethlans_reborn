# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the worker-write race tests (issue #217).

The race is: a worker request reads the job, an admin cancel or requeue
commits, then the worker request saves.  The tests reproduce it on one
thread by running the admin change from a hook the view calls between
its read and its write (:func:`run_after`).
"""

import io
import os

from django.utils import timezone
from PIL import Image

from workers.models import Job, JobStatus

JOBS_URL = '/api/jobs/'


def png_upload(name='output.png', size=(64, 64)):
    """Return an in-memory PNG upload named ``name``."""
    buf = io.BytesIO()
    Image.new('RGB', size, color='red').save(buf, format='PNG')
    buf.seek(0)
    buf.name = name
    return buf


def create_job(admin_client, asset, name):
    """Create a QUEUED job through the API and return its id."""
    resp = admin_client.post(
        JOBS_URL,
        data={
            'name': name,
            'asset_id': asset.pk,
            'output_file_pattern': '//render/#.png',
        },
        format='json',
    )
    assert resp.status_code == 201, resp.data
    return resp.data['id']


def claim(worker, worker_client, job_id):
    """Claim ``job_id`` for ``worker`` (job becomes RENDERING)."""
    resp = worker_client.post(
        f'{JOBS_URL}{job_id}/claim/',
        data={'worker_id': worker.pk},
        format='json',
    )
    assert resp.status_code == 200, resp.data
    return resp


def create_claimed_job(admin_client, worker, worker_client, asset, name):
    """Create a job and claim it for ``worker``; return the job id."""
    job_id = create_job(admin_client, asset, name)
    claim(worker, worker_client, job_id)
    return job_id


def cancel_job(admin_client, job_id):
    """Return a callable that cancels ``job_id`` through the admin API."""
    def _cancel():
        resp = admin_client.post(f'{JOBS_URL}{job_id}/cancel/')
        assert resp.status_code == 200, resp.data
    return _cancel


def requeue_job(job_id):
    """Return a callable that commits a requeue of a RENDERING job.

    Writes the fields the requeue action resets.  (The requeue endpoint
    only accepts ERROR/CANCELED jobs; a RENDERING job returns to QUEUED
    through the stuck-job reaper or a cancel + requeue, with the same
    resulting row.)
    """
    def _requeue():
        Job.objects.filter(pk=job_id).update(
            status=JobStatus.QUEUED, assigned_worker=None,
            started_at=None, completed_at=None, error_message='',
            last_output='', auto_requeue_count=0, is_paused=False,
        )
    return _requeue


def reassign_job(job_id, other_worker):
    """Return a callable that requeues ``job_id`` and gives it to another worker."""
    def _reassign():
        Job.objects.filter(pk=job_id).update(
            status=JobStatus.RENDERING, assigned_worker=other_worker,
            started_at=timezone.now(),
        )
    return _reassign


def run_after(mocker, target, action):
    """Patch ``target`` so ``action()`` runs right after each real call.

    ``target`` is an import path (``'pkg.module.func'``) or a
    ``(class, 'method')`` pair.  Returns the mock.
    """
    if isinstance(target, tuple):
        owner, name = target
        original = getattr(owner, name)

        def _method(*args, **kwargs):
            result = original(*args, **kwargs)
            action()
            return result
        return mocker.patch.object(
            owner, name, autospec=True, side_effect=_method,
        )

    module_path, name = target.rsplit('.', 1)
    module = __import__(module_path, fromlist=[name])
    original = getattr(module, name)

    def _function(*args, **kwargs):
        result = original(*args, **kwargs)
        action()
        return result
    return mocker.patch(target, side_effect=_function)


def stored_render_files(media_root):
    """Return every file under an ``outputs`` or ``thumbnails`` directory."""
    found = []
    for dirpath, _dirs, files in os.walk(media_root):
        parts = set(os.path.normpath(dirpath).split(os.sep))
        if parts & {'outputs', 'thumbnails'}:
            found.extend(os.path.join(dirpath, f) for f in files)
    return sorted(found)
