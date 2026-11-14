# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Post-commit behaviour of the locked job writes (issue #217 review).

1. ``on_commit`` callbacks run inside ``Atomic.__exit__``.  A callback that
   raises "database is locked" after the write committed must not make
   the write look failed: no retry, no 409, nothing stored twice.
2. The deferred thumbnail save writes only the thumbnail column.  A cancel
   committed while the thumbnail is generated survives, and a deleted
   row is not re-created.
"""

import os

import pytest
from django.db import OperationalError, transaction
from django.db.models.signals import post_save

from workers.models import Job, JobStatus
from workers.services import worker_job_writes
from workers.signal_helpers import _save_thumbnails_for_instances

from .worker_write_race_helpers import (
    JOBS_URL, cancel_job, create_claimed_job, png_upload, run_after,
    stored_render_files,
)


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def locked_after_commit():
    """Return ``arm()``: afterwards every Job save registers an on_commit
    callback that raises "database is locked"."""
    def _boom():
        raise OperationalError('database is locked')

    def _register(sender, instance, **kwargs):
        transaction.on_commit(_boom)

    uid = 'test-217-locked-after-commit'

    def _arm():
        post_save.connect(_register, sender=Job, weak=False, dispatch_uid=uid)
    yield _arm
    post_save.disconnect(sender=Job, dispatch_uid=uid)


def _once(action):
    done = []

    def _run():
        if not done:
            done.append(True)
            action()
    return _run


def _outputs(media_root):
    return [f for f in stored_render_files(media_root)
            if f'{os.sep}outputs{os.sep}' in f]


@pytest.mark.django_db(transaction=True)
class TestCallbackErrorAfterCommit:

    def test_done_patch_succeeds_without_retry(
        self, mocker, admin_client, worker_with_token, asset,
        locked_after_commit,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'CommitPatch',
        )
        locked_after_commit()
        lock_spy = mocker.spy(worker_job_writes, 'lock_own_rows')
        sleep = mocker.patch.object(worker_job_writes.time, 'sleep')

        resp = worker_client.patch(
            f'{JOBS_URL}{job_id}/', data={'status': 'DONE'}, format='json',
        )

        assert resp.status_code == 200, resp.data
        assert resp.data['status'] == 'DONE'
        assert Job.objects.get(pk=job_id).status == JobStatus.DONE
        assert lock_spy.call_count == 1
        sleep.assert_not_called()

    def test_upload_succeeds_without_storing_twice(
        self, mocker, media_root, admin_client, worker_with_token, asset,
        locked_after_commit,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'CommitUpload',
        )
        locked_after_commit()
        lock_spy = mocker.spy(worker_job_writes, 'lock_own_rows')
        mocker.patch.object(worker_job_writes.time, 'sleep')
        save_spy = mocker.spy(Job, 'save')

        resp = worker_client.post(
            f'{JOBS_URL}{job_id}/upload_output/',
            data={'output_file': png_upload()}, format='multipart',
        )

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.output_file.name
        assert lock_spy.call_count == 1
        assert len([c for c in save_spy.call_args_list
                    if c.args[0].pk == job_id]) == 1
        assert _outputs(media_root) == [
            os.path.join(str(media_root), job.output_file.name.replace('/', os.sep)),
        ]


@pytest.mark.django_db(transaction=True)
class TestThumbnailSaveKeepsConcurrentChanges:

    def test_cancel_during_thumbnail_generation_survives(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'ThumbCancel',
        )
        run_after(
            mocker, 'workers.image_utils.generate_thumbnail',
            _once(cancel_job(admin_client, job_id)),
        )

        resp = worker_client.post(
            f'{JOBS_URL}{job_id}/upload_output/',
            data={'output_file': png_upload()}, format='multipart',
        )

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.completed_at is not None
        assert job.thumbnail.name

    def test_stale_instance_writes_only_the_thumbnail(
        self, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'ThumbStale',
        )
        stale = Job.objects.get(pk=job_id)
        Job.objects.filter(pk=job_id).update(
            status=JobStatus.CANCELED, last_output='canceled meanwhile',
        )

        _save_thumbnails_for_instances(
            [stale], sender=Job, handler=None,
            thumb_content=png_upload().getvalue(),
        )

        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert job.last_output == 'canceled meanwhile'
        assert job.thumbnail.name == stale.thumbnail.name

    def test_deleted_row_is_not_recreated(
        self, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'ThumbGone',
        )
        stale = Job.objects.get(pk=job_id)
        Job.objects.filter(pk=job_id).delete()

        _save_thumbnails_for_instances(
            [stale], sender=Job, handler=None,
            thumb_content=png_upload().getvalue(),
        )

        assert not Job.objects.filter(pk=job_id).exists()
        assert stored_render_files(media_root) == []
