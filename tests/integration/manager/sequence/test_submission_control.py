# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``cancel/`` and ``shots/<index>/rerender/`` of a
sequence version (Unreal sequence spec FR-SS-5, FR-SS-6, Q9, D22, AC-18,
AC-22).  ``DELETE`` is in ``test_submission_delete.py``.

Covers: cancel leaves the shared pilot QUEUED and the shots job alone and
cancels every piece and audio job (running ones too); a DISCOVERING
version is canceled while another waiting on the same discovery is planned
when it finishes; a finished version is 409; cancel locks every job in id
order before the submission, and the claim refuses a piece of a canceled
version; re-rendering a failed shot of a PARTLY_DONE version ends DONE
with the whole video built; re-render is 409 for a shot that did not fail
or a canceled version, 404 for an unknown shot; the lock order.
"""

from unittest import mock

import pytest

from workers.models import Job, SequenceSubmission
from workers.parent_status import lock_parent
from workers.services import sequence_control

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import claim, ready_worker
from .build_helpers import RUN_FFMPEG, FakeFfmpeg, build_all, finish_pieces, manual_queue, version
from .sound_helpers import audio_jobs_of
from .submission_helpers import (
    SUBMISSIONS, VIDEO, done_discovery, finish_discovery, measure_submission, piece_body, pieces_of, pilot_of,
    post_piece, shots_of, submit, upload_all,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return settings.MEDIA_ROOT


@pytest.fixture
def asset(db, media):
    return unreal_asset()


def _post(client, submission, action):
    return client.post(f'{SUBMISSIONS}{submission.pk}/{action}/')


def _statuses(jobs):
    return {job.pk: Job.objects.get(pk=job.pk).status for job in jobs}


class TestCancel:

    def test_cancels_pieces_and_audio_but_not_the_shared_jobs(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        discovery = done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=True)
        measure_submission(asset, worker=worker, submission=submission)  # per-machine D10
        audio = audio_jobs_of(submission)
        assert claim(client, audio[0], worker).status_code == 200
        resp = _post(admin_client, submission, 'cancel')
        assert (resp.status_code, resp.data) == (200, {'status': 'CANCELED', 'canceled': 4})
        assert set(_statuses(pieces_of(submission) + audio).values()) == {'CANCELED'}
        assert pilot_of(asset).status == 'QUEUED'
        assert Job.objects.get(pk=discovery.job_id).status == 'DONE'
        submission.refresh_from_db()
        assert (submission.cancel_requested, submission.status) == (True, 'CANCELED')
        assert {shot.status for shot in shots_of(submission)} == {'CANCELED'}

    def test_waiting_versions_on_one_discovery(self, admin_client, asset, worker_with_token):
        """AC-22: canceling one waiting version leaves the other waiting; it is planned later."""
        worker, client = worker_with_token
        canceled, _ = submit(admin_client, asset)
        waiting, _ = submit(admin_client, asset)
        resp = _post(admin_client, canceled, 'cancel')
        assert (resp.status_code, resp.data) == (200, {'status': 'CANCELED', 'canceled': 0})
        waiting.refresh_from_db()
        assert waiting.status == 'DISCOVERING'
        assert finish_discovery(worker, client, canceled.discovery).status_code == 200
        canceled.refresh_from_db()
        waiting.refresh_from_db()
        assert (canceled.status, waiting.status) == ('CANCELED', 'QUEUED')
        assert not pieces_of(canceled) and len(pieces_of(waiting)) == 2

    def test_finished_version_is_409(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission = version(admin_client, asset, worker, client, sound=False)
        finish_pieces(client, worker, submission)
        submission.refresh_from_db()
        assert submission.status == 'DONE'
        resp = _post(admin_client, submission, 'cancel')
        assert (resp.status_code, resp.data) == (409, {'error': "This version has finished; there is nothing to cancel."})
        submission.refresh_from_db()
        assert (submission.status, submission.cancel_requested) == ('DONE', False)

    def test_unknown_version_is_404(self, admin_client, db):
        assert admin_client.post(f'{SUBMISSIONS}987654/cancel/').status_code == 404

    def test_locks_every_job_in_id_order_before_the_submission(self, admin_client, asset, worker_with_token):
        """Review item 1: the same order as rerender and delete, so a requeued piece is seen and none deadlock."""
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=True)
        order, real_rows = [], sequence_control.lock_own_rows

        def rows(queryset, *args):
            locked = real_rows(queryset, *args)
            order.append((queryset.model.__name__, sorted(locked.values_list('pk', flat=True))))
            return locked

        def parent(model, pk):
            order.append((model.__name__, pk))
            return lock_parent(model, pk)

        with mock.patch('workers.services.sequence_control.lock_own_rows', side_effect=rows), \
                mock.patch('workers.services.sequence_control.lock_parent', side_effect=parent):
            assert _post(admin_client, submission, 'cancel').status_code == 200
        jobs = sorted(job.pk for job in pieces_of(submission) + audio_jobs_of(submission))
        assert order == [('Job', jobs), ('SequenceSubmission', submission.pk)]

    def test_claim_of_a_canceled_versions_piece_is_refused(self, admin_client, asset, worker_with_token):
        """Review item 1: a piece requeued after the cancel's update is never rendered."""
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset, sound=False)
        measure_submission(asset)
        ready_worker(worker)
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        piece = pieces_of(submission)[0]
        resp = claim(client, piece, worker)
        assert (resp.status_code, resp.data) == (409, {'error': "This job's sequence version was canceled."})
        assert Job.objects.get(pk=piece.pk).status == 'QUEUED'
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=False)
        assert claim(client, piece, worker).status_code == 200


def _partly_done(admin_client, asset, worker_with_token, capture):
    worker, client = worker_with_token
    submission = version(admin_client, asset, worker, client, video_settings=VIDEO, sound=False)
    finish_pieces(client, worker, submission, fail_last=True)
    with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
        build_all(capture, submission.pk)
    submission.refresh_from_db()
    return submission


class TestRerender:

    def test_failed_shot_renders_again_and_the_whole_video_is_built(
            self, admin_client, asset, worker_with_token, django_capture_on_commit_callbacks):
        """AC-18."""
        capture = django_capture_on_commit_callbacks
        submission = _partly_done(admin_client, asset, worker_with_token, capture)
        assert (submission.status, submission.video_status) == ('PARTLY_DONE', None)
        assert [(s.status, s.video_status) for s in shots_of(submission)] == [
            ('DONE', 'DONE'), ('DONE', 'DONE'), ('ERROR', None)]
        resp = _post(admin_client, submission, 'shots/3/rerender')
        # Other pieces are DONE, so the version counts as started (FR-SS-3).
        assert (resp.status_code, resp.data) == (200, {'status': 'RENDERING', 'requeued': 1})
        assert shots_of(submission)[-1].status == 'PENDING'
        piece = pieces_of(submission)[-1]
        assert (piece.status, piece.assigned_worker_id, piece.auto_requeue_count) == ('QUEUED', None, 0)
        worker, client = worker_with_token
        ready_worker(worker)
        assert claim(client, piece, worker).status_code == 200
        piece.refresh_from_db()
        upload_all(client, piece)
        assert post_piece(client, piece, piece_body(piece)).status_code == 200
        with manual_queue(), mock.patch(RUN_FFMPEG, FakeFfmpeg()):
            build_all(capture, submission.pk)
        submission.refresh_from_db()
        assert (submission.status, submission.video_status) == ('DONE', 'DONE')

    def test_shot_that_did_not_fail_is_409(self, admin_client, asset, worker_with_token,
                                           django_capture_on_commit_callbacks):
        submission = _partly_done(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks)
        resp = _post(admin_client, submission, 'shots/1/rerender')
        assert (resp.status_code, resp.data) == (409, {'error': "Only a failed or canceled shot can be rendered again."})
        assert admin_client.post(f'{SUBMISSIONS}{submission.pk}/shots/9/rerender/').status_code == 404

    def test_canceled_version_is_409_and_rolled_back(self, admin_client, asset, worker_with_token,
                                                     django_capture_on_commit_callbacks):
        submission = _partly_done(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks)
        SequenceSubmission.objects.filter(pk=submission.pk).update(cancel_requested=True)
        resp = _post(admin_client, submission, 'shots/3/rerender')
        assert (resp.status_code, resp.data) == (409, {'error': "This version was canceled."})
        assert pieces_of(submission)[-1].status == 'ERROR'
        assert shots_of(submission)[-1].status == 'ERROR'

    def test_lock_order_job_then_submission(self, admin_client, asset, worker_with_token,
                                            django_capture_on_commit_callbacks):
        submission = _partly_done(admin_client, asset, worker_with_token, django_capture_on_commit_callbacks)
        order = []
        real_rows = sequence_control.lock_own_rows

        def rows(queryset, *args):
            order.append(queryset.model.__name__)
            return real_rows(queryset, *args)

        def parent(model, pk):
            order.append(model.__name__)
            return lock_parent(model, pk)

        with mock.patch('workers.services.sequence_control.lock_own_rows', side_effect=rows), \
                mock.patch('workers.services.sequence_control.lock_parent', side_effect=parent):
            assert _post(admin_client, submission, 'shots/3/rerender').status_code == 200
        assert order == ['Job', 'SequenceSubmission']
