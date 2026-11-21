# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the sound items and the audio jobs in the
submission state machine (Unreal sequence spec FR-SA-8, FR-SV-8, FR-SS-3,
FR-SS-8, FR-SL-5, D22, AC-34, AC-39).

Covers: an audio job that fails makes the sound items of its run's shots
and the whole sound item ERROR with "The sound render failed: <reason>."
(merged items too when asked for), leaves the other run alone, and the
submission still ends DONE once its pieces are; a project cancel-all
gives "... was canceled."; ``retry-sound/`` requeues the failed audio job
and clears the items, refuses a version with nothing to retry or a
canceled one, and a worker token gets 403; an audio job's success clears
an earlier failure's items; ``sound-result/`` locks Job, then the
submission.
"""

from unittest import mock

import pytest

from workers.models import Job, SequenceShot
from workers.parent_status import lock_parent
from workers.services.row_locks import lock_own_rows

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from .build_helpers import build_all, manual_queue
from .sound_helpers import audio_jobs_of, claimed_audio, post_sound
from .submission_helpers import (
    SUBMISSIONS, VIDEO, ffmpeg_ready, measure_submission, piece_body, pieces_of, post_piece, shots_of, upload_all,
)

FAILED = "The sound render failed: Audio streaming fault."


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


def _fail_audio(client, job, message='Audio streaming fault.'):
    resp = client.patch(f'{JOBS}{job.pk}/', {'status': 'ERROR', 'error_message': message}, format='json')
    assert resp.status_code == 200, resp.data


def _finish_pieces(client, worker, submission):
    measure_submission(submission.asset)
    for piece in pieces_of(submission):
        ready_worker(worker)
        assert claim(client, piece, worker).status_code == 200
        piece.refresh_from_db()
        upload_all(client, piece)
        assert post_piece(client, piece, piece_body(piece)).status_code == 200


def _sound(submission):
    return [(s.sound_status, s.sound_error, s.sound_video_status) for s in shots_of(submission)]


@pytest.mark.django_db
class TestFailedAudio:

    def test_items_of_its_run_fail_and_the_version_ends(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job)
        assert _sound(submission) == [('ERROR', FAILED, None), ('ERROR', FAILED, None), (None, '', None)]
        submission.refresh_from_db()
        assert (submission.sound_status, submission.sound_error) == ('ERROR', FAILED)
        assert submission.status == 'QUEUED'
        other = audio_jobs_of(submission)[1]
        Job.objects.filter(pk=other.pk).update(status='CANCELED')
        _finish_pieces(client, worker, submission)
        submission.refresh_from_db()
        assert submission.status == 'DONE' and submission.completed_at is not None

    def test_merged_items_fail_too(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        with ffmpeg_ready():
            submission, job = claimed_audio(admin_client, asset, worker, client, video_settings=VIDEO,
                                            merge_sound=True)
        _fail_audio(client, job, 'GPU lost')
        assert _sound(submission)[0] == ('ERROR', "The sound render failed: GPU lost.", 'ERROR')
        submission.refresh_from_db()
        assert submission.sound_video_status == 'ERROR'

    def test_reason_is_cleaned(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job, 'bad\x07 seektable\x1b')
        assert shots_of(submission)[0].sound_error == "The sound render failed: bad seektable."

    def test_project_cancel_all_cancels_the_sound(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _job = claimed_audio(admin_client, asset, worker, client)
        resp = admin_client.post(f'/api/projects/{asset.project_id}/cancel_all_jobs/')
        assert resp.status_code in (200, 202), resp.data
        assert {item[:2] for item in _sound(submission)} == {('ERROR', "The sound render was canceled.")}
        submission.refresh_from_db()
        assert submission.status == 'CANCELED'


@pytest.mark.django_db
class TestRetrySound:

    def _url(self, submission):
        return f'{SUBMISSIONS}{submission.pk}/retry-sound/'

    def test_requeues_and_clears(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job)
        resp = admin_client.post(self._url(submission))
        assert (resp.status_code, resp.data) == (200, {'requeued': 1})
        job.refresh_from_db()
        assert (job.status, job.assigned_worker_id, job.error_message, job.auto_requeue_count) == ('QUEUED', None, '', 0)
        assert _sound(submission) == [(None, '', None)] * 3
        submission.refresh_from_db()
        assert (submission.sound_status, submission.sound_error, submission.status) == (None, '', 'QUEUED')

    def test_nothing_to_retry_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _job = claimed_audio(admin_client, asset, worker, client)
        resp = admin_client.post(self._url(submission))
        assert (resp.status_code, resp.data) == (409, {'error': "No sound render of this version failed."})

    def test_canceled_version_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _job = claimed_audio(admin_client, asset, worker, client)
        assert admin_client.post(f'/api/projects/{asset.project_id}/cancel_all_jobs/').status_code in (200, 202)
        resp = admin_client.post(self._url(submission))
        assert (resp.status_code, resp.data) == (409, {'error': "This version was canceled."})
        assert {j.status for j in audio_jobs_of(submission)} == {'CANCELED'}

    def test_done_version_without_a_failed_sound_item_is_409(self, admin_client, asset, worker_with_token,
                                                             django_capture_on_commit_callbacks):
        """Review item 4: retry-sound reopens a version only for a sound item a failed render left ERROR."""
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        assert post_sound(client, job).status_code == 200
        other = audio_jobs_of(submission)[1]
        ready_worker(worker, source='override')
        assert claim(client, other, worker).status_code == 200
        other.refresh_from_db()
        assert post_sound(client, other).status_code == 200
        _finish_pieces(client, worker, submission)
        submission.refresh_from_db()
        assert submission.status == 'ASSEMBLING'  # the sound cuts wait for the build queue (chunk 6)
        with manual_queue():
            build_all(django_capture_on_commit_callbacks, submission.pk)
        submission.refresh_from_db()
        assert (submission.status, submission.sound_status) == ('DONE', 'DONE')
        assert admin_client.post(self._url(submission)).data == {'error': "No sound render of this version failed."}
        # A failed audio row that left no sound item ERROR does not reopen it either.
        Job.objects.filter(pk=job.pk).update(status='ERROR')
        resp = admin_client.post(self._url(submission))
        assert (resp.status_code, resp.data) == (409, {'error': "No sound render of this version failed."})
        submission.refresh_from_db()
        assert submission.status == 'DONE'
        assert Job.objects.get(pk=job.pk).status == 'ERROR'

    def test_partly_done_version_with_failed_sound_reopens(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job)
        pieces = pieces_of(submission)
        Job.objects.filter(pk=audio_jobs_of(submission)[1].pk).update(status='CANCELED')
        failed = pieces[1]
        failed.status, failed.error_message = 'ERROR', 'GPU lost'
        failed.save(update_fields=['status', 'error_message'])
        measure_submission(submission.asset)
        ready_worker(worker)
        assert claim(client, pieces[0], worker).status_code == 200
        pieces[0].refresh_from_db()
        upload_all(client, pieces[0])
        assert post_piece(client, pieces[0], piece_body(pieces[0])).status_code == 200
        submission.refresh_from_db()
        assert submission.status == 'PARTLY_DONE'
        assert admin_client.post(self._url(submission)).data == {'requeued': 2}
        submission.refresh_from_db()
        assert submission.status == 'RENDERING' and submission.completed_at is None

    def test_worker_token_is_403(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job)
        assert client.post(self._url(submission)).status_code == 403
        assert Job.objects.get(pk=job.pk).status == 'ERROR'

    def test_unknown_version_is_404(self, admin_client, db):
        assert admin_client.post(f'{SUBMISSIONS}987654/retry-sound/').status_code == 404

    def test_success_after_a_single_requeue_clears_the_items(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, job = claimed_audio(admin_client, asset, worker, client)
        _fail_audio(client, job)
        assert admin_client.post(f'{JOBS}{job.pk}/requeue/').status_code == 200
        assert _sound(submission)[0] == (None, '', None)
        ready_worker(worker, source='override')
        assert claim(client, job, worker).status_code == 200
        job.refresh_from_db()
        assert post_sound(client, job).status_code == 200
        assert {item[0] for item in _sound(submission)} == {None}
        assert SequenceShot.objects.filter(submission=submission, sound_status='ERROR').count() == 0


@pytest.mark.django_db
class TestLockOrder:

    def test_sound_result_locks_job_then_submission(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, job = claimed_audio(admin_client, asset, worker, client)
        order = []

        def own_rows(queryset, *related):
            order.append(queryset.model.__name__)
            return lock_own_rows(queryset, *related)

        def parent(model, pk):
            order.append(model.__name__)
            return lock_parent(model, pk)

        with mock.patch('workers.services.sequence_sound_result.lock_own_rows', side_effect=own_rows), \
                mock.patch('workers.services.sequence_sound_result.lock_parent', side_effect=parent), \
                mock.patch('workers.services.sequence_status.lock_parent', side_effect=parent):
            assert post_sound(client, job).status_code == 200
        assert order == ['Job', 'SequenceSubmission', 'SequenceSubmission']
