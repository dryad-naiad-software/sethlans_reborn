# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the submission state machine and the lock order
(Unreal sequence spec FR-SS-3, FR-SS-4, D22).

Covers: a failed piece fails its shots with its error, and the
submission ends PARTLY_DONE (with a message and ``completed_at``) once the
other piece is DONE; every piece failed gives ERROR; canceled pieces give
CANCELED; ``refresh_submission`` recomputes only the shots it is given;
and the D22 order Project -> Job -> SequenceDiscovery ->
SequenceSubmission -> SequenceFrame on submission create, ``shots-result/``
planning, ``frames/``, ``piece-result/`` and a worker status PATCH.
"""

from unittest import mock

import pytest

from workers.models import Job, SequenceShot, SequenceSubmission
from workers.parent_status import lock_parent
from workers.services.row_locks import lock_own_rows
from workers.services.sequence_status import refresh_submission
from workers.services.unreal_pilot import lock_project

from ..unreal_helpers import unreal_asset
from ..unreal_pilot_helpers import JOBS, claim, ready_worker
from .submission_helpers import (
    claimed_piece, done_discovery, finish_discovery, piece_body, pieces_of, post_piece, shots_of, submit,
    upload_all, upload_frame,
)


@pytest.fixture
def asset(db, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    return unreal_asset()


def _fail(job, message):
    job.refresh_from_db()
    job.status, job.error_message = 'ERROR', message
    job.save(update_fields=['status', 'error_message'])


def _finish(client, worker, job):
    ready_worker(worker)
    assert claim(client, job, worker).status_code == 200
    job.refresh_from_db()
    upload_all(client, job)
    assert post_piece(client, job, piece_body(job)).status_code == 200


@pytest.mark.django_db
class TestStates:

    def test_failed_piece_then_partly_done(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, piece = claimed_piece(admin_client, asset, worker, client)
        resp = client.patch(f'{JOBS}{piece.pk}/', {'status': 'ERROR', 'error_message': 'GPU lost'}, format='json')
        assert resp.status_code == 200, resp.data
        shots = shots_of(submission)
        assert [(s.status, s.error_message) for s in shots] == [('ERROR', 'GPU lost'), ('ERROR', 'GPU lost'),
                                                                ('PENDING', '')]
        submission.refresh_from_db()
        assert submission.status == 'QUEUED'
        _finish(client, worker, pieces_of(submission)[1])
        submission.refresh_from_db()
        assert submission.status == 'PARTLY_DONE'
        assert submission.error_message == "2 of 3 shots did not finish. sh010: GPU lost"
        assert submission.completed_at is not None

    def test_every_piece_failed_is_error(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        for piece in pieces_of(submission):
            _fail(piece, 'Render crashed')
        submission.refresh_from_db()
        assert submission.status == 'ERROR'
        assert {s.status for s in shots_of(submission)} == {'ERROR'}

    def test_canceled_pieces_cancel_the_submission(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        for piece in pieces_of(submission):
            assert admin_client.post(f'{JOBS}{piece.pk}/cancel/').status_code == 200
        submission.refresh_from_db()
        assert (submission.status, submission.error_message) == ('CANCELED', '')
        assert {s.status for s in shots_of(submission)} == {'CANCELED'}

    def test_rendering_while_a_piece_runs(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submission, _piece = claimed_piece(admin_client, asset, worker, client)
        assert [s.status for s in shots_of(submission)] == ['RENDERING', 'RENDERING', 'PENDING']
        submission.refresh_from_db()
        assert submission.status == 'RENDERING'

    def test_refresh_only_the_given_shots(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        submission, _ = submit(admin_client, asset)
        SequenceShot.objects.filter(submission=submission).update(status='DONE')
        assert refresh_submission(submission.pk, shot_indexes=[1]) == 'QUEUED'
        assert [s.status for s in shots_of(submission)] == ['PENDING', 'DONE', 'DONE']
        refresh_submission(submission.pk)
        assert [s.status for s in shots_of(submission)] == ['PENDING', 'PENDING', 'PENDING']

    def test_discovering_submission_untouched(self, admin_client, asset):
        submission, _ = submit(admin_client, asset)
        assert refresh_submission(submission.pk) is None
        assert SequenceSubmission.objects.get(pk=submission.pk).status == 'DISCOVERING'


def _recording(order):
    """``side_effect`` factories that log the locked model, then lock as the real function does."""
    def own_rows(queryset, *related):
        order.append(queryset.model.__name__)
        return lock_own_rows(queryset, *related)

    def parent(model, pk):
        order.append(model.__name__)
        return lock_parent(model, pk)

    def project(value):
        order.append('Project')
        return lock_project(value)

    return own_rows, parent, project


def _dedupe(order):
    return [name for i, name in enumerate(order) if i == 0 or order[i - 1] != name]


@pytest.mark.django_db
class TestLockOrder:

    def test_piece_result_locks_job_then_submission(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        upload_all(client, piece)
        order = []
        own_rows, parent, _project = _recording(order)
        with mock.patch('workers.services.sequence_pieces.lock_own_rows', side_effect=own_rows), \
                mock.patch('workers.services.sequence_status.lock_parent', side_effect=parent):
            assert post_piece(client, piece, piece_body(piece)).status_code == 200
        assert order == ['Job', 'SequenceSubmission']

    def test_frames_lock_job_then_frame(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        order = []
        own_rows, _parent, _project = _recording(order)
        with mock.patch('workers.services.sequence_frames.lock_own_rows', side_effect=own_rows):
            assert upload_frame(client, piece, 0).status_code == 200
        assert order == ['Job', 'SequenceFrame']

    def test_create_locks_project_first(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        done_discovery(admin_client, asset, worker, client)
        order = []
        own_rows, _parent, project = _recording(order)
        with mock.patch('workers.services.sequence_submit.lock_project', side_effect=project), \
                mock.patch('workers.services.sequence_create.lock_own_rows', side_effect=own_rows):
            submit(admin_client, asset)
        assert order == ['Project', 'SequenceSubmission']

    def test_shots_result_planning_order(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        submit(admin_client, asset)
        submission, _ = submit(admin_client, asset)
        order = []
        own_rows, _parent, project = _recording(order)
        with mock.patch('workers.services.sequence_shots_result.lock_project', side_effect=project), \
                mock.patch('workers.services.sequence_shots_result.lock_own_rows', side_effect=own_rows), \
                mock.patch('workers.services.sequence_submit.lock_own_rows', side_effect=own_rows), \
                mock.patch('workers.services.sequence_create.lock_own_rows', side_effect=own_rows):
            assert finish_discovery(worker, client, submission.discovery).status_code == 200
        assert _dedupe(order) == ['Project', 'Job', 'SequenceDiscovery', 'SequenceSubmission']
        assert order.count('SequenceSubmission') == 3  # the waiting list, then each submission's CAS lock

    def test_worker_patch_locks_job_then_submission(self, admin_client, asset, worker_with_token):
        worker, client = worker_with_token
        _submission, piece = claimed_piece(admin_client, asset, worker, client)
        order = []
        own_rows, parent, _project = _recording(order)
        with mock.patch('workers.services.worker_job_writes.lock_own_rows', side_effect=own_rows), \
                mock.patch('workers.services.sequence_status.lock_parent', side_effect=parent):
            resp = client.patch(f'{JOBS}{piece.pk}/', {'status': 'ERROR', 'error_message': 'x'}, format='json')
        assert resp.status_code == 200, resp.data
        assert order == ['Job', 'SequenceSubmission']
        assert Job.objects.get(pk=piece.pk).status == 'ERROR'
