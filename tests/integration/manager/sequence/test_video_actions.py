# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the build actions and the task's claim (Unreal
sequence spec FR-SV-6, FR-SV-7, FR-SV-8, FR-SL-5, D16, D22, AC-18,
AC-35).

Covers: ``retry-video/`` resets failed builds, reopens a finished version
and builds them; 409 when nothing failed or the version was canceled;
and 409, rolled back, when every reset item would fail again at once;
``merge-sound/`` turns the merged copies on and builds them, 400 without
sound, 409 while the shots are read, 404 for an unknown version; a worker
token, an anonymous request and a non-staff user are refused on both; a
task claims nothing without a boot id, for a canceled version (the PENDING
item goes back to null) or twice; a task locks only the submission, never
a Job; a locked database while failing an item is retried.
"""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import OperationalError
from rest_framework.test import APIClient

from workers import video_sequence
from workers.models import SequenceShot, SequenceSubmission
from workers.parent_status import lock_parent
from workers.video_sequence import run_task

from ..unreal_helpers import unreal_asset
from .build_helpers import (
    BOOT, RUN_FFMPEG, FakeFfmpeg, build_all, finish_pieces, finish_sound, items, manual_queue, version,
)
from .sound_helpers import audio_jobs_of
from .submission_helpers import SUBMISSIONS, VIDEO, ffmpeg_ready, shots_of, submit


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


pytestmark = pytest.mark.django_db


def _url(submission, action):
    return f'{SUBMISSIONS}{submission.pk}/{action}/'


def _done_version(admin_client, asset, worker_with_token, capture, fake, merge=True, sound=True):
    worker, client = worker_with_token
    submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=sound, merge_sound=merge)
    if sound:
        finish_sound(client, worker, submission)
    finish_pieces(client, worker, submission)
    with manual_queue(), mock.patch(RUN_FFMPEG, fake):
        build_all(capture, submission.pk)
    submission.refresh_from_db()
    return submission


class TestRetryVideo:

    def test_rebuilds_the_failed_items(self, admin_client, asset, worker_with_token,
                                       django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(code=1))
        assert submission.status == 'DONE'
        assert {items(shot) for shot in shots_of(submission)} == {('ERROR', 'DONE', 'ERROR')}
        resp = admin_client.post(_url(submission, 'retry-video'))
        assert (resp.status_code, resp.data) == (200, {'reset': 8})  # 3 shot videos, 3 shot merges, 2 whole items
        submission.refresh_from_db()
        assert (submission.status, submission.completed_at, submission.video_status) == ('ASSEMBLING', None, 'PENDING')
        assert {items(shot) for shot in shots_of(submission)} == {('PENDING', 'DONE', 'PENDING')}
        with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
            build_all(django_capture_on_commit_callbacks, submission.pk)
        submission.refresh_from_db()
        assert (submission.status, items(submission)) == ('DONE', ('DONE', 'DONE', 'DONE'))

    def test_nothing_failed_is_409(self, admin_client, asset, worker_with_token, django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg())
        resp = admin_client.post(_url(submission, 'retry-video'))
        assert (resp.status_code, resp.data) == (
            409, {'error': "No video or sound file of this version failed to build."})

    def test_canceled_version_is_409(self, admin_client, asset, worker_with_token,
                                     django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(code=1))
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        resp = admin_client.post(_url(submission, 'retry-video'))
        assert (resp.status_code, resp.data) == (409, {'error': "This version was canceled."})

    def test_worker_token_and_unknown_version(self, admin_client, asset, worker_with_token,
                                              django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(code=1))
        _worker, client = worker_with_token
        assert client.post(_url(submission, 'retry-video')).status_code == 403
        assert client.post(_url(submission, 'merge-sound')).status_code == 403
        assert admin_client.post(f'{SUBMISSIONS}987654/retry-video/').status_code == 404
        assert admin_client.post(f'{SUBMISSIONS}987654/merge-sound/').status_code == 404
        assert {s.video_status for s in shots_of(submission)} == {'ERROR'}

    def test_anonymous_and_non_staff_are_refused(self, admin_client, asset, worker_with_token,
                                                 django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(code=1))
        plain = APIClient()
        plain.force_authenticate(get_user_model().objects.create_user('plain-viewer', password='pw-12345678'))
        for action in ('retry-video', 'merge-sound'):
            assert APIClient().post(_url(submission, action)).status_code in (401, 403)
            assert plain.post(_url(submission, action)).status_code == 403
        assert {s.video_status for s in shots_of(submission)} == {'ERROR'}

    def test_nothing_rebuildable_is_409_and_rolled_back(self, admin_client, asset, worker_with_token,
                                                        django_capture_on_commit_callbacks):
        """Review: merged copies whose sound render failed fail again at once, so nothing is rebuilt."""
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=True, merge_sound=True)
        for job in audio_jobs_of(submission):
            job.status, job.error_message = 'ERROR', 'Audio streaming fault.'
            job.save(update_fields=['status', 'error_message'])
        finish_pieces(client, worker, submission)
        with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
            build_all(django_capture_on_commit_callbacks, submission.pk)
        before = [items(shot) for shot in shots_of(submission)]
        assert set(before) == {('DONE', 'ERROR', 'ERROR')}
        resp = admin_client.post(_url(submission, 'retry-video'))
        assert (resp.status_code, resp.data) == (
            409, {'error': "No video or sound file of this version failed to build."})
        assert [items(shot) for shot in shots_of(submission)] == before


class TestMergeSound:

    def test_turns_merging_on_and_builds(self, admin_client, asset, worker_with_token,
                                         django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(), merge=False)
        assert (submission.status, submission.sound_video_status) == ('DONE', None)
        resp = admin_client.post(_url(submission, 'merge-sound'))
        assert (resp.status_code, resp.data) == (200, {'status': 'ASSEMBLING'})
        assert {shot.sound_video_status for shot in shots_of(submission)} == {'PENDING'}
        fake = FakeFfmpeg()
        with manual_queue(), mock.patch(RUN_FFMPEG, fake):
            build_all(django_capture_on_commit_callbacks, submission.pk)
        submission.refresh_from_db()
        assert (submission.merge_sound, submission.status, submission.sound_video_status) == (True, 'DONE', 'DONE')
        assert len(fake.of_kind('mux')) == 4 and not fake.of_kind('frames')
        assert admin_client.post(_url(submission, 'merge-sound')).data == {'status': 'DONE'}

    def test_without_sound_is_400(self, admin_client, asset, worker_with_token, django_capture_on_commit_callbacks):
        submission = _done_version(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks,
                                   FakeFfmpeg(), merge=False, sound=False)
        resp = admin_client.post(_url(submission, 'merge-sound'))
        assert resp.status_code == 400
        submission.refresh_from_db()
        assert submission.merge_sound is False

    def test_discovering_version_is_409(self, admin_client, asset):
        with ffmpeg_ready():
            submission, _ = submit(admin_client, asset, video_settings=VIDEO, sound=True)
        assert submission.status == 'DISCOVERING'
        resp = admin_client.post(_url(submission, 'merge-sound'))
        assert (resp.status_code, resp.data) == (409, {'error': "The shots of this version are still being read."})
        submission.refresh_from_db()
        assert submission.merge_sound is False


class TestClaim:

    def _pending(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=False)
        finish_pieces(client, worker, submission)
        return submission, (submission.pk, 1, 'video')

    def test_no_boot_id_claims_nothing(self, admin_client, asset, worker_with_token):
        submission, key = self._pending(admin_client, asset, worker_with_token)
        assert run_task(key, '') is False
        assert SequenceShot.objects.get(submission=submission, index=1).video_status == 'PENDING'

    def test_canceled_version_drops_the_item(self, admin_client, asset, worker_with_token):
        submission, key = self._pending(admin_client, asset, worker_with_token)
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        assert run_task(key, BOOT) is False
        assert SequenceShot.objects.get(submission=submission, index=1).video_status is None

    def test_one_claim_per_item_and_only_the_submission_is_locked(self, admin_client, asset, worker_with_token):
        submission, key = self._pending(admin_client, asset, worker_with_token)
        locked = []

        def spy(model, pk):
            locked.append(model.__name__)
            return lock_parent(model, pk)

        with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()), \
                mock.patch('workers.video_sequence.lock_parent', side_effect=spy), \
                mock.patch('workers.services.sequence_status.lock_parent', side_effect=spy):
            assert run_task(key, BOOT) is True
            assert run_task(key, BOOT) is False
        shot = SequenceShot.objects.get(submission=submission, index=1)
        assert (shot.video_status, shot.assembly_boot) == ('DONE', BOOT)
        assert set(locked) == {'SequenceSubmission'}

    def test_a_locked_database_while_failing_is_retried(self, admin_client, asset, worker_with_token):
        """Review: a lock error in the ``finally`` must not leave the item ASSEMBLING until a restart."""
        submission, key = self._pending(admin_client, asset, worker_with_token)
        real, calls = video_sequence.fail_if_ours, []

        def flaky(*args):
            calls.append(args)
            if len(calls) == 1:
                raise OperationalError('database is locked')
            return real(*args)

        with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg(code=1)), \
                mock.patch('workers.video_sequence.fail_if_ours', side_effect=flaky), \
                mock.patch('workers.services.worker_job_writes.time.sleep'):
            assert run_task(key, BOOT) is False
        assert len(calls) == 2
        shot = SequenceShot.objects.get(submission=submission, index=1)
        assert (shot.video_status, shot.video_error) == ('ERROR', "FFmpeg failed (exit 1); see the manager log.")
