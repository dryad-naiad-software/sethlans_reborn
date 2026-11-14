# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``POST /api/jobs/{id}/upload_output/`` cannot undo a concurrent cancel/requeue.

Issue #217: the upload checked for CANCELED on an unlocked read, then
``output_file.save(save=True)`` wrote the whole stale row back.  A cancel
or requeue committed in between was lost.  The race is reproduced by
committing the admin change from ``validate_upload``, which the view
calls after its read and before its write.  A rejected upload must also
leave no file in storage.
"""

import pytest
from django.db import OperationalError

from workers.models import Job, JobStatus
from workers.services import worker_job_writes

from .worker_write_race_helpers import (
    JOBS_URL, cancel_job, create_claimed_job, png_upload, requeue_job,
    run_after, stored_render_files,
)

VALIDATION_HOOK = 'workers.views.job_worker_actions.validate_upload'


@pytest.fixture
def media_root(settings, tmp_path):
    """Store uploads under a per-test directory."""
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _upload(worker_client, job_id, thumbnail=False):
    data = {'output_file': png_upload()}
    if thumbnail:
        data['thumbnail'] = png_upload('thumb.png', size=(16, 16))
    return worker_client.post(
        f'{JOBS_URL}{job_id}/upload_output/', data=data, format='multipart',
    )


@pytest.mark.django_db(transaction=True)
class TestCancelDuringUpload:

    @pytest.mark.parametrize('thumbnail', [False, True],
                             ids=['output-only', 'with-thumbnail'])
    def test_cancel_survives_and_worker_gets_409(
        self, mocker, media_root, admin_client, worker_with_token, asset,
        thumbnail,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadCancel',
        )
        run_after(mocker, VALIDATION_HOOK, cancel_job(admin_client, job_id))

        resp = _upload(worker_client, job_id, thumbnail=thumbnail)

        assert resp.status_code == 409, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.CANCELED
        assert not job.output_file
        assert not job.thumbnail
        assert stored_render_files(media_root) == []


@pytest.mark.django_db(transaction=True)
class TestRequeueDuringUpload:

    def test_requeue_survives_and_worker_gets_409(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadRequeue',
        )
        run_after(mocker, VALIDATION_HOOK, requeue_job(job_id))

        resp = _upload(worker_client, job_id, thumbnail=True)

        assert resp.status_code == 409, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.QUEUED
        assert job.assigned_worker is None
        assert not job.output_file
        assert stored_render_files(media_root) == []


@pytest.mark.django_db(transaction=True)
class TestUploadNormalPath:

    def test_upload_saves_output_and_generates_thumbnail(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadOk',
        )
        save_spy = mocker.spy(Job, 'save')

        resp = _upload(worker_client, job_id)

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.RENDERING
        assert job.output_file.name.endswith('.png')
        # Server-side thumbnail from the post_save handler (on_commit).
        assert job.thumbnail.name
        first_save = next(
            c for c in save_spy.call_args_list if c.args[0].pk == job_id
        )
        assert first_save.kwargs['update_fields'] == ['output_file']
        assert len(stored_render_files(media_root)) == 2

    def test_worker_thumbnail_is_saved_with_output(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadThumb',
        )
        generate = mocker.patch('workers.image_utils.generate_thumbnail')

        resp = _upload(worker_client, job_id, thumbnail=True)

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.output_file.name.endswith('.png')
        assert job.thumbnail.name.endswith('_thumbnail.png')
        generate.assert_not_called()

    def test_then_done_patch_completes_job(
        self, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadThenDone',
        )
        assert _upload(worker_client, job_id).status_code == 200

        resp = worker_client.patch(
            f'{JOBS_URL}{job_id}/', data={'status': 'DONE'}, format='json',
        )

        assert resp.status_code == 200, resp.data
        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.DONE
        assert job.output_file.name

    def test_upload_to_reaped_job_is_rejected_without_storing(
        self, media_root, admin_client, worker_with_token, asset,
    ):
        """A job the stuck-job reaper set to ERROR keeps its worker but
        no longer accepts output."""
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadReaped',
        )
        Job.objects.filter(pk=job_id).update(status=JobStatus.ERROR)

        resp = _upload(worker_client, job_id)

        assert resp.status_code == 409, resp.data
        assert not Job.objects.get(pk=job_id).output_file
        assert stored_render_files(media_root) == []


@pytest.mark.django_db(transaction=True)
class TestUploadLockRetry:

    def test_lock_error_retries_without_storing_twice(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadRetry',
        )
        real_lock = worker_job_writes.lock_own_rows
        attempts = []

        def _locked_once(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError('database is locked')
            return real_lock(*args, **kwargs)
        mocker.patch.object(
            worker_job_writes, 'lock_own_rows', side_effect=_locked_once,
        )
        mocker.patch.object(worker_job_writes.time, 'sleep')

        resp = _upload(worker_client, job_id)

        assert resp.status_code == 200, resp.data
        assert Job.objects.get(pk=job_id).output_file.name
        # One output + one server thumbnail: the retry stored nothing new.
        assert len(stored_render_files(media_root)) == 2

    def test_exhausted_retries_remove_the_stored_file(
        self, mocker, media_root, admin_client, worker_with_token, asset,
    ):
        worker, worker_client = worker_with_token
        job_id = create_claimed_job(
            admin_client, worker, worker_client, asset, 'UploadNoRetry',
        )
        mocker.patch.object(
            worker_job_writes, 'lock_own_rows',
            side_effect=OperationalError('database is locked'),
        )
        mocker.patch.object(worker_job_writes.time, 'sleep')

        with pytest.raises(OperationalError):
            _upload(worker_client, job_id)

        job = Job.objects.get(pk=job_id)
        assert job.status == JobStatus.RENDERING
        assert not job.output_file
        assert stored_render_files(media_root) == []
