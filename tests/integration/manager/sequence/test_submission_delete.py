# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``DELETE`` of a sequence version and the start
hook's folder sweep (Unreal sequence spec FR-SS-7, D16, D22, AC-22).

Covers: 409 while a piece renders or an item is assembling; otherwise the
rows and the storage folder are removed; a folder in use is left, the
manifest still refreshed, and the sweep removes it later; the sweep skips
names that are not ASCII digits, logs each removal with the folder's ids,
never runs on a database without submissions, and runs from the start
hook.
"""

import os
from unittest import mock

import pytest

from workers import video_startup
from workers.models import Job, SequenceFrame, SequenceShot, SequenceSubmission
from workers.services import sequence_delete

from ..unreal_helpers import unreal_asset
from .build_helpers import finish_pieces, version
from .submission_helpers import SUBMISSIONS, done_discovery, pieces_of, pilot_of, submit

pytestmark = pytest.mark.django_db


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return settings.MEDIA_ROOT


@pytest.fixture
def asset(db, media):
    return unreal_asset()


class TestDelete:

    def test_running_piece_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=False)
        Job.objects.filter(pk=pieces_of(submission)[0].pk).update(status='RENDERING', assigned_worker=worker)
        resp = admin_client.delete(f'{SUBMISSIONS}{submission.pk}/')
        assert (resp.status_code, resp.data) == (409, {'error': "A render of this version is still running."})
        assert SequenceSubmission.objects.filter(pk=submission.pk).exists()

    def test_assembling_item_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=False)
        SequenceShot.objects.filter(submission=submission, index=2).update(video_status='ASSEMBLING')
        resp = admin_client.delete(f'{SUBMISSIONS}{submission.pk}/')
        assert (resp.status_code, resp.data) == (
            409, {'error': "A video or sound file of this version is still being built."})

    def test_removes_rows_and_files(self, admin_client, asset, worker_with_token, media,
                                    django_capture_on_commit_callbacks):
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, sound=False)
        finish_pieces(client, worker, submission)
        folder = os.path.join(media, 'sequences', str(asset.project_id), str(submission.pk))
        assert os.path.isdir(folder) and SequenceFrame.objects.count() == 14
        pilot = pilot_of(asset)
        with django_capture_on_commit_callbacks(execute=True):
            resp = admin_client.delete(f'{SUBMISSIONS}{submission.pk}/')
        assert resp.status_code == 204
        assert not SequenceSubmission.objects.filter(pk=submission.pk).exists()
        assert not Job.objects.filter(sequence_submission_id=submission.pk).exists()
        assert not SequenceShot.objects.exists() and not SequenceFrame.objects.exists()
        assert not os.path.exists(folder)
        assert Job.objects.filter(pk=pilot.pk).exists()
        assert admin_client.delete(f'{SUBMISSIONS}{submission.pk}/').status_code == 404

    def test_folder_in_use_is_left_and_removed_at_the_next_start(self, admin_client, asset, worker_with_token, media,
                                                                 django_capture_on_commit_callbacks):
        """Review item 2: an open file (Windows) leaves the folder; the manifest is still refreshed."""
        worker, client = worker_with_token
        kept = version(admin_client, asset, worker, client, sound=False)
        finish_pieces(client, worker, kept)
        deleted = version(admin_client, unreal_asset(asset.project, name='Second'), worker, client, sound=False)
        finish_pieces(client, worker, deleted)
        root = os.path.join(media, 'sequences')
        folder = os.path.join(root, str(deleted.project_id), str(deleted.pk))
        with mock.patch('workers.services.sequence_delete.shutil.rmtree', side_effect=PermissionError('in use')), \
                mock.patch('workers.services.sequence_create._update_manifest') as manifest, \
                django_capture_on_commit_callbacks(execute=True):
            assert admin_client.delete(f'{SUBMISSIONS}{deleted.pk}/').status_code == 204
        manifest.assert_called_once_with(deleted.project_id)
        assert os.path.isdir(folder)
        assert sequence_delete.remove_orphan_folders() == 1
        assert not os.path.exists(folder)
        assert os.path.isdir(os.path.join(root, str(kept.project_id), str(kept.pk)))
        assert sequence_delete.remove_orphan_folders() == 0

    def test_sweep_skips_odd_names_and_logs_each_removal(self, admin_client, asset, worker_with_token, media):
        """Gatekeeper: a non-ASCII digit name must not abort the sweep; removals are logged with ids."""
        worker, client = worker_with_token
        kept = version(admin_client, asset, worker, client, sound=False)
        project_dir = os.path.join(media, 'sequences', str(asset.project_id))
        for name in ('²', 'abc', '987654', str(kept.pk)):
            os.makedirs(os.path.join(project_dir, name), exist_ok=True)
        with mock.patch.object(sequence_delete, 'logger') as log:
            assert sequence_delete.remove_orphan_folders() == 1
        assert sorted(os.listdir(project_dir)) == sorted(['²', 'abc', str(kept.pk)])
        log.info.assert_any_call(
            "Start sweep removed the folder of deleted sequence submission %s (project %s).",
            987654, str(asset.project_id))

    def test_sweep_never_runs_on_a_database_without_submissions(self, db, media):
        """Gatekeeper: a swapped or empty database must not wipe the version folders."""
        folder = os.path.join(media, 'sequences', 'some-project', '12')
        os.makedirs(folder)
        assert sequence_delete.remove_orphan_folders() == 0
        assert os.path.isdir(folder)

    def test_start_hook_runs_the_sweep(self, db, media):
        with mock.patch('workers.services.sequence_delete.remove_orphan_folders', return_value=0) as sweep, \
                mock.patch('workers.video_startup.video_queue') as queue:
            queue.is_accepting.return_value = False
            video_startup.on_server_start()
        sweep.assert_called_once_with()
